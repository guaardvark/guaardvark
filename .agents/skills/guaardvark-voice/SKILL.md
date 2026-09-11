---
name: guaardvark-voice
description: >-
  Narration and text-to-speech on the user's machine through Guaardvark's Audio Foundry
  (Chatterbox, Kokoro, Piper) and consent-gated voice cloning from a reference clip. Use
  when the user wants a voiceover, narration of a script, a spoken line, or "make it sound
  like this voice".
---

# Voice with Guaardvark

Read `guaardvark-setup` first. Expressive voices need the `audio_foundry` plugin running;
Piper works without it. `B=${GUAARDVARK_URL:-http://localhost:5000}`.

## Which engine

| engine | route | when |
|---|---|---|
| Chatterbox | Audio Foundry `backend: "chatterbox"` | expressive, emotion presets, **cloning** |
| Kokoro | Audio Foundry `backend: "kokoro"` | fast, clean, 10+ built-in voices (`af_heart` default) |
| Piper | `/api/voice/text-to-speech` | offline fallback, no GPU |

`GET $B/api/audio-foundry/voices` lists what is installed. `GET $B/api/voice/voices` lists Piper voices.

## Speak a line or a script

```bash
curl -s -X POST $B/api/audio-foundry/generate/voice -H 'Content-Type: application/json' -d '{
  "text": "The line to speak.",
  "backend": "auto",            # auto | chatterbox | kokoro
  "voice_id": "af_heart",       # Kokoro voice, or omit
  "emotion": "calm",            # Chatterbox preset, or omit
  "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8,   # Chatterbox knobs, optional
  "seed": 7, "output_format": "wav", "async": true
}'
```
- Short text returns the file directly (`path`, `document_id`). With `"async": true` or long
  text you get `202 {"job_id"}`: poll `GET $B/api/audio-foundry/jobs/<job_id>` until `status`
  is `done`; the result has `path` and `document_id`. Cancel: `POST .../jobs/<job_id>/cancel`.
- Multi-section narration with pauses: `POST $B/api/voice/narrate`
  `{"script": "...", "engine": "kokoro", "voice": "...", "pause_between_sections": 0.6, "output_format": "wav"}`.
- Piper only: `POST $B/api/voice/text-to-speech {"text", "voice": "libritts"}` returns `audio_url`.

## Clone a voice (consent-gated)

1. The reference must go through the upload route; that is what records consent. Arbitrary
   file paths are refused with 403.
   ```bash
   curl -s -X POST $B/api/audio-foundry/voice-clips/upload -F file=@/abs/path/ref.wav -F name="Dean sample"
   ```
   The response gives the stored path. `GET $B/api/audio-foundry/voice-clips` lists clips.
2. Generate with `"backend": "chatterbox", "reference_clip_path": "<that path>"`.
3. Before uploading, ask whether the voice belongs to the user or someone who consented. Do not
   clone a public figure or anyone who has not agreed. Refuse politely if unclear.

## Rules

- 10 to 20 seconds of clean speech is enough for a clone; more is not better.
- Say which engine ran (the response reports it); `auto` falls back to Kokoro on a Chatterbox error.
- Audio files are local under `data/outputs/`; they also appear in the Audio library page.
