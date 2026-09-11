# Guaardvark for coding agents

This repository is a self-hosted AI studio that a coding agent can drive: image, video,
music-video and Film Crew generation, voice and music, upscaling, LoRA training, model
onboarding from Hugging Face, agent swarms, local knowledge, code intelligence and
supervised outreach, all on the user's own machine.

**Before acting on any Guaardvark request, read `.agents/skills/guaardvark-setup/SKILL.md`.**
It checks that the backend is reachable, which plugins are running, and which models are
installed, then routes to the skill for the job. Every skill under `.agents/skills/` names
the exact MCP tool or REST route it uses; do not invent endpoints.

- Claude Code: `python -m backend.mcp install --skills` links the skills and the MCP server.
- Cursor, Codex, OpenClaw, Gemini CLI: `python -m backend.mcp install` writes the MCP server
  entry; the skills are read from `.agents/skills/` in this checkout.
- Backend URL: `GUAARDVARK_URL`, default `http://localhost:5000` (macOS: 5055).

Two rules that hold everywhere: start GPU services through the plugin routes, never with
`systemctl`, so the orchestrator knows what holds the card; and nothing downloads a model
without the user saying so.

Contributor rules for changing this codebase are in `CONTRIBUTING.md`.
