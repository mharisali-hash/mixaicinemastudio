"""The print job's stage machine (contract v41): concepts -> pictures -> layout -> print files.

Ad Studio's runner with print steps: the creative director writes one concept per option to a family plan
(type only, a photo, or an illustration), photo and illustration options get their picture (generated, or the
owner's own photo), upscaled with Real-ESRGAN up to what the print needs (8192 px at most; Lanczos does the
rest), then every option is laid out at a working size. The files stage renders at print size and writes the
PDF, PNG, JPG, SVG and layers ZIP, for every option when the brief asks for it (the MCP tool does), else only
for the designs someone asked to download.
"""
import copy
import logging
import time

from PIL import Image
from sqlalchemy.orm.attributes import flag_modified

from app import brand as br
from app import library as lib
from app import models_catalog as mc
from app import rights
from app import styles as st
from app.ads import colours as col
from app.ads import copy as cp
from app.ads import service as ads
from app.ads.stages import Review, Run, brand_context, brand_tokens
from app.autopilot import POLL_S, WAIT_PRIORITY, StageFailed, Wait
from app.config import get_settings
from app.llm import LLMError, chat_sync
from app.llm.print_prompts import (PRINT_ART_LOOK, PRINT_NO_TEXT, PRINT_PHOTO_LOOK, PrintConcepts,
                                   print_concept_messages)
from app.models import AdCampaign, Generation, Job
from app.photo import restore
from app.photo.restore import RestoreError
from app.print_studio import layout as pl
from app.print_studio import service as ps
from app.print_studio import sizes as sz
from app.services import generation_file
from app.upscale_image import ANIME_MODEL

log = logging.getLogger("mixai.print")
ESRGAN_LONG = 8192
ART_NEGATIVE = "text, letters, words, numbers, watermark, logo, photo, photorealistic, busy background, shadow"


