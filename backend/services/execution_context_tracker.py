"""Process-local runtime-liveness tracker.

Phase 1 of the runtime-liveness layer. Records what code ACTUALLY RAN — not
just what imported — into the `symbol_hits` table.

Design constraints (load-bearing — read before editing):
  * `record_hit()` is on a HOT PATH (every Celery task __call__). It must NEVER
    touch the DB and must NEVER raise into the caller. It only mutates an
    in-memory dict under a short-held lock.
  * `flush()` drains the buffer to the DB in one batch (an upsert). It is the
    only method that does DB I/O, and it too swallows all exceptions — a
    runtime-audit failure must never break a worker.
  * The singleton is per-process. Each Celery child (concurrency=1, solo pool,
    max_tasks_per_child=50) gets its own tracker; flush is registered on
    worker_process_shutdown so a recycling child doesn't lose its buffer.
"""

import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# Execution-mode bitfield. Phase 1 only used bit 1 (celery task). Phase 2/3 adds
# the http route mode (bit 2). Future modes (cli = 4, tool = 8, ...) OR into the
# same column.
MODE_CELERY_TASK = 1
MODE_FLASK_ROUTE = 2


class ExecutionContextTracker:
    """Aggregates symbol hits in-memory and flushes them to `symbol_hits`."""

    def __init__(self):
        self._lock = threading.Lock()
        # symbol_id -> {"kind", "display_name", "module", "count", "last_ts", "mode_flags"}
        self._buffer = {}
        self._last_flush_ts = time.monotonic()
        self._flushing = threading.Lock()

    # ------------------------------------------------------------------ hot path
    def record_hit(self, symbol_id, kind, display_name, module, mode_bit):
        """Record one execution of a symbol. Hot path: in-memory only, never raises."""
        try:
            now = time.time()
            with self._lock:
                entry = self._buffer.get(symbol_id)
                if entry is None:
                    self._buffer[symbol_id] = {
                        "kind": kind,
                        "display_name": display_name,
                        "module": module,
                        "count": 1,
                        "last_ts": now,
                        "mode_flags": int(mode_bit),
                    }
                else:
                    entry["count"] += 1
                    entry["last_ts"] = max(entry["last_ts"], now)
                    entry["mode_flags"] |= int(mode_bit)
                    # Keep the latest non-null descriptive fields.
                    if display_name:
                        entry["display_name"] = display_name
                    if module:
                        entry["module"] = module
                    if kind:
                        entry["kind"] = kind
        except Exception:  # noqa: BLE001 - hot path must never raise
            logger.debug("record_hit swallowed exception", exc_info=True)

    def seconds_since_flush(self):
        return time.monotonic() - self._last_flush_ts

    def maybe_flush(self, interval_s=60.0):
        """Flush only if it's been longer than interval_s since the last flush."""
        try:
            if self.seconds_since_flush() >= interval_s:
                self.flush()
        except Exception:  # noqa: BLE001
            logger.debug("maybe_flush swallowed exception", exc_info=True)

    def maybe_flush_async(self, app, interval_s=60.0):
        """Flush on a background thread once interval_s has passed since the
        last flush. For the web process: the beat-driven flush runs in the
        Celery worker and drains only the worker's own tracker, so route hits
        recorded here were never written. Never raises, never blocks the request.
        """
        try:
            if self.seconds_since_flush() < interval_s:
                return
            if not self._flushing.acquire(blocking=False):
                return
            self._last_flush_ts = time.monotonic()

            def run():
                try:
                    with app.app_context():
                        self.flush()
                        try:
                            from backend.models import db
                            db.session.remove()
                        except Exception:
                            pass
                except Exception:  # noqa: BLE001
                    logger.debug("background flush swallowed exception", exc_info=True)
                finally:
                    self._flushing.release()

            threading.Thread(target=run, name="liveness-flush", daemon=True).start()
        except Exception:  # noqa: BLE001
            logger.debug("maybe_flush_async swallowed exception", exc_info=True)

    # ------------------------------------------------------------------ DB path
    def _drain(self):
        """Atomically swap out the buffer and return it. Never raises."""
        with self._lock:
            drained = self._buffer
            self._buffer = {}
            self._last_flush_ts = time.monotonic()
            return drained

    def flush(self):
        """Drain the in-memory buffer to the `symbol_hits` table.

        Uses a Postgres ON CONFLICT upsert (additive on hit_count, OR on
        mode_flags, GREATEST on last_fired_at). Falls back to a per-row
        read-modify-write upsert for non-postgres dialects (sqlite in tests).
        Swallows all exceptions; on DB failure the drained buffer is merged
        back so the counts aren't lost.
        """
        drained = self._drain()
        if not drained:
            return 0

        try:
            from backend.models import db, SymbolHit

            rows = []
            for symbol_id, e in drained.items():
                rows.append({
                    "symbol_id": symbol_id,
                    "symbol_kind": e["kind"],
                    "display_name": e["display_name"],
                    "module": e["module"],
                    "mode_flags": int(e["mode_flags"]),
                    "hit_count": int(e["count"]),
                    "last_fired_at": datetime.fromtimestamp(e["last_ts"]),
                })

            dialect = db.session.bind.dialect.name if db.session.bind is not None else ""

            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = pg_insert(SymbolHit.__table__).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[SymbolHit.__table__.c.symbol_id],
                    set_={
                        "hit_count": SymbolHit.__table__.c.hit_count + stmt.excluded.hit_count,
                        "mode_flags": SymbolHit.__table__.c.mode_flags.op("|")(stmt.excluded.mode_flags),
                        "last_fired_at": db.func.greatest(
                            SymbolHit.__table__.c.last_fired_at, stmt.excluded.last_fired_at
                        ),
                        # Refresh descriptive fields with the latest values.
                        "symbol_kind": stmt.excluded.symbol_kind,
                        "display_name": stmt.excluded.display_name,
                        "module": stmt.excluded.module,
                    },
                )
                db.session.execute(stmt)
            else:
                # Portable read-modify-write fallback (sqlite, etc).
                for r in rows:
                    existing = db.session.get(SymbolHit, r["symbol_id"])
                    if existing is None:
                        db.session.add(SymbolHit(**r))
                    else:
                        existing.hit_count = (existing.hit_count or 0) + r["hit_count"]
                        existing.mode_flags = (existing.mode_flags or 0) | r["mode_flags"]
                        if existing.last_fired_at is None or r["last_fired_at"] > existing.last_fired_at:
                            existing.last_fired_at = r["last_fired_at"]
                        existing.symbol_kind = r["symbol_kind"]
                        existing.display_name = r["display_name"]
                        existing.module = r["module"]

            db.session.commit()
            return len(rows)
        except Exception:  # noqa: BLE001 - a runtime-audit failure must never break a worker
            logger.debug("flush swallowed exception; merging buffer back", exc_info=True)
            try:
                from backend.models import db
                db.session.rollback()
            except Exception:
                pass
            # Merge the drained counts back so they aren't lost.
            try:
                with self._lock:
                    for symbol_id, e in drained.items():
                        cur = self._buffer.get(symbol_id)
                        if cur is None:
                            self._buffer[symbol_id] = e
                        else:
                            cur["count"] += e["count"]
                            cur["mode_flags"] |= e["mode_flags"]
                            cur["last_ts"] = max(cur["last_ts"], e["last_ts"])
            except Exception:
                pass
            return 0


_tracker = None
_tracker_lock = threading.Lock()


def get_tracker():
    """Return the process-local ExecutionContextTracker singleton."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = ExecutionContextTracker()
    return _tracker
