"""Settings loaded from the repo-root .env (same file on Windows dev and the Linux box)."""
import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from typing import Annotated

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "dev"
    app_secret: str = "change-me"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    admin_email: str = "admin@mixai.local"
    admin_password: str = "change-me"
    dev_login_prefill: bool = True
    admin_name: str = "Admin"

    # token API (contract v27). Off: only the admin (user.is_admin since v38) sees the token page and the docs,
    # everyone else gets 404. On: owners and editors mint tokens and any signed-in user can read the docs
    public_api: bool = False
    api_docs: bool = True

    # accounts (contract v38). open: anyone signs up; invite: only accounts an admin makes; closed: none
    signup: str = "open"
    email_verify: bool = True
    # where links in emails point, and an origin the Origin check accepts; empty: the first CORS origin
    public_url: str = ""
    email_backend: str = "console"  # console | smtp
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: str = "starttls"  # starttls | ssl | off

    database_url: str = "sqlite:///./data/mixai.db"
    data_dir: Path = Path("./data")

    gen_driver: str = "mock"

    comfy_urls: Annotated[list[str], NoDecode] = []
    # one ComfyUI (GPU) per lane so stills and video render side by side; empty -> COMFY_URLS
    comfy_image_urls: Annotated[list[str], NoDecode] = []
    comfy_video_urls: Annotated[list[str], NoDecode] = []
    comfy_auth_token: str = ""
    comfy_verify_tls: bool = False
    # after a reboot the workers come up before ComfyUI has loaded; jobs wait this long rather than fail
    comfy_start_wait_s: float = 300.0
    comfy_lora_dir: str = ""
    comfy_output_dir: str = ""

    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: str = "ollama"
    llm_model_reasoning: str = ""
    llm_model_creative: str = ""
    llm_model_vision: str = ""
    llm_reasoning_format: str = "deepseek"
    # Ad Studio's copywriter: this model when set, else the largest of ads_llm_models that is installed
    ads_llm_model: str = ""
    ads_llm_models: Annotated[list[str], NoDecode] = ["gemma4:26b", "qwen2.5:14b-instruct", "gemma4:e4b"]
    # every translation step (dub lines today) goes to this model; not installed or empty: the creative model.
    # Google TranslateGemma, Gemma terms: commercial use is fine within the Gemma prohibited-use policy
    translate_llm_model: str = "translategemma:27b"
    # the eye that picks the best photo and checks the finished ads; empty: LLM_MODEL_VISION, then the copywriter
    ads_vision_model: str = ""
    # auto: use Ollama's native /api/chat when the server answers /api/version
    # (it honours num_ctx and think=false), else plain OpenAI-compatible calls
    llm_api: str = "auto"
    llm_num_ctx: int = 8192
    llm_timeout_s: float = 600.0
    # Ollama keep_alive for the big one-shot models (aerab marks, TranslateGemma): Ollama's 5 min default
    # leaves ~17 GB on a card ComfyUI needs. Empty: Ollama's default
    ollama_keep_alive_big: str = "30s"
    # every LLM step of a Quick Create film (outline, scenes, shot lists, cast and frame prompts) when installed;
    # the small creative / reasoning models ignore shot counts and languages. "off": the roles' models
    film_llm_model: str = "gemma4:26b"
    # magic prompt (UI polish P1): what generators do when the client doesn't say; auto | on | off
    magic_prompt_default: str = "auto"
    enhance_timeout_s: float = 30.0

    ffmpeg_bin: str = ""

    # long takes (contract v2)
    longtake_max_s: float = 300.0
    chunk_s: float = 8.0
    chunk_overlap_s: float = 1.0
    chunk_context_frames: int = 9
    longtake_method: str = "extend"  # extend | i2v

    # Quick Create autopilot (contract v4)
    auto_approve_score: float = 6.0
    auto_retries: int = 2
    vision_timeout_s: float = 180.0

    # upscaling (contract v4)
    upscale_segment_s: float = 4.0
    upscale_overlap_s: float = 0.5
    upscale_default_engine: str = "best"  # best | fast | quick
    upscale_seedvr2_model: str = "3b"  # 3b | 7b
    image_upscale_max_mp: float = 8.4  # redraw / faithful image outputs; 3840x2160 is 8.3
    # SUPIR weights are non-commercial only. On while the owner tests; false hides Heavy restore and refuses it
    photo_allow_noncommercial: bool = True
    # image tools (contract v30) whose weights are non-commercial or unverified: CatVTON try-on and the
    # Annotators line art / soft edge models. On while testing; false hides them and the templates fall back
    # to Qwen-Image-Edit. Contract v32: also hides and refuses the image / edit models with non-commercial or
    # revenue-capped weights (Anima, Krea 2, FLUX.2 klein 9B, Qwen-Image 2.1)
    image_allow_noncommercial: bool = True
    # MiniMax-Music3 (revenue-capped community licence) and OmniVoice (CC-BY-NC weights). On while the owner
    # tests; false hides both, refuses them with a 422 and makes VoxCPM2 the default speech engine
    audio_allow_noncommercial: bool = True
    # aerab for Arabic-script languages (contract v23): the master switch keeps its old name so existing
    # .env files carry on; aerab_languages says which languages it covers
    # the vision model looks at every finished picture (and a frame of each video) for anything nude or sexual;
    # off only while the vision model is away (app.content_policy)
    content_check_pictures: bool = True
    urdu_aerab: bool = True
    aerab_languages: str = "ur,ar,fa,ps,sd,pnb"
    # whether ACE-Step sees the aerab on Urdu lyrics; the lyrics keep them either way. Off is the A/B for
    # songs drifting into Arabic, since its training lyrics were almost all written without marks
    song_lyrics_aerab: bool = True
    # the model that adds the marks, when installed; empty or missing: the creative role's model
    aerab_llm_model: str = "gemma4:26b"
    # the model that writes Ideogram 4's JSON captions, when installed; "creative" or empty: the creative role's.
    # e4b's layouts were nonsense and its prose ("celebratory calligraphy") got painted as made-up words; the
    # 26b is unloaded straight after each caption so Ideogram gets the card back
    ideogram_caption_model: str = "gemma4:26b"
    # read an Ideogram picture's lettering back with the vision model; stray or misspelt words: one redraw
    ideogram_text_check: bool = True
    # 3+ quoted English lines: Ideogram draws blank banners and the app sets the words (app.ideogram_text).
    # auto | off
    ideogram_exact_text: str = "auto"
    # what lip-syncs a speaking take: wan_s2v (Wan 2.2 S2V, the default) or ltx (LTX-2.3 audio-to-video, whose
    # mouths often stayed still while the voice ran)
    film_lipsync_engine: str = "wan_s2v"
    # Wan S2V sampling: fast = the 4-step lightx2v LoRA (soft faces, waxy teeth, smeared hands); full = no LoRA
    # at Wan's own shift 3 / cfg 4.5, 20 steps, about ten times slower. A test clip can ask for either
    wan_s2v_finish: str = "fast"
    # LTX lip-sync: LTXVModalityGuidance on the first pass (Lightricks' reference scale 3); 0 turns it off
    ltx_modality_scale: float = 3.0
    # Voice studio (contract v16): speech engines run in their own venvs, on the claiming worker's GPU
    audio_venvs_dir: str = "/data/venvs"
    # where the engine venvs keep their Hugging Face downloads (empty: their own default)
    audio_hf_home: str = ""
    audio_gpu_image: int = 0
    audio_gpu_video: int = 1
    audio_runner_timeout_s: float = 900.0
    # Dub a video (contract v18): audio-separator's vocal model, downloaded into the model dir on first use
    dub_separation_model: str = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
    dub_separation_model_dir: str = "/data/models/audio-separator"
    # cosine distance under which two speech segments count as one speaker ("auto" speaker count)
    dub_speaker_threshold: float = 0.25
    # Stems (contract v21): the four-stem Demucs model; two-stem splits reuse dub_separation_model.
    # Both live in dub_separation_model_dir
    stems_four_model: str = "htdemucs_ft.yaml"
    # songs sung in a chosen voice (child, choir, a Library voice): "" is off, "seed-vc" turns it on once
    # scripts/install-song-voice.sh has set up the venv and the Seed-VC checkout (GPL-3.0, runs here only)
    song_voice_engine: str = ""
    song_voice_venv: str = "seed-vc"
    song_voice_repo: str = "/data/venvs/seed-vc/src"
    # Seed-VC's own advice for singing is 30-50 steps
    song_voice_steps: int = 30
    song_voice_vram_gb: float = 8.0
    # burned subtitles (contract v24a): where the Noto fonts live (fonts-noto-core / -extra on Debian);
    # renderer auto | ass | pillow
    subtitle_fonts_dir: str = "/usr/share/fonts/truetype/noto"
    subtitle_renderer: str = "auto"

    @field_validator("cors_origins", "comfy_urls", "comfy_image_urls", "comfy_video_urls", "ads_llm_models",
                     mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [p.strip().rstrip("/") for p in v.split(",") if p.strip()]
        return v

    @field_validator("film_llm_model", mode="before")
    @classmethod
    def _film_model(cls, v):
        return v.strip() or "gemma4:26b" if isinstance(v, str) else v

    @field_validator("ideogram_caption_model", mode="before")
    @classmethod
    def _ideogram_model(cls, v):
        if isinstance(v, str) and v.strip().lower() == "creative":
            return ""
        return v

    @field_validator("aerab_llm_model", mode="before")
    @classmethod
    def _aerab_model(cls, v):
        # servers carry an empty AERAB_LLM_MODEL= from the old .env.example; that now means the default, and
        # "creative" is how to ask for the creative model
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return "gemma4:26b"
            if v.lower() == "creative":
                return ""
        return v

    @field_validator("data_dir", mode="after")
    @classmethod
    def _abs_data_dir(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    @field_validator("database_url", mode="after")
    @classmethod
    def _abs_sqlite(cls, v: str) -> str:
        # sqlite:///./x.db is relative to whatever cwd uvicorn was started from;
        # pin it to the repo root so api, worker and alembic share one file.
        prefix = "sqlite:///"
        if v.startswith(prefix) and not v.startswith("sqlite:////"):
            raw = v[len(prefix):]
            if raw and raw != ":memory:" and not Path(raw).is_absolute():
                return prefix + (REPO_ROOT / raw).resolve().as_posix()
        return v

    def comfy_urls_for(self, lane: str | None) -> list[str]:
        """ComfyUI instances for a job lane: the lane's own list, then COMFY_URLS, then anything set.

        General jobs don't normally touch ComfyUI; if one ever does it borrows the image GPU. Audio jobs
        are driven with the claiming worker's own GPU lane (worker.run_job); "audio" here is the fallback."""
        own = self.comfy_video_urls if lane == "video" else self.comfy_image_urls
        return list(own or self.comfy_urls or self.all_comfy_urls())

    def all_comfy_urls(self) -> list[str]:
        return list(dict.fromkeys([*self.comfy_urls, *self.comfy_image_urls, *self.comfy_video_urls]))

    @property
    def public_base(self) -> str:
        if self.public_url.strip():
            return self.public_url.strip().rstrip("/")
        return self.cors_origins[0] if self.cors_origins else f"http://{self.app_host}:{self.app_port}"

    @property
    def signup_mode(self) -> str:
        mode = self.signup.strip().lower()
        return mode if mode in ("open", "invite", "closed") else "closed"

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() == "prod"

    def ffmpeg_path(self) -> str:
        if self.ffmpeg_bin:
            return self.ffmpeg_bin
        found = shutil.which("ffmpeg")
        if found:
            return found
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()


@lru_cache
def get_settings() -> Settings:
    return Settings()
