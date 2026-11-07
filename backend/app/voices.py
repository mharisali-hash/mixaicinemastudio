"""Voice studio (contract v16): the speech engines, the voice library rules and the speech text.

The engines are not ComfyUI models, so they live here rather than in models_catalog. Each one runs in
its own venv on the GPU box through app.venv_runner; app.speech does the splitting and joining.
"""
import re
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import library as lib, speech_language, voice_gallery
from app.config import get_settings
from app.models import Generation, Job, MediaItem, VoiceProfile, new_id, utcnow
from app.services import enqueue_generation, generation_file, job_out, media_url, new_seed

CONSENT = "I have the right to use this voice, and the person it belongs to agreed to it being cloned."
TRANSCRIBE_JOB = "voice_transcribe"
REF_MIN_S, REF_MAX_S = 3.0, 30.0
REF_GOOD = (5.0, 15.0)
FINISHED = ("ready", "approved")

LANG_NAMES = {
    "en": "English", "ur": "Urdu", "pa": "Punjabi", "sd": "Sindhi", "ps": "Pashto", "hi": "Hindi",
    "bn": "Bengali", "ar": "Arabic", "fa": "Persian", "es": "Spanish", "fr": "French", "de": "German",
    "zh": "Mandarin Chinese", "yue": "Cantonese", "ja": "Japanese", "ko": "Korean", "tr": "Turkish",
    "ru": "Russian", "pt": "Portuguese", "it": "Italian", "id": "Indonesian", "ms": "Malay", "sw": "Swahili",
    "ta": "Tamil", "te": "Telugu", "mr": "Marathi", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam",
    "or": "Odia", "as": "Assamese", "ne": "Nepali", "si": "Sinhala", "ks": "Kashmiri", "bal": "Balochi",
    "skr": "Saraiki", "my": "Burmese", "th": "Thai", "vi": "Vietnamese", "km": "Khmer", "lo": "Lao",
    "tl": "Tagalog", "uk": "Ukrainian", "pl": "Polish", "nl": "Dutch", "sv": "Swedish", "da": "Danish",
    "no": "Norwegian", "fi": "Finnish", "el": "Greek", "he": "Hebrew", "cs": "Czech", "ro": "Romanian",
    "hu": "Hungarian", "bg": "Bulgarian", "sr": "Serbian", "hr": "Croatian", "sk": "Slovak", "az": "Azerbaijani",
    "kk": "Kazakh", "uz": "Uzbek", "tg": "Tajik", "ku": "Kurdish", "am": "Amharic", "ha": "Hausa",
    "yo": "Yoruba", "ig": "Igbo", "zu": "Zulu", "so": "Somali",
}


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code, code)


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    language: str
    gender: str


@dataclass(frozen=True)
class Engine:
    id: str
    label: str
    features: tuple[str, ...]
    languages: tuple[str, ...]
    licence: str
    venv: str
    runner: str
    vram_gb: float
    # tags the model itself voices; the rest are stripped before it sees the text
    tags: frozenset = frozenset()
    native_speed: bool = False
    native_duration: bool = False
    # OmniVoice takes far more languages than we list; anything well-formed goes through
    open_languages: bool = False
    note: str = ""
    default_for: tuple[str, ...] = ()
    presets: tuple[Preset, ...] = field(default_factory=tuple)
    # weights that may not be used commercially: hidden and refused unless AUDIO_ALLOW_NONCOMMERCIAL
    noncommercial: bool = False


# every Kokoro voice, from the gallery catalogue (contract v19), so the two lists can't drift apart
KOKORO_PRESETS = tuple(Preset(v["preset"], v["name"], v["languages"][0], v["gender"])
                       for v in voice_gallery.kokoro_rows())

