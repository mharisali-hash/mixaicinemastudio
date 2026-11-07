"""Brand kit request/response shapes (contract v7).

Separate from app.schemas so the compositing code can import the settings model without pulling in
the whole API surface.
"""
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.limits import PROMPT_MAX
from app.schemas import JobOut, MediaItemOut, Utc

HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def norm_hex(v: str) -> str:
    m = HEX.match((v or "").strip())
    if not m:
        raise ValueError(f"'{v}' isn't a #RRGGBB colour")
    return "#" + m[1].upper()


# ---------------------------------------------------------------- output-time settings

class WatermarkSettings(BaseModel):
    enabled: bool = False
    position: Literal["tl", "tr", "bl", "br"] = "br"
    # the logo's longer side as a % of the frame's short side
    size_pct: float = Field(12.0, ge=2, le=50)
    opacity: float = Field(0.85, ge=0.05, le=1)
    margin_pct: float = Field(3.0, ge=0, le=20)
    variant: Literal["auto", "primary", "light", "dark"] = "auto"


Background = int | str | None  # palette index, "#RRGGBB", or None for the first palette colour


class CardSettings(BaseModel):
    enabled: bool = False
    duration_s: float = Field(2.5, ge=1, le=8)
    bg: Background = None
    show_tagline: bool = True
    show_name: bool = False


class LowerThirdSettings(BaseModel):
    enabled: bool = False
    at_s: float = Field(1.0, ge=0)
    duration_s: float = Field(4.0, ge=1, le=30)
    text: str | None = Field(None, max_length=120)
    subtext: str | None = Field(None, max_length=160)


class GradeSettings(BaseModel):
    enabled: bool = False
    strength: float = Field(0.25, ge=0, le=1)


def _intro_default() -> CardSettings:
    return CardSettings(duration_s=2.0, show_tagline=False, show_name=True)


class BrandSettings(BaseModel):
    """All post-processing extras. Everything is off by default (PLAN-m8 B2: the logo belongs inside
    the scenes; these are optional add-ons)."""
    watermark: WatermarkSettings = WatermarkSettings()
    end_card: CardSettings = CardSettings()
    intro_card: CardSettings = Field(default_factory=_intro_default)
    lower_third: LowerThirdSettings = LowerThirdSettings()
    grade: GradeSettings = GradeSettings()
    # how an advert planned with this kit ends (brand_moments.closing_mode); "none" plans no closing shot
    closing: Literal["auto", "ai_packshot", "logo_reveal", "none"] = "auto"

    def enabled(self) -> list[str]:
        return [k for k in ("watermark", "intro_card", "end_card", "lower_third", "grade") if getattr(self, k).enabled]


def merged_settings(base: dict | None, override: dict | None = None) -> BrandSettings:
    """Kit settings with per-call overrides on top (one level deep per section)."""
    # start from the defaults section by section: the intro card's own defaults differ from CardSettings'
    data = BrandSettings().model_dump()
    for k, v in (base or {}).items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k].update(v)
        elif k in data and not isinstance(data[k], dict):
            data[k] = v
    for k, v in (override or {}).items():
        if k not in data:
            continue
        if not isinstance(data[k], dict):
            data[k] = v  # scalar settings (closing) replace outright
        elif isinstance(v, bool):
            data[k]["enabled"] = v
        elif isinstance(v, dict):
            data[k].update(v)
    return BrandSettings.model_validate(data)


# ---------------------------------------------------------------- kit

class PaletteColour(BaseModel):
    hex: str
    name: str | None = Field(None, max_length=60)

    @field_validator("hex")
    @classmethod
    def _hex(cls, v):
        return norm_hex(v)


class LogoRef(BaseModel):
    media_id: str
    description: str = Field("", max_length=300)


class Logos(BaseModel):
    primary: LogoRef | None = None
    light: LogoRef | None = None
    dark: LogoRef | None = None


class Product(BaseModel):
    media_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=300)


CONTACT_KEYS = ("phone", "whatsapp", "email", "website", "booking_url", "location", "opening_hours")


class Contact(BaseModel):
    """What an advert's footer can show (contract v36 + addendum). Free text, shown as typed."""
    phone: str = Field("", max_length=40)
    whatsapp: str = Field("", max_length=40, description="The WhatsApp number; may be the same as the phone")
    email: str = Field("", max_length=120)
    website: str = Field("", max_length=120)
    booking_url: str = Field("", max_length=200)
    location: str = Field("", max_length=120, description="Address or area; off in ads unless picked")
    opening_hours: str = Field("", max_length=60, description="Short: Mon-Fri 8-18")

    @field_validator("*")
    @classmethod
    def _tidy(cls, v):
        return " ".join((v or "").split())


