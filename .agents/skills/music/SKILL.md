---
name: music
description: >-
  Generate full songs with vocals or instrumentals (ACE-Step) and sound effects or
  ambience (Stable Audio Open) on the user's GPU through Guaardvark's Audio Foundry. Use
  when the user asks for a song, a beat, a jingle, background music, or a sound effect,
  made locally with no subscription.
---

# Music and sound effects with Guaardvark

Read `setup` first; needs the `audio_foundry` plugin. `B=${GUAARDVARK_URL:-http://localhost:5000}`.
ACE-Step takes ~10 GB VRAM; the orchestrator evicts other models while it runs.

## A song

Over MCP, call `generate_music` (`style`, optional `lyrics`, `seconds` up to 240,
`instrumental`, `seed`, `model`); it returns a `job_id` for `get_generation_status`, which reports
the file and a download link when the song is done. Without MCP, use REST:

```bash
curl -s -X POST $B/api/audio-foundry/generate/music -H 'Content-Type: application/json' -d '{
  "style_prompt": "indie folk, fingerpicked acoustic guitar, warm male vocal, 96 bpm, intimate",
  "lyrics": "[verse]\nLines here\n[chorus]\nHook here",
  "negative_prompt": "distorted, muddy, synth",
  "duration_s": 90, "instrumental_only": false,
  "output_format": "wav", "seed": 1234, "async": true
}'
```
- `model`: omit (or `"ace-step"`) for ACE-Step v1; `"ace-step-1.5"` for ACE-Step 1.5 (MIT,
  48 kHz, turbo model plus a planner) once it is installed in Audio Studio → Manage models.
  Not installed → `400` with `needs_install`; nothing is downloaded.
- For v1, `style_prompt` uses ACE-Step's tag vocabulary: genre, instruments, mood, tempo, vocal type.
  Vague words ("professional", "futuristic") drift to the model's prior; be concrete.
  `POST $B/api/audio-foundry/rewrite-music-prompt` turns plain English into that tag form.
  For 1.5, write a sentence. The loaded 1.7B planner expands it into a caption, lyrics, tempo,
  key and meter before the render, and keeps a bpm or key written in the sentence.
  `POST $B/api/audio-foundry/plan-music` with the same body as generate returns that arrangement
  and does not render. `negative_prompt` is accepted but neither ACE-Step model applies it (the result says
  `negative_prompt_applied: false`).
- `lyrics` with `[verse]` / `[chorus]` / `[bridge]` markers; omit or set `instrumental_only: true`.
- `duration_s` up to 240. Always send `"async": true` for anything over a few seconds.
- Response `202 {"job_id"}` → poll `GET $B/api/audio-foundry/jobs/<job_id>`; done when `status`
  is `done`, with `path`, `document_id`, `duration_s`, `seed`. Reuse the seed to reproduce a take.

## A sound effect or ambience

```bash
curl -s -X POST $B/api/audio-foundry/generate/fx -H 'Content-Type: application/json' -d '{
  "prompt": "rain on a tin roof, distant thunder, no music", "duration_s": 20, "output_format": "wav", "async": true
}'
```
`duration_s` up to 47. Same job polling.

## Then

- A finished song can go straight into the music skill-video as the `song`.
- Jobs: `GET $B/api/audio-foundry/jobs` lists, `DELETE` clears finished ones.
- Attribution comes back in the job result; keep it with the file if the user publishes.
