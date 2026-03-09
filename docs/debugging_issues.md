# Debugging Issues Log

## Resolved

### 1. Progress Footer — Celery-to-Flask SocketIO Bridge (2026-02-26)
**Symptom:** Progress footer bar did NOT show indexing progress after document uploads.
**Root Cause:** Indexing runs in Celery workers (separate process). `_emit_event()` called `_get_socketio()` which returned `None` in Celery context. Progress events were silently dropped.
**Fix:**
- `backend/utils/unified_progress_system.py`: Added Redis pub/sub fallback in `_emit_event()` — when SocketIO is `None`, publishes to `guaardvark:progress` Redis channel.
- `backend/app.py`: Added daemon thread that subscribes to `guaardvark:progress` and re-emits via SocketIO to both process-specific room and `global_progress`.

### 2. Progress Footer — Stale "Indexing document 38" Message (2026-02-26)
**Symptom:** Footer showed "INDEXING: Starting indexing... (Indexing document 38)" persistently even after uploading new files.
**Root Cause:** 508 stale `.progress_jobs/` metadata files on disk (32 with status "start" that never completed). The file-polling thread re-emitted ALL of them on every restart, flooding the frontend with zombie jobs.
**Fix:**
- `backend/app.py` (`poll_celery_progress`): Added `TERMINAL_STATUSES` filter to skip `complete`/`error`/`cancelled`/`end` jobs. Added `STALE_THRESHOLD` (10 min) to skip old metadata files.
- Cleaned up 501 stale `.progress_jobs/` directories older than 10 minutes.

### 3. RAG Performance Stats — All Zeros (2026-02-26)
**Symptom:** Settings > Data > RAG Performance showed Cached Indexes: 0, Memory: 0 B.
**Root Cause:** `/api/rag-debug/system-health` read from `UnifiedIndexManager.cached_indexes` (always empty) and `Flask INDEX_CACHE` (process-local, only populated by Celery workers, not the Flask process serving the API).
**Fix:**
- `backend/api/rag_debug_api.py` (`get_system_health`): Changed to scan filesystem for persisted index files (`docstore.json`, `default__vector_store.json`) in `INDEX_ROOT` and per-project subdirectories. Reports actual file sizes as memory usage. Now shows Cached Indexes: 1, Memory: 216.81 MB.

### 4. start.sh — Preflight Log Path (2026-02-26)
**Symptom:** `./start.sh: line 1199: /preflight.log: Permission denied`
**Root Cause:** Line 1199 used `$LOG_DIR` which is undefined. The correct variable is `$GUAARDVARK_LOG_DIR` (defined on line 106).
**Fix:** Changed `"$LOG_DIR/preflight.log"` to `"$GUAARDVARK_LOG_DIR/preflight.log"` on line 1199.

### 5. Backend Log Level — WARNING Hides INFO Messages (2026-02-26)
**Symptom:** `logs/backend.log` only showed WARNING+ messages. Redis relay startup confirmations and progress emissions were invisible.
**Root Cause:** No `BACKEND_LOG_LEVEL` set in `.env`, so `backend/app.py` defaulted to `logging.WARNING`.
**Fix:** Added `BACKEND_LOG_LEVEL=info` to `.env`.

### 6. Indexing Jobs Stuck at "start" Status (2026-02-26)
**Symptom:** 32 out of 508 historical indexing jobs had status "start" and never progressed to "complete" or "error".
**Root Cause:** Celery worker crashed or was killed during indexing. The in-memory `_timeout_stuck_process()` timer (threading.Timer) died with the worker process. File cleanup in `_cleanup_process()` was disabled. No mechanism existed to detect and clean up stale files on disk.
**Fix:** Modified the polling thread in `backend/app.py` (`poll_celery_progress`) to actively mark stale non-terminal jobs as "error" instead of silently skipping them. When a metadata file is older than 10 minutes and its status is not in `TERMINAL_STATUSES`, the poller writes `status: "error"`, `is_complete: true`, and a timeout message into the file. This permanently closes zombie jobs so they don't accumulate.

### 8. Progress Footer Not Showing Active Indexing — SocketIO Void in Celery (2026-02-26)
**Symptom:** While documents were actively indexing (confirmed by GPU activity in nvitop), the progress footer showed nothing.
**Root Cause (3 interconnected issues):**
1. **SocketIO emit goes into void in Celery worker**: `create_app()` initializes SocketIO on the progress system in the Celery worker, so `_emit_event()` found `socketio is not None` and tried direct SocketIO emit. But this SocketIO instance has NO connected clients — events vanished. The Redis pub/sub fallback (Fix #1) only fired when `socketio is None`, which never happened.
2. **Premature timeout kills progress**: Initial timeout was 10 min, active timeout was 5 min. Indexing takes 14-23 minutes per document. Jobs were marked "error" before completion.
3. **Massive queue backlog**: 290 pending tasks in indexing queue with solo pool concurrency=1.
**Fix:**
- `backend/utils/unified_progress_system.py` (`_emit_event`): Changed from "Redis as fallback when SocketIO is None" to "ALWAYS publish to Redis first, then also emit via SocketIO". Ensures events reach the Flask relay thread regardless of execution context.
- `backend/utils/unified_progress_system.py`: Increased initial timeout from 600s to 2400s (40 min) and active process timeout from 300s to 2400s (40 min).
- `backend/app.py`: Increased `STALE_THRESHOLD` from 600 to 2700 (45 min).
**Verified:** Redis probe events and real indexing events (`indexing_a6988489`, `indexing_28eb0fcb`) confirmed flowing to frontend. Footer shows "INDEXING: Starting indexing... (Indexing document 135)".

### 7. Cache Hit Rate Always 0% (2026-02-26)
**Symptom:** RAG Performance shows Cache Hit Rate: 0%.
**Root Cause:** `UnifiedIndexManager.access_stats` counters were never incremented because `indexing_service.py` uses Flask's `INDEX_CACHE` directly rather than going through `UnifiedIndexManager`. The access_stats tracking was disconnected from actual usage.
**Fix:** Added counter increments in `backend/services/indexing_service.py` `get_or_create_index()`:
- Cache hit (key found in `INDEX_CACHE`): increments `total_loads` + `cache_hits`
- Cache miss (loaded from disk, stored in cache): increments `total_loads` + `cache_misses` + `index_creates`
- Cache miss (no Flask context): increments `total_loads` + `cache_misses`

All counters update the global `UnifiedIndexManager` singleton which `rag_debug_api.py` already reads via `get_cache_stats()`.

## Open / Known Issues