Platform = Literal["instagram", "facebook", "tiktok", "linkedin", "x", "youtube", "pinterest", "snapchat",
                   "threads", "whatsapp"]
PLATFORMS: tuple[str, ...] = Platform.__args__

# the hosts each platform answers on, for reading the handle out of a pasted link
HOSTS = {"instagram": ("instagram.com", "instagr.am"), "facebook": ("facebook.com", "fb.com", "fb.me"),
         "tiktok": ("tiktok.com",), "linkedin": ("linkedin.com",), "x": ("x.com", "twitter.com"),
         "youtube": ("youtube.com", "youtu.be"), "pinterest": ("pinterest.com", "pin.it"),
         "snapchat": ("snapchat.com",), "threads": ("threads.net", "threads.com"),
         "whatsapp": ("wa.me", "whatsapp.com")}
PROFILE = {"instagram": "https://instagram.com/{}", "facebook": "https://facebook.com/{}",
           "tiktok": "https://tiktok.com/@{}", "linkedin": "https://linkedin.com/company/{}",
           "x": "https://x.com/{}", "youtube": "https://youtube.com/@{}", "pinterest": "https://pinterest.com/{}",
           "snapchat": "https://snapchat.com/add/{}", "threads": "https://threads.net/@{}",
           "whatsapp": "https://wa.me/{}"}
# path words that come before the name on some platforms (linkedin.com/company/x, snapchat.com/add/x)
_SKIP = {"company", "in", "school", "showcase", "add", "c", "user", "channel", "pages", "people"}
_NAME = re.compile(r"^[\w.\-]{1,60}$")
_LINK = re.compile(r"^(?:https?://)?(?:www\.|m\.)?([a-z0-9.\-]+\.[a-z]{2,})(/[^?#]*)?", re.I)


def platform_of(url: str) -> str | None:
    m = _LINK.match((url or "").strip())
    if not m or "." not in m[1]:
        return None
    host = m[1].lower()
    return next((p for p, hosts in HOSTS.items() if any(host == h or host.endswith("." + h) for h in hosts)), None)


def _from_link(platform: str, url: str) -> str:
    m = _LINK.match(url)
    if not m:
        raise ValueError(f"'{url}' isn't a link")
    parts = [p for p in (m[2] or "").split("/") if p]
    if platform == "whatsapp":
        return "+" + re.sub(r"\D", "", parts[0] if parts else "")
    parts = [p for p in parts if p.lower() not in _SKIP] or parts
    if not parts:
        raise ValueError(f"'{url}' has no account name in it")
    return parts[0].lstrip("@")


def norm_handle(platform: str, handle: str, url: str | None = None) -> tuple[str, str]:
    """(the handle as an ad shows it, the profile link). Spaces go, a pasted link gives up its name, and every
    platform but LinkedIn gets an "@" in front. WhatsApp's handle is the number."""
    h = re.sub(r"\s+", "", handle or "")
    u = re.sub(r"\s+", "", url or "") or None
    if h and ("/" in h or h.lower().startswith(("http", "www."))):
        u, h = u or h, ""
    if not h and u:
        h = _from_link(platform, u)
    if platform == "whatsapp":
        digits = re.sub(r"\D", "", h)
        if h.startswith("00"):
            digits = digits[2:]
        if not 6 <= len(digits) <= 15:
            raise ValueError(f"'{handle or url}' isn't a WhatsApp number")
        h = ("+" if h.startswith(("+", "00")) else "") + digits
    else:
        h = h.lstrip("@")
        if not _NAME.match(h):
            raise ValueError(f"'{handle or url}' isn't a {platform} handle")
        if platform != "linkedin":
            h = "@" + h
    if u and not u.lower().startswith("http"):
        u = "https://" + u
    return h, (u or PROFILE[platform].format(h.lstrip("@+")))[:300]


class Social(BaseModel):
    platform: Platform
    handle: str = Field("", max_length=300, description="@name, or a profile link the handle is read from")
    url: str | None = Field(None, max_length=300)
    show_in_ads: bool = True

    @model_validator(mode="after")
    def _norm(self):
        if not (self.handle.strip() or (self.url or "").strip()):
            raise ValueError(f"The {self.platform} entry needs a handle or a link")
        self.handle, self.url = norm_handle(self.platform, self.handle, self.url)
        return self


def social_key(s: dict) -> str:
    return f"{s.get('platform')}:{(s.get('handle') or '').lstrip('@+').lower()}"


