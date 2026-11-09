---
name: video-editor
description: >-
  Edit video the user already has with Guaardvark's Video Editor (MLT/melt and auto-editor,
  on the CPU): cut a bin of clips to a song's beat, trim silence and dead air out of a
  recording, put a soundtrack and titles on one clip, and hand any timeline to Shotcut as an
  .mlt project. Use when the user brings video files and wants them cut, trimmed, synced to
  music or assembled rather than generated.
---

# Editing video with Guaardvark

Read `setup` first. Needs the `video_editor` plugin (`POST $B/api/plugins/video_editor/start`).
`B=${GUAARDVARK_URL:-http://localhost:5000}`. Edits render on the CPU with `melt` and
`auto-editor`, so they work without an NVIDIA card; `GET $B/api/video-editor/status` says whether
`melt`, `auto-editor`, `ffmpeg` and `ffprobe` were found, with an install hint for each.

Paths are absolute paths on the Guaardvark machine. Most routes also take a Documents id instead
(noted per route). Results land under `data/outputs/videos/` (`mlt-projects/`, `editor-renders/`)
and are added to Documents unless the call sends `"register": false`.

A video with no other instruction can mean this skill or `upscale`: ask which.

## Cut a bin of clips to a song (what the Video Editor page does)

1. Plan: the bin and the song become an arrangement. Queued; an Ollama vision model looks at
   sampled frames of each clip, so say it takes a while for a large bin.

```bash
curl -s -X POST $B/api/video-editor/plan -H 'Content-Type: application/json' -d '{
  "bin_clips": [{"clip_id": "a", "source_path": "/abs/a.mp4"}, {"document_id": 812}],
  "song_path": "/abs/song.mp3", "style_recipe_name": "Music Video", "scan_mode": "both-and", "seed": 0
}'
```
`song_document_id` replaces `song_path`. `GET $B/api/video-editor/recipes` lists the style
recipes (a stock install has Cinematic, Dark, Default, Grunge, Music Video). `scan_mode`
(`audio`, `motion`, `both-or`, `both-and`) decides how dead stretches of a clip are found, with
`audio_threshold`, `motion_threshold` and `margin`. Returns `job_id`; poll
`GET $B/api/video-editor/jobs/<job_id>` until `status` is `done` or `failed`. `result.arrangement`
is the timeline and `result.song.duration_seconds` the song length.

2. Render it (synchronous; minutes for a long song):

```bash
curl -s -X POST $B/api/video-editor/shotcut/compose-arrangement -H 'Content-Type: application/json' -d '{
  "arrangement": <result.arrangement>, "song_document_id": 77, "song_duration_seconds": 184.2,
  "width": 1920, "height": 1080, "render_mp4": true
}'
```
`audio_path` replaces `song_document_id`. Returns `mlt_path`, `rendered_mp4` and `documents`.

## Quick beat-sync from a pool of clips (queued)

```bash
curl -s -X POST $B/api/video-editor/beat-sync/render -H 'Content-Type: application/json' -d '{
  "audio_path": "/abs/song.mp3", "video_paths": ["/abs/a.mp4", "/abs/b.mp4"],
  "subdivision": 2, "min_clip_seconds": 1.2, "use_onsets": false, "render_mp4": true
}'
```
`audio_document_id` and `video_document_ids` replace the paths. Cuts land on beats with no
vision pass. Poll the job as above; the result carries `tempo_bpm`, `cut_count`, `mlt_path` and,
with `render_mp4`, `rendered_mp4`. `POST $B/api/video-editor/analyze {"audio_path": ...}` returns
a song's tempo, beat times and sections without editing anything.

## Trim silence and dead air (synchronous)

```bash
curl -s -X POST $B/api/video-editor/auto-editor/trim -H 'Content-Type: application/json' -d '{
  "input_path": "/abs/recording.mp4", "threshold": 0.04, "margin": "0.2sec", "mode": "mp4"
}'
```
`document_id` replaces `input_path`. Returns the kept `clips` (start and end seconds) and
`output_path`; `"mode": "kdenlive"` writes a Kdenlive project instead of a trimmed MP4.

## One clip with a soundtrack and titles (queued)

```bash
curl -s -X POST $B/api/video-overlay/render-timeline -H 'Content-Type: application/json' -d '{
  "video_document_id": 812, "video_trim_start": 2.0, "video_trim_end": 14.5,
  "audio_document_id": 77, "audio_volume": 0.8,
  "text_elements": [{"text": "Opening night", "fontSize": 64, "fontColor": "#ffffff",
                     "x": 120, "y": 900, "startSeconds": 0, "endSeconds": 4}]
}'
```
Document ids only. `x` and `y` are pixels from the top left of the frame; `fontColor` is a colour
name or `#rrggbb`; titles are drawn level (no rotation). Returns `job_id`; poll
`GET $B/api/video-overlay/render-status/<job_id>` until it carries `document_id`, the finished MP4.

## Rules

- Never overwrite or delete the user's source clips; every route writes new files.
- `POST $B/api/video-editor/open-in-shotcut {"mlt_path": ...}` opens Shotcut on the desktop of
  the machine running Guaardvark. Ask before using it; offer the `.mlt` path otherwise.
- `GET $B/api/video-editor/projects` lists the projects saved on the Video Editor page. Read them;
  leave changing them to the page.
- Filters and transitions an arrangement may name: `GET $B/api/video-editor/catalog/filters` and
  `GET $B/api/video-editor/catalog/transitions`.
