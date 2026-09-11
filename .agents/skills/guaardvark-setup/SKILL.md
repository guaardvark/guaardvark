---
name: guaardvark-setup
description: >-
  Connect this agent to a running Guaardvark (self-hosted AI studio) and check what it can
  do right now. Use first when the user mentions Guaardvark, asks to generate
  images/video/music/voice locally, run a Film Crew, train a LoRA, launch a swarm, or when
  a guaardvark MCP tool is missing or a call fails with "not reachable" / "plugin
  offline".
---

# Guaardvark: connect and check

Guaardvark runs on the user's own machine. Everything below is local; nothing leaves the box.

## Where it is

- Backend URL: `${GUAARDVARK_URL:-http://localhost:5000}`. The macOS default port is **5055**
  (AirPlay owns 5000). The web UI is on port 5173 in dev, or the same port as the backend in Docker.
- Health: !`curl -sf ${GUAARDVARK_URL:-http://localhost:5000}/api/health || curl -sf http://localhost:5055/api/health || echo "backend not reachable on 5000 or 5055"`
- Plugins: !`curl -sf ${GUAARDVARK_URL:-http://localhost:5000}/api/plugins/status || echo "plugin status unavailable"`

If the backend is not reachable, tell the user to start it from the Guaardvark checkout
(`./start.sh`, or `docker compose up`), then retry. Do not try to start it yourself.

## Two ways to drive it

1. **MCP tools** (preferred when present): the `guaardvark` MCP server exposes chat, RAG, memory,
   code intelligence, file processing, web fetch, image/video/animation/music-video/film-crew
   generation, `get_generation_status` for any queued batch, outreach drafting and GPU/log
   inspection. Generation tools queue by default over MCP and return a batch id. In Claude Code they appear as
   `mcp__guaardvark__<tool>`. Install once from the checkout:
   ```bash
   python -m backend.mcp install      # writes the server entry into Claude Code, Cursor, Claude Desktop, Codex, Zed, Gemini
   python -m backend.mcp doctor       # self-test + stale-config scan
   python -m backend.mcp list-tools   # what is exposed right now
   ```
   Restart the client after installing so it re-reads its MCP config.
2. **REST** for everything the MCP policy does not expose (voice, music, upscaling, batches,
   cast/LoRA training, swarm launch, interconnector, plugins). Use `curl` against the backend URL.
   Responses are wrapped as `{"success": bool, "data": {...}, "message": str}` on most routes; a few
   older routes return the bare object. Read `data` when it is present.

## Before generating anything

- GPU services are plugins. Check `GET /api/plugins/status`; a generation route answers 503 when
  its plugin is not running. Start one with `POST /api/plugins/<id>/start` where `<id>` is
  `comfyui` (image + video), `audio_foundry` (voice, music, FX), `upscaling`, `lora_trainer`,
  `swarm`. Only one heavy model owns the GPU at a time; the orchestrator evicts Ollama for video
  and vice versa, so a first call after a switch is slow. `inspect_gpu` (MCP) shows who holds it.
- Installed models: `GET /api/batch-video/models` and `GET /api/batch-image/models` list every
  registry entry with capabilities; check `installed` before naming a model. Nothing downloads
  without an explicit Install, so if a model is missing say so and offer
  `POST /api/batch-video/models/download {"model_id": "..."}` (or the image route with
  `{"model_path": "..."}`).
- Outputs land under `data/outputs/` in the checkout and are also served read-only over MCP as
  `guaardvark://outputs/...` resources.

## Skills in this pack

| Skill | Use for |
|---|---|
| guaardvark-image | one image, edits, cast characters, batch image runs |
| guaardvark-video | one clip, image-to-video, first/last frame, batch video runs, MiniMax H3 with sound |
| guaardvark-music-video | a song in, a beat-cut music video out, with the approval gate |
| guaardvark-film-crew | screenplay to finished short: writer, casting, storyboards, render, edit |
| guaardvark-voice | narration, TTS, consent-gated voice cloning |
| guaardvark-music | full songs with lyrics, instrumentals, sound effects |
| guaardvark-upscale | 2x to 8K upscaling of images and video |
| guaardvark-cast | Cast Library subjects and LoRA training for consistent characters |
| guaardvark-models | add any Hugging Face model or LoRA from a URL |
| guaardvark-swarm | parallel coding agents in git worktrees from a plan file |
| guaardvark-knowledge | the user's indexed documents, memory, web fetch |
| guaardvark-code | code search, repository map, self-improvement status |
| guaardvark-outreach | supervised social drafts (never posts) |
| guaardvark-ops | GPU, logs, Celery, plugins, Interconnector sync, autoresearch, infographics |
