"""What Jafat won't picture (owner, 24 Oct 2026; Pakistan is an Islamic state by law):

- nudity, sexual or revealing pictures and videos, for everyone but admins. The private Implant Preview has its
  own consent-gated rules and is left to them;
- the Prophets (Prophet Muhammad ﷺ first of all), the Sahaba, the Ahl-e-Bayt, angels and other revered
  religious figures, for everyone, admins included (Khatm-e-Nabuwwat; PPC 295-C);
- a child (anyone who looks under 18) in a sexual or nude way, for everyone, admins included. There is no setting
  for this one, on purpose.

Words about them are fine: a naat or a poetry short may praise the Prophet ﷺ. It is only showing them that is
refused, so a hit on a revered name goes to the writer model, which tells a depiction from calligraphy, a mosque
or a landscape. Three places check: a new request (words only, no model call), the worker once the prompt is
final, and the finished picture."""
import logging
import re
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger("mixai.policy")

SEXUAL_MSG = ("Jafat doesn't make nude, sexual or revealing pictures or videos. Please describe something "
              "else, with everyone modestly dressed.")
RELIGIOUS_MSG = ("Jafat doesn't show the Prophets ﷺ, the Sahaba, the Ahl-e-Bayt or other revered religious "
                 "figures in pictures or videos. Calligraphy, mosques, light and landscapes are fine.")
CHILD_MSG = "This can't be made: it shows a child in a sexual or nude way."

# English, Roman Urdu and Urdu. Whole words, so "Sussex" or "comic strip" never trip it
_SEXUAL = [
    r"nudes?", r"nudity", r"naked", r"topless", r"bottomless", r"nsfw", r"porn\w*", r"xxx", r"hentai",
    r"erotic\w*", r"sex", r"sexual\w*", r"sexy", r"intercourse", r"orgasm\w*", r"nipples?", r"genital\w*",
    r"vagina\w*", r"penis\w*", r"boobs?", r"tits", r"strip ?tease", r"strippers?", r"lingerie", r"bikinis?",
    r"thongs?", r"swim ?suits?", r"swimwear", r"underwear", r"panties", r"see[- ]through", r"undress\w*",
    r"unclothed", r"no clothes", r"without (any )?clothes", r"fetish\w*", r"bdsm", r"seductive\w*", r"sensual\w*",
    r"cleavage", r"lewd", r"onlyfans", r"playboy", r"making out", r"french kiss\w*", r"bare (breasts?|chest(ed)?)",
    r"nangi", r"nanga", r"nangay", r"be ?libas", r"bay ?libas", r"chudai", r"gandi (photo|tasveer|video)",
]
_SEXUAL_UR = ["ننگی", "ننگا", "ننگے", "برہنہ", "عریاں", "عریانی", "فحش", "جنسی", "سیکس", "بے لباس"]

# a child word alone is fine ("a boy flying a kite"); only next to a sexual word does it refuse
_MINOR = [
    r"child(ren|ish|like)?", r"kids?", r"kiddies?", r"kiddos?", r"boys?", r"girls?", r"teens?", r"teenagers?",
    r"teenaged?", r"pre-?teens?", r"tweens?", r"minors?", r"under-?age(d)?", r"school ?(girl|boy)s?", r"toddlers?",
    r"infants?", r"babies", r"juveniles?", r"young[- ]looking", r"loli(ta|con)?", r"shota(con)?", r"jailbait",
    r"(1[0-7]|[1-9]) ?(yo|y/o|-?years?[- ]old)",
    r"bach(c)?(a|ay|e|i|on|iyan|iyon)", r"lark(a|ay|e|i|iyan|iyon)", r"ladk(a|ay|e|i|iyan|iyon)",
    r"kam ?sin", r"na ?baligh", r"munn[ai]", r"masoom (bach\w*|lar\w*)",
]
_MINOR_UR = ["بچہ", "بچی", "بچے", "بچوں", "بچیاں", "بچیوں", "لڑکا", "لڑکی", "لڑکے", "لڑکیاں", "لڑکیوں", "کمسن", "کم سن",
             "نابالغ"]