ENGINES: dict[str, Engine] = {e.id: e for e in (
    # the code is Apache-2.0 but the model card puts the pre-trained weights under CC-BY-NC
    Engine("omnivoice", "OmniVoice", ("clone", "design", "tags"), tuple(LANG_NAMES),
           "CC-BY-NC (weights, non-commercial); code Apache-2.0",
           "tts-omnivoice", "tts_omnivoice", 6.0, tags=frozenset({"laughter", "sigh"}), native_speed=True,
           native_duration=True, open_languages=True,
           note="Speaks 600+ languages; the list shows the common ones and any other language code works.",
           default_for=("ur", "pa", "sd", "ps"), noncommercial=True),
    Engine("voxcpm", "VoxCPM2", ("clone", "design"),
           ("zh", "ja", "ko", "my", "id", "km", "lo", "ms", "tl", "th", "vi", "ar", "he", "hi", "tr", "da", "nl",
            "en", "fi", "fr", "de", "el", "it", "no", "pl", "pt", "ru", "es", "sv", "sw"),
           "Apache-2.0", "tts-voxcpm", "tts_voxcpm", 8.0),
    Engine("chatterbox", "Chatterbox", ("clone", "emotion"),
           ("ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl", "no", "pl",
            "pt", "ru", "sv", "sw", "tr", "zh"),
           "MIT; output carries a Perth watermark", "tts-chatterbox", "tts_chatterbox", 6.0),
    Engine("kokoro", "Kokoro", ("preset_voices", "fast"), ("en", "es", "fr", "hi", "it", "ja", "pt", "zh"),
           "Apache-2.0", "tts-kokoro", "tts_kokoro", 1.0, native_speed=True, presets=KOKORO_PRESETS),
)}
DEFAULT_ENGINE = "omnivoice"
# what "auto" falls back to when OmniVoice is switched off
COMMERCIAL_DEFAULT = "voxcpm"
WHISPER = {"venv": "stt-whisperx", "runner": "stt_whisper", "vram_gb": 5.0}
KOKORO_DEFAULT = {"en": "af_heart", "es": "ef_dora", "fr": "ff_siwis", "hi": "hf_alpha", "it": "if_sara",
                  "ja": "jf_alpha", "pt": "pf_dora", "zh": "zf_xiaobei"}

# a short line in each language for design previews; anything else hears the English one
PANGRAMS = {
    "en": "The quick brown fox jumps over the lazy dog, and the story begins.",
    "ur": "یہ میری آواز ہے۔ آج موسم بہت خوشگوار ہے، چلیے کہانی شروع کرتے ہیں۔",
    "hi": "यह मेरी आवाज़ है। आज मौसम बहुत सुहाना है, चलिए कहानी शुरू करते हैं।",
    "pa": "ਇਹ ਮੇਰੀ ਆਵਾਜ਼ ਹੈ। ਅੱਜ ਮੌਸਮ ਬਹੁਤ ਸੋਹਣਾ ਹੈ।",
    "ar": "هذا صوتي. الطقس جميل اليوم، فلنبدأ الحكاية.",
    "fa": "این صدای من است. امروز هوا خیلی خوب است.",
    "es": "El veloz murciélago hindú comía feliz cardillo y kiwi.",
    "fr": "Portez ce vieux whisky au juge blond qui fume.",
    "de": "Victor jagt zwölf Boxkämpfer quer über den großen Sylter Deich.",
    "pt": "Esta é a minha voz. Hoje o dia está lindo, vamos começar a história.",
    "it": "Questa è la mia voce. Oggi è una bella giornata, cominciamo la storia.",
    "tr": "Bu benim sesim. Bugün hava çok güzel, hikâyeye başlayalım.",
    "zh": "这是我的声音。今天天气很好，我们开始讲故事吧。",
    "ja": "これは私の声です。今日はいい天気ですね、物語を始めましょう。",
    "ko": "이것은 제 목소리입니다. 오늘 날씨가 정말 좋네요.",
}


class VoiceError(ValueError):
    pass


def allowed(e: Engine) -> bool:
    return not e.noncommercial or get_settings().audio_allow_noncommercial


def default_engine() -> str:
    return DEFAULT_ENGINE if allowed(ENGINES[DEFAULT_ENGINE]) else COMMERCIAL_DEFAULT


def default() -> Engine:
    return ENGINES[default_engine()]


