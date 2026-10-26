#!/usr/bin/env python3
"""Undo Guaardvark's own recent code changes when the backend cannot start.

start.sh calls this when the backend fails its import check or never starts
listening. If changes Guaardvark made to its own backend code are waiting
for their first start (backups/self_edits/pending.json, written by
backend/services/self_edit.py), their files are put back from the copies
taken before each change, newest first, and each record says why. Exit 0
when something was put back, so start.sh tries once more; 1 when there was
nothing to undo.

Standard library only: it runs when the backend's own code will not import.

    python3 scripts/self_edit_guard.py restore-pending --reason "the import check failed"
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stores() -> list:
    """Where the backend keeps its records: GUAARDVARK_BACKUP_DIR when set
    (as backend/config.py reads it), and the default backups/ folder."""
    found = []
    base = os.environ.get("GUAARDVARK_BACKUP_DIR")
    for folder in ([Path(base)] if base else []) + [ROOT / "backups"]:
        if (folder / "self_edits") not in found:
            found.append(folder / "self_edits")
    return found


def restore_pending(reason: str) -> int:
    undone = []
    for store in stores():
        undone += _restore_from(store, reason)
    for line in undone:
        print(f"Undid a change Guaardvark made to itself: {line}")
    return 0 if undone else 1


def _restore_from(store: Path, reason: str) -> list:
    pending = store / "pending.json"
    try:
        edits = json.loads(pending.read_text()).get("edits", [])
    except (OSError, ValueError):
        return []
    undone = []
    for edit_id in reversed(edits):
        manifest = store / edit_id / "manifest.json"
        try:
            record = json.loads(manifest.read_text())
        except (OSError, ValueError):
            continue
        if record.get("status") != "applied":
            continue
        if record.get("root") and Path(record["root"]).resolve() != ROOT.resolve():
            continue  # a change to another install sharing this backup folder
        for f in reversed(record.get("files", [])):
            target = ROOT / f["path"]
            if f.get("created"):
                if target.exists():
                    target.unlink()
            elif f.get("before"):
                shutil.copyfile(store / edit_id / f["before"], target)
        record["status"] = "auto_reverted"
        record["auto_reverted_at"] = datetime.now().isoformat(timespec="seconds")
        record["error"] = f"Guaardvark did not start after this change ({reason}), so it was undone."
        tmp = manifest.with_name("manifest.json.tmp")
        tmp.write_text(json.dumps(record, indent=1))
        os.replace(tmp, manifest)
        undone.append(f"{record.get('title') or edit_id} ({', '.join(f['path'] for f in record.get('files', []))})")
    pending.unlink(missing_ok=True)
    return undone


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    restore = sub.add_parser("restore-pending")
    restore.add_argument("--reason", default="the backend did not start")
    args = parser.parse_args(argv)
    if args.command == "restore-pending":
        return restore_pending(args.reason)
    return 1


if __name__ == "__main__":
    sys.exit(main())
