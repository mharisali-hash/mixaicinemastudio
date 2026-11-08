"""The public API description (contract v27): Swagger at /api/docs, ReDoc at /api/redoc, spec at /api/openapi.json.

Routers keep their own small tags; the spec regroups them by studio area here so the docs read like the app.
"""
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.security import require_docs_access

AREAS = [
    ("Image", "Text to image, edits with reference pictures, image to image. All return 202 and a job per picture."),
    ("Video", "Text or image to video. One job per take; poll it, then download the file."),
    ("Film", "Projects, scenes, shots, takes, the reel and Quick Create: the film pipeline."),
    ("Characters", "Character Studio: the cast library, character sheets, voices and test clips."),
    ("Audio", "Songs, music, sound effects, stems and the audio editor."),
    ("Voice", "Speech: text to speech, the voice library, voice design, transcripts and dubbing."),
    ("Podcast", "Podcast shows and episodes, rendering and the AI video podcast."),
    ("Photo", "Photo Studio develop, restore and background tools, looks and the photo catalogue."),
    ("3D", "3D Studio: a picture to a textured 3D model (GLB). Results are Library items of kind model."),
    ("Library", "Uploads, the media library, folders, generations and their downloads, templates, models."),
    ("Jobs", "Everything slow runs as a job. Poll GET /api/jobs/{job_id} until it is done."),
]

TAG_AREA = {
    "ai": "Film", "projects": "Film", "scenes": "Film", "shots": "Film", "locations": "Film", "reel": "Film",
    "quick": "Film", "characters": "Characters", "audio": "Audio", "stems": "Audio", "text": "Audio",
    "voices": "Voice", "dialogue": "Voice", "dub": "Voice", "transcribe": "Voice", "stories": "Voice", "podcast": "Podcast",
    "photo": "Photo", "photos": "Photo", "looks": "Photo", "videos": "Video", "poetry": "Video", "presenter": "Video", "ads": "Library", "jobs": "Jobs",
    "library": "Library", "media": "Library", "generations": "Library", "organise": "Library",
    "mentions": "Library", "models": "Library", "brand": "Library", "prompts": "Library", "3d": "3D",
}

# path prefixes that beat the router's tag (library.py also serves the Image studio, audio.py the speak route)
PATH_AREA = [
    ("/api/images/", "Image"),
    ("/api/audio/speak", "Voice"),
    ("/api/characters", "Characters"),
    ("/api/character-sheet", "Characters"),
]

BEARER = {
    "type": "http",
    "scheme": "bearer",
    "bearerFormat": "mxa_<prefix>_<secret>",
    "description": "An API token from Settings → API tokens. Paste the whole token, without the word Bearer.",
}

INTRO = """The studio's REST API. Everything the app does goes through these routes.

**Auth.** Send `Authorization: Bearer mxa_…` with a token from Settings → API tokens.
A token's scopes decide what it can call: `read` for GET, `generate` for POST/PUT/PATCH and `manage` for DELETE.
Your role still applies, so a viewer's token can't generate. Tokens can't sign in, change passwords or make tokens.

**Long work.** Generation routes answer `202` with one or more jobs. Poll `GET /api/jobs/{job_id}` until
`status` is `done`, then fetch the file from `GET /api/generations/{generation_id}/download` or the item's
`media_url`.
"""


def area_for(path: str, tags: list[str]) -> str:
    for prefix, area in PATH_AREA:
        if path.startswith(prefix):
            return area
    for t in tags:
        if t in TAG_AREA:
            return TAG_AREA[t]
    return tags[0].title() if tags else "Library"


def build_schema(app: FastAPI) -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=INTRO, routes=app.routes,
                         tags=[{"name": n, "description": d} for n, d in AREAS])
    for path, ops in schema.get("paths", {}).items():
        for op in ops.values():
            if isinstance(op, dict) and "responses" in op:
                op["tags"] = [area_for(path, op.get("tags") or [])]
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["bearerAuth"] = BEARER
    schema["security"] = [{"bearerAuth": []}]
    app.openapi_schema = schema
    return schema


def multipart(fields: dict[str, str] | None = None, *, file_help: str = "The file to upload",
              many: bool = False) -> dict:
    """openapi_extra for routes that stream a raw multipart body themselves (app.uploads), so FastAPI
    can't see the form. Only documents it; parsing stays in the route."""
    file_schema = {"type": "string", "format": "binary", "description": file_help}
    props = {"file": {"type": "array", "items": file_schema} if many else file_schema}
    for name, desc in (fields or {}).items():
        props[name] = {"type": "string", "description": desc}
    return {"requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": ["file"], "properties": props}}}}}


docs_router = APIRouter(include_in_schema=False)


@docs_router.get("/api/openapi.json")
def openapi_json(request: Request, _=Depends(require_docs_access)):
    return JSONResponse(build_schema(request.app))


@docs_router.get("/api/docs")
def swagger(_=Depends(require_docs_access)):
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title="Jafat AI Studio API",
                               swagger_ui_parameters={"persistAuthorization": True, "docExpansion": "none",
                                                      "tryItOutEnabled": False, "displayRequestDuration": True})


@docs_router.get("/api/redoc")
def redoc(_=Depends(require_docs_access)):
    return get_redoc_html(openapi_url="/api/openapi.json", title="Jafat AI Studio API")