_REVERED = [
    r"prophets?", r"messengers? of (allah|god)", r"rasool\w*", r"rasul\w*", r"nabi", r"nabee", r"anbiya",
    r"pa[iy]g?h?ambar\w*", r"sahab(a|ah|i|is)", r"companions? of the prophet", r"ahl[- ]?[ea]l?[- ]?ba[iy]t",
    r"ahlul ?bayt", r"rashidun", r"khulafa\w*", r"caliph \w+", r"imam (hussain|husain|hasan|hassan|ali|mahdi)",
    r"hazrat \w+", r"hadrat \w+", r"allah", r"jibr[ae]e?l", r"angel gabriel", r"archangel\w*", r"jesus", r"christ",
    r"virgin mary", r"moses", r"pbuh", r"s\.a\.w", r"alaih?is? ?salaa?m", r"radi ?allahu",
    r"razi ?allah",
]
_REVERED_MARKS = ["ﷺ", "صلی اللہ", "صلى الله", "علیہ السلام", "عليه السلام", "رضی اللہ", "رضي الله", "حضرت", "نبی",
                  "رسول", "پیغمبر", "صحابہ", "صحابی", "اہل بیت", "اہلِ بیت", "امام حسین", "(as)", "(a.s)", "(ra)",
                  "(r.a)", "(saw)", "(saww)", "(sws)"]


def _words(parts: list[str]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(parts) + r")\b", re.IGNORECASE)


_SEX_RE = _words(_SEXUAL)
_REV_RE = _words(_REVERED)
_MINOR_RE = _words(_MINOR)


def sexual_words(text: str) -> bool:
    t = text or ""
    return bool(_SEX_RE.search(t)) or any(w in t for w in _SEXUAL_UR)


def minor_words(text: str) -> bool:
    t = text or ""
    return bool(_MINOR_RE.search(t)) or any(w in t for w in _MINOR_UR)


def child_refusal(text: str) -> str | None:
    # everyone, admins included, and nothing switches it off
    return CHILD_MSG if sexual_words(text) and minor_words(text) else None


def revered_words(text: str) -> bool:
    t = text or ""
    return bool(_REV_RE.search(t)) or any(w in t.lower() for w in _REVERED_MARKS)


class _Verdict(BaseModel):
    depicts_revered_figure: bool
    sexual_or_revealing: bool


def _ask_writer(text: str, tick=None) -> _Verdict | None:
    from app.config import get_settings
    from app.llm import LLMError, chat_sync

    msgs = [{"role": "system", "content": "You review prompts for an image and video generator in Pakistan. "
                                          "Answer only with JSON."},
            {"role": "user", "content":
                "Prompt:\n<<<\n" + text[:12000] + "\n>>>\n"
                "depicts_revered_figure: true when the picture would SHOW any Prophet (including Prophet "
                "Muhammad ﷺ, Isa/Jesus, Musa/Moses, Ibrahim), any of the Sahaba or the Ahl-e-Bayt, an angel, "
                "God, or another revered religious figure or saint, even veiled, as a silhouette, from behind or "
                "as light in a human shape. False when they are only named or praised, or the picture shows "
                "calligraphy of a name, a mosque, a shrine building, a book, a landscape or ordinary people "
                "praying.\n"
                "sexual_or_revealing: true for nudity, sexual acts or poses, or revealing clothing such as "
                "lingerie or swimwear.\n"
                'Reply as {"depicts_revered_figure": true|false, "sexual_or_revealing": true|false}.'}]
    try:
        res = chat_sync("creative", msgs, schema=_Verdict, temperature=0.0, max_tokens=60,
                        timeout=get_settings().llm_timeout_s, tick=tick, house_rules=False)
        return res.data
    except LLMError as e:
        log.warning("content check: the writer model couldn't answer: %s", e)
        return None