def engine_out(e: Engine) -> dict:
    out = {"id": e.id, "label": e.label, "features": list(e.features),
           "languages": [{"code": c, "name": lang_name(c)} for c in e.languages], "licence": e.licence}
    if e.noncommercial:
        out["noncommercial"] = True
    if e.note:
        out["note"] = e.note
    if e.default_for:
        out["default_for"] = list(e.default_for)
    if e.presets:
        out["presets"] = [{"id": p.id, "name": p.name, "language": p.language, "gender": p.gender} for p in e.presets]
    return out


def engines_out() -> list[dict]:
    return [engine_out(e) for e in ENGINES.values() if allowed(e)]


def get_engine(engine_id: str) -> Engine:
    e = ENGINES.get(engine_id)
    if e is None:
        names = ", ".join(x.id for x in ENGINES.values() if allowed(x))
        raise VoiceError(f"Unknown speech engine '{engine_id}' (expected {names} or auto)")
    if not allowed(e):
        raise VoiceError(f"{e.label} isn't available on this server right now.")
    return e


def kokoro_preset(voice: str) -> Preset | None:
    return next((p for p in KOKORO_PRESETS if p.id == voice), None)


def _who_can(feature: str) -> str:
    names = [e.label for e in ENGINES.values() if feature in e.features and allowed(e)]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def _best_for(language: str) -> str:
    return next((x.label for x in ENGINES.values() if language in x.languages and allowed(x)), default().label)


def check_language(e: Engine, language: str) -> None:
    if language in e.languages or e.open_languages:
        return
    raise VoiceError(f"{e.label} doesn't speak {lang_name(language)}. Use {_best_for(language)}.")


def resolve_engine(requested: str | None, voice: VoiceProfile | None, design: dict | None) -> Engine:
    if requested and requested != "auto":
        e = get_engine(requested)
    elif voice is not None and voice.kind == "preset":
        e = get_engine((voice.preset or {}).get("engine") or "kokoro")
    elif voice is not None and voice.kind == "design":
        # a designed voice is a description *and* a seed for one engine; another engine is another voice
        e = get_engine((voice.design or {}).get("engine") or default_engine())
    else:
        e = default()
    if voice is not None:
        need = {"clone": "clone", "design": "design", "preset": "preset_voices"}[voice.kind]
        if need not in e.features:
            what = {"clone": "clone a voice", "design": "speak a designed voice",
                    "preset": "use preset voices"}[voice.kind]
            raise VoiceError(f"{e.label} can't {what}. Use {_who_can(need)}.")
        if voice.kind == "preset" and (voice.preset or {}).get("engine") != e.id:
            raise VoiceError(f"“{voice.name}” is a {get_engine(voice.preset['engine']).label} voice")
    elif design is not None and "design" not in e.features:
        raise VoiceError(f"{e.label} can't design a voice. Use {_who_can('design')}.")
    return e


# ---------------------------------------------------------------- expression tags

TAG = re.compile(r"\[\s*(laughter|laugh|sigh|breath|whisper|pause(?:\s+(\d+(?:\.\d+)?)\s*s?)?)\s*\]", re.I)
PAUSE_DEFAULT_S = 0.5
PAUSE_MAX_S = 10.0


def segments(text: str, e: Engine) -> list[tuple[str, str | float]]:
    """[("text", "..."), ("pause", 0.5), ...]: pauses become silence the backend adds (no engine voices a
    pause reliably), tags the engine knows stay in its text, the others go."""
    out: list[tuple[str, str | float]] = []
    buf, pos = [], 0

    def flush():
        chunk = " ".join("".join(buf).split())
        if chunk:
            out.append(("text", chunk))
        buf.clear()

    for m in TAG.finditer(text):
        buf.append(text[pos:m.start()])
        pos = m.end()
        tag = m[1].lower()
        if tag.startswith("pause"):
            flush()
            secs = min(PAUSE_MAX_S, float(m[2]) if m[2] else PAUSE_DEFAULT_S)
            if out and out[-1][0] == "pause":
                out[-1] = ("pause", min(PAUSE_MAX_S, out[-1][1] + secs))
            elif secs > 0:
                out.append(("pause", secs))
            continue
        tag = "laughter" if tag == "laugh" else tag
        if tag in e.tags:
            buf.append(f" [{tag}] ")
    buf.append(text[pos:])
    flush()
    # silence at the very start or end is padding nobody asked for
    while out and out[0][0] == "pause":
        out.pop(0)
    while out and out[-1][0] == "pause":
        out.pop()
    return out


