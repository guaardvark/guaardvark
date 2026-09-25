# GPU coordination: who decides what

This document maps the layers that decide who may use the GPU and when a GPU
service (Ollama, ComfyUI, Audio Foundry, the in-process pipelines) is started,
stopped, loaded or evicted. It traces four real flows through those layers and
ends with a findings list. It describes the code as it stands; it changes no
behaviour.

Line numbers are for `main` at `994d303f`. Everything below was established by
reading the code. None of it was run against a GPU, ComfyUI, Ollama or a
database. Where a statement depends on the behaviour of an outside program
(ComfyUI, Ollama, PyTorch), it is marked **assumption**.

All paths are under `backend/` unless they start with `plugins/` or `scripts/`
at the repository root.

## The one thing to know first: there are two processes

The Flask backend and the Celery worker are separate processes (`start.sh`
starts Celery with `--pool=solo --concurrency=1`). Every layer below except the
coordinator's lock file is a per-process singleton. So a Flask render and a
Celery render each see their own gate, their own orchestrator registry and
their own plugin manager.

The only GPU state both processes share:

| Shared state | Where | Written by |
|---|---|---|
| Cross-process GPU lease | `pids/gpu_lock.json` (+ `gpu_lock.tmp`, `gpu_lock.flock`) | `GPUResourceCoordinator` |
| Plugin enabled/running list | `data/plugin_state.json` | `PluginManager` (`PluginStateStore`) |
| Active video-render progress | `<OUTPUT_DIR>/.progress_jobs/*/metadata.json` | the unified progress system; read by `plugin_bridge._count_active_video_render_jobs` |
| The GPU itself, ComfyUI's queue, Ollama's loaded models | outside the backend | everyone |

A session serializes against the other process only when it passes
`gpu_session(cross_process=True)`.

## The layers

### 1. `services/gpu_resource_coordinator.py`: the cross-process lease and the VRAM probe

**Owns:** the single on-disk GPU lock, the physical VRAM probe, and the
low-level Ollama stop/start/unload helpers.

**Entry points:**

- `get_gpu_coordinator()` (879): the per-process singleton.
- `acquire_generic` (505), `renew_generic` (494), `release_generic` (540): the
  lease used by `gpu_session`. The default generic lease is 900 s
  (`GENERIC_LEASE_SECONDS`, 471).
- `acquire_for_video_generation` (333) and `release_video_generation_lock` (411):
  the older video lock. It stops Ollama through systemctl/pkill; the default
  lease is 3600 s (62). `acquire_for_video_generation` has no callers.
  `release_video_generation_lock` is called from `api/gpu_api.py:58` and from
  `batch_video_generator.cancel_all_active` (1856).
- `force_release_lock` (845): called from `api/gpu_api.py:85`.
- `get_available_vram` (561, module wrapper 888): tries MPS on Darwin, then
  pynvml, then nvidia-smi. Nothing is cached except a "no NVIDIA GPU" flag.
- `unload_ollama_models` (750), `_stop_ollama` (241), `_start_ollama` (288).
- `get_gpu_status` (167).

**State:**

- On disk: `pids/gpu_lock.json` holds one owner label, the PID, `acquired_at`
  and `lease_expires_at`. Times are naive `datetime.now()`.
- Cross-process mutual exclusion: `fcntl.flock` on `pids/gpu_lock.flock`, taken
  in `_cross_process_critical_section` (474) by the three `*_generic` methods
  only.
- In-process: a `threading.Lock`.

**Stale-lock cleanup:** a lock is removed when its PID is dead
(`os.kill(pid, 0)`) or its lease has expired. That happens in
`_cleanup_stale_lock` (94, once per process at construction), in
`acquire_generic`, and in `get_gpu_status`.

**Interacts with:** `gpu_resource_policy` (the only caller of the generic
lease); `batch_video_generator.cancel_all_active`; `api/gpu_api.py`; and VRAM
readers throughout the code base.

### 2. `services/gpu_resource_policy.py`: `gpu_session`, the single front door

**Owns:** the composed claim "exclusive gate + cross-process lease + reclaim +
fit check + RAM admission + orchestrator booking", and the reclaim helpers.

**Entry points:**

- `gpu_session(kind, op_id, *, on_busy, evict_ollama, free_comfyui, vram_estimate_mb, ram_estimate_gb, require_fit, cross_process, slot_id, lease_seconds, vram_reserve_mb)` (593).
- `adopt_gpu_session()` (453): lets a worker thread run inside a session that
  its parent thread holds. Nested `gpu_session` calls inside an adopted thread
  become pass-throughs.
- Reclaim helpers:
  - `free_comfyui_vram` (57): ComfyUI `/free`.
  - `evict_ollama_models` (80): `keep_alive=0` on every loaded Ollama model.
  - `evict_audio_foundry_backends` (100): Audio Foundry `/evict`.
  - `reclaim_in_process_vram` (140).
  - `reclaim_gpu` (245): combines the above.
- Fit helpers: `fit_verdict` (323, margin 1024 MB, plus a "mostly idle" rule at
  85 % free) and `vram_probe_snapshot` (414).

**What `gpu_session` does, in order** (625-765):