def quick_refusal(text: str, *, admin: bool) -> str | None:
    """For a new request, words only and no model call: the sexual words are refused at once. Revered names
    wait for the worker, where the writer model can tell praise and calligraphy from a depiction."""
    if why := child_refusal(text):
        log.warning("refused a request that puts a child with sexual words: %.120s", text)
        return why
    if not admin and sexual_words(text):
        return SEXUAL_MSG
    return None


def prompt_refusal(text: str, *, admin: bool, tick=None) -> str | None:
    """The final prompt, in the worker. A revered name with no answer from the writer model is refused: better a
    reworded prompt than a picture of a Prophet."""
    if why := child_refusal(text):
        return why
    if not admin and sexual_words(text):
        return SEXUAL_MSG
    if revered_words(text):
        v = _ask_writer(text, tick)
        if v is None or v.depicts_revered_figure:
            return RELIGIOUS_MSG
        if not admin and v.sexual_or_revealing:
            return SEXUAL_MSG
    return None


class _Look(BaseModel):
    nudity_or_sexual: bool
    why: str = ""


class _Minor(BaseModel):
    minor_nude_or_sexual: bool
    why: str = ""


def _ask_vision(path: Path, question: str, schema, tick=None):
    from app.config import get_settings
    from app.llm import LLMError, chat_sync
    from app.vision import image_b64

    try:
        pic = image_b64(path)
    except Exception as e:
        log.warning("content check: can't read %s: %s", path.name, e)
        return None
    msgs = [{"role": "system", "content": "You check generated images for a family-safe studio in Pakistan. "
                                          "Answer only with JSON."},
            {"role": "user", "images": [pic], "content": question}]
    try:
        return chat_sync("vision", msgs, schema=schema, temperature=0.0, max_tokens=80,
                         timeout=get_settings().vision_timeout_s, tick=tick).data
    except LLMError as e:
        log.warning("content check: the vision model couldn't look at %s: %s", path.name, e)
        return None


def picture_refusal(path: Path, tick=None) -> str | None:
    """The finished picture, for everyone but admins: the vision model looks for nudity or anything sexual. If it
    can't answer the picture stands; the words were already checked."""
    look = _ask_vision(path, "Does this picture show nudity, exposed breasts or genitals, underwear or lingerie, "
                             "swimwear, a sexual act or a sexual pose? Ordinary modest clothing, including sleeveless "
                             "or fitted clothes, is fine. Reply as JSON "
                             '{"nudity_or_sexual": true|false, "why": "short reason"}.', _Look, tick)
    if look and look.nudity_or_sexual:
        log.info("content check refused %s: %s", path.name, look.why)
        return SEXUAL_MSG
    return None


def minor_refusal(path: Path, tick=None) -> str | None:
    """Admins may make adult nudity, so their pictures get only this narrower question."""
    look = _ask_vision(path, "Is anyone in this picture who looks under 18 shown nude, partly nude, in underwear "
                             "or swimwear, or in a sexual way or pose? Children dressed and doing ordinary things "
                             "are fine. Reply as JSON "
                             '{"minor_nude_or_sexual": true|false, "why": "short reason"}.', _Minor, tick)
    if look and look.minor_nude_or_sexual:
        log.warning("child check refused %s: %s", path.name, look.why)
        return CHILD_MSG
    return None


RELIGIOUS_RULE = ("House rules for anything that will be pictured (Jafat AI Studio, Pakistan): never show or "
                  "describe the appearance of any Prophet (including Prophet Muhammad ﷺ), the Sahaba, the Ahl-e-Bayt, "
                  "angels or other revered religious figures; show calligraphy, a mosque, light, a landscape or "
                  "objects instead. Never show a child (anyone under 18) nude, partly nude or in a sexual way.")
# admins may make what the nudity checks would stop for others, so their writer keeps their words (llm.client.MODEST)
MODESTY_RULE = "Keep every person modestly dressed; nothing nude, sexual or revealing."
HOUSE_RULES = RELIGIOUS_RULE + " " + MODESTY_RULE