def spoken_text(text: str, e: Engine) -> str:
    return " ".join(str(v) if k == "text" else f"[pause {v:g}s]" for k, v in segments(text, e))


# ---------------------------------------------------------------- the library

def _item_url(db: Session, media_id: str | None) -> str | None:
    if not media_id:
        return None
    item = db.get(MediaItem, media_id)
    g = db.get(Generation, item.generation_id) if item and item.generation_id else None
    return media_url(g.file_path) if g is not None and g.status in FINISHED else None


def voice_out(db: Session, v: VoiceProfile, warnings: list[str] | None = None) -> dict:
    ref = None
    if v.ref_media_id:
        g = db.get(Generation, v.ref_generation_id or "")
        ref = {"media_id": v.ref_media_id, "generation_id": v.ref_generation_id, "duration_s": v.ref_duration_s,
               "url": media_url(g.file_path) if g is not None else None}
    return {"id": v.id, "name": v.name, "language": v.language, "notes": v.notes, "gender_hint": v.gender_hint,
            "kind": v.kind, "ref": ref, "ref_text": v.ref_text, "design": v.design, "preset": v.preset,
            "portrait_url": _item_url(db, v.portrait_media_id), "starred": v.starred, "locked": v.locked,
            "consent": v.consent, "created_at": v.created_at, "updated_at": v.updated_at, "uses": v.uses,
            "warnings": warnings or []}


def ref_clip(db: Session, workspace_id: str, media_id: str) -> tuple[Generation, float, list[str]]:
    from app import audio_analysis as aa

    item = db.get(MediaItem, media_id)
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(404, "Reference clip not found")
    if item.kind != "audio":
        raise HTTPException(422, "The reference has to be an audio clip")
    g = db.get(Generation, item.generation_id or "")
    if g is None or g.status not in FINISHED or not g.file_path:
        raise HTTPException(422, "The reference clip isn't finished yet")
    path = generation_file(g)
    if path is None or not path.is_file():
        raise HTTPException(422, "The reference clip's file is missing")
    dur = round(aa.stream_info(path).duration or item.duration_s or 0.0, 3)
    if not REF_MIN_S <= dur <= REF_MAX_S:
        raise HTTPException(422, f"The reference clip is {dur:.1f} s; it needs to be {REF_MIN_S:g} to "
                                 f"{REF_MAX_S:g} seconds (5 to 15 is best)")
    warnings = ["short"] if dur < REF_GOOD[0] else ["long"] if dur > REF_GOOD[1] else []
    return g, dur, warnings


def check_consent(consent: dict | None, user_id: str) -> dict:
    c = consent or {}
    name = " ".join(str(c.get("name") or "").split())
    if not name:
        raise HTTPException(422, "Cloning a voice needs a consent record: the name of the person it belongs to")
    if " ".join(str(c.get("statement") or "").split()) != CONSENT:
        raise HTTPException(422, f"The consent statement must read exactly: “{CONSENT}”")
    return {"name": name, "statement": CONSENT, "at": utcnow().isoformat() + "Z", "user_id": user_id}


def check_portrait(db: Session, workspace_id: str, media_id: str | None) -> str | None:
    if not media_id:
        return None
    item = db.get(MediaItem, media_id)
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(404, "Portrait not found")
    if item.kind != "image":
        raise HTTPException(422, "The portrait has to be an image")
    return item.id


def clean_design(design: dict | None) -> dict:
    d = design or {}
    desc = " ".join(str(d.get("description") or "").split())
    if not desc:
        raise HTTPException(422, "A designed voice needs a description")
    engine = d.get("engine") or default_engine()
    try:
        if "design" not in get_engine(engine).features:
            raise VoiceError(f"{get_engine(engine).label} can't design a voice. Use {_who_can('design')}.")
    except VoiceError as e:
        raise HTTPException(422, str(e)) from None
    seed = d.get("seed")
    # the seed is half the identity; without one every take would be a different person
    return {"description": desc[:500], "seed": int(seed) if seed is not None else new_seed(), "engine": engine}


