"""Per-host state file management. Stdlib-only."""
from __future__ import annotations

import json
import os
import socket
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def default_state_path() -> Path:
    """data/dep_reconciler/state-${HOSTNAME}.json — overridable via env."""
    override = os.environ.get("GUAARDVARK_DEP_STATE_FILE")
    if override:
        return Path(override)
    hostname = socket.gethostname() or "unknown"
    # Slug: keep alnums, dashes, dots; replace anything else with '_'
    safe = "".join(c if c.isalnum() or c in "-." else "_" for c in hostname)
    return (Path("data/dep_reconciler") / f"state-{safe}.json").resolve()


@dataclass
class State:
    version: int = 1
    hostname: str = ""
    updated_at: str = ""
    reconcilers: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        return cls(
            version=d.get("version", 1),
            hostname=d.get("hostname", ""),
            updated_at=d.get("updated_at", ""),
            reconcilers=dict(d.get("reconcilers", {})),
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "hostname": self.hostname,
            "updated_at": self.updated_at,
            "reconcilers": self.reconcilers,
        }


def load_state(path: Path) -> State:
    """Read state from `path`. Missing/corrupt → empty State (treated as full drift)."""
    if not path.is_file():
        return State()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return State.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return State()


def save_state(path: Path, state: State) -> None:
    """Atomic write: temp file in same dir, fsync, rename."""
    state.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not state.hostname:
        state.hostname = socket.gethostname() or "unknown"

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), indent=2).encode("utf-8")

    # NamedTemporaryFile in same dir so rename is atomic on the same filesystem.
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # Cleanup the .tmp on any error so we don't leave debris.
        if Path(tmp).exists():
            Path(tmp).unlink(missing_ok=True)
        raise