1. **Re-entrancy.** A nested session on the same thread (thread-local `held`)
   yields `True` and does nothing else.
2. **Gate.** `JobOperationGate.gpu_exclusive(kind, op_id, on_busy)`. With
   `on_busy="register"` and a busy gate, it yields `False` and skips steps 3-6
   entirely: no lease, no reclaim, no booking.
3. **Lease** (if `cross_process`). `acquire_generic(slot, lease)`, then a
   heartbeat thread that calls `renew_generic` every `max(60, lease/3)` s. The
   default lease by kind (490) is 4 h for any kind containing "train", 1 h for
   "video", and 900 s otherwise. If another process holds the lock, this raises
   `GpuBusyError`. If the coordinator cannot be imported, the session carries on
   in-process only.
4. **Reclaim.**
   - With `require_fit` and an estimate that does not already fit: `reclaim_gpu`
     (Ollama, ComfyUI `/free`, Audio Foundry, in-process auxiliaries), then
     `_wait_until_fits`.
   - Without `require_fit`: the flagged evictions only.
5. **Fit.** `_raise_unless_fits(fit_verdict(...))`, then GlobalLoadGate RAM
   admission.
6. **Booking.** `_orchestrator_request` → `request_model(slot, estimate, priority=95, hard_fit=False)`.
7. **On clean exit:** `mark_model_loaded(slot)`.
8. **Teardown**, in this order:
   - stop the heartbeat;
   - release the load weight;
   - `_orchestrator_release`, which drops `image_batch:` slots and any slot
     containing "video" and calls `release_model` on everything else;
   - ComfyUI `/free` when the slot id contains "video";
   - release the lease (in a `finally`);
   - the gate's 8 s cooldown.

**State:** only thread-local `held`; everything else is borrowed from the
other layers.

### 3. `services/job_operation_gate.py`: in-process GPU exclusivity

**Owns:** "one GPU-exclusive job at a time in this process".

**Entry points:**

- `get_gate()` (304).
- `gpu_exclusive(kind, native_id, on_busy)` (201). `on_busy` is one of:
  - `raise`: `GpuBusyError`;
  - `wait`: poll up to `wait_timeout`;
  - `register`: run without exclusivity and log DEGRADED.
- `try_claim_gpu_exclusive` (134), `release_gpu_exclusive` (174),
  `register_running` (103), `snapshot` (269).
- `is_cuda_oom`, `classify_render_exception`, `GpuBusyError`,
  `GpuCapacityError`, `GpuOOMError`.

**Rules:**

- `GPU_EXCLUSIVE_KINDS = {TRAINING, VIDEO_RENDER, LORA_TRAIN}` (44). Any other
  kind is only registered.
- 8 s cooldown after a release (`GPU_RELEASE_COOLDOWN_S`, 52).
- The claim is idempotent for the same `(kind, id)`.
- `release_gpu_exclusive` ignores a caller that is not the holder.
- A holder whose thread has died is reaped on the next claim
  (`_reap_dead_holder_locked`, 116). `snapshot()` does not reap.

**State:** in-process only: the holder tuple, the holder thread, the
in-progress sets and the last-release time.

### 4. `services/gpu_memory_orchestrator.py`: the VRAM booking registry

**Owns:** a registry of "slots" (what this process believes is resident and
how big it is), eviction by priority, the route and stage model maps, and
quality tiers.

**Entry points:**

- `get_orchestrator()`.
- Booking:
  - `request_model(slot_id, vram_estimate_mb, priority, model_type, exclusive, hard_fit, vram_reserve_mb)` (≈250-365);
  - `mark_model_loaded`, `begin_use`, `end_use`, `release_model`, `drop_booking`.
- Intent:
  - `prepare_for_route(route)` (461): `ROUTE_MODEL_MAP`, then
    `plugin_bridge.prepare_plugins_for_route`.
  - `prepare_for_stage(context, stage)` (514): `STAGE_MODEL_REQUIREMENTS`
    (120-140).
- Quality tiers: `get_quality_tier`, `set_quality_tier` (the latter adjusts
  Ollama keep_alive).

**Fit rule in `request_model`:**

- Margin: `max(1024 MB, GUAARDVARK_GPU_SAFETY_MARGIN_PCT (10) % of total)`.
- Shortfall: `_evict_until_free`, then `_physical_reclaim_untracked` (774), which
  unloads in-process SD and auxiliaries **and calls `evict_ollama_models()`**.
- Still short after that:
  - with `hard_fit` (default `GUAARDVARK_GPU_HARD_FIT=1`): raise `RuntimeError`,
    unless the card is "mostly free" (85 % rule);
  - otherwise: admit anyway.
- `exclusive=True` evicts every other slot.
- The slot is registered as LOADING.

**Background thread** (every 30 s):

- `_sync_from_hardware` discovers Ollama's loaded models (`/api/ps`) and the
  in-process reranker/auxiliaries. It keeps LOADING slots, pinned slots,
  non-Ollama/SD/reranker slots, and anything younger than 90 s.
- Idle eviction after 300 s. Slots with priority ≥ 90 and `use_count` > 10 are
  exempt (1180-1181).

