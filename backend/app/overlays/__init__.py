"""Animated overlays: karaoke captions, title cards, lower thirds and a logo sting, drawn as transparent video
by the HyperFrames renderer (Apache-2.0, pinned in tools/hyperframes) and laid over a finished cut with ffmpeg.

Anything that goes wrong here falls back to the plain burned subtitles (app.subtitles), so a video is never
lost because of its captions.
"""
from app.overlays.render import (CAPTION_STYLES, available, burn_captions, card, compose)  # noqa: F401
