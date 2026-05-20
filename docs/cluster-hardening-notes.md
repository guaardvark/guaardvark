# Cluster (Interconnector) hardening — 2026-05-20

Pass done while the Android/LiteRT work is parked (see
`android-litert-exploration.md`). Goal: make the PC fleet robust, since the
riskiest Android edge-node assumptions (liveness under flaky nodes, routing
correctness) live in this code.

## Fixed in this pass

**P1 — routing is now liveness-aware; offline transitions re-broadcast.**
Root cause: `RoutingTableBuilder`/`FleetMap` had no concept of `online`, and
`compute_fleet_hash` hashed only hardware profiles — so a node dropping didn't
change the hash, and `recompute_and_broadcast(reason="heartbeat_timeout")`
silently no-op'd. A dead node stayed listed as `primary` in every cached table.
- `FleetMap` gained `_online` + `set_online`/`is_online` (`fleet_map.py`).
- `compute_fleet_hash(profiles, online=None)` folds liveness into the hash
  (back-compat: old 1-arg form still works) (`cluster_routing.py`).
- `RoutingTableBuilder.build` splits candidates by liveness: only online nodes
  are primary/parallel-worker eligible; offline nodes drop to the tail of the
  fallback chain.
- The sweeper now calls `FleetMap.set_online(...)` on both transitions
  (`cluster_heartbeat_sweeper.py`), so the next recompute sees the change and
  the hash differs → real rebuild + broadcast.

**P2 — model-aware chat routing wired into the HTTP proxy path.**
`ProxyTargetResolver.resolve` ignored `model_hint`; the model-aware
`route_for_chat` was only used by the Socket.IO path. Added
`ProxyTargetResolver._select_route`, which for `llm_chat` + a model hint prefers
nodes with the model resident, falling back to the static table
(`cluster_proxy.py`).

**P3 — heartbeat restores `online=True` immediately.**
`node_heartbeat` set `status="active"` but not `online`, so a recovered node
stayed unroutable until the next sweeper pass. Now sets `online=True`, updates
FleetMap liveness + address, and triggers `recompute_and_broadcast(
reason="node_recovered")` when the node was previously down
(`interconnector_api.py`).

**Smaller hardening:**
- `_get_target` reads host/port/online from FleetMap first (DB fallback),
  removing a per-candidate DB query on the proxy hot path. FleetMap address is
  populated on register, heartbeat, and startup seed
  (`cluster_proxy.py`, `interconnector_api.py`, `app.py`).
- Builder GPU gating broadened: AMD/Intel GPUs that advertise sufficient VRAM
  are now eligible (NVIDIA path unchanged for back-compat). Paired with a
  best-effort `rocm-smi` VRAM probe in `hardware_detector.py`.
- Cluster chat bridge self-evicts from the registry when the remote node drops
  (`reconnection=False` previously leaked dead clients)
  (`cluster_socketio_bridge.py`).

Tests: `backend/tests/test_cluster_hardening.py` (13 pure-logic tests). Full
runnable cluster suite (no DB/celery needed): 94 passing.

## Deferred / needs verification

- **Inter-node auth (intentionally left as-is).** The codebase uses the node's
  UUID as a bearer token (`_is_valid_node_api_key` validates against `node_id`;
  the forwarder sends `node_id` as `X-Guaardvark-API-Key`). It's internally
  consistent and a v4 UUID is ~122 bits of entropy, so it's a defensible bearer
  token — but HTTP **workload** endpoints (`/api/chat/unified` etc.) don't
  validate it on inbound proxied requests (only the Socket.IO `connect` gate
  does). Hardening it (shared secret + inbound enforcement) is a coordinated,
  security-sensitive change across forwarder + validator + key distribution that
  can't be integration-tested in the current sandbox; do it deliberately with a
  real multi-node setup. **Most relevant before exposing nodes beyond a trusted
  LAN — and before any Android edge node over cellular.**
- **AMD/Intel VRAM detection unverified.** The `rocm-smi --showmeminfo vram
  --json` parse is best-effort and untested on real AMD hardware; if it returns
  None the node stays CPU-only (safe). Intel VRAM is still `None` (no reliable
  probe). Verify on actual AMD/Intel boxes.
- **Worker-side peer reachability.** The proxy assumes a target is reachable at
  `host:port`. A worker forwarding to a peer queries its *local* DB for the
  peer's address; whether non-master nodes reliably hold peer rows wasn't fully
  traced. This is also the core blocker for an Android edge node behind
  NAT/cellular (it isn't addressable) — both point to the same future need: an
  outbound/pull-style worker protocol rather than master-pushed forwarding.