**Unload dispatch** (`_unload_model`): Ollama over HTTP; in-process SD pipeline;
video via `offline_video_generator.force_clear_gpu_memory` (in-process only);
image-batch booking drop. It never calls ComfyUI `/free` and never stops a
plugin.

**Slot typing:** `_infer_model_type` maps an unknown prefix (for example
`lora_train:`) to `OLLAMA_LLM` (≈1236). Any slot containing `video_render` is
typed `VIDEO_PIPELINE`.

**State:** in-process only (the registry, the lock, the sync thread).

### 5. `services/plugin_bridge.py`: which service a route or stage needs

**Owns:** the maps from "what the user is doing" to "which sidecar must be up",
and the decision to stop the other GPU sidecar when one is needed.

**Maps:**

- `ROUTE_PLUGIN_MAP` (43-66): for example `/chat → ollama`, `/video → comfyui`,
  `/music-video → comfyui, video_editor, ollama`, `/audio → audio_foundry`.
- `STAGE_PLUGIN_REQUIREMENTS` (73-107):
  - `video/generating`;
  - `music-video/{analyzing, storyboard, generating, assembling}`;
  - `film-crew/{…, storyboard_gen, rendering}`;
  - `cast/{planning, generate_samples, regen_sample, train}`.
- `GPU_EXCLUSIVE_PLUGIN_IDS = {"ollama", "comfyui"}` (40).

**Entry points:**

- `ensure_plugins_for_stage(context, stage)` (169): starts each plugin through
  `ensure_plugin_running`, then calls `orchestrator.prepare_for_stage(context, stage)`.
- `prepare_plugins_for_route(route)` (511): called from
  `orchestrator.prepare_for_route`, which is reached from the socket event
  `gpu:intent` (`socketio_events.py:366`) and `POST /api/gpu/intent`
  (`api/gpu_orchestrator_api.py:47`). It runs `_resolve_gpu_conflict` (362),
  which stops the other member of `GPU_EXCLUSIVE_PLUGIN_IDS` if this process
  started it.
- `ensure_plugin_running` (384): deprecated (`DeprecationWarning` at 407-412).
- `_try_start_plugin` (424), `_stop_plugin` (344).
- `_stop_blocked_reason` (308): refuses to stop ComfyUI while a
  `.progress_jobs` video render is active or this process's gate holds a
  render.
- `auto_orchestrator_enabled()` (123): reads `GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR`,
  default on.

**State:** in-process sets of plugins this process started ("claims") and of
plugins the user controls.

### 6. `plugins/plugin_manager.py`: starting and stopping services

**Owns:** the plugin processes: start, stop, restart, enable, disable, health,
and the persisted enabled/running list.

**Entry points:**

- `get_plugin_manager()` (≈1336).
- `start_plugin` (769), `stop_plugin(cancel_video_jobs=True)` (959),
  `restart_plugin` (1076), `enable_plugin` (1093), `disable_plugin` (1151),
  `health_check` (1229).

**PluginOperationGate** (52): serializes start/stop only. It applies a 3 s
per-plugin cooldown, 8 s for GPU plugins, and a 2 s global cooldown (8 s after a
GPU op) (45-48). It has its own `GPU_EXCLUSIVE_PLUGIN_IDS` (49), mirrored from
the frontend constant.

**State:**

- On disk: `data/plugin_state.json` (enabled flags and the running list), with
  no cross-process lock around read-modify-write.
- In-process: the status map and the operation gate.

**Boot routine:** `_init_plugin_status` (289) runs in every process that
constructs the manager. It seeds `user_enabled`, health-checks each plugin,
kills disabled plugins' orphans by port, restores the persisted running list
(behind the breaker), and saves.

### 7. `services/job_registry.py`: display only

**Owns:** adapting the many job tables into one `Job` view for the Jobs page
and `get_generation_status` (`get_job` 514, `adapt_*`). It makes no GPU
decision, holds no lock, and keeps no state of its own.

### Who is called by whom

```
 route intent (socket gpu:intent / POST /api/gpu/intent)
   └─ orchestrator.prepare_for_route ──► plugin_bridge.prepare_plugins_for_route
                                             └─ _resolve_gpu_conflict ─► plugin_manager.stop/start

 job code (Celery task, batch worker, tool)
   ├─ plugin_bridge.ensure_plugins_for_stage ─► ensure_plugin_running (deprecated) ─► plugin_manager.start_plugin
   │                                         └─ orchestrator.prepare_for_stage
   └─ policy.gpu_session
         ├─ gate.gpu_exclusive                              (in-process)
         ├─ coordinator.acquire_generic + heartbeat         (cross-process, opt-in)
         ├─ reclaim: Ollama keep_alive=0, ComfyUI /free, Audio Foundry /evict, in-process
         ├─ fit_verdict + load gate
         └─ orchestrator.request_model(hard_fit=False)      (in-process booking)
              generator code inside the session may also call
              orchestrator.request_model(hard_fit=True) + begin_use / end_use
```

## Four flows

### Flow 1: a chat turn (`POST /api/chat/unified`)

1. `api/unified_chat_api.py:67`. Slash and direct tools are intercepted first
   (274-294). AgentBrain is the default (142-156); the legacy engine runs only
   when `AGENT_BRAIN_ENABLED` is false. The turn runs in a thread (377-379).
   *Layer: none.*
