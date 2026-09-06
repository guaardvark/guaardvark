# Guaardvark Audio — How It Works & Using Your Own Audio

This guide explains how Guaardvark handles audio, where generated audio is saved,
and how to use your own audio files in a video render.

## 0. Setup & model download

Audio generation is provided by the **AudioFoundry plugin**
(`plugins/audio_foundry`, port **8206**). Its runtime config lives in
`plugins/audio_foundry/config.yaml`.

### Audio models

| Purpose | Model | Approx. size |
|---------|-------|--------------|
| Voice (TTS, primary) | `resemble-ai/chatterbox` | ~500 MB |
| Voice (TTS, fallback) | `hexgrad/Kokoro-82M` | ~80 MB |
| Music | `ACE-Step/ACE-Step-v1-3.5B` | 3.5B params (runs in a separate `venv-music` daemon) |
| FX | `stable-audio-open-1.0` | ~1.5 GB |

### Are the models already on this PC?

**No — they download on first use.** The HuggingFace cache
(`~/.cache/huggingface/hub/`) only holds the embedding and image models by
default; the audio models above are pulled the first time you generate a
voiceover or music track. That first call needs internet and takes a while.

### Pre-downloading the audio models

To avoid a stall on the first render, download the weights ahead of time. The
models are pulled into the HuggingFace cache (`~/.cache/huggingface/hub/`).

Using the `hf` CLI (or `huggingface-cli` on older versions) from the backend
venv. **Gated repos need `HF_TOKEN` set** (see below):

```bash
# Voice (primary) — chatterbox (repo is ResembleAI/chatterbox, capital R/A)
backend/venv/bin/hf download ResembleAI/chatterbox

# Voice (fallback) — Kokoro-82M
backend/venv/bin/hf download hexgrad/Kokoro-82M

# Music — ACE-Step (3.5B)
backend/venv/bin/hf download ACE-Step/ACE-Step-v1-3.5B

# FX — stable-audio-open-1.0 (GATED — see below)
backend/venv/bin/hf download stabilityai/stable-audio-open-1.0
```

> **Note:** ACE-Step runs in a **separate `venv-music` daemon** (its pinned
> `transformers`/`accelerate` conflict with the main venv). Its weights still
> land in the shared HF cache, but the daemon must be running to use it — the
> first music request spawns it and downloads the model.

### Gated repos (stable-audio) & the stability agreement

`stabilityai/stable-audio-open-1.0` is a **gated** repo. To download it you must:
1. Open `https://hf.co/stabilityai/stable-audio-open-1.0` and click
   **Agree and access repository** with the **same account** as your `HF_TOKEN`.
2. Set `HF_TOKEN` in `.env` (Guaardvark loads it into the backend env).
3. Download with the token exported:
   ```bash
   HF_TOKEN=<your-token> backend/venv/bin/hf download stabilityai/stable-audio-open-1.0
   ```

If the download says *"Access denied. This repository requires approval"*, the
`HF_TOKEN` account hasn't been granted access yet — either you agreed on a
different account, or the acceptance hasn't propagated (wait a few minutes).

### GPU support (CUDA / MPS)

The audio backends now support **both NVIDIA CUDA and Apple Silicon MPS** (the
`_pick_device()` helper in each backend picks CUDA > MPS > cpu).

- **Voice (chatterbox/kokoro):** works on CUDA and MPS.
- **Music (ACE-Step):** works on CUDA and MPS (tested — generates a valid WAV).
- **FX (stable-audio):** works on CUDA and MPS. On MPS the pipeline scheduler is
  swapped to `EDMDPMSolverMultistepScheduler` to avoid a `torchsde` recursion
  bug (see `ISSUES.md`).

To run music/FX on a CUDA machine instead, point `AUDIO_FOUNDRY_URL` at it (see
"Running AudioFoundry on a different machine").

Alternatively, trigger the download by starting the plugin and issuing a short
TTS / music request. After the first download the pipeline runs fully offline.

### Verifying the download

```bash
ls ~/.cache/huggingface/hub/ | grep -iE "chatterbox|kokoro|ace-step|stable-audio"
```

Each model appears as a `models--<org>--<name>` directory once downloaded.

### Gemma 4 is NOT used for audio

Gemma 4 is **not** part of the audio stack. It is used elsewhere in Guaardvark
(swarm coding agents via `ollama/gemma4:e4b`, and the LTX-2.5 video text
encoder), but audio generation uses **chatterbox/kokoro** (voice) and
**ACE-Step** (music).

### Using an already-running whisper.cpp server for STT

By default Guaardvark's voice STT uses its own `whisper-cli` binary (built via
**Settings > Voice**) or the installed `faster-whisper`. If you already run a
**whisper.cpp HTTP server** (e.g. your LiveKit/pi-omni stack on port 5800), you
can route Guaardvark's `/api/voice/speech-to-text` through it instead — no
whisper.cpp build needed.

Set in `.env`:

```env
GUAARDVARK_USE_WHISPER_SERVER=1
GUAARDVARK_WHISPER_SERVER_URL=http://127.0.0.1:5800   # optional; default 5800
```

When enabled, the `speech-to-text` endpoint saves the uploaded audio to a temp
WAV and POSTs it to the whisper-server's `/inference` endpoint, returning the
transcribed text. This mirrors the `GUAARDVARK_ZIMAGE_USE_COMFYUI=1` opt-in
pattern. The realtime `/api/voice/stream` voice-chat path is also routed to the
server when the env var is set.

`./start.sh` and `./stop.sh` also manage the whisper-server when the env var is
set: `start.sh` launches it (if not already running) and `stop.sh` kills it.
The binary/model paths are overridable:

```env
GUAARDVARK_WHISPER_SERVER_BIN=~/GitHub/whisper.cpp/build/bin/whisper-server
GUAARDVARK_WHISPER_SERVER_MODEL=~/GitHub/whisper.cpp/models/ggml-base.bin
GUAARDVARK_WHISPER_SERVER_PORT=5800
```

## 1. The AudioFoundry plugin

All audio generation (voiceover TTS + background music) is handled by the
**AudioFoundry plugin** (`plugins/audio_foundry`, port **8206**).

- **Voice (TTS):** dialogue text → spoken voiceover via `/generate/voice`.
- **Music:** a mood descriptor → an original instrumental track via `/generate/music`.
- **Effects:** additional audio effects via `/generate/fx`.

If the plugin is down, audio is **silently skipped** — renders still complete but
come out **video-only (silent)** rather than failing.

### Running AudioFoundry on a different machine (remote)

AudioFoundry is **remote-capable**, mirroring the ComfyUI plugin. The client
(`backend/services/swarm/clients.py`) fetches generated files over HTTP via the
plugin's `/view` endpoint (like ComfyUI's `/view`), so it never needs the
plugin's local filesystem path.

To point Guaardvark at a remote AudioFoundry, set in `.env`:

```env
AUDIO_FOUNDRY_URL=http://<remote-host>:8206
```

Then restart the backend. The plugin must be running on that host (start it with
`plugins/audio_foundry/scripts/start.sh` there). The `/view` endpoint only
serves files under the plugin's output dir (path-traversal guarded).

## 2. Where generated audio is saved

Every generated file is written to:

```
data/uploads/Audio/
```

and registered as a **Document** row, so it appears in the **DocumentsPage**.
The output format defaults to `wav` (configurable in
`plugins/audio_foundry/config.yaml` → `output.default_format`).

## 3. How Film Crew renders with audio

The Film Crew **Editor** agent mixes two audio layers into the final MP4:

1. **Voiceover (dialogue → TTS)**
   - Each shot's `dialogue_text` is turned into a spoken VO.
   - The voice comes from **casting**: the shot's speaking character's `voice_id`.
   - If no voice is assigned, it falls back to the **default voice** ("let the
     backend choose").
   - Shots with no dialogue get no VO.

2. **Background music (score)**
   - Generated from the shot/scene's `scene_mood` (a free-text mood like
     `"calm"`, `"tense"`, `"melancholic"`) as a `style_prompt`.
   - `instrumental_only: true` (no vocals).
   - Generated **per scene**, falling back to one track for the whole production.

3. **Mixing (`ffmpeg.concat_with_audio`)**
   - All video clips are concatenated into one silent video.
   - Each VO is delayed to its shot's start offset.
   - Music is added at **35% volume**, trimmed to the total duration.
   - Everything is mixed (`amix`) and muxed into the final MP4.

### Lip-sync (optional, currently inert)

`GUAARDVARK_FILM_CREW_LIPSYNC` (default `false`) is a **forward-looking flag**
for lip-syncing each shot's clip to its voiceover. The editor has the hook
(`LipsyncGenerator` protocol + a check in `editor.py`), but **no lipsync engine
is wired in yet** — `run_editor` creates the Editor without a `lipsync`
generator, so setting the flag to `true` has no effect today. To use it, a
lipsync engine (e.g. Wav2Lip, Video-Retalking, MuseTalk) would need to be
implemented and passed to the Editor.

### Voice vs. Music — how each is chosen

| | Selection mechanism | Source |
|---|---|---|
| **Voice** | `voice_id` string (pick from TTS voices) | character's `voice_id`, fallback `default` |
| **Music** | `style_prompt` mood text (generate a new track) | shot's `scene_mood` |

In short: **voice = pick an existing voice by id; music = generate a brand-new
track from a mood description.** There is no music "id" — each scene gets a
unique synthesized score based on its mood.

## 4. Using your own audio files

There are two different video features, and they differ:

### A. Music Video (`/music-video`) — built-in support for your own song
- Upload your own song as a **Document**, then create a music video with that
  `song_document_id`.
- The video is analyzed and cut to **your song**.
- This is the intended "use my own audio" path.

### B. Film Crew (sequential pipeline) — no built-in option
- Film Crew **always generates** music (from `scene_mood`) and VO (from TTS).
- There is no field to point it at your own music/VO file.

**To use your own audio in a Film Crew render**, choose one of:

1. **Post-process (simplest, no code):** render the film, then replace the audio
   track with your own file using ffmpeg:
   ```bash
   # Replace the audio track entirely
   ffmpeg -i final.mp4 -i your_music.wav \
     -map 0:v -map 1:a -c:v copy -c:a aac -shortest output.mp4

   # Mix your VO on top of the existing audio
   ffmpeg -i final.mp4 -i your_vo.wav \
     -filter_complex "[1:a]adelay=2000|2000[vo];[0:a][vo]amix=inputs=2:duration=longest[aout]" \
     -map 0:v -map "[aout]" -c:v copy -c:a aac output.mp4
   ```

2. **Add a code feature:** extend the Film Crew editor to accept a **custom music
   track path** (e.g., a `music_file` field or config) so it uses your file
   instead of generating one. This is a small, clean change to
   `backend/services/swarm/agents/editor.py` + `backend/tasks/production_swarm_tasks.py`.

## 5. Checking audio status

- **Plugin up?** `curl -s http://127.0.0.1:8206/health` → `{"status":"ok",...}`
- **Generated files?** `ls data/uploads/Audio/`
- **Character voices?** Each character's `voice_id` is set in casting/subject
  settings. If `None`, the default voice is used.
