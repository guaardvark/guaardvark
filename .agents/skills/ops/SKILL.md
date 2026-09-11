---
name: ops
description: >-
  Operate a running Guaardvark: GPU and VRAM state, plugin start/stop, logs, Celery tasks,
  the Interconnector sync to other machines, overnight RAG autoresearch, and infographics.
  Use when the user asks what is using the GPU, why a job is stuck, to start or stop
  ComfyUI/Audio Foundry/Swarm, to sync another box, to run autoresearch, or to make an
  infographic.
---

# Operating Guaardvark

`B=${GUAARDVARK_URL:-http://localhost:5000}`. MCP tools: `inspect_gpu`, `read_logs`, `swarm_status`, `self_improvement_status`.

## GPU and plugins

- `inspect_gpu` (MCP): nvidia-smi, the exclusive lock (Ollama vs video), orchestrator slots, running plugins.
- `GET $B/api/plugins/status` and `GET $B/api/plugins/<id>/health`; `GET $B/api/plugins/stats/gpu`.
- `POST $B/api/plugins/<id>/start | stop | restart | enable | disable` for `ollama`, `comfyui`,
  `audio_foundry`, `upscaling`, `lora_trainer`, `swarm`, `vision_pipeline`, `discord`,
  `gpu_embedding`, `video_editor`. Start services through these routes, never with systemctl;
  the orchestrator must know what is resident.
- `GET $B/api/plugins/<id>/logs?lines=200` for a plugin's own log.
- Memory orchestrator: `GET $B/api/gpu/memory/status`, `GET/POST $B/api/gpu/memory/tier`.
  `POST .../evict {"slot_id"}` frees a slot; use only when the user asks to clear the card.

## Logs and tasks

- `read_logs` (MCP) tails a file under `logs/` (backend, celery, plugin logs).
- `GET $B/api/celery/tasks` shows active Celery tasks; `GET $B/api/meta/...` job routes list and
  retry jobs (`POST $B/api/meta/retry_job/<job_id>`, `POST $B/api/meta/cancel_job/<job_id>`).
- A "stuck" job is usually the GPU lock held by another plugin: check `inspect_gpu` before retrying.

## Interconnector (other machines sync from this one)

- `GET $B/api/interconnector/status`, `GET .../nodes`, `GET .../sync/history?limit=20`.
- `POST $B/api/interconnector/sync/manual` with `{"direction": "push", "entities": [...],
  "sync_files": true, "profile_name": "..."}`; `POST .../nodes/<node_id>/test` checks a link.
- Other boxes never `git pull`; they sync through this. A frontend change needs a bundle rebuild
  there afterwards.

## Autoresearch (overnight RAG tuning)

`POST $B/api/autoresearch/start {"mode": "...", "budget_hours": 6}`; `GET .../status`,
`GET .../runs`, `GET .../runs/<run_id>/ledger`; `POST .../stop`. Promotions of a better config are
explicit: `POST .../promotions/<config_id>/activate`, `POST .../promotions/revert`.

## Infographic

```bash
curl -s -X POST $B/api/infographic/generate -H 'Content-Type: application/json' -d '{
  "title": "Three numbers", "scene": "clean flat design, navy and white",
  "callouts": ["42% faster", "0 cloud calls", "1 GPU"], "hashtags": ["#localai"], "footer": "guaardvark.com",
  "style": "flat", "aspect": "1:1", "seed": 3
}'
```
`GET $B/api/infographic/status` for the job; the image lands in outputs.

## Rules

- Restarting a plugin kills its jobs. Check the queues and say so before restarting.
- Never restart the backend or Celery from here; tell the user what to run in the checkout.
