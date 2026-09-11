---
name: guaardvark-swarm
description: >-
  Launch and watch Guaardvark's Swarm Orchestrator: parallel coding agents, each in its
  own git worktree, working a markdown plan and merging back deterministically. Use when
  the user wants several agents on one codebase at once, says "swarm this", or asks about
  a running swarm, worktrees, or merge status.
---

# Swarm with Guaardvark

Read `guaardvark-setup` first; needs the `swarm` plugin (`POST /api/plugins/swarm/start`).
`B=${GUAARDVARK_URL:-http://localhost:5000}`. Status is also the MCP tool `swarm_status`.

## 1. Write the plan file

A markdown file in the target repo. Each `##` heading is one task. Structured fields are optional;
the parser infers file scope from paths in the prose when they are absent:

```markdown
# Plan: split the settings page

## Extract the GPU panel
- files: frontend/src/pages/SettingsPage.jsx, frontend/src/components/settings/GpuPanel.jsx
- backend: claude
Move the GPU section into its own component with the same props.

## Add a test for the panel
- files: frontend/src/__tests__/GpuPanel.test.jsx
- depends_on: Extract the GPU panel
```
`backend` / `assign to` picks the agent backend (Claude Code, or a local Cline/OpenClaw agent on
Ollama in Flight Mode). Tasks that touch the same files should depend on each other; the parser
warns about conflicts.

## 2. Launch

```bash
curl -s -X POST $B/api/swarm/launch -H 'Content-Type: application/json' -d '{
  "plan_path": "/abs/path/repo/docs/plan.md", "repo_path": "/abs/path/repo",
  "max_agents": 4, "auto_merge": false, "flight_mode": false, "dry_run": false,
  "acknowledge_dirty_tree": false
}'
```
- A dirty working tree is refused unless `acknowledge_dirty_tree: true`; tell the user what is
  uncommitted first.
- `self_code: true` targets Guaardvark's own checkout and is only allowed on the configured root.
- `dry_run: true` parses the plan and reports tasks and conflicts without starting agents.

## 3. Watch, merge, clean up

| call | |
|---|---|
| `GET $B/api/swarm/status` and `GET $B/api/swarm/status/<swarm_id>` | tasks, states, worktrees |
| `GET $B/api/swarm/<swarm_id>/logs/<task_id>?lines=100` | one agent's log |
| `GET $B/api/swarm/<swarm_id>/diff/<task_id>` | its diff before merge |
| `POST $B/api/swarm/<swarm_id>/bus/broadcast` | message every agent |
| `POST $B/api/swarm/merge {"swarm_id": "...", "repo_path": "..."}` | merge finished branches in dependency order |
| `POST $B/api/swarm/cancel` | stop |
| `POST $B/api/swarm/cleanup {"swarm_id": "...", "delete_branches": false}` | remove worktrees |
| `GET $B/api/swarm/templates` | saved plan templates |

## Rules

- Review diffs before `merge` unless the user asked for `auto_merge`.
- Keep `max_agents` at or below the machine's capacity; each Claude agent is a separate session and
  each local agent competes for the GPU with generation jobs.
- Report per task: state, files touched, whether it merged. Never say "done" on a swarm that still
  has running or failed tasks.
