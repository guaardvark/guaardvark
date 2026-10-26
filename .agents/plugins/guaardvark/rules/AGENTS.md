# Guaardvark Operating Contract & Router

Guaardvark is a self-hosted AI studio on your own GPU: image, video, music-video and
Film Crew generation, voice and music, upscaling, LoRA training, model onboarding from
Hugging Face, coding swarms, local knowledge, code intelligence and supervised outreach.

## Skill Router

Every generation, training, sync or swarm request runs through its matching skill:

| The user wants | Skill | Runs through |
|---|---|---|
| what can this box do, or anything is unclear | `setup` | health, plugins, models, `inspect_gpu` |
| an image, an edit, a batch, a consistent character | `image` | MCP `generate_image`, `edit_image`, `remove_background`, `outpaint_image`; REST `/api/batch-image` |
| a clip, image-to-video, a clip with sound, a batch | `video` | MCP `generate_video`; REST `/api/batch-video` |
| a music video from a song | `music-video` | MCP `generate_music_video`; REST `/api/music-video` (approval gate) |
| a short film from a script or logline | `film-crew` | MCP `start_film_crew`; REST `/api/production` (two gates) |
| narration, a spoken line, a cloned voice | `voice` | REST `/api/audio-foundry`, `/api/voice` |
| a song, a beat, a sound effect | `music` | REST `/api/audio-foundry` |
| a bigger or sharper image or video | `upscale` | REST `/api/upscaling` |
| the same face or object across renders, a LoRA | `cast` | REST `/api/cast-library` |
| a model or LoRA from a Hugging Face link | `models` | REST `/models/from-hf`, `/models/user` |
| several agents on one codebase | `swarm` | REST `/api/swarm` |
| an answer from their documents, a memory | `knowledge` | MCP `search_knowledge_base`, `save_memory`, … |
| where something is implemented, a repo map | `code` | MCP `search_codebase`, `get_repository_map`, … |
| a reply drafted for a social thread | `outreach` | MCP `outreach_draft_post` (never posts) |
| GPU, logs, plugins, sync, autoresearch, an infographic | `ops` | MCP `inspect_gpu`, `read_logs`; REST `/api/plugins`, … |
| is a queued render done | any of the above | MCP `get_generation_status` |

Backend URL: `GUAARDVARK_URL`, default `http://localhost:5000` (macOS: 5055).

## Rule Zero: Every job goes through a skill

Every generation, training, sync or swarm request runs through the matching skill and the
tool or route it names. Do not write ad-hoc Python against the diffusion libraries, do not
call ComfyUI's port directly, do not start a service with `systemctl`, do not invent a route.
The skills carry the request fields the handlers actually read and the limits the models
declare; an improvised call skips both and produces the wrong output or a fight for the GPU.

## Mandatory Preflight

Before the first generation in a session, establish four facts:
1. **The backend answers**: `GET ${GUAARDVARK_URL:-http://localhost:5000}/api/health`.
2. **Which plugins are running**: `GET /api/plugins/status` (needs `comfyui`, `audio_foundry`, etc.).
3. **What models are installed**: `GET /api/batch-video/models` and `GET /api/batch-image/models`.
4. **What the hardware allows**: `inspect_gpu` (MCP) shows the card, free memory, and resident plugins.

| Tier | VRAM | What this box can do |
|---|---|---|
| A, no NVIDIA GPU | none | chat, retrieval, voice in and out, coding swarm, video editor, MCP. No image/video/music/upscaling |
| B, entry GPU | 8–12 GB | plus vector retrieval, screen agents, SD/SDXL-class images, upscaling; music at 12 GB. Video refused below 16 GB |
| C, design target | 16 GB | full stack, one heavy job at a time: FLUX-class images, Wan 2.2 / LTX / CogVideoX / MiniMax H3 video, music and voice, Film Crew |
| D, headroom | 20–24 GB+ | concurrency and quality: two resident LLMs, heavier image models by default, full-batch LoRA training |

## Decision Communication Contract

- **Announce before spend**: Before any call that occupies the GPU for more than a few seconds, state in one line: the skill, the model that will run, the size or duration, the step count, and expected wall time.
- **Ask before switching**: Get a yes before changing model family, dropping step count below minimum, changing resolution/duration, dropping audio/narration, or switching from single clip to batch.
- **Never downgrade silently**: If the model is not installed or GPU is too small, say so and offer the substitute; never run a smaller model and present it as the requested one.
- **Escalate blockers in a fixed shape**: What was attempted, exact error text, likely cause, fix needing user, and what you can do meanwhile. Then stop and wait.
