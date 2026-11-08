# API contract v43: Remix a song

No migration. Builds on v21 (stems, the vocal separator), v14 (ACE-Step 1.5 songs) and the song voices
(Seed-VC, `app/song_voice.py`). The page is **Audio → Remix a song** (`/audio/remix`), inside the Audio section.

One to three Library songs (audio items, or videos with sound) go in; every remix comes out as a **new Library
audio item** (FLAC, 48 kHz, -14 LUFS). The sources are never changed.

## Recipes
| `recipe` | Songs | Job type / lane | What it does |
|---|---|---|---|
| `slowed_reverb` | 1 | `audio_remix_fx` / general | resampled to 80 %, convolution reverb (`afir` with a 3 s hall impulse made once in numpy, `data/remix/hall_ir_v1.wav`; echo taps when ffmpeg lacks `afir`), darker top |
| `sped_up` | 1 | `audio_remix_fx` / general | resampled to 115 % |
| `nightcore` | 1 | `audio_remix_fx` / general | resampled to 125 %, `crystalizer` for sparkle |
| `lofi` | 1 | `audio_remix_fx` / general | 95 %, tape wow and flutter (two `vibrato`s), band-limited, light bit-crush, brown-noise bed |
| `bass_boost` | 1 | `audio_remix_fx` / general | low shelf and 60 Hz lift, limited |
| `eight_d` | 1 | `audio_remix_fx` / general | slow auto-pan and a short room |
| `key_tempo` | 1 | `audio_remix_fx` / general | `options.semitones` (-6..6), `options.tempo` (0.7..1.4); rubberband when ffmpeg has it |
| `new_beat` | 1 | `audio_remix` / audio | vocals split off; ACE-Step 1.5 makes an instrumental at the song's tempo and key (`options.style`; Auto picks one when empty), pulled onto the tempo, lined up by onsets, vocals mixed over it |
| `new_voice` | 1 | `audio_remix` / audio | vocals sung again by `options.vocal`: `boy`, `girl`, `choir` or `voice` (+ `voice_id`), over the original music. Needs the Song voices right and Seed-VC on the server |
| `mashup` | 2–3 | `audio_remix` / audio | vocals of song `options.vocals_from` over the music of song `options.music_from` (indexes into `sources`), the music moved to the vocals' tempo (half/double time aware, 0.8–1.25) and to the nearest Camelot-compatible key (same, relative, or a fifth either side; smallest shift wins), lined up by onsets |
| `medley` | 2–3 | `audio_remix_fx` / general | the loudest 40 s of each song (usually its chorus), tempo-matched to song 1 (and key-matched within 3 semitones), cross-faded about two bars |

**Vocals over music** (new beat, mashup): the vocals get a 100 Hz high-pass; the music gets a 3 dB dip at 2.5 kHz
and a gentle duck keyed by the vocals (`sidechaincompress` threshold 0.05, ratio 4, 5/150 ms, mix 0.8), then
`loudnorm` -14 LUFS. Time and pitch moves use rubberband with `pitchq=quality:channels=together` when ffmpeg has it.
Medley joins use equal-power (`qsin`) cross-fades.

Any recipe takes `options.then`: one of the effect recipes laid over the finished version (a slowed medley).

## Endpoints
- `GET /api/audio/remix/recipes` → `{recipes: [{id, label, hint, min_sources, max_sources, available, reason}], styles, max_sources}`.
  `reason` is plain words (not set up on this server, not on your plan); it never mentions licences.
- `GET /api/audio/remix/analyse/{item_id}` → `{item_id, bpm, key, camelot, duration_s}` (`camelot`: `8A` = A minor, `8B` = C major). Estimates (numpy: spectral-flux
  autocorrelation, Krumhansl key profiles), cached beside the file as `<stem>.remix.json`. `bpm` / `key` may be null.
- `POST /api/audio/remix` with `{sources: [item ids, 1–3], auto?: bool, recipes?: [{recipe, options?}], folder_id?}`
  → **202** `{items, jobs}`, one item and one job per version.
  - `auto: true` ignores `recipes`. One song: slowed + reverb, sped up, lo-fi, new beat (8D when a new beat isn't
    available). Two or three: mashup 1→2, mashup 2→1, medley, slowed medley.
  - Items: title `"<song> — <Recipe>"` (`"A × B — Mashup"`, `"A + B — Medley"`), `tags: ["remix", "remix:<source id
    without dashes>", …]` (one per source), in the first source's folder unless `folder_id`. The version's params:
    `{kind: "remix", recipe, options, source_ids, source_generation_ids, created_by: {flow: "remix"}}`, plus
    `sing_as` (new voice) or `model` / `model_resolved` (new beat).
  - Errors: 422 for 0 or more than 3 songs, the same song twice, a song over 10 minutes, a video without sound,
    an unknown recipe, a recipe that doesn't fit the number of songs, a mashup from one song to itself, an
    unavailable recipe, a new voice without `vocal`, key and tempo left untouched. 404 for a missing or removed
    song. 403 from the quota when the plan doesn't include the Audio studio (or Song voices, for a new voice).
- `GET /api/audio/remix/{item_id}` → `{source_id, items (newest first), jobs (queued or running)}`.

## Jobs
- `audio_remix_fx` is ffmpeg only and bills no GPU time (ledger kind `audio_edit`).
- `audio_remix` splits the vocals once per source version into
  `data/workspaces/<ws>/remix/stems/<generation id>/{vocals,instrumental}.flac` and reuses it for later versions;
  splits untouched for 7 days are pruned when a new one is written.
- `job.result`: `{file_path, media_type, duration_s, bytes, recipe, seconds}` plus `bpm`, `key`, `style`,
  `beat_tempo`, `offset_s` (new beat) or `tempo`, `semitones`, `offset_s` (mashup).
- A version that fails leaves its item with a failed version, like any other failed generation.