# ---------------------------------------------------------------- the request check

def _visual(gen) -> bool:
    from app.lanes import IMAGE_KINDS, MODEL_KINDS, VIDEO_KINDS

    return gen.kind in IMAGE_KINDS | VIDEO_KINDS | MODEL_KINDS and not (gen.target_type or "").startswith("implant_")


def words_of(gen) -> str:
    p = gen.params or {}
    return " ".join(str(x) for x in (gen.prompt, p.get("instruction"), p.get("prompt_original")) if x)


def _register() -> None:
    from fastapi import HTTPException
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from app.models import Generation, User
    from app.quota import QUOTA_USER

    @event.listens_for(Session, "before_flush")
    def _check_new_generations(session, _ctx, _instances):
        # only a person's request carries QUOTA_USER; the worker's own children are checked when they run
        uid = session.info.get(QUOTA_USER)
        if not uid:
            return
        gens = [o for o in session.new if isinstance(o, Generation) and _visual(o)]
        if not gens:
            return
        with session.no_autoflush:
            user = session.get(User, uid)
        admin = bool(user and user.is_admin)
        for g in gens:
            why = quick_refusal(words_of(g), admin=admin)
            if why:
                log.info("refused a request from %s: %.80s", uid, words_of(g))
                raise HTTPException(422, why)


_register()


# ---------------------------------------------------------------- in the worker

SAFE_NEGATIVE = "nudity, naked, nsfw, sexual, lingerie, underwear, swimwear, cleavage, revealing clothes"


def _owner(db, job):
    from app.models import User

    return db.get(User, job.user_id) if job.user_id else None


def _refuse(db, user, gen, why: str, text: str) -> None:
    from app import audit

    if user is not None:
        # the admins can see who asked for what (Admin → audit)
        action = "child_safety_refused" if why == CHILD_MSG else "content_refused"
        audit.record(db, user, action, "generation", gen.id, reason=why[:60], prompt=text[:300])
    db.commit()
    raise RuntimeError(why)


def check_before(ctx, gen, params: dict) -> None:
    """Once the prompt is final (a writer model may have filled it): the words, and the safety negative for
    everyone but admins. Raises RuntimeError with the reason the person sees."""
    if not _visual(gen):
        return
    db, job = ctx.db, ctx.job
    user = _owner(db, job)
    admin = bool(user and user.is_admin)
    text = words_of(gen)
    why = prompt_refusal(text, admin=admin, tick=lambda: ctx.progress(0.01, "Checking the request"))
    if why:
        _refuse(db, user, gen, why, text)
    if not admin and gen.kind not in ("model3d",):
        base = params.get("negative") or params.get("negative_prompt") or ""
        params["negative"] = f"{base}, {SAFE_NEGATIVE}" if base else SAFE_NEGATIVE


def check_after(ctx, gen, path: Path, media_type: str) -> None:
    """The finished picture (or a frame of the video). Members get the full nudity question; admins, and everyone
    while CONTENT_CHECK_PICTURES is off, still get the child one."""
    from app.config import get_settings

    if not _visual(gen):
        return
    if not (media_type.startswith("image/") or media_type.startswith("video/")):
        return
    db, job = ctx.db, ctx.job
    user = _owner(db, job)
    admin = user is not None and user.is_admin
    # the full check already refuses any nudity, children's included
    ask = picture_refusal if get_settings().content_check_pictures and not admin else minor_refusal
    look = path
    if media_type.startswith("video/"):
        from app.brand_render import grab_frame

        look = path.with_suffix(".check.png")
        if grab_frame(path, 1.0, look) is None:
            return
    try:
        why = ask(look, tick=lambda: ctx.progress(0.98, "Checking the picture"))
    finally:
        if look != path:
            look.unlink(missing_ok=True)
    if why:
        path.unlink(missing_ok=True)
        _refuse(db, user, gen, why, words_of(gen))
