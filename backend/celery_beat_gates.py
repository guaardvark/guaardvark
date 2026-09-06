"""Feature gates for Celery Beat entries.

A scheduled task whose feature is switched off should not be sent at all: not
queued, not logged, not consuming a worker slot to early-return. Beat's
schedule is static, so the gate is checked here, in the scheduler, against the
same settings the feature's UI toggle writes. A toggle therefore takes effect
within GATE_TTL_SECONDS without a restart, in both directions.

Each gate is a name mapped to a predicate; a beat entry opts in through
``celery_app.conf.beat_feature_gates = {"<entry name>": "<gate name>"}``,
declared next to the entry it constrains (see ``gate_beat_entries``).
Beat runs with ``--scheduler=backend.celery_beat_gates:GatedScheduler``.
"""
from __future__ import annotations

import heapq
import json
import logging
import time
from typing import Callable, Dict, Optional

from celery.beat import PersistentScheduler, event_t
from celery.schedules import schedstate

logger = logging.getLogger(__name__)

# How long a gate decision is reused before the setting is read again. Also
# how long beat waits before re-asking about a closed entry.
GATE_TTL_SECONDS = 60.0

_TRUE = ("true", "1", "yes", "on")
_engine = None


def _read_setting(key: str, default: Optional[str] = None, table: str = "settings") -> Optional[str]:
    """Read one row of a key/value settings table without a Flask app context.

    Beat has no app context. ``table`` is one of the two key/value tables the
    toggles live in ("settings" or "system_settings"); it is never user input.
    """
    if table not in ("settings", "system_settings"):
        raise ValueError(f"unexpected settings table {table!r}")
    global _engine
    try:
        from sqlalchemy import create_engine, text

        if _engine is None:
            from backend import config

            _engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
        with _engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT value FROM {table} WHERE key = :key"), {"key": key}
            ).fetchone()
        if row and row[0] is not None:
            return str(row[0])
    except Exception as exc:  # a gate must never take beat down
        logger.warning("beat gate: could not read setting %s: %s", key, exc)
    return default


def _flag(key: str) -> bool:
    return (_read_setting(key, "false") or "false").strip().lower() in _TRUE


def autoresearch_gate() -> bool:
    """Settings → RAG Autoresearch → "Auto-start in nightly window"."""
    return _flag("rag_autoresearch_auto_enabled")


def social_outreach_gate() -> bool:
    """Settings → Outreach master switch (the kill switch)."""
    return _flag("social_outreach_enabled")


def self_improvement_gate() -> bool:
    """Self-improvement analysis loop; mirrors
    self_improvement_service._is_self_improvement_enabled (no row → on)."""
    raw = _read_setting("self_improvement_enabled", None, table="system_settings")
    if raw is None:
        return True
    return raw.strip().lower() in _TRUE


def interconnector_client_gate() -> bool:
    """This node is an enabled Interconnector *client* with a master URL."""
    raw = _read_setting("interconnector_config")
    if not raw:
        return False
    try:
        cfg = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return False
    return bool(
        cfg.get("is_enabled")
        and cfg.get("node_mode") == "client"
        and (cfg.get("master_url") or "").strip()
    )


GATES: Dict[str, Callable[[], bool]] = {
    "autoresearch": autoresearch_gate,
    "social_outreach": social_outreach_gate,
    "self_improvement": self_improvement_gate,
    "interconnector_client": interconnector_client_gate,
}


def gate_beat_entries(celery_app, mapping: Dict[str, str]) -> None:
    """Declare which gate governs which beat entries (entry name → gate name)."""
    unknown = sorted(set(mapping.values()) - set(GATES))
    if unknown:
        raise ValueError(f"unknown beat gates: {unknown}")
    current = dict(celery_app.conf.get("beat_feature_gates") or {})
    current.update(mapping)
    celery_app.conf.beat_feature_gates = current


class GateCache:
    """Per-gate decisions, remembered for GATE_TTL_SECONDS."""

    def __init__(self, gates: Dict[str, Callable[[], bool]] = None, ttl: float = GATE_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._gates = gates if gates is not None else GATES
        self._ttl = ttl
        self._clock = clock
        self._decisions: Dict[str, tuple] = {}

    def is_open(self, gate: str) -> bool:
        now = self._clock()
        cached = self._decisions.get(gate)
        if cached and now - cached[1] < self._ttl:
            return cached[0]
        predicate = self._gates.get(gate)
        if predicate is None:
            logger.warning("beat gate %r is not defined; treating it as open", gate)
            value = True
        else:
            try:
                value = bool(predicate())
            except Exception as exc:
                logger.warning("beat gate %r failed (%s); treating it as closed", gate, exc)
                value = False
        previous = cached[0] if cached else None
        if previous is not None and previous != value:
            logger.info("beat gate %r is now %s", gate, "open" if value else "closed")
        self._decisions[gate] = (value, now)
        return value


class GatedScheduler(PersistentScheduler):
    """PersistentScheduler that skips entries whose feature gate is closed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate_cache = GateCache()
        gates = self.app.conf.get("beat_feature_gates") or {}
        if gates:
            logger.info("beat feature gates: %s", ", ".join(f"{k}→{v}" for k, v in sorted(gates.items())))

    def _closed_gate(self, entry) -> Optional[str]:
        gate = (self.app.conf.get("beat_feature_gates") or {}).get(entry.name)
        if gate and not self.gate_cache.is_open(gate):
            return gate
        return None

    def tick(self, *args, **kwargs):
        # Celery's tick looks only at the top of its heap. Answering "not due"
        # for a closed entry there would leave it on top and starve every
        # entry behind it (observed: beat sent nothing for eleven minutes).
        # So a closed entry is moved GATE_TTL_SECONDS into the future and the
        # tick repeats at once for whatever is next.
        heap = self._heap
        if heap and self.schedules_equal(self.old_schedulers, self.schedule):
            event = heap[0]
            if self._closed_gate(event.entry):
                heapq.heappop(heap)
                heapq.heappush(
                    heap,
                    event_t(self._when(event.entry, GATE_TTL_SECONDS), event.priority, event.entry),
                )
                return 0
        return super().tick(*args, **kwargs)

    def is_due(self, entry):
        # Reached only when the heap was just rebuilt (tick above has not had
        # its turn yet); answer "soon" so the next tick can rotate the entry.
        if self._closed_gate(entry):
            return schedstate(is_due=False, next=1.0)
        return super().is_due(entry)