2. `AgentBrain.process` (`unified_chat_api.py:303`) routes the turn:
   - Tier 2, social and vision go to `_instinct`
     (`services/agent_brain.py:421,432,452`), which calls
     `UnifiedChatEngine.chat` (1132-1137).
   - Tier 3 goes to `AgentExecutor` (1240-1246) and LlamaIndex `llm.chat`
     (`services/agent_executor.py:603,1212,1309`).

   *Layer: none. Tier 3 never books the orchestrator.*
3. `UnifiedChatEngine._call_llm_streaming` (`services/unified_chat_engine.py:2377`).
4. **Booking only.** `orchestrator.request_model("ollama:<model>", 8192, priority=90)`
   (4172-4180), with the default `hard_fit`.
   - If the model is short of VRAM, this evicts by priority and can reach
     `_physical_reclaim_untracked`, which evicts **every** Ollama model,
     including the one about to be used.
   - A refusal is swallowed at debug level (4181-4182).
   - The slot stays LOADING until the 30 s sync sees the model in `/api/ps`.

   *Layer: orchestrator. No gate, no session, no lease.*
5. `ollama.chat(..., keep_alive=get_chat_keep_alive())` (4319-4334). The
   default is 15 minutes on GPU (`config.py:474`). Ollama loads the weights
   itself. *Layer: none.*
6. A GPU-heavy tool in the turn (`GPU_HEAVY_TOOLS`, 648-651) first calls
   `evict_ollama_models()` (2649-2652). After an animation or video tool it
   calls `force_clear_gpu_memory` (2992).
7. The model is unloaded by Ollama's keep_alive, by orchestrator idle eviction
   (a busy chat model is exempt), or by any render's `gpu_session(evict_ollama=True)`.

Nothing on this path frees ComfyUI. If ComfyUI is resident, the chat model
competes with it, and an OOM shows up as the "model crashed" message
(2391-2396). Navigating to `/chat` (not a turn) runs `prepare_for_route("/chat")`,
whose plugin half may stop ComfyUI if this process started it.

### Flow 2: a video batch from MCP `generate_video`

1. `tools/image_tools.py:1246`. The MCP server forwards the call into the
   backend process. *Layer: none.*
2. `prepare_video_model` (`services/video_model_registry.py:1653`):
   - Preflight (1662).
   - If ComfyUI is down: `ensure_plugins_for_stage("video","generating")`
     (1674) → `ensure_plugin_running` → `plugin_manager.start_plugin`.
   - Its `prepare_for_stage("video","generating")` returns "skipped": there is
     no `video` context in `STAGE_MODEL_REQUIREMENTS`.
   - Waits for ComfyUI (1681-1684).

   *Layers: plugin_bridge, plugin_manager.*
3. `start_batch_from_prompts` → `_start_batch`
   (`services/batch_video_generator.py:1431`) → `batch_queue.put` (1578). The
   tool returns "queued" unless `wait_for_result` is set.
4. The queue worker runs `_run_batch` (795):
   - `vram_probe_snapshot` (832), read-only.
   - **`gpu_session`** (848-860) with `VIDEO_RENDER`, `on_busy="wait"`,
     `evict_ollama`, `free_comfyui`, `cross_process`, `lease_seconds=3600`,
     `require_fit`, the model's estimate, and `slot_id="video_render:batch_<id>"`.
   - Busy is retried with backoff up to `GUAARDVARK_VIDEO_VRAM_WAIT_S`
     (881-915). A capacity refusal ends the batch (869-880).

   *Layers: policy, gate, coordinator, orchestrator.*
5. Optional cinematic keyframe pre-pass (1238-1272) through
   `render_character_still` or `ComfyUIImageGenerator.generate_image`, with
   ComfyUI `/free` after the stills. Covered by the batch session.
6. The i2v auto-caption (`_caption_image_for_i2v`, 1143 → `VisionAnalyzer().analyze`, 64)
   calls an Ollama vision model **inside** the session, right after the session
   evicted Ollama.
7. Items run in a `ThreadPoolExecutor` (1296), one worker unless
   `GUAARDVARK_BATCH_COMFYUI_PARALLEL` is set. Each item runs under
   `adopt_gpu_session` (1289-1294).
8. The router's `generate_video` (`services/video_generation_router.py:147`)
   opens `_render_session` (175-196), a pass-through here because the item is
   adopted. `get_active_generator` (87-121) may launch ComfyUI itself through
   `start.sh` (206-238), or fall back to the in-process offline generator.
9. `ComfyUIVideoGenerator.generate_video` (`services/comfyui_video_generator.py:1663`):
   - `_ensure_comfyui_reserve_for` (1019-1079): waits for ComfyUI's queue to
     empty, then `plugin_manager.restart_plugin("comfyui", cancel_video_jobs=False)`
     when the model needs a larger `--reserve-vram`.
   - `_vram_preflight` (advisory).
   - `_ensure_vram_for_model` (1081-1140): a **second** booking,
     `request_model("video:comfyui:<item>", hard_fit=True, priority=90)` +
     `begin_use`.
   - `POST /prompt`. ComfyUI loads the weights in its own process.
   - In `finally`: `_release_vram_booking` (1142-1154), which does `end_use` +
     `drop_booking`.
