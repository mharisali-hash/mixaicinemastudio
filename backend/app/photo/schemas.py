"""Request/response models for Photo Studio (contract v9). Kept here rather than in app/schemas.py."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.limits import PROMPT_MAX
from app.schemas import Utc


def _s(lo: float = -100, hi: float = 100):
    return Field(0, ge=lo, le=hi)


class _Loose(BaseModel):
    # unknown keys are ignored so an older or newer client never gets a 422 for an extra field
    model_config = ConfigDict(extra="ignore")


class Curve(_Loose):
    blacks: float = _s()
    shadows: float = _s()
    mids: float = _s()
    highlights: float = _s()
    whites: float = _s()


class HslBand(_Loose):
    h: float = _s()
    s: float = _s()
    l: float = _s()  # noqa: E741  (the NoorViz key)


class Hsl(_Loose):
    red: HslBand = HslBand()
    orange: HslBand = HslBand()
    yellow: HslBand = HslBand()
    green: HslBand = HslBand()
    aqua: HslBand = HslBand()
    blue: HslBand = HslBand()
    purple: HslBand = HslBand()
    magenta: HslBand = HslBand()


class Crop(_Loose):
    x: float = Field(0, ge=0)
    y: float = Field(0, ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class LightPoint(_Loose):
    x: float
    y: float
    exposure: float = _s()
    falloff: float | None = Field(None, gt=0)
    refW: float | None = Field(None, gt=0)  # noqa: N815
    refH: float | None = Field(None, gt=0)  # noqa: N815


class PaletteEntry(_Loose):
    centerR: float = Field(ge=0, le=255)  # noqa: N815
    centerG: float = Field(ge=0, le=255)  # noqa: N815
    centerB: float = Field(ge=0, le=255)  # noqa: N815
    enabled: bool = True
    h: float = _s()
    s: float = _s()
    l: float = _s()  # noqa: E741


class LutRef(_Loose):
    look_id: str = Field(min_length=1, max_length=36)
    amount: float = Field(100, ge=0, le=100)


class ProfileRef(_Loose):
    id: str = Field(min_length=1, max_length=60)
    amount: float = Field(100, ge=0, le=200)


class PCurve(_Loose):
    highlights: float = _s()
    lights: float = _s()
    darks: float = _s()
    shadows: float = _s()
    s1: float = Field(25, ge=5, le=95)
    s2: float = Field(50, ge=5, le=95)
    s3: float = Field(75, ge=5, le=95)


CurvePts = list[tuple[float, float]]


class CurvePoints(_Loose):
    rgb: CurvePts = Field(default_factory=list, max_length=16)
    red: CurvePts = Field(default_factory=list, max_length=16)
    green: CurvePts = Field(default_factory=list, max_length=16)
    blue: CurvePts = Field(default_factory=list, max_length=16)


class PointColorEntry(_Loose):
    hue: float = Field(0, ge=0, le=360)
    sat: float = Field(0, ge=0, le=1)
    val: float = Field(0, ge=0, le=1)
    dh: float = _s()
    ds: float = _s()
    dl: float = _s()
    hueRange: float = Field(50, ge=0, le=100)  # noqa: N815
    satRange: float = Field(50, ge=0, le=100)  # noqa: N815
    lumRange: float = Field(50, ge=0, le=100)  # noqa: N815


class GradeZone(_Loose):
    h: float = Field(0, ge=0, le=360)
    s: float = Field(0, ge=0, le=100)
    l: float = _s()  # noqa: E741


class Grading(_Loose):
    shadows: GradeZone = GradeZone()
    midtones: GradeZone = GradeZone()
    highlights: GradeZone = GradeZone()
    global_: GradeZone = Field(GradeZone(), alias="global")
    blending: float = Field(50, ge=0, le=100)
    balance: float = _s()

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Calibration(_Loose):
    shadowsTint: float = _s()  # noqa: N815
    redHue: float = _s()  # noqa: N815
    redSat: float = _s()  # noqa: N815
    greenHue: float = _s()  # noqa: N815
    greenSat: float = _s()  # noqa: N815
    blueHue: float = _s()  # noqa: N815
    blueSat: float = _s()  # noqa: N815


class BwMix(_Loose):
    red: float = _s()
    orange: float = _s()
    yellow: float = _s()
    green: float = _s()
    aqua: float = _s()
    blue: float = _s()
    purple: float = _s()
    magenta: float = _s()


class Sharpen(_Loose):
    amount: float = Field(0, ge=0, le=150)
    radius: float = Field(1.0, ge=0.5, le=3.0)
    detail: float = Field(25, ge=0, le=100)
    masking: float = Field(0, ge=0, le=100)


class NoiseReduction(_Loose):
    luminance: float = Field(0, ge=0, le=100)
    detail: float = Field(50, ge=0, le=100)
    contrast: float = Field(0, ge=0, le=100)
    color: float = Field(0, ge=0, le=100)
    colorDetail: float = Field(50, ge=0, le=100)  # noqa: N815
    smoothness: float = Field(50, ge=0, le=100)


class Lens(_Loose):
    caAuto: bool = False  # noqa: N815
    caRed: float = _s()  # noqa: N815
    caBlue: float = _s()  # noqa: N815
    distortion: float = _s()
    vignette: float = _s()
    vignetteMidpoint: float = Field(50, ge=0, le=100)  # noqa: N815
    purpleAmount: float = Field(0, ge=0, le=20)  # noqa: N815
    purpleHueLo: float = Field(30, ge=0, le=100)  # noqa: N815
    purpleHueHi: float = Field(70, ge=0, le=100)  # noqa: N815
    greenAmount: float = Field(0, ge=0, le=20)  # noqa: N815
    greenHueLo: float = Field(40, ge=0, le=100)  # noqa: N815
    greenHueHi: float = Field(60, ge=0, le=100)  # noqa: N815


UprightMode = Literal["off", "auto", "level", "vertical", "full"]


class Transform(_Loose):
    upright: UprightMode = "off"
    uprightMatrix: list[float] | None = Field(None, min_length=9, max_length=9)  # noqa: N815
    constrain: bool = False
    vertical: float = _s()
    horizontal: float = _s()
    rotate: float = _s(-10, 10)
    aspect: float = _s()
    scale: float = Field(100, ge=50, le=150)
    offsetX: float = _s()  # noqa: N815
    offsetY: float = _s()  # noqa: N815


class DevelopParams(_Loose):
    version: int = 1
    exposure: float = Field(0, ge=-100, le=100)  # v2: EV -5..5; v1 records: -100..100
    contrast: float = _s()
    highlights: float = _s()
    shadows: float = _s()
    whites: float = _s()
    blacks: float = _s()
    temperature: float = _s()
    tint: float = _s()
    vibrance: float = _s()
    saturation: float = _s()
    clarity: float = _s()
    sharpness: float = _s(0, 100)
    noiseReduction: float = _s(0, 100)  # noqa: N815
    vignette: float = _s()
    curve: Curve = Curve()
    hsl: Hsl = Hsl()
    crop: Crop | None = None
    rotate: float = _s(-180, 180)
    flipH: bool = False  # noqa: N815
    flipV: bool = False  # noqa: N815
    lightPoints: list[LightPoint] = Field(default_factory=list, max_length=16)  # noqa: N815
    palette: list[PaletteEntry] = Field(default_factory=list, max_length=16)
    lut: LutRef | None = None
    off: list[str] = Field(default_factory=list, max_length=12)
    # params v2 (M10 / D2)
    profile: ProfileRef | None = None
    treatment: Literal["color", "bw"] = "color"
    bw: BwMix = BwMix()
    pcurve: PCurve = PCurve()
    points: CurvePoints = CurvePoints()
    refineSat: float = Field(100, ge=0, le=100)  # noqa: N815
    pointColor: list[PointColorEntry] = Field(default_factory=list, max_length=8)  # noqa: N815
    grading: Grading = Grading()
    calibration: Calibration = Calibration()
    vignetteMidpoint: float = Field(50, ge=0, le=100)  # noqa: N815
    vignetteRoundness: float = _s()  # noqa: N815
    vignetteFeather: float = Field(50, ge=0, le=100)  # noqa: N815
    vignetteHighlights: float = Field(0, ge=0, le=100)  # noqa: N815
    vignetteStyle: Literal["highlight", "color", "paint"] = "highlight"  # noqa: N815
    grainAmount: float = Field(0, ge=0, le=100)  # noqa: N815
    grainSize: float = Field(25, ge=0, le=100)  # noqa: N815
    grainRoughness: float = Field(50, ge=0, le=100)  # noqa: N815
    # D2b
    texture: float = _s()
    dehaze: float = _s()
    sharpen: Sharpen = Sharpen()
    nr: NoiseReduction = NoiseReduction()
    lens: Lens = Lens()
    transform: Transform = Transform()

    def plain(self) -> dict:
        return self.model_dump(exclude_none=False, by_alias=True)


Format = Literal["jpeg", "png", "png16", "tiff16"]
EffectName = Literal["deyellow", "pop", "bw", "sepia", "warm", "cool", "soften_skin", "denoise", "auto_restore"]


class PreviewIn(BaseModel):
    params: DevelopParams = DevelopParams()
    max_side: int = Field(1280, ge=64, le=2560)
    # Lightroom's Alt-drag view of the sharpening Masking slider
    debug: Literal["sharpen_mask"] | None = None


class UprightIn(BaseModel):
    mode: UprightMode = "auto"


class ParamsIn(BaseModel):
    params: DevelopParams = DevelopParams()


class RenderIn(BaseModel):
    params: DevelopParams = DevelopParams()
    format: Format = "jpeg"
    quality: int = Field(95, ge=60, le=100)
    note: str | None = Field(None, max_length=2000)


class SyncIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    params: DevelopParams = DevelopParams()
    groups: list[str] = Field(min_length=1, max_length=12)
    format: Format = "jpeg"
    quality: int = Field(95, ge=60, le=100)


class SnapshotIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    params: DevelopParams = DevelopParams()
    base_id: str | None = None


class SnapshotPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    params: DevelopParams | None = None


class EffectIn(BaseModel):
    name: EffectName
    strength: float = Field(1.0, ge=0, le=1)
    format: Format = "jpeg"
    quality: int = Field(95, ge=60, le=100)
    note: str | None = Field(None, max_length=2000)


class CubeIn(BaseModel):
    params: DevelopParams = DevelopParams()
    size: int = Field(33, ge=2, le=65)
    title: str = Field("", max_length=120)


class LookIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    params: DevelopParams = DevelopParams()
    description: str = Field("", max_length=2000)
    category: str = Field("", max_length=80)
    generation_id: str | None = None


class LookPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    params: DevelopParams | None = None
    description: str | None = Field(None, max_length=2000)
    category: str | None = Field(None, max_length=80)


class ApplyVideoIn(BaseModel):
    generation_id: str = Field(min_length=1, max_length=36)
    intensity: float = Field(1.0, ge=0, le=1)


class LookOut(BaseModel):
    id: str
    name: str
    description: str
    category: str
    source: str
    params: dict[str, Any]
    has_cube: bool
    cube_size: int | None = None
    editable: bool
    spatial: list[str]
    thumb_url: str
    created_at: Utc
    updated_at: Utc


class ImportReport(BaseModel):
    file: str
    ok: bool
    kind: str | None = None
    look_id: str | None = None
    name: str | None = None
    cube_size: int | None = None
    mapped: list[str] = []
    unmapped: list[str] = []
    notes: list[str] = []
    error: str | None = None


class ImportOut(BaseModel):
    looks: list[LookOut]
    reports: list[ImportReport]



# ---------------------------------------------------------------- restore / cut-out (contract v10)

class Point(_Loose):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Edge(_Loose):
    feather: float = Field(0.6, ge=0, le=20)
    shift: int = Field(0, ge=-10, le=10)


class RestoreIn(_Loose):
    tool: str = Field(min_length=1, max_length=40)
    variant: str | None = Field(None, max_length=40)
    strength: float = Field(1.0, ge=0.05, le=1.0)
    prompt: str | None = Field(None, max_length=PROMPT_MAX)
    # select (SAM 3): words and/or clicks, 0..1 of the picture
    words: str | None = Field(None, max_length=200)
    include: list[Point] = Field(default_factory=list, max_length=24)
    exclude: list[Point] = Field(default_factory=list, max_length=24)
    op: Literal["replace", "add", "subtract"] | None = None
    edge: Edge | None = None
    note: str | None = Field(None, max_length=500)


class SmartStep(_Loose):
    tool: str = Field(min_length=1, max_length=40)
    on: bool = True
    variant: str | None = Field(None, max_length=40)
    strength: float = Field(1.0, ge=0.05, le=1.0)


class SmartRestoreIn(BaseModel):
    steps: list[SmartStep] = Field(min_length=1, max_length=12)


class SmartRestoreQueued(BaseModel):
    chain_id: str
    steps: list[dict[str, Any]] = Field(description="The steps that will run, in order")
    first_job_id: str = Field(description="Each step queues the next when it finishes; poll this one first")


class HistogramOut(BaseModel):
    r: list[float] = Field(description="256 bins, scaled so the tallest bin of any channel is 1")
    g: list[float]
    b: list[float]
    luma: list[float]
    clipped: dict[str, float] = Field(description="Share of pixels clipped in the shadows and highlights (0-1)")


class AutoOut(BaseModel):
    params: dict[str, Any] = Field(description="Develop params to apply; nothing is saved")
    analysis: dict[str, Any]
    notes: list[Any]


class Background(_Loose):
    type: Literal["transparent", "colour", "image", "blur", "generated"] = "transparent"
    colour: str | None = Field(None, max_length=9)
    generation_id: str | None = Field(None, max_length=64)
    radius: float | None = Field(None, ge=2, le=60)


class BackgroundIn(BaseModel):
    background: Background = Background()
    edge: Edge | None = None
