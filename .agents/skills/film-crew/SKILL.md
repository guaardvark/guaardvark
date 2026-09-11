---
name: film-crew
description: >-
  Run Guaardvark's five-role Film Crew locally: screenwriter, casting (Cast Library
  LoRAs), cinematographer storyboards, keyframe renders, editor. Turns a logline or
  screenplay into a finished short video with human gates at casting and storyboard. Use
  when the user says "film this", "make a short film", "storyboard this script", or "start
  the film crew".
---

# Film Crew with Guaardvark

Read `setup` first. Needs `comfyui`; casting with trained characters needs
`lora_trainer` (see the cast skill). Renders take minutes per shot.

## Start: MCP `start_film_crew`

- `script_text`: a screenplay or a plain scene list. A logline works; the screenwriter expands it.
- `name` (defaults to the first line), `video_model` (defaults to the active scene model,
  `minimax-h3-int8` on a typical box; `wan22-5b` for silent i2v).
- The screenwriter starts at once. Casting, storyboards and GPU renders **wait for the user** in
  Studio (Agents > Film Crew) or through the routes below.

## Stages and gates (REST)

`B=${GUAARDVARK_URL:-http://localhost:5000}`, `P` = production id from the tool.

| stage | call | note |
|---|---|---|
| status | `GET $B/api/production/$P` | stage, subjects, shots, errors |
| subjects found by the writer | `GET $B/api/production/$P/subjects` | characters, environments, props |
| cast one subject | `POST $B/api/production/$P/cast/<subject_id>` with `{"action": ...}` | link to an existing Cast Library subject or plan a new one |
| **confirm casting** | `POST $B/api/production/$P/casting/confirm` | user gate: every subject needs a cast plan first |
| storyboard shot image | `GET $B/api/production/$P/storyboard/shot/<shot_id>/image` | review keyframes |
| redo one shot | `POST $B/api/production/$P/storyboard/shot/<shot_id>/regenerate` | |
| **approve storyboard** | `POST $B/api/production/$P/storyboard/approve` | user gate: releases the renders |
| retry a failed stage | `POST $B/api/production/$P/retry` | |
| list / delete | `GET $B/api/production`, `DELETE $B/api/production/$P` | |

Without the MCP tool: `POST $B/api/production` with `{"name", "script_text", "settings": {"video_model": "..."}}`.

## How to run it well

1. Show the screenwriter's output (scenes, shots, subjects) before touching casting.
2. Consistent faces come from trained LoRAs. If a subject has no Cast entry, offer to create and
   train one (the cast skill) or cast it "as described" and warn identity may drift.
3. State the render cost before the storyboard approval: shots × seconds × model speed.
4. The editor assembles the shots in the Video Editor; the result appears in the media library.
5. Nothing posts or uploads. The film stays on the machine unless the user moves it.