10. Session exit: the booking is dropped, ComfyUI `/free` runs (the slot
    contains "video"), the lease is released, and the gate cooldown starts. The
    router schedules an idle shutdown, but only for a ComfyUI it launched
    itself (428-470).
11. Cancel (`cancel_all_active`, 1845-1872) calls `release_generic` for the
    batch labels and the legacy `release_video_generation_lock`, and releases
    whichever `VIDEO_RENDER` holder the gate currently has.

### Flow 3: the music-video clip render stage (Celery)

1. `music_video.run_clip_generator` (`tasks/music_video_tasks.py:1192`) →
   `run_clip_generator` (613), in the Celery process.
2. `ensure_plugins_for_stage("music-video","generating")` (649) starts ComfyUI
   and calls `prepare_for_stage`. That stage's need is
   `video:pipeline, exclusive=True` (`gpu_memory_orchestrator.py:129-131`), so
   the Celery orchestrator **unloads every LOADED/LOADING slot that does not
   start with `video:`** (528-532), including Ollama models found by its sync.
   This happens **before** any gate or lease is taken.
3. `_generate_one_clip` → **`gpu_session`** (800-810) with `VIDEO_RENDER`,
   `on_busy="raise"`, `evict_ollama`, `free_comfyui`, `require_fit`,
   `vram_mb_for_model` (default 14000), `cross_process`, `lease_seconds=3600`,
   and `slot_id="video_render:mv_<id>_<idx>"`.
   - `GpuBusyError` or `PluginUnavailable` defers the clip: it is
     re-dispatched with `countdown=12` for up to 3 h (651-669).
4. Keyframe still (skipped when a curated storyboard exists):
   - With cast LoRAs: `render_character_still(keep_pipeline=False)` (821-834).
     The Z-Image route runs `offline_image_generator` in-process, with a nested
     pass-through session and an `sd:pipeline` booking.
   - Otherwise: `ComfyUIImageGenerator.generate_image` (849-860).

   Then `free_comfyui_vram()` (861/863), the FLUX→i2v eviction.
5. I2V uses `get_video_generator()` from `comfyui_video_generator` directly
   (912), **not the router**, then `vg.generate_video` (920): the same reserve
   restart, second booking and `/prompt` as Flow 2 step 9, all in the Celery
   process.
6. Session exit as in Flow 2. The ffmpeg fill (939) runs after the session. The
   next clip is tail-called with `countdown=12` (691).

The storyboard stage (`run_storyboard_generator`, 1075) is similar:
`ensure_plugins_for_stage("music-video","storyboard")` (1096), where
`sd:pipeline` is not exclusive, so it only records actions. It then opens
`gpu_session` (1116) **without `cross_process`**. The Flask single-thumbnail
regenerate (`api/music_video_api.py:615`) does the same.

The music-video pipeline's own stage prep (`services/pipeline_service.py:126,135`)
calls `ensure_plugins_for_stage(self.task_namespace, stage)` and
`prepare_for_stage(self.task_namespace, stage)` with `task_namespace="music_video"`
(`services/music_video_service.py:355`). The maps are keyed `"music-video"`, so
those calls find nothing (finding D1).

### Flow 4: image generation from the Studio (batch image)

1. `POST /api/batch-image/generate/prompts` (`api/batch_image_generation_api.py:1451`)
   → `start_batch_from_prompts` → `start_batch_generation`
   (`services/batch_image_generator.py:1173`) → `batch_queue.put` (1239). The
   route does no GPU or plugin prep.
2. `_queue_worker` (1135) → `_run_batch_job` (1246) → `run_batch`.
3. **Only when the offline generator's device is `cuda`**
   (`_batch_uses_cuda_offline_gen`, 560-565):
   - `_batch_resource_estimates` (484-548), with FLUX priced at 12000 MB and a
     discount for an already-resident model.
   - **`gpu_session`** (1550-1566) with `VIDEO_RENDER`, `on_busy="wait"`,
     `evict_ollama`, `free_comfyui`, `cross_process`, `lease_seconds=1800`, the
     VRAM and RAM estimates, `require_fit`, the compositor reserve, and
     `slot_id="image_batch:<id>"`.
   - Busy is retried up to 600 s (1573-1626).

   On any other device the batch body runs with no session (1627-1628).
4. One worker under CUDA (1306), with each image under `adopt_gpu_session`
   (1276-1281). `_generate_single_image` (818) picks one of:
   - cast: `render_character_still`;
   - FLUX/SDXL via ComfyUI: `_generate_with_comfy_flux` → `ComfyUIImageGenerator.generate_image` (718);
   - default: `run_stills_pipeline(hold_gpu=False)` (841) → `offline_image_generator.generate_image`.
5. Offline route (`services/offline_image_generator.py`):
   - Nested `gpu_session` (2322) is a pass-through.
   - `_ensure_vram_for_pipeline` (891-941): `torch.cuda.mem_get_info`,
     `evict_ollama_models`, then `request_model("sd:pipeline", hard_fit=True, priority=85)`.
   - `_load_pipeline` (1456) → `from_pretrained` (1533) and `.to(device)` or CPU
     offload. **This is where weights load.** Then `mark_model_loaded("sd:pipeline")`
     (1714) and `begin_use` (2409).
   - In `finally`: `end_use`. The batch passes `keep_pipeline=True`, so the
     pipeline stays resident.