def clean_preset(preset: dict | None) -> dict:
    p = preset or {}
    engine, pid = p.get("engine") or "kokoro", p.get("id") or ""
    e = ENGINES.get(engine)
    if e is None or not any(x.id == pid for x in e.presets):
        raise HTTPException(422, f"'{pid}' isn't a preset voice of {e.label if e else engine}")
    return {"engine": engine, "id": pid}


# ---------------------------------------------------------------- finding a voice

def find_voice(db: Session, workspace_id: str, voice_id: str | None) -> VoiceProfile | None:
    """A library voice, or a gallery one ("gallery:<id>") as an unsaved VoiceProfile."""
    if not voice_id:
        return None
    gid = voice_gallery.parse(voice_id)
    if gid is not None:
        v = voice_gallery.get(gid)
        return voice_gallery.as_profile(v, workspace_id) if v else None
    p = db.get(VoiceProfile, voice_id)
    return p if p is not None and p.workspace_id == workspace_id else None


def find_voices(db: Session, workspace_id: str, ids) -> dict[str, VoiceProfile]:
    ids = {i for i in ids if i}
    gallery = {i for i in ids if voice_gallery.parse(i) is not None}
    out = {i: p for i in gallery if (p := find_voice(db, workspace_id, i)) is not None}
    rest = ids - gallery
    if rest:
        rows = db.scalars(select(VoiceProfile).where(VoiceProfile.id.in_(rest),
                                                     VoiceProfile.workspace_id == workspace_id)).all()
        out.update({v.id: v for v in rows})
    return out


def is_gallery(voice: VoiceProfile | None) -> bool:
    return voice is not None and voice_gallery.parse(voice.id) is not None


# ---------------------------------------------------------------- speaking

def voice_spec(db: Session, voice: VoiceProfile) -> dict:
    """What a speech job (or a song sung in this voice) needs to know about the voice."""
    spec: dict = {"kind": voice.kind, "name": voice.name}
    if voice.kind == "clone":
        g = db.get(Generation, voice.ref_generation_id or "")
        if g is None or not g.file_path:
            raise HTTPException(422, f"The reference clip of “{voice.name}” is gone")
        spec.update(ref_file=g.file_path, ref_text=voice.ref_text or "")
    elif voice.kind == "design":
        spec["design"] = voice.design
    else:
        spec["preset"] = (voice.preset or {}).get("id")
    return spec


