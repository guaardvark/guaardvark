# Guaardvark for coding agents

Guaardvark is a self-hosted AI studio on the user's own GPU: image, video, music-video and
Film Crew generation, voice and music, upscaling, LoRA training, model onboarding from
Hugging Face, coding swarms, local knowledge, code intelligence and supervised outreach.
This file is for an agent that has this checkout open, or the plugin installed, and is
asked to *use* Guaardvark. Contributor rules for *changing* it are in `CONTRIBUTING.md`.

## First move

Read `.agents/skills/setup/SKILL.md` before acting on any Guaardvark request.
It checks that the backend answers, which plugins are running, and which models are
installed, then hands off to the skill for the job. Every skill names the exact MCP tool
or REST route it uses. Do not invent an endpoint; if a skill does not cover the ask, say so.

## Where to go

| The user wants | Read | Runs through |
|---|---|---|
| an image, an edit, a batch of images, a consistent character | `image` | MCP `generate_image`, `edit_image`; REST `/api/batch-image` |
| a video clip, image-to-video, a clip with its own soundtrack, a batch | `video` | MCP `generate_video`; REST `/api/batch-video` |
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
| GPU state, logs, plugins, sync, autoresearch, an infographic | `ops` | MCP `inspect_gpu`, `read_logs`; REST `/api/plugins`, … |
| a queued render's state | any of the above | MCP `get_generation_status` |

## How the tools behave

- Over MCP, generation tools **queue and return a batch id** in milliseconds; poll
  `get_generation_status` for the file. Pass `wait_for_result: true` only when the user
  wants you to block. A failed call carries the backend's reason: read it and act on it.
- The GPU is exclusive. One heavy model owns it; the orchestrator swaps Ollama out for a
  render and back. A first call after a switch is slow. Say so instead of retrying blindly.
- Music video and Film Crew stop at human gates before GPU spend. State the cost (cuts or
  shots times seconds per clip) and get a clear yes before calling the approve route.
- Voice cloning and LoRA training need consent for the voice or face. Ask; refuse if unclear.

## Rules that hold everywhere

1. Start GPU services through `POST /api/plugins/<id>/start`, never `systemctl`, so the
   orchestrator knows what holds the card.
2. Nothing downloads a model without the user saying so; offer the install route and wait.
3. Everything is local. Do not upload the user's files, prompts or outputs anywhere.
4. Report what actually happened: a queued job is queued, not done; a 503 is a plugin that
   is off, not a broken product. Quote batch ids, file URLs and the model that ran.

## Setup in one line each

- Claude Code: `/plugin marketplace add guaardvark/guaardvark` then `/plugin install guaardvark@guaardvark`,
  or from the checkout `python -m backend.mcp install --skills`.
- Cursor, Codex, OpenClaw, Gemini CLI, Zed: `python -m backend.mcp install` from the checkout
  writes the MCP entry; the skills are read from `.agents/skills/` in this repository.
- Backend URL: `GUAARDVARK_URL`, default `http://localhost:5000` (macOS: 5055).