6. Session exit: `image_batch:<id>` is dropped (also in `_release_batch_booking`,
   459-468). There is no ComfyUI `/free`, because the slot has no "video". The
   lease is released and the cooldown starts. The resident `sd:pipeline` is
   reclaimed later by idle eviction or by another job's reclaim.

## Findings

Findings only; nothing here was changed. Each finding names where it was seen.
**Assumption** marks anything that depends on runtime behaviour this review
could not observe.

### A. Duplicated decisions

- **A1. Three fit checks with different margins.**
  - `gpu_resource_policy.fit_verdict`: 1024 MB, plus the 85 % "mostly idle" rule.
  - `gpu_memory_orchestrator.request_model`: `max(1024, 10 % of total)`, plus
    its own copy of the 85 % rule.
  - `offline_image_generator._ensure_vram_for_pipeline` (891-941).

  A job that passes the session's check can still fall short in the
  orchestrator's booking on a card over 10 GB.
- **A2. Ollama eviction is implemented in several places:**
  `gpu_resource_coordinator.unload_ollama_models` (750);
  `gpu_resource_policy.evict_ollama_models` (80);
  `bark_tts_service` (≈82); `api/gpu_orchestrator_api` (≈132);
  `plugin_manager._unload_all_ollama_models` (1197); the orchestrator's
  `_unload_model`; and the coordinator's systemctl/pkill `_stop_ollama` (241).
- **A3. The orchestrator evicts Ollama when the caller asked it not to.**
  `gpu_session(evict_ollama=False)` still books with `request_model`. On a
  shortfall that reaches `_physical_reclaim_untracked`, which calls
  `evict_ollama_models()` unconditionally (788).
- **A4. `GPU_EXCLUSIVE_PLUGIN_IDS` is defined twice:** `plugin_bridge.py:40`
  and `plugin_manager.py:49` (plus a frontend constant).
- **A5. Route and stage maps are split.** `ROUTE_PLUGIN_MAP` / `ROUTE_MODEL_MAP`
  and `STAGE_PLUGIN_REQUIREMENTS` / `STAGE_MODEL_REQUIREMENTS` must be kept in
  step by hand. They already differ: `video` and `cast` have plugin entries but
  no model entries.
- **A6. Two gates that do not know about each other.** `JobOperationGate`
  (jobs) and `PluginOperationGate` (plugin start/stop) each have an 8 s GPU
  cooldown.
- **A7. Double booking per render.** A video render is booked twice: the
  session's `video_render:*` slot and the generator's `video:comfyui:*` slot.
  An offline image is booked as `image_batch:*` and `sd:pipeline`. Fit
  decisions probe physical VRAM, so this mainly inflates the registry's view
  (snapshots, `_evict_until_free` arithmetic).
- **A8. `prepare_for_stage` runs twice per music-video dispatch:** once
  directly from `pipeline_service` and once through `ensure_plugins_for_stage`
  (`pipeline_service.py:126,135`). Today both are no-ops (D1).

### B. State that can disagree between layers

- **B1. Everything but the lock file is per process.** The Flask gate does not
  see a Celery holder, and each process's orchestrator registry sees only its
  own bookings. `_stop_blocked_reason` partly compensates by reading
  `.progress_jobs` from disk, but a Celery `gpu_session` for a non-video kind
  is invisible to Flask.
- **B2. Every process that touches the plugin manager runs its boot routine**
  (`_init_plugin_status`, 289). That includes a Celery worker the first time it
  calls `ensure_plugins_for_stage`. It restores the persisted running list and
  kills the orphans of plugins whose saved preference is off. A plugin the web
  process started for a job without persisting the preference
  (`persist_user_pref=False`) fits that case. Verified in the code, not
  reproduced.
- **B3. `data/plugin_state.json` is read-modify-written by both processes**
  with no cross-process lock.
- **B4. `plugin_bridge._is_running` changes the status map as a side effect of
  a probe.** `_plugin_pids` is never read.
- **B5. Orchestrator slot types are guessed from the slot-id prefix.**
  `lora_train:*` becomes `OLLAMA_LLM` (≈1236), so an unload of it takes the
  Ollama path.
- **B6. A booking can be LOADING forever.** A session's booking is LOADING for
  the whole job (`mark_model_loaded` runs only after `yield`), and LOADING slots
  are never swept by `_sync_from_hardware`. A booking whose owner raised
  between `request_model` and cleanup in code that does not use `gpu_session`
  (for example `offline_video_generator.py:653-660`, below) stays until the
  process restarts.
- **B7. The gate snapshot can show a dead holder.** `snapshot()` does not reap,
  so the Jobs/GPU UI and `_stop_blocked_reason` can report a holder whose
  thread has exited, until the next claim reaps it.
- **B8. Chat bookings never reach LOADED through the engine.** The chat
  engine's `ollama:<model>` booking is flipped to LOADED only by the 30 s
  hardware sync, not by the engine.