def queue_speech(db: Session, cur, body, *, flow: str = "speak", seed: int | None = None,
                 home: str | None = None) -> dict:
    """Validate a speak request and queue its takes. `body` is a SpeakIn (or the design preview's
    equivalent). Returns the SpeakOut fields; the caller commits."""
    from app.api.library import generate_into

    voice = None
    if body.voice_id:
        voice = find_voice(db, cur.workspace_id, body.voice_id)
        if voice is None:
            raise HTTPException(422, "That gallery voice doesn't exist" if voice_gallery.parse(body.voice_id)
                                else "That voice isn't in the library any more")
    design = body.design.model_dump() if getattr(body, "design", None) is not None and voice is None else None
    try:
        e = resolve_engine(body.engine, voice, design)
        # the letters win over the voice's language: an Urdu voice reading an English line speaks English
        language = speech_language.dominant(body.text, body.language, voice.language if voice else None)
        for _, lang in speech_language.runs(body.text, language):
            if lang != language and lang not in e.languages and not e.open_languages:
                raise VoiceError(f"{e.label} doesn't speak {lang_name(lang)}, and part of this text is in "
                                 f"{lang_name(lang)}. Use {_best_for(lang)}.")
        if e.id == "kokoro":
            preset = (voice.preset or {}).get("id") if voice else KOKORO_DEFAULT.get(language)
            p = kokoro_preset(preset or "")
            if p is not None and p.language != language:
                raise VoiceError(f"The Kokoro voice {p.name} speaks {lang_name(p.language)}, not "
                                 f"{lang_name(language)}")
        check_language(e, language)
    except VoiceError as err:
        raise HTTPException(422, str(err)) from None

    text = body.text.strip()
    spoken = spoken_text(text, e)
    if not spoken or not any(k == "text" for k, _ in segments(text, e)):
        raise HTTPException(422, "There are no words to speak once the tags are taken out")
    into = generate_into(db, cur.workspace_id, body.folder_id, "audio")

    vspec: dict = {"kind": None}
    if voice is not None:
        vspec = voice_spec(db, voice)
    elif design is not None:
        design = {"description": " ".join(design["description"].split()),
                  "seed": design.get("seed") if design.get("seed") is not None else new_seed(), "engine": e.id}
        vspec = {"kind": "design", "design": design}
    elif e.id == "kokoro":
        vspec = {"kind": "preset", "preset": KOKORO_DEFAULT.get(language, "af_heart")}

    base_seed = seed
    if base_seed is None:
        base_seed = body.seed if body.seed is not None else (vspec.get("design") or {}).get("seed")
    params = {"kind": "speech", "model": e.id, "engine": e.id, "voice_id": voice.id if voice else None,
              "language": language, "text": text, "text_spoken": spoken, "aerab": getattr(body, "aerab", True),
              "speed": body.speed, "duration_s": body.duration_s, "voice": vspec,
              "created_by": {"user_id": cur.id, "flow": flow}}
    for key in ("steps", "guidance", "denoise", "exaggeration"):
        if getattr(body, key, None) is not None:
            params[key] = getattr(body, key)

    title = (getattr(body, "title", None) or "").strip() or lib.short_title(spoken.replace("[", "").replace("]", ""))
    home = home or lib.home_of(getattr(body, "part_of", None))
    items, jobs = [], []
    for i in range(body.count):
        item = MediaItem(id=new_id(), workspace_id=cur.workspace_id, kind="audio", origin="generated", title=title,
                         tags=[], duration_s=body.duration_s, folder_id=into, home=home)
        db.add(item)
        db.flush()
        s = (base_seed + i) % 2**31 if base_seed is not None else None
        g = enqueue_generation(db, workspace_id=cur.workspace_id, project_id=None, target_type="media",
                               target_id=item.id, kind="speech", prompt=text, params=dict(params), seed=s)
        item.generation_id = g.id
        items.append(item)
        jobs.append(db.get(Job, g.job_id))
        if i == 0:
            base_seed = g.seed if base_seed is None else base_seed
    if voice is not None and not is_gallery(voice):
        voice.uses = (voice.uses or 0) + body.count
    return {"items": lib.items_out(db, items), "jobs": [job_out(j) for j in jobs], "engine_resolved": e.id,
            "seed": base_seed}


# ---------------------------------------------------------------- transcripts

def queue_transcribe(db: Session, v: VoiceProfile) -> Job:
    job = Job(workspace_id=v.workspace_id, type=TRANSCRIBE_JOB, payload={"voice_id": v.id},
              message="Waiting for a worker · Transcript")
    db.add(job)
    db.flush()
    return job


def handle_transcribe(ctx) -> dict:
    db, job = ctx.db, ctx.job
    v = db.get(VoiceProfile, (job.payload or {}).get("voice_id") or "")
    if v is None:
        raise RuntimeError("The voice no longer exists")
    g = db.get(Generation, v.ref_generation_id or "")
    path = generation_file(g) if g is not None else None
    if path is None or not path.is_file():
        raise RuntimeError("The voice has no reference clip to transcribe")
    driver = ctx.driver_factory()
    transcribe = getattr(driver, "transcribe", None)
    if transcribe is None:
        raise RuntimeError(f"The {getattr(driver, 'name', '?')} driver can't transcribe")
    ctx.progress(0.02, "Transcribing the reference clip")
    res = transcribe(path, v.language or None, ctx.progress)
    text = " ".join(str(res.get("text") or "").split())
    db.refresh(v)
    if text:
        v.ref_text = text
    return {"voice_id": v.id, "text": text, "language": res.get("language"), "segments": res.get("segments") or []}
