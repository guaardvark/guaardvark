#!/usr/bin/env python3
"""
One-shot migration: strip per-machine runtime state from every
plugins/<id>/plugin.json, seeding the user_enabled overlay in
data/plugin_state.json so behavior on this machine is preserved.

Idempotent — running it twice is a no-op.

Usage:
    python scripts/migrate_plugin_manifests.py            # rewrite in place
    python scripts/migrate_plugin_manifests.py --dry-run  # show diff only
"""

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = REPO_ROOT / "plugins"
STATE_FILE = REPO_ROOT / "data" / "plugin_state.json"

LEGACY_RUNTIME_KEYS = ("enabled", "auto_start")
RENAMES = {"enabled": "default_enabled", "auto_start": "default_auto_start"}


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"version": 1, "user_enabled": {}, "running": []}
    with STATE_FILE.open() as f:
        raw = json.load(f) or {}
    if "version" not in raw:
        raw = {"version": 1, "user_enabled": {}, "running": list(raw.get("running", []))}
    raw.setdefault("user_enabled", {})
    raw.setdefault("running", [])
    return raw


def save_state(state: dict) -> None:
    state["version"] = 1
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2)
    tmp.replace(STATE_FILE)


def migrate_one(plugin_json: Path, state: dict, dry_run: bool) -> bool:
    """Returns True if the manifest changed."""
    with plugin_json.open() as f:
        manifest = json.load(f)
    config = manifest.get("config", {})

    plugin_id = manifest.get("id") or plugin_json.parent.name

    changed = False
    for legacy in LEGACY_RUNTIME_KEYS:
        if legacy not in config:
            continue
        value = bool(config[legacy])
        new_key = RENAMES[legacy]

        # Seed user_enabled overlay (only the 'enabled' key — auto_start stays
        # as a manifest default; it's not currently a per-machine toggle).
        if legacy == "enabled":
            if plugin_id not in state["user_enabled"]:
                state["user_enabled"][plugin_id] = value

        # Rename in-place if the new key isn't already there.
        if new_key not in config:
            config[new_key] = value
        del config[legacy]
        changed = True

    if not changed:
        return False

    if dry_run:
        print(f"[DRY-RUN] would rewrite {plugin_json.relative_to(REPO_ROOT)}")
        return True

    tmp = plugin_json.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    tmp.replace(plugin_json)
    print(f"rewrote {plugin_json.relative_to(REPO_ROOT)}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not PLUGINS_DIR.is_dir():
        print(f"ERROR: {PLUGINS_DIR} not found", file=sys.stderr)
        return 2

    state = load_state()
    any_changed = False

    for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
            continue
        plugin_json = plugin_dir / "plugin.json"
        if not plugin_json.exists():
            continue
        if migrate_one(plugin_json, state, dry_run=args.dry_run):
            any_changed = True

    if any_changed and not args.dry_run:
        save_state(state)
        print(f"updated {STATE_FILE.relative_to(REPO_ROOT)}")
    elif not any_changed:
        print("nothing to migrate (already done)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
