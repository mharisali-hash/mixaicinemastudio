---
title: "Contract v42: Admin bulk runs"
status: Active
created: 2026-11-08
extends: contract-v10 (Restore and Cut-out), contract-v5 (Image Edit), contract-v38/v39 (admin)
migration: 0033_bulk_batches
---

# Contract v42: Admin bulk runs

An admin uploads thousands of pictures (3,000–5,000 at a time is the design point) and runs one action on all of
them, to build training datasets: **Remove background** (the Cut-out tool) or **Edit with an instruction**
(Qwen-Image-Edit through the Image Edit path). Results download as ZIP parts with a caption per picture and a
metadata file.

**Admins only.** Every route answers 404 to anyone else, refuses API tokens (403, `session_only`) and is left out
of the OpenAPI document. The UI is `/admin/bulk` and `/admin/bulk/:id`.

## 1. Where things live

- `bulk_batch` (name, action `cutout | edit`, options, status `uploading | running | paused | done | cancelled`,
  run_job_id, exports) and `bulk_item` (filename as picked, size, sha256, media_id, source_gen_id, instruction /
  keywords from a prompts CSV, result_gen_id, job_id, status `uploaded | queued | running | done | failed |
  cancelled`, error, prompt_used, sample, attempts). `(batch_id, filename, size)` is unique: that is what lets an
  upload resume.
- Every picture, result, job and export of every batch is in one **hidden workspace** ("Bulk datasets", a fixed
  id, no members). The Library, the job tray and the event stream are all per workspace, so none of it reaches
  any studio, the admin's own included.

## 2. Queueing: everyone else goes first

The run is one orchestrator job (`bulk_run`, general lane) that keeps `BULK_IN_FLIGHT` (default 2) pictures on
the image queue at priority **−30**, below every other job (normal work is 0 or above, the autopilot waits at
−20). Any request made meanwhile, from the app, MCP or the REST API, the admin's own included, is claimed before
the next bulk picture; the batch carries on by itself. The orchestrator waits at −40 and a finishing picture
lifts it to −30 (`bulk.on_job_finished`). The three preview pictures run at priority 0.

Each picture is an ordinary `generate` job built by the existing code: `photo.restore.queue_tool(..., "cutout")`
or the Image Edit params with `reference_ids`. So `content_policy.check_before` runs as for any admin: the
sexual-words rule is skipped, the **revered-figures rule is not**. Instructions (batch and CSV) are also screened
for revered names when saved, so a bad line is refused before thousands of jobs queue.

## 3. Routes (`/api/admin/bulk`)

| Method | Path | |
|---|---|---|
| GET / POST | `` | list (last 100) / create `{name, action, options}` |
| GET / PATCH / DELETE | `/{id}` | batch with `counts` and `eta_s`; new options reset the samples; delete removes every file |
| POST | `/{id}/check` | `{files: [{name, size}]}` → `{have: [name]}`, which picks are already in |
| POST | `/{id}/files?name=` | one picture, multipart `file`; `name` is the path inside the picked folder. 201 `{id, duplicate}`; 507 when the disk would drop under `BULK_DISK_RESERVE_GB` (20) |
| POST | `/{id}/zip` | a ZIP up to `BULK_ZIP_MAX_GB` (4), unpacked by a `bulk_unzip` job; pictures only, `__MACOSX` skipped |
| POST | `/{id}/prompts` | CSV `filename, instruction, keywords` (also `file`, `prompt`, `caption`, `tags`); matches the path, or the bare name when unique |
| POST | `/{id}/preview` | three samples at priority 0 |
| POST | `/{id}/run`, `/pause`, `/cancel`, `/retry-failed` | pause takes queued pictures off (they don't count as a try); cancel also stops the running one |
| GET | `/{id}/items?status=&after=&limit=` | paged by `seq`; `status=samples` for the previews |
| GET | `/{id}/items/{item}/{source\|result}?w=256` | a thumb (`w` = 256, 512…) or the full file (`w=0`) |
| POST / GET | `/{id}/export`, `/{id}/exports` | `{originals, captions}` → a `bulk_export` job; list with parts |
| GET | `/{id}/exports/{job}/{n}` | part n, streamed |

## 4. Export layout

Parts of at most 1,000 pictures or 2 GB. Each part has `images/<folder>/<name>.png`, a `<name>.txt` caption
beside each (instruction and keywords; the kohya_ss / ai-toolkit layout), `originals/…` when asked (before/after
pairs), `metadata.jsonl` and `captions.csv` (UTF-8 with BOM) with `file, original_file, original_filename, action,
instruction, prompt_used, keywords, caption, width, height, seed, model, sha256, batch, finished_at`. The last
part also has `failed.csv`.

## 5. Settings (.env)

`BULK_IN_FLIGHT=2`, `BULK_DISK_RESERVE_GB=20`, `BULK_ZIP_MAX_GB=4`.
