# Guaardvark for coding agents

**Read [`AGENT_GUIDE.md`](AGENT_GUIDE.md) before acting on any Guaardvark request.** It is the
operating contract: preflight, what to announce before spending the GPU, where to stop for the
user, how the tools behave, prompting rules per model, and what never to do. This file is the
router only.

Guaardvark is a self-hosted AI studio on the user's own GPU: image, video, music-video and
Film Crew generation, voice and music, upscaling, LoRA training, model onboarding from
Hugging Face, coding swarms, local knowledge, code intelligence and supervised outreach.
Every skill under `.agents/skills/` names the exact MCP tool or REST route it uses; do not
invent an endpoint.

| The user wants | Read | Runs through |
|---|---|---|
| what can this box do, or anything is unclear | `.agents/skills/setup/SKILL.md` | health, plugins, models, `inspect_gpu` |
| an image, an edit, a batch, a consistent character | `image` | MCP `generate_image`, `edit_image`; REST `/api/batch-image` |
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

Setup, one line each:

- Claude Code: `/plugin marketplace add guaardvark/guaardvark` then `/plugin install guaardvark@guaardvark`,
  or from the checkout `python -m backend.mcp install --skills`.
- Cursor, Codex, OpenClaw, Gemini CLI, Zed: `python -m backend.mcp install` from the checkout
  writes the MCP entry; the skills are read from `.agents/skills/` in this repository.
- Backend URL: `GUAARDVARK_URL`, default `http://localhost:5000` (macOS: 5055).

Changing Guaardvark itself: `CONTRIBUTING.md`, and section 12 of the guide.
