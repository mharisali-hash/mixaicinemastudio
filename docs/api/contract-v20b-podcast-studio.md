---
title: "Contract v20b: Podcast Studio (audio or video episodes, Character hosts)"
status: Active
created: 2026-11-08
extends: contract-v20-podcast.md, contract-v24-video-podcast.md
migration: none (JSON columns only)
---

# Contract v20b: Podcast Studio

Podcasts leave Audio for a studio of their own at `/podcast`. Every episode is an **audio** or a **video**
podcast from the start; a video episode's stepper runs on from Publish into the AI video's six steps.

## Episode medium

- Stored in `podcast_episode.settings["medium"]`: `"audio" | "video"`, missing = audio.
  - It isn't `format`, because `format` is already the conversation style.
- `EpisodeIn.medium` (default `"audio"`). With `"video"`, the `VideoPodcast` row is created in the same request.
- `EpisodePatch.medium` accepts only `"video"`: an audio episode can become a video podcast (this creates its
  VideoPodcast); the way back is refused with 422.
- `EpisodeOut.medium` and `EpisodeSummary.medium` are `"video"` when the setting says so **or** a VideoPodcast
  exists. So an audio episode given an AI video before Podcast Studio counts as video.
- A settings patch, which is written whole, keeps the medium.
- `EpisodePatch.step`: 1–12. Steps 7–12 are the video steps: Cast, Set, Look, Cameras & cut, Render, Result.

## Hosts linked to Character Studio

- `Host` (episodes and shows) gains `character_id` and `look_id`, both optional.
  - `look_id` is dropped when there's no `character_id`.
  - The character must be a Character Studio one (no project) in the workspace: 404 when unknown, 422 when it
    belongs to a project.
  - The look must be that character's: 422 otherwise.
- **Video side:** `HostChoice.source` gains `"studio"`, with `{character_id, look_id?}`.
  - The look's finished picture (or the character's approved/ready portrait when there's no look) is copied
    into the video's own project and approved.
  - A look with no finished picture yet: 409.
  - Regenerating a studio host's portrait is refused (409), as for `existing`.
- **Kept in sync:** episode hosts with a `character_id` are applied to the video as `studio` automatically.
  - When: on create, on an episode hosts patch (when the video is idle), and on `continue`.
  - What it redoes: the cast stage, whenever a portrait changed.
  - Photo hosts stay: a host given a photo in the Cast step keeps it.
  - Unlinking: a host unlinked in the episode goes back to `persona`.
- `VideoPodcastOut.hosts[].look_id` is new.

## Routes (frontend)

- `/podcast`, `/podcast/:id` (with `?step=cast|set|look|cameras|render|result` or an episode step id), and
  `/podcast?new=audio|video`, which opens the new-episode dialog.
- `/audio/podcast…` and `/podcast/:id/video` redirect.
