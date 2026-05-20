# Android + LiteRT-LM exploration notes

Status: **parked.** Decision (2026-05-20) — harden/debug the existing PC cluster
(Interconnector) code first; revisit Android afterward. Strengthening the PC
fleet directly de-risks the Android edge-node path (option 3 below).

## What LiteRT-LM gives us on Android

Google's on-device LLM runtime (engine behind MediaPipe LLM Inference), Kotlin API.
Runs fully on-device, no server/network.

- Local chat/completion: load a `.litertlm` model (Gemma3-1B-IT etc. on LiteRT HF
  community), sync or streaming inference via `Engine` -> `Conversation`.
- HW acceleration: CPU, GPU (OpenCL), or NPU backends; can split modalities
  (`visionBackend=GPU`, `audioBackend=CPU`).
- Multimodal: text + image + audio in one prompt.
- Tool/function calling: `@Tool`-annotated Kotlin fns, auto or manual.
- Streaming via Kotlin `Flow`, plus a callback API.
- Gradle: `com.google.ai.edge.litertlm:litertlm-android:latest.release`.
- Caveat: `engine.initialize()` ~10s (background thread); models are small
  (1-4B class) -> "good-enough offline assistant," not a server-model replacement.

## Two pursued directions

### Option 1 - Companion app with on-device fallback brain
AgentBrain's three tiers map ~1:1 onto a phone/server split.

- Entry point: `AgentBrain.process(...)` at `backend/services/agent_brain.py:142`;
  tier decision tree at lines 188-306. Tier signals are pure regex
  (`CONVERSATIONAL_PASSTHROUGH` 99-104, `DELIBERATION_SIGNALS` 89-96, vision 107-111)
  -> cheap to port to Kotlin.
- Split: Tier 1 Reflex (<100ms, regex, on-phone) / Tier 2 Instinct (1 LLM call,
  on-phone LiteRT, fall back to server) / Tier 3 Deliberation (ReACT loop,
  server only).
- Server endpoints: `POST /api/chat/unified` (`backend/api/unified_chat_api.py`),
  `POST /api/agent/chat` (forces tier 3), `GET /api/brain/health`,
  `/telemetry`, `POST /api/brain/refresh` (`backend/api/brain_api.py`).
- Friction: responses stream over **Socket.IO** not plain HTTP (need a Socket.IO
  client or a REST/SSE shim); reflexes + prompts are built server-side at startup
  (`brain_state.py`) so phone needs a synced snapshot or a fetch endpoint.

### Option 3 - Phone as edge node in the Interconnector fleet
Master/client, hardware-aware routing. Joining is plain REST (lighter than chat).

- Code: `backend/api/interconnector_api.py` (register/heartbeat/sync),
  `backend/services/cluster_routing.py` (`RoutingTableBuilder`),
  `backend/services/cluster_proxy.py` (`WorkloadClassifier`),
  `backend/services/hardware_detector.py` (`hardware.json` profile),
  `backend/services/fleet_map.py` + `backend/tasks/cluster_heartbeat_sweeper.py`
  (liveness), `backend/models.py:1340` (`InterconnectorNode`).
- A node must: (1) register `POST /api/interconnector/nodes/register` w/ HW profile
  + `X-API-Key`; (2) heartbeat `POST .../nodes/<id>/heartbeat` every 15s;
  (3) fetch `GET /api/cluster/routing-table` on boot / fleet_hash change;
  (4) expose `GET /api/node/live-state`.
- Routing gates on hardware (`WorkloadRoute`: `required_services`, `min_vram_mb`,
  `cpu_acceptable` - `cluster_routing.py:84`). Phone profiles as aarch64 / tiny VRAM
  so it's correctly refused heavy workloads; its only real lane is small
  `cpu_acceptable` `llm_chat` == the LiteRT tier-2 inference from option 1.
- Friction: proxy assumes a node is reachable at `host:port`; a phone behind
  NAT/cellular usually isn't addressable -> needs an outbound/pull-only worker
  protocol (a protocol addition, not a free fit). `hardware_detector.py` scans
  `/proc`/`nvidia-smi` (absent on Android) -> need a Kotlin profiler emitting the
  same `hardware.json` shape.

## How 1 and 3 combine
Same LiteRT engine, same small-model constraint. Option 1 = phone serves its own
user offline. Option 3 = phone offers spare capacity to the fleet. Build order:
1 first (standalone, offline value, no fleet dep), then 3 as an add-on once the
outbound-worker protocol exists.

## Why we're hardening the cluster first
The riskiest assumptions for Android edge nodes (NAT/addressability, routing
correctness under flaky nodes, heartbeat/liveness accuracy) all live in the
existing PC cluster code. Making that robust de-risks the Android path and
improves the multi-PC product today. See follow-up cluster findings.
