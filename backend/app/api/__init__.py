from fastapi import APIRouter

from app.api import ai, audio, audio_edit, auth, brands, catalog, character_looks, characters, events, generations, jobs, library, locations, looks, media, mentions, organise, photo, photos, projects, prompts, quick, reel, scenes, shots, system, videos, voices
from app.api import dialogue
from app.api import dub, transcribe
from app.api import gallery
from app.api import podcast
from app.api import stems
from app.api import video_podcast
from app.api import tokens
from app.api import files
from app.api import stories
from app.api import poetry
from app.api import presenter
from app.api import ads
from app.api import model3d
from app.api import admin
from app.api import admin_panel
from app.api import bulk
from app.api import account
from app.api import print as print_studio
from app.api import implant
from app.api import site

router = APIRouter(prefix="/api")
for mod in (auth, system, projects, scenes, shots, characters, character_looks, locations, generations, jobs, events, library, organise, media, ai, reel, quick, catalog, videos, audio, audio_edit, brands, prompts, mentions, photo, looks, photos, gallery, voices):
    # sign-in, the event stream and system checks are for the app itself, not API clients
    router.include_router(mod.router, include_in_schema=mod not in (auth, system, events))
router.include_router(dialogue.router)
router.include_router(dub.router)
router.include_router(transcribe.router)
router.include_router(podcast.router)
router.include_router(stems.router)
router.include_router(video_podcast.router)
router.include_router(audio.text_router)
router.include_router(stories.router)
router.include_router(poetry.router)
router.include_router(presenter.router)
router.include_router(ads.router)
router.include_router(model3d.router)
router.include_router(tokens.router, include_in_schema=False)
router.include_router(admin.router, include_in_schema=False)
router.include_router(admin_panel.router, include_in_schema=False)
router.include_router(bulk.router, include_in_schema=False)
# contract v41: admins only, so not in the API document either
router.include_router(print_studio.router, include_in_schema=False)
router.include_router(account.router, include_in_schema=False)
# M22: surgeons with access only; kept out of the API document like Print Studio
router.include_router(implant.router, include_in_schema=False)
# the public website's own data, no sign-in
router.include_router(site.router, include_in_schema=False)
# signed links handed out by the MCP server; not something to call by hand
router.include_router(files.router, include_in_schema=False)