- **B9. `offline_video_generator.py:80` calls `has_gpu` on the coordinator
  instance.** Only a module-level `has_gpu()` exists (893). The call is guarded
  by `hasattr`, so it always falls back to `torch.cuda.is_available()`. It is
  harmless, but it does not do what it reads as.

### C. Paths that bypass a layer

Each item names the GPU work and which layer it skips.

- **C1. Chat FLUX stills.** `run_stills_pipeline` returns into
  `_generate_comfy_flux` for any model containing "flux"
  (`services/stills_pipeline.py:252`) **before** the `hold_gpu` branch (297).
  The chat `ImageGeneratorTool` passes `hold_gpu=True`
  (`tools/image_tools.py:370`), but a FLUX render runs in ComfyUI with no
  session, lease or booking. The only protection is the chat engine's Ollama
  eviction (`unified_chat_engine.py:2649`).
- **C2. Chat cast still through ComfyUI.** `render_character_still` accepts
  `hold_gpu` (`services/character_still_pipeline.py:116`) and never reads it.
  The SDXL/FLUX route queues to ComfyUI (333) with no session of its own. The
  Z-Image route is covered by `offline_image_generator`'s own session.
- **C3. Batch image on a non-CUDA device**
  (`batch_image_generator.py:1627-1628`). FLUX/SDXL prompts in that batch still
  render on ComfyUI's GPU with no session.
- **C4. Sessions without `cross_process`**, which are not serialized against
  the other process:
  - `tasks/music_video_tasks.py:1116` (storyboards);
  - `api/music_video_api.py:615` (single storyboard);
  - the router's `_render_session` (`video_generation_router.py:190`) for
    direct, non-batch calls.

  The router session also uses `on_busy="register"`. When the gate is busy it
  runs DEGRADED, with no lease, reclaim or booking (policy steps 3-6 are
  skipped).
- **C5. Music-video stage prep evicts before claiming.**
  `ensure_plugins_for_stage("music-video","generating")`
  (`music_video_tasks.py:649`) runs the exclusive `prepare_for_stage` eviction
  (Ollama and every non-`video:` slot) before `gpu_session` has taken the gate
  or the lease. If the clip is then deferred as busy, the eviction was for
  nothing. It also evicts a `video_render:*` booking, because only the prefix
  `video:` is kept (528-532). For such a booking, "unload" calls
  `offline_video_generator.force_clear_gpu_memory` and pops the slot. The same
  applies to exclusive route intents `/video`, `/music-video` and `/film-crew`
  (478-490).
- **C6. Ollama vision inside a video session.** `VisionAnalyzer` is called
  inside the batch video session (`batch_video_generator.py:64`), reloading an
  Ollama model the session just evicted.
- **C7. Chat LLM load.** It books the orchestrator only (Flow 1 step 4): no
  gate, no session. A render in progress in either process can find the chat
  model reloaded next to it.
- **C8. Services started outside plugin_manager.**
  `VideoGenerationRouter._start_comfyui` launches ComfyUI through `start.sh`
  (206-238), and the router runs its own idle shutdown. The coordinator's
  `_stop_ollama` / `_start_ollama` use systemctl and pkill. Neither updates
  `PluginManager`'s status.
- **C9. Weights loaded with no booking:**
  - Upscaling plugin (RealESRGAN on CUDA,
    `plugins/upscaling/service/model_manager.py:192,198`). The backend only
    proxies it (`api/upscaling_api.py`).
  - Background removal (ONNX `CUDAExecutionProvider`,
    `services/background_removal.py:66-83`) from `RemoveBackgroundTool`
    (`tools/image_tools.py:1705`), which is not in `GPU_HEAVY_TOOLS`.
  - faster-whisper (`utils/faster_whisper_utils.py:49`, `device="auto"`).
  - The reranker (`utils/reranker.py:53-124`). The orchestrator discovers it
    only in sync.
  - SetFit intent model (`services/intent_service.py:170`). **Assumption:** it
    lands on CUDA under sentence-transformers' default device.
  - Offline SVD video (`offline_video_generator.py:431,437`).
- **C10. The offline CogVideoX path books without ever releasing.** It books
  `video:pipeline` with `exclusive=True` (`offline_video_generator.py:653-660`),
  evicting everything else in the registry, and the file never calls
  `release_model` or `drop_booking`.
- **C11. Audio Foundry books over HTTP.** It books the orchestrator
  (`plugins/audio_foundry/service/dispatcher.py` → `api/gpu_orchestrator_api.py:120`),
  in whichever backend process serves that request, with no gate or lease. It
  is evicted by `gpu_session`'s `/evict`.
- **C12. ComfyUI reserve restart.** `comfyui_video_generator` calls
  `plugin_manager.restart_plugin` directly (1067), inside a render session,
  bypassing `plugin_bridge`. It waits for ComfyUI's queue to be empty first.

### D. Stage wiring that does not match

- **D1. `PipelineService` stage prep never matches.** `task_namespace` is
  `"music_video"` (`music_video_service.py:355`) and `"production"`
  (`production_service.py:57`). The maps are keyed `"music-video"` and
  `"film-crew"`. `pipeline_service.py:126,135` therefore always gets empty
  lists. The Celery tasks that call `ensure_plugins_for_stage("music-video", …)`
  directly are what actually start ComfyUI.
