"""Plugin state store — owns the data/plugin_state.json file.

The plugin state file is per-machine runtime state that lives alongside
plugin.json (which is the static manifest). This module is the single
source of truth for that file's schema, atomic-write semantics, and
access patterns. PluginManager talks to a PluginStateStore instance;
tests inject their own pointed at a tmp_path and never touch the real
file.

Schema v1:
  {
    "version": 1,
    "user_enabled": { "<plugin_id>": bool, ... },  # explicit user toggles
    "running":      [ "<plugin_id>", ... ],         # last-known running set
    "updated_at":   "<iso8601>"
  }

Legacy {"running": [...]} files (pre-v1) are auto-upgraded on read.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


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
        state = self._read()
        state.setdefault("user_enabled", {})[plugin_id] = bool(enabled)
        self._write(state)

    def get_running(self) -> List[str]:
        """Return a copy of the last-known running set."""
        return list(self._read().get("running", []))

    def set_running(self, plugin_ids: List[str]) -> None:
        """Atomically set the running list; preserves user_enabled overlay."""
        state = self._read()
        state["running"] = list(plugin_ids)
        self._write(state)

    def _read(self) -> dict:
        try:
            if not self.path.exists():
                return {"version": SCHEMA_VERSION, "user_enabled": {}, "running": []}
            with open(self.path) as f:
                raw = json.load(f) or {}
        except Exception as e:
            logger.warning(f"Could not read plugin state file ({e}); starting fresh")
            return {"version": SCHEMA_VERSION, "user_enabled": {}, "running": []}

        # Legacy upgrade: pre-v1 file had only {"running": [...]}.
        if "version" not in raw:
            return {
                "version": SCHEMA_VERSION,
                "user_enabled": {},
                "running": list(raw.get("running", [])),
            }

        raw.setdefault("user_enabled", {})
        raw.setdefault("running", [])
        return raw

    def _write(self, state: dict) -> None:
        state = dict(state)
        state["version"] = SCHEMA_VERSION
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception as e:
            logger.warning(f"Could not save plugin state: {e}")
