# Guaardvark agent skills

Skill files that let an AI coding agent (Claude Code, Cursor, Codex, OpenClaw, Gemini CLI, Zed)
drive a running Guaardvark: local image, video, music-video, Film Crew, voice, music, upscaling,
Cast/LoRA training, model onboarding, swarms, knowledge, code intelligence, outreach and operations.

Format: [Agent Skills](https://agentskills.io) — one folder per skill with a `SKILL.md`
(YAML front matter `name` + `description`, then instructions). Claude Code reads the same files
from `~/.claude/skills/` or `.claude/skills/`.

## Install

From the Guaardvark checkout:

```bash
python -m backend.mcp install            # MCP server entry into every detected agent client
python -m backend.mcp install --skills   # link these skills into ~/.claude/skills (Claude Code, all projects)
```

Cursor, Codex and OpenClaw read `.agents/skills/` in the project you open; open the Guaardvark
checkout, or copy this folder into your own project.

Set `GUAARDVARK_URL` when the backend is not on `http://localhost:5000` (macOS default: 5055).

## Skills

Start with `guaardvark-setup`; it lists the rest and checks the backend, plugins and models.
Every skill states the MCP tool or REST route it uses; nothing here invents an endpoint.