class PrintRun(Run):
    @property
    def b(self) -> dict:
        return ps.brief_of(self.c)

    def tick(self, frac: float = 0.0, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        i = ps.STAGE_KEYS.index(self.current) if self.current in ps.STAGE_KEYS else 0
        label = dict(ps.STAGES).get(self.current or "", "")
        self.save()
        self.ctx.progress((i + max(0.0, min(1.0, frac))) / len(ps.STAGE_KEYS),
                          f"{label}: {self.message}" if label and self.message else self.message or label)

    def run(self) -> dict:
        for key in ps.STAGE_KEYS:
            self.current = key
            rec = self.rec()
            if rec.get("status") in ("done", "skipped"):
                continue
            reviewed = rec.get("status") == "review"
            self.c.stage = key
            rec.update(status="running", detail=None)
            self.put(rec)
            self.tick(0.0, "")
            while True:
                out = getattr(self, f"stage_{key}")(reviewed)
                if isinstance(out, Wait):
                    self.wait(out)
                    continue
                break
            rec = self.rec()
            if out is not None and not isinstance(out, str):
                rec.update(status="review", detail=out.message)
                self.put(rec)
                self.r["waiting_on"] = []
                self.tick(1.0, out.message)
                return copy.deepcopy(self.r)
            rec.update(status="done", detail=out if isinstance(out, str) else None)
            rec.pop("want", None)
            self.put(rec)
            self.tick(1.0, "")
        self.c.stage = "done"
        self.r["waiting_on"] = []
        self.save()
        return copy.deepcopy(self.r)

    def wait(self, w: Wait) -> None:
        from app.ads.stages import _w

        ids = list(dict.fromkeys(w.job_ids))
        self.r["waiting_on"] = ids
        self.tick(message=w.message)
        if not self.pending(ids):
            self.spins += 1
            if self.spins > 200:
                raise StageFailed("Stuck waiting for work that already finished; retry the job")
            return
        self.spins = 0
        if not self.other_work_queued():
            deadline = time.monotonic() + POLL_S
            while time.monotonic() < deadline:
                time.sleep(1.0)
                self.tick()
                if not self.pending(ids):
                    return
                if self.other_work_queued():
                    break
        self.db.commit()
        raise _w().JobDeferred(f"{dict(ps.STAGES)[self.current]}: {w.message}", priority=WAIT_PRIORITY)

    # ---------------------------------------------------------------- concepts
    def write(self, count: int, families: list[str] | None = None, avoid: list[str] | None = None) -> list[dict]:
        b, c = self.b, self.c
        families = families or ps.families_for(b, count)
        cv = ps.canvas_of(b)
        shape = ps.shape_family(b)
        logo = ps.kit_logo(self.kit)
        layouts = {f: ps.layouts_for(b, f, logo) for f in pl.DESIGN_FAMILIES}
        lines = b["lines"]
        msgs = print_concept_messages(
            job=b["job"], job_label=sz.JOB_LABELS.get(b["job"], "Sign"), shape=shape,
            size_label=f"{sz.cm(cv.width_mm)} × {sz.cm(cv.height_mm)} cm",
            distance_label=sz.DISTANCE_LABELS.get(b["viewing_distance"], "the street"),
            business_type=b.get("business_type") or "", lines=lines, language=c.language, market=b["market"],
            style=" ".join(x for x in (b.get("style") or "", b.get("brief") or "") if x).strip(),
            brand=brand_context(self.kit), palette_words=col.palette_words(self.kit.palette) if self.kit else [],
            families=families, layouts=layouts, avoid=avoid, has_photos=bool(b.get("photo_media_ids")))
        try:
            res = chat_sync("creative", msgs, schema=PrintConcepts, think=False, temperature=0.8,
                            max_tokens=700 + 420 * count, tick=lambda: self.tick(0.1), model=self.writer())
        except LLMError as e:
            raise StageFailed(f"The copywriter is offline: {e}") from e
        rows = [d.model_dump() for d in res.data.concepts[:count]]
        if not rows:
            raise StageFailed("The copywriter came back with no designs")
        while len(rows) < count:
            # a short answer still gives as many options as asked: the extra ones lean on the first's copy
            rows.append({**rows[len(rows) % len(rows)]})
        for r, fam in zip(rows, families):
            r["family"] = fam
            r.update(kicker="", body="")
        ps.spread(rows, b, logo)
        if avoid is None:
            # a rewrite of one option keeps the layout it had; only a fresh set needs the logo-led one
            ps.offer_logo_hero(rows, b, self.kit)
        rows = self.self_check(rows)
        extra = " ".join(str(v) for k, v in lines.items() if k != "offers" and v) + " " + " ".join(lines["offers"])
        channels = cp.channels_of({"phone": lines["phone"], "website": lines["website"]}, [])
        out = []
        for data in rows:
            had = (data.get("headline") or "").strip()
            clean, report = cp.guard(data, lines["offers"], extra, channels)
            if not had or (clean.get("headline") == clean.get("cta") and had != clean.get("cta")):
                # no hero line is a choice on a sign, not a gap for the guard to fill with the button
                clean["headline"] = ""
            out.append((clean, report))
        self.mark(out)
        return [{**d, "_guard": g} for d, g in out]

    def stage_concepts(self, reviewed: bool):
        c = self.c
        if not c.concepts:
            self.tick(0.1, "Writing the designs")
            n = max(1, min(6, int(self.b["variants"])))
            rows = self.write(n)
            c.concepts = [ps.new_concept(i, d, d.pop("_guard")) for i, d in enumerate(rows, 1)]
            flag_modified(c, "concepts")
        for con in [dict(x) for x in c.concepts if x.get("copy_dirty")]:
            self.tick(0.3, f"Rewriting option {con['n']}")
            d = self.write(1, [con.get("family") or "typographic"], avoid=[con.get("headline") or ""])[0]
            g = d.pop("_guard")
            for k in ("headline", "cta", "offers", "insight", "copy_check"):
                con[k] = d.get(k) or ([] if k in ("offers", "copy_check") else "")
            con.update(guard=g, copy_dirty=False, edited=False)
            ads.put_concept(c, con)
            ads.mark_dirty(c, con["id"])
        rec = self.rec()
        if reviewed or rec.get("approved") or not self.b.get("review"):
            rec["approved"] = True
            self.put(rec)
            return None
        return Review("Pick the options to make and edit any line, then continue")

    # ---------------------------------------------------------------- pictures
    def need_px(self, con: dict, group: str) -> int:
        w, h = ps.canvas_of(self.b).size_px
        # an emblem is at most about half the short side; a photo can run the full length
        need = max(w, h) if group == "photo" else round(min(w, h) * 0.6)
        return min(ESRGAN_LONG, need)

    def picture_ratio(self, con: dict) -> tuple[float, float]:
        """The shape of the photo's box in this design, so the picture is made at about that ratio."""
        spec = ps.spec_for(self.c, con, ps.new_design(con), self.kit)
        scene = pl.build(spec, pl.Assets(photo=Image.new("RGB", (8, 8))))
        box = next((e.box for e in scene.els if e.src == "photo"), None)
        if box is None:
            return 1.0, 1.0
        w, h = box[2] - box[0], box[3] - box[1]
        # image models draw badly past about 1:3; the cover crop takes the rest
        r = max(1 / 3, min(3.0, w / max(h, 1e-6)))
        return r, 1.0

    def photo_request(self, con: dict, group: str) -> tuple[str, dict]:
        b, c = self.b, self.c
        tokens = brand_tokens(self.kit) + [b["lines"]["name"]]
        base = {"print_job": c.id, "concept_id": con["id"], "photo_group": group,
                "created_by": {"user_id": c.created_by, "flow": "print_studio"}}
        if group == "art":
            w, h = 1024, 1024
            idea = col.scrub(con.get("art_prompt") or b["subject"], tokens)
            colours = col.palette_words([{"hex": v} for v in (con.get("colours") or {}).values() if v])
            look = f" A limited palette of {', '.join(colours[:3])}." if colours else ""
            scene = " ".join(f"{PRINT_ART_LOOK} {idea.rstrip('.')}.{look} {PRINT_NO_TEXT}".split())
            neg = ART_NEGATIVE
        else:
            rw, rh = self.picture_ratio(con)
            w, h = lib.size_for_ratio(rw, rh)
            idea = col.scrub(con.get("photo_prompt") or b["subject"], tokens)
            scene = " ".join(f"{PRINT_PHOTO_LOOK} {idea.rstrip('.')}. {PRINT_NO_TEXT}".split())
            neg = ads.PHOTO_NEGATIVE
        aspect = f"{w}:{h}"
        refs = self.products() if group == "photo" else []
        if refs:
            m, chosen = mc.choose(mc.AUTO, "edit", scene, refs=len(refs))
            names = " and ".join(f"picture {i}" for i in range(1, len(refs) + 1))
            text = (f"{scene} The product from {names} is the hero of the frame: keep its exact shape, colours and "
                    "label as in the reference. No other text anywhere.")
            return text, {**base, "aspect": aspect, "width": w, "height": h, "instruction": text,
                          "reference_ids": refs, "model": m.id, **chosen}
        want = b.get("image_model") or ads.AUTO_MODEL
        m, chosen = rights.choose(self.db, ads.DEFAULT_MODEL if want == ads.AUTO_MODEL else want, "image",
                                   scene)
        speed = mc.speed_of(m, None) if m.speeds else None
        params = {**base, "aspect": aspect, "width": w, "height": h, "model": m.id, "style": None,
                  "user_prompt": scene, **chosen}
        if speed:
            params["speed"] = speed.id
        negative = st.joined_negative(neg, None, speed.cfg if speed else None)
        if negative:
            params["negative"] = negative
        return scene, params

    def upscale_request(self, con: dict, src: Generation, group: str, size: tuple[int, int],
                        need: int) -> tuple[str, dict]:
        """Real-ESRGAN up to what the print needs: 4x at most, 8192 px on the long side at most (the anime
        model for an illustration keeps its flat colour and clean lines)."""
        w, h = size
        sc = min(4.0, min(need, ESRGAN_LONG) / max(w, h))
        width, height = max(2, int(w * sc) // 2 * 2), max(2, int(h * sc) // 2 * 2)
        engine = "anime" if group == "art" else "quick"
        up = {"engine": engine, "target": "2x", "variant": None, "denoise": None, "template": "image_upscale_esrgan",
              "width": width, "height": height, "scale": round(sc, 3), "capped": need > ESRGAN_LONG,
              "source_id": src.id, "source_file": src.file_path, "source_version": src.version,
              "source_size": [w, h]}
        if engine == "anime":
            up["model"] = ANIME_MODEL
        return src.prompt or "Print picture", {"print_job": self.c.id, "concept_id": con["id"], "photo_group": group,
                                               "created_by": {"user_id": self.c.created_by, "flow": "print_studio"},
                                               "upscale": up, "size": [width, height],
                                               "aspect": (src.params or {}).get("aspect")}

    def owner_photo(self, con: dict) -> Generation | None:
        ids = [m for m in self.b.get("photo_media_ids") or [] if m]
        if not ids:
            return None
        photo_cons = [x["id"] for x in self.c.concepts or [] if x.get("family") == "photo"]
        i = photo_cons.index(con["id"]) if con["id"] in photo_cons else 0
        g = br.media_generation(self.db, self.c.workspace_id, ids[i % len(ids)])
        return g if g is not None and g.status in ads.FINISHED else None

    def advance(self, con: dict, grp: str, rec: dict):
        """One picture, step by step: the owner's photo or the candidates, the pick, the upscale."""
        db = self.db
        cands = list(rec.get("candidates") or ([rec["base"]] if rec.get("base") else []))
        if not cands and grp == "photo":
            own = self.owner_photo(con)
            if own is not None:
                rec.update(base=own.id, candidates=[own.id], owner=True, pick={"by": "owner", "model": None})
                cands = [own.id]
        gens = {gid: db.get(Generation, gid) for gid in cands}
        live = [g.job_id for g in gens.values() if g is not None and g.status in ads.LIVE]
        if live:
            return "wait", live
        done = [gid for gid in cands if gens.get(gid) is not None and gens[gid].status in ads.FINISHED]
        if not done:
            if cands and rec.get("tries", 0) >= ads.ATTEMPTS:
                g = next((x for x in gens.values() if x is not None), None)
                job = db.get(Job, g.job_id or "") if g is not None else None
                return "failed", (job.error if job else None) or "no picture came back"
            prompt, params = self.photo_request(con, grp)
            n = max(1, min(4, int(self.b.get("candidates") or 2))) if self.final and grp == "photo" else 1
            new = [self.enqueue(con, prompt, params) for _ in range(n)]
            for k in ("base", "final", "upscale", "pick", "owner"):
                rec.pop(k, None)
            rec.update(candidates=[g.id for g in new], scores={}, tries=rec.get("tries", 0) + 1)
            return "wait", [g.job_id for g in new]
        rec["candidates"] = cands
        if rec.get("base") not in done:
            if grp == "photo" and len(done) > 1:
                self.pick(con, grp, rec, done)
            else:
                # TODO: let the editor swap in another candidate by hand, as Ad Studio plans to
                rec.update(base=done[0], pick={"by": "only", "model": None})
        src = db.get(Generation, rec["base"])
        final = src.id
        if self.final:
            path = generation_file(src)
            size = None
            if path is not None and path.is_file():
                with Image.open(path) as im:
                    size = im.size
            need = self.need_px(con, grp)
            if size and max(size) < need * 0.95 and max(size) < ESRGAN_LONG:
                state, got = self.child(con, rec, "upscale", src,
                                        lambda: self.upscale_request(con, src, grp, size, need), "upscale")
                if state == "wait":
                    return "wait", got
                final = got.id if state == "done" else final
        rec["final"] = final
        return "ready", None

    def logo_cutouts(self) -> list[str]:
        """A kit logo whose backdrop won't key out (not one plain colour) gets Photo Studio's BiRefNet cut-out,
        once: the cut logo is cached by the kit and the file's content, and every layout uses that."""
        kit = self.kit
        cons = [x for x in self.c.concepts or [] if x.get("approved")]
        if kit is None or not cons:
            return []
        rec = self.rec()
        cuts = dict(rec.get("logo_cuts") or {})
        waits = []
        for variant in ("primary", "light", "dark"):
            mid = br.logo_ref(kit, variant).get("media_id")
            src = br.media_generation(self.db, kit.workspace_id, mid)
            path = generation_file(src) if src is not None else None
            if path is None or not path.is_file() or ps.logo_cut(kit, path) is not None:
                continue
            r = dict(cuts.get(mid) or {})
            state, got = self.child(cons[0], r, "cutout", src, lambda: self.logo_cutout_request(cons[0], src),
                                    "cutout")
            cuts[mid] = r
            if state == "wait":
                waits += got
            elif state == "done":
                mask = ((got.params or {}).get("cutout") or {}).get("mask_file")
                if mask is None or ps.logo_cut(kit, path, get_settings().data_dir / mask) is None:
                    log.warning("print job %s: the %s logo couldn't be cut out; it stays off the designs",
                                self.c.id, variant)
        rec["logo_cuts"] = cuts
        self.put(rec)
        return waits

    def logo_cutout_request(self, con: dict, src: Generation) -> tuple[str, dict]:
        params, _ = restore.build_restore(self.db, src, "cutout", {"edge": {"feather": 0.6, "shift": 0}})
        return "Logo cut-out", {"print_job": self.c.id, "concept_id": con["id"], "photo_group": "logo",
                                "created_by": {"user_id": self.c.created_by, "flow": "print_studio"}, **params}

    def stage_pictures(self, _reviewed: bool):
        c = self.c
        waits, failed, ready, total = [], [], 0, 0
        try:
            waits += self.logo_cutouts()
        except RestoreError as e:
            # the cut-out tool is off on this server: the job goes on, the unkeyable logo stays off
            log.warning("print job %s: no logo cut-out (%s)", c.id, e)
        for con in [dict(x) for x in c.concepts or [] if x.get("approved")]:
            photos = dict(con.get("photos") or {})
            for grp in ps.groups_of(con):
                total += 1
                rec = dict(photos.get(grp) or {})
                state, got = self.advance(con, grp, rec)
                photos[grp] = rec
                if state == "wait":
                    waits += got
                elif state == "failed":
                    failed.append((con, got))
                else:
                    ready += 1
            con["photos"] = photos
            ads.put_concept(c, con)
        if waits:
            return Wait(waits, f"{ready} of {total} pictures ready")
        for con, why in failed:
            con = ads.concept(c, con["id"])
            con["error"] = f"The picture wouldn't come: {why}"[:500]
            ads.put_concept(c, con)
        # a design without its picture still lays out (as type only), so a failed picture never stops the job
        return f"{len(failed)} picture(s) failed" if failed else None

    # ---------------------------------------------------------------- layout and files
    def stage_layout(self, _reviewed: bool):
        c = self.c
        cons = [dict(x) for x in c.concepts or [] if x.get("approved")]
        made = 0
        for i, con in enumerate(cons):
            self.tick(i / max(1, len(cons)), f"Laying out option {con['n']}")
            made += ps.render_concept(self.db, c, ads.concept(c, con["id"]), self.kit)
        ps.touch(c)
        self.r["designs_made"] = made
        if not any(a.get("status") == "ready" for a in c.ads or []):
            raise StageFailed("No design could be laid out")
        return None

    def stage_files(self, _reviewed: bool):
        c = self.c
        want = list(self.rec().get("want") or [])
        if not want and self.b.get("export_all"):
            want = [a["id"] for a in c.ads or [] if a.get("status") == "ready"]
        cons = {x["id"]: x for x in c.concepts or []}
        made = 0
        for i, did in enumerate(want):
            rec = next((dict(a) for a in c.ads or [] if a["id"] == did), None)
            if rec is None or rec.get("status") != "ready" or rec["concept_id"] not in cons:
                continue
            con = cons[rec["concept_id"]]
            spec = ps.spec_for(c, con, rec, self.kit)
            if (rec.get("files") or {}).get("hash") == ps.spec_hash(spec):
                continue
            self.tick(i / max(1, len(want)), f"Print files for option {con['n']}")
            ps.make_files(self.db, c, con, rec, self.kit)
            made += 1
        self.r["files_made"] = made
        ps.touch(c)
        return None


def handle(ctx) -> dict:
    from app.ads.stages import _w

    W = _w()
    run = PrintRun(ctx)
    try:
        return run.run()
    except (W.JobDeferred, W.WorkerStopping):
        raise
    except W.JobCancelled:
        run.db.rollback()
        c = run.db.get(AdCampaign, run.c.id)
        if c is not None and run.current:
            rec = ps.stage(c, run.current)
            if rec.get("status") == "running":
                rec.update(status="pending", detail="Cancelled")
                ps.put_stage(c, run.current, rec)
        run.db.commit()
        raise
    except Exception as e:
        log.exception("print job %s failed in %s", run.c.id, run.current)
        run.db.rollback()
        msg = str(e) or type(e).__name__
        if isinstance(e, LLMError):
            msg = f"The AI couldn't be reached: {msg}"
        c = run.db.get(AdCampaign, run.c.id)
        if c is not None and run.current:
            rec = ps.stage(c, run.current)
            rec.update(status="failed", detail=msg[:500])
            ps.put_stage(c, run.current, rec)
        job = run.db.get(Job, run.job.id)
        job.result = {**(job.result or {}), "waiting_on": []}
        run.db.commit()
        label = dict(ps.STAGES).get(run.current or "", "Print job")
        raise RuntimeError(msg if msg.startswith(label) else f"{label} failed: {msg}") from e
