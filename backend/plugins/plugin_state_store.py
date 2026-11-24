"""Plugin state store — owns the data/plugin_state.json file.

The plugin state file is per-machine runtime state that lives alongside
plugin.json (which is the static manifest). This module is the single
source of truth for that file's schema, atomic-write semantics, and
access patterns. PluginManager talks to a PluginStateStore instance;
tests inject their own pointed at a tmp_path and never touch the real
file.

Schema v2:
  {
    "version": 2,
    "user_enabled":        { "<plugin_id>": bool, ... },  # explicit user toggles
    "running":             [ "<plugin_id>", ... ],         # last-known running set
    "breaker_tripped":     { "<plugin_id>": bool, ... },   # circuit breaker — see below
    "start_failure_counts":{ "<plugin_id>": int, ... },    # consecutive start failures
    "updated_at":          "<iso8601>"
  }

The circuit breaker ("breaker_tripped") damps a retry storm: a disposable
plugin that fails to start `threshold` times in a row has its breaker tripped,
which stops auto-restore until the operator explicitly re-enables it (that
resets the breaker). Core pillars are exempt from this upstream in
PluginManager (they never reach record_start_failure).

Migrations on read:
  * pre-v1 {"running": [...]} files are upgraded.
  * v1 used the key "quarantined" for the same concept; it is renamed to
    "breaker_tripped" on read and the old key is dropped on next write.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# Every mutator reads the file, changes one key and writes it back. Request
# threads, the boot restore and a status refresh all do that in one process;
# unserialized, the later write drops the earlier one's change.
_MUTATE_LOCK = threading.RLock()


class PluginStateStore:
    """Owns plugin_state.json. Atomic writes, schema migration on read."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def snapshot(self) -> dict:
        """Read the full state, normalized to current schema. Always returns
        a dict with version / user_enabled / running keys present."""
        return self._read()

    def get_user_enabled(self) -> Dict[str, bool]:
        """Return a copy of the user_enabled overlay."""
        return dict(self._read().get("user_enabled", {}))

    def set_user_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Atomically set user_enabled[plugin_id]; preserves running set."""
        with _MUTATE_LOCK:
            state = self._read()
            state.setdefault("user_enabled", {})[plugin_id] = bool(enabled)
            self._write(state)

    def get_running(self) -> List[str]:
        """Return a copy of the last-known running set (always deduplicated, order preserved)."""
        raw = self._read().get("running", []) or []
        # Dedup while preserving first-seen order (defensive against prior races / bad writes).
        seen = set()
        deduped = []
        for pid in raw:
            if pid not in seen:
                seen.add(pid)
                deduped.append(pid)
        return deduped

    def set_running(self, plugin_ids: List[str]) -> None:
        """Atomically set the running list (deduplicated); preserves user_enabled overlay."""
        with _MUTATE_LOCK:
            state = self._read()
            # Always store a clean unique list.
            seen = set()
            clean = []
            for pid in (plugin_ids or []):
                if pid not in seen:
                    seen.add(pid)
                    clean.append(pid)
            state["running"] = clean
            self._write(state)

    def _empty(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "user_enabled": {},
            "running": [],
            "breaker_tripped": {},
            "start_failure_counts": {},
        }

    def _read(self) -> dict:
        try:
            if not self.path.exists():
                return self._empty()
            with open(self.path) as f:
                raw = json.load(f) or {}
        except Exception as e:
            logger.warning(f"Could not read plugin state file ({e}); starting fresh")
            return self._empty()

        # v1→v2 migration: "quarantined" was renamed to "breaker_tripped".
        # Merge any legacy entries in (breaker_tripped wins on conflict) and drop
        # the old key so the next write persists the v2 shape.
        if "quarantined" in raw:
            legacy = dict(raw.pop("quarantined") or {})
            merged = {**legacy, **dict(raw.get("breaker_tripped") or {})}
            raw["breaker_tripped"] = merged

        # Legacy upgrade: pre-v1 file had only {"running": [...]}.
        if "version" not in raw:
            return {
                "version": SCHEMA_VERSION,
                "user_enabled": {},
                "running": list(raw.get("running", [])),
                "breaker_tripped": dict(raw.get("breaker_tripped", {})),
                "start_failure_counts": dict(raw.get("start_failure_counts", {})),
            }

        raw.setdefault("user_enabled", {})
        raw.setdefault("running", [])
        raw.setdefault("breaker_tripped", {})
        raw.setdefault("start_failure_counts", {})
        # Defensive dedup of running list (can accumulate from races across restarts / concurrent toggles / LAN clients).
        if raw.get("running"):
            seen = set()
            dedup = []
            for p in raw["running"]:
                if p not in seen:
                    seen.add(p)
                    dedup.append(p)
            raw["running"] = dedup
        return raw

    def _write(self, state: dict) -> None:
        state = dict(state)
        state["version"] = SCHEMA_VERSION
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # One temp file per writer: two writers sharing a name can
            # interleave and leave unparseable JSON, which reads as empty state.
            tmp_path = self.path.with_suffix(
                f"{self.path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception as e:
            logger.warning(f"Could not save plugin state: {e}")

    def is_breaker_tripped(self, plugin_id: str) -> bool:
        """True if the plugin's circuit breaker is tripped (auto-restore suspended
        after repeated start failures)."""
        return bool(self._read().get("breaker_tripped", {}).get(plugin_id))

    def set_breaker_tripped(self, plugin_id: str, value: bool) -> None:
        with _MUTATE_LOCK:
            state = self._read()
            state.setdefault("breaker_tripped", {})[plugin_id] = bool(value)
            self._write(state)

    def record_start_failure(self, plugin_id: str, threshold: int = 4) -> None:
        """Count one consecutive start failure; trip the breaker at the threshold."""
        with _MUTATE_LOCK:
            state = self._read()
            counts = state.setdefault("start_failure_counts", {})
            c = int(counts.get(plugin_id, 0)) + 1
            counts[plugin_id] = c
            if c >= threshold:
                state.setdefault("breaker_tripped", {})[plugin_id] = True
            self._write(state)

    def reset_plugin_health_counters(self, plugin_id: str) -> None:
        """Reset a plugin's health state: drop its start-failure count AND reset the
        circuit breaker. A plugin that recovers must clear its breaker too — this
        used to pop only the counter and leave the sticky flag set, locking the
        plugin out forever (the bug that stranded comfyui)."""
        with _MUTATE_LOCK:
            state = self._read()
            if "start_failure_counts" in state:
                state["start_failure_counts"].pop(plugin_id, None)
            if "breaker_tripped" in state:
                state["breaker_tripped"].pop(plugin_id, None)
            self._write(state)