def tidy_socials(v: list | None) -> list | None:
    if v is None:
        return None
    out, seen = [], set()
    for s in v:
        k = social_key(s.model_dump() if isinstance(s, BaseModel) else s)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def legacy_social(text: str) -> dict | None:
    """The old one-line `social` field as a socials entry: the platform from a link, else Instagram (what a
    bare "@name" nearly always meant)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return Social(platform=platform_of(text) or "instagram", handle=text).model_dump()
    except ValueError:
        return None


def lift_social(data):
    # kits saved before the socials list sent `contact.social`; it becomes the first entry
    if isinstance(data, dict) and isinstance(data.get("contact"), dict) and "social" in data["contact"]:
        contact = dict(data["contact"])
        old = legacy_social(contact.pop("social") or "")
        data = {**data, "contact": contact}
        if old and not data.get("socials"):
            data["socials"] = [old]
    return data


def tidy_claims(v: list[str] | None) -> list[str] | None:
    if v is None:
        return None
    out: list[str] = []
    for c in v:
        c = " ".join((c or "").split())
        if c and c not in out:
            out.append(c)
    return out


Claim = Annotated[str, Field(max_length=160)]


class BrandKitIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    is_default: bool = False
    palette: list[PaletteColour] = Field([], max_length=8)
    style_text: str = Field("", max_length=PROMPT_MAX)
    voice_text: str = Field("", max_length=PROMPT_MAX)
    tagline: str = Field("", max_length=300)
    logos: Logos = Logos()
    products: list[Product] = Field([], max_length=12)
    font_files: list[str] = Field([], max_length=4)
    reference_media_ids: list[str] = Field([], max_length=6)
    settings: dict[str, Any] = {}
    contact: Contact = Contact()
    socials: list[Social] = Field([], max_length=10)
    claims: list[Claim] = Field([], max_length=30, description="Approved offer lines: prices, discounts, guarantees")

    @model_validator(mode="before")
    @classmethod
    def _lift(cls, data):
        return lift_social(data)

    @field_validator("socials")
    @classmethod
    def _socials(cls, v):
        return tidy_socials(v)

    @field_validator("claims")
    @classmethod
    def _claims(cls, v):
        return tidy_claims(v)


class BrandKitPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    palette: list[PaletteColour] | None = Field(None, max_length=8)
    style_text: str | None = Field(None, max_length=PROMPT_MAX)
    voice_text: str | None = Field(None, max_length=PROMPT_MAX)
    tagline: str | None = Field(None, max_length=300)
    logos: Logos | None = None
    products: list[Product] | None = Field(None, max_length=12)
    font_files: list[str] | None = Field(None, max_length=4)
    reference_media_ids: list[str] | None = Field(None, max_length=6)
    settings: dict[str, Any] | None = None
    contact: Contact | None = None
    socials: list[Social] | None = Field(None, max_length=10)
    claims: list[Claim] | None = Field(None, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def _lift(cls, data):
        return lift_social(data)

    @field_validator("socials")
    @classmethod
    def _socials(cls, v):
        return tidy_socials(v)

    @field_validator("claims")
    @classmethod
    def _claims(cls, v):
        return tidy_claims(v)


class AssetOut(BaseModel):
    media_id: str
    kind: str | None = None
    media_url: str | None = None
    missing: bool = False


class BrandKitOut(BaseModel):
    id: str
    workspace_id: str
    name: str
    is_default: bool
    palette: list[dict[str, Any]]
    style_text: str
    voice_text: str
    tagline: str
    logos: dict[str, Any]
    products: list[dict[str, Any]]
    font_files: list[str]
    reference_media_ids: list[str]
    settings: BrandSettings
    contact: dict[str, str] = {}
    socials: list[dict[str, Any]] = []
    claims: list[str] = []
    ad_style: dict[str, Any] = {}
    assets: dict[str, AssetOut] = {}
    prompt_context: str = ""
    created_at: Utc
    updated_at: Utc


class BrandPreviewIn(BaseModel):
    kind: Literal["image", "end_card", "intro_card", "lower_third"] = "image"
    sample_media_id: str | None = None
    # unsaved editor changes, merged over the kit's settings
    settings: dict[str, Any] | None = None


class BrandApplyIn(BaseModel):
    kit_id: str | None = None
    # per-call overrides: {"watermark": true} or {"end_card": {"enabled": true, "duration_s": 3}}
    options: dict[str, Any] | None = None


class RevealBackground(BaseModel):
    kind: Literal["color", "image"] = "color"
    color: int | str | None = None
    media_id: str | None = None


class LogoRevealIn(BaseModel):
    duration_s: float = Field(3.0, ge=1.5, le=10)
    aspect: Literal["16:9", "9:16", "1:1", "4:5"] = "16:9"
    background: RevealBackground = RevealBackground()
    show_tagline: bool = True
    title: str | None = Field(None, max_length=300)


class LogoRevealOut(BaseModel):
    item: MediaItemOut
    job: JobOut


class ProjectBrandIn(BaseModel):
    kit_id: str | None = None
