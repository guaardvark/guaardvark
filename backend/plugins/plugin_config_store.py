"""Plugin config store — owns the data/plugin_config.json file.

Plugin settings the operator changes in the UI are *per-machine*, exactly
like the runtime state in plugin_state.json. They must not be written into
plugins/<id>/plugin.json, which is tracked in git and forked into customer
projects: a config edit there shows up as a repo diff, collides on pull, and
leaks one machine's setup into everyone else's checkout.

So plugin.json stays the shipped default, and this file is a thin overlay
applied on top of it at load time.

Schema v1:
  {
    "version": 1,
    "overrides": { "<plugin_id>": { "<key>": <value>, ... }, ... },
    "updated_at": "<iso8601>"
  }
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Runtime state lives in plugin_state.json and must never be mirrored here.
FORBIDDEN_KEYS = frozenset(
    {"enabled", "auto_start", "default_enabled", "default_auto_start"}
)


class PluginConfigStore:
    """Owns plugin_config.json. Atomic writes, tolerant reads."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def all_overrides(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._read().get("overrides", {}))

    def get(self, plugin_id: str) -> Dict[str, Any]:
        """Return this plugin's overlay (a copy; never the live dict)."""
        return dict(self._read().get("overrides", {}).get(plugin_id, {}))

    def update(self, plugin_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Merge *updates* into the plugin's overlay and persist.

        Runtime-state keys are dropped rather than rejected: the config dialog
        posts the whole config object back, so they arrive as noise on an
        otherwise legitimate save.
        """
        clean = {k: v for k, v in (updates or {}).items() if k not in FORBIDDEN_KEYS}
        state = self._read()
        overrides = state.setdefault("overrides", {})
        current = overrides.setdefault(plugin_id, {})
        current.update(clean)
        self._write(state)
        return dict(current)

    def clear(self, plugin_id: str) -> None:
        state = self._read()
        state.setdefault("overrides", {}).pop(plugin_id, None)
        self._write(state)

    def _empty(self) -> dict:
        return {"version": SCHEMA_VERSION, "overrides": {}}

    def _read(self) -> dict:
        try:
            if not self.path.exists():
                return self._empty()
            with open(self.path) as f:
                raw = json.load(f) or {}
        except Exception as e:
            logger.warning(f"Could not read plugin config file ({e}); starting fresh")
            return self._empty()
        if not isinstance(raw.get("overrides"), dict):
            raw["overrides"] = {}
        raw["version"] = SCHEMA_VERSION
        return raw

    def _write(self, state: dict) -> None:
        state["version"] = SCHEMA_VERSION
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Write-and-rename so a crash mid-write cannot truncate the file.
            tmp = self.path.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except Exception as e:
            logger.error(f"Failed to write plugin config file: {e}")
