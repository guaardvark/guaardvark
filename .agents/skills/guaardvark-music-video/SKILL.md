---
name: guaardvark-music-video
description: >-
  Turn a song into a beat-synced music video with Guaardvark's Director: song analysis,
  per-cut shot prompts, storyboard keyframes, image-to-video renders, cuts on the beat.
  Use when the user says "music video", "video for this track", or wants visuals timed to
  a song. Has an approval gate before GPU spend.
---

# Music video with Guaardvark

Read `guaardvark-setup` first. Needs the `comfyui` plugin and an installed i2v model
(`wan22-5b` by default; `minimax-h3-int8` for frame-matched continuity).

## Start: MCP `generate_music_video`

- `song`: document id or a filesystem path to mp3/wav/flac/ogg. A path is uploaded for the user.
- `style_prompt`: mood, palette, movement, era ("grainy 16 mm, neon rain, slow dolly, 1984").
- `name` (defaults to the song file name), `i2v_model` (defaults to the active i2v model).
- The tool analyses the song (tempo, beats, sections), writes one unique prompt per cut and
  **stops at the approval gate**. It does not spend GPU rendering clips. Tell the user that.

## The gate and the rest of the pipeline (REST)

`B=${GUAARDVARK_URL:-http://localhost:5000}`, `ID` = the project id the tool returned.

| step | call | what it does |
|---|---|---|
| read the plan | `GET $B/api/music-video/$ID` | cuts, prompts, status, storyboard state |
| edit prompts | `POST $B/api/music-video/$ID/plan` with the edited cut prompts (and optional global style) | operator edits before render |
| regenerate the plan | `POST $B/api/music-video/$ID/regenerate-plan` or `/replan` | new prompts from the Director |
| storyboards first | `POST $B/api/music-video/$ID/generate-storyboards` | one keyframe still per cut, thumbnails-first review |
| redo one keyframe | `POST $B/api/music-video/$ID/regen-storyboard/<idx>` | keeps the others |
| **approve** | `POST $B/api/music-video/$ID/approve` | releases per-clip generation. Only on the user's explicit yes |
| re-analyse | `POST $B/api/music-video/$ID/analyze` | restart song analysis |
| cancel | `POST $B/api/music-video/$ID/cancel` | |
| list all | `GET $B/api/music-video` | |

Creating a project without the MCP tool: `POST $B/api/music-video` with
`{"name", "song_document_id", "style_prompt", "settings": {"i2v_model": "..."}, "user_treatment": "optional short story"}`.
`user_treatment` becomes the creative source the Director writes cuts from.

## How to run it well

1. Start the project, show the cut list and the beat count.
2. Offer storyboards before approval: they are cheap and catch a wrong style early.
3. Ask for approval in plain words with the cost: number of cuts times the clip time for the
   chosen model. Approve only after a clear yes.
4. Character in the video: create a Cast subject and train a LoRA first (guaardvark-cast) so the
   Director can lock identity across cuts.
5. The finished cut is assembled to the detected beat grid. The Video Editor page can trim or
   overlay text afterwards.