- **D2. The stage path has no GPU-conflict resolution.**
  `ensure_plugins_for_stage` (169-199) never calls `_resolve_gpu_conflict`, so a
  stage start never stops the other GPU sidecar. Only the route path does, and
  only for plugins this process claimed.
- **D3. `prepare_plugins_for_route("/video")` goes through the music-video
  stage.** It ensures via `("music-video","generating")` (612-621), so a visit
  to the plain video page runs the music-video stage's exclusive model prep.

### E. Deprecated `ensure_plugin_running` callers

- **E1. The only live call is inside the bridge itself.**
  `plugin_bridge.py:188`, in `ensure_plugins_for_stage`. Every stage ensure
  therefore emits the `DeprecationWarning` (407-412).
- **E2. Unused imports:** `tasks/music_video_tasks.py:32` and
  `api/music_video_api.py:19` import it and do not call it.

### F. What can leave a lock, lease or booking held

- **F1. Process crash with the lease held.** `pids/gpu_lock.json` stays until
  another process's `acquire_generic` sees the PID is dead, or the lease
  expires: 1 h for video, 4 h for training, 30 min for batch image. The init
  cleanup, `acquire_generic` and `get_gpu_status` all handle the dead-PID case.
  - **Assumption:** after a reboot or a container restart, the recorded PID can
    belong to an unrelated live process. `os.kill(pid, 0)` then reports it
    alive, and the lock holds until the lease expires.
- **F2. Hung job.** A thread that is alive but stuck (a ComfyUI poll with no
  timeout, a blocked HTTP call) keeps the gate forever: the reaper only
  releases dead threads. Its heartbeat keeps renewing the lease, so the other
  process is refused for as long as the process lives.
  - The lease can be cleared by hand with `force_release_lock`
    (`api/gpu_api.py:85`), whatever its owner.
  - The gate can be cleared only by `cancel_all_active`, and only for a
    `VIDEO_RENDER` holder.
  - A hung `TRAINING` or `LORA_TRAIN` holder is cleared only by restarting the
    process.
- **F3. The lease can be removed under a running owner.**
  - `force_release_lock` deletes the lock file whatever its owner.
  - `cancel_all_active` calls `release_generic` for each cancelled batch before
    that batch's thread has unwound.

  In both cases the owner's heartbeat logs "lock no longer ours" and stops, the
  job keeps running without a lease, and the other process can acquire it and
  start beside it. `cancel_all_active` also calls
  `release_video_generation_lock`, which releases only the legacy
  `VIDEO_GENERATION` owner. Nothing takes that lock any more
  (`acquire_for_video_generation` has no callers), so the call is a no-op.
- **F4. A heartbeat failure can let the lease lapse.** Each failed
  `renew_generic` is logged and retried at the next interval. A run of failures
  longer than the remaining lease lets it expire mid-job, and another process
  may then take it.
- **F5. Lock writers that skip the flock.** `_cleanup_stale_lock` (at init),
  `get_gpu_status`, `acquire_for_video_generation`,
  `release_video_generation_lock` and `force_release_lock` read or delete the
  lock file without `_cross_process_critical_section`. One process can delete a
  lock another has just written.
- **F6. `cancel_all_active` releases the gate's current VIDEO_RENDER holder**
  (1845-1872), whether or not that holder is one of the batches being
  cancelled (for example a music-video clip in the same process).
- **F7. Orchestrator bookings leak when the owner dies before its cleanup.**
  - LOADING slots and pinned (`begin_use`) slots are exempt from the sync
    sweep.
  - `video_render:*` and `image_batch:*` bookings are not Ollama/SD/reranker,
    so the sync keeps them too.

  A booking leaked this way stays until the process restarts, because the
  registry is in-process. `gpu_session` itself releases its booking in every
  exit path it controls. The leak cases are bookings made outside it (C10), and
  `lora_train:` slots, which only get `release_model` (B5).
- **F8. ComfyUI keeps rendering after its client dies.** **Assumption:**
  ComfyUI finishes a prompt it has already queued after the backend process
  that queued it has died. The dead-PID sweep then frees the lease while
  ComfyUI is still busy, and the next job's reclaim `/free` and fit check run
  against a card that is still in use.

## Follow-ups this document suggests (not done here)

These belong to later tasks on the list, notably B2, which covers job-driven
service start through `STAGE_PLUGIN_REQUIREMENTS`:

- Key `PipelineService` stage prep with the map's names, or map the namespaces
  (D1).
- Move the exclusive stage eviction after the claim, and keep
  `video_render:*` bookings (C5).
- Pass `cross_process=True` for the storyboard sessions and the router's
  direct path (C4).
- Put the FLUX branch of `run_stills_pipeline` and the ComfyUI route of
  `render_character_still` under a session when `hold_gpu` is set (C1, C2).
- Replace the internal `ensure_plugin_running` call and drop the unused
  imports (E1, E2).
- Take the flock in every lock-file writer (F5). Record a process start time
  next to the PID so PID reuse is detectable (F1).
- One fit function and one margin (A1). One Ollama eviction helper (A2).
