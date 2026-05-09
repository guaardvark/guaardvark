#!/usr/bin/env python3
"""Dependency reconciler entry point.

Stdlib-only top imports. Reconciler classes live in scripts/dep_reconciler/
and are imported lazily once the venv is known to be at least usable.

Exit codes:
  0  no drift, or drift fully reconciled
  1  one or more reconcilers failed
  2  fatal config / sync-in-progress / lock timeout
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the scripts/ namespace importable regardless of how we're invoked.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts.dep_reconciler.base import ReconcileResult
from scripts.dep_reconciler.lock import LockTimeoutError, StateLock
from scripts.dep_reconciler.registry import build_active_reconcilers
from scripts.dep_reconciler.state import State, default_state_path, load_state, save_state


def _setup_logging(quiet: bool) -> logging.Logger:
    level = logging.WARNING if quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="[reconciler] %(levelname)-7s %(message)s",
        stream=sys.stdout,
    )
    return logging.getLogger("dep_reconciler")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Guaardvark dependency reconciler")
    p.add_argument("--dry-run", action="store_true", help="report drift, don't install")
    p.add_argument("--only", default="", help="comma-separated reconciler ids to run")
    p.add_argument("--force", action="store_true", help="re-run all active reconcilers")
    p.add_argument("--quiet", action="store_true", help="warnings/errors only")
    p.add_argument("--state-file", default="", help="override state file path")
    return p.parse_args(argv)


def _entries_match(stored: dict, current_hash: str, current_extra: dict) -> bool:
    if stored.get("manifest_hash") != current_hash:
        return False
    return stored.get("extra", {}) == current_extra


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    log = _setup_logging(args.quiet)

    if os.environ.get("GUAARDVARK_DEP_RECONCILER") == "disabled":
        log.info("kill switch set; skipping reconciliation")
        return 0

    repo = Path.cwd()

    # Sync-in-progress sentinel: refuse to run.
    sentinel = repo / "data" / "dep_reconciler" / ".sync_in_progress"
    if sentinel.exists():
        log.error("sync in progress; refusing to reconcile (retry on next boot)")
        return 2

    state_path = Path(args.state_file) if args.state_file else default_state_path()
    lock_path = state_path.with_suffix(".lock")
    log_path = repo / "logs" / "dep_reconciler.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    only = {s.strip() for s in args.only.split(",") if s.strip()}

    try:
        with StateLock(lock_path).acquire(timeout=30.0):
            return _run(repo, state_path, log_path, only, args.dry_run, args.force, log)
    except LockTimeoutError as e:
        log.error(str(e))
        return 2


def _run(
    repo: Path,
    state_path: Path,
    log_path: Path,
    only: set[str],
    dry_run: bool,
    force: bool,
    log: logging.Logger,
) -> int:
    state = load_state(state_path)
    reconcilers = build_active_reconcilers(repo)
    if only:
        reconcilers = [r for r in reconcilers if r.id in only]
    active_ids = {r.id for r in reconcilers if hasattr(r, "id") and r.id}

    # Orphan pruning: drop state entries for reconcilers that are no longer active.
    # plugin:* entries get matched against the current PluginBundle members.
    plugin_bundle = next((r for r in reconcilers if r.id == "plugin_bundle"), None)
    member_ids = set(getattr(plugin_bundle, "members", []) or [])
    stale = []
    for sid in list(state.reconcilers.keys()):
        if sid in active_ids:
            continue
        if sid.startswith("plugin:") and sid.split(":", 1)[1] in member_ids:
            continue
        stale.append(sid)
    for sid in stale:
        log.info("pruning orphaned state entry: %s", sid)
        del state.reconcilers[sid]

    # Detection pass.
    dirty: list = []
    for r in reconcilers:
        if r.id == "torch_venv_detector":
            continue  # detect-only, handled below
        if not r.is_active():
            log.debug("%s inactive; skipping", r.id)
            continue
        current = r.compute_hash()
        extra = r.extra_state()
        prior = state.reconcilers.get(r.id, {})
        if force or not _entries_match(prior, current, extra):
            log.info("drift: %s", r.id)
            dirty.append((r, current, extra))
        else:
            log.debug("clean: %s", r.id)

    if dry_run:
        log.info("dry-run: %d drifted reconciler(s)", len(dirty))
        return 0

    # Install pass.
    failures: list[ReconcileResult] = []
    for r, current, extra in dirty:
        log.info("installing: %s", r.id)
        rc = r.install(log_path)
        if rc != 0:
            log.error("FAILED: %s (rc=%d)", r.id, rc)
            failures.append(ReconcileResult(r.id, "failed", f"exit {rc}"))
            continue
        # Update state for the successful install.
        state.reconcilers[r.id] = {
            "manifest_hash": current,
            "extra": extra,
            "last_installed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        # If this was the plugin_bundle, also update per-plugin entries.
        if r.id == "plugin_bundle" and plugin_bundle is not None:
            for pid, h in plugin_bundle.member_hashes().items():
                state.reconcilers[f"plugin:{pid}"] = {
                    "manifest_hash": h, "extra": {},
                    "last_installed_at": state.reconcilers[r.id]["last_installed_at"],
                }

    # Always persist state, even on partial success.
    save_state(state_path, state)

    # Detect-only checks (warnings only, never blocking).
    detector = next((r for r in reconcilers if r.id == "torch_venv_detector"), None)
    if detector is not None:
        for w in detector.detect():
            log.warning(w)

    if failures:
        # Print error tail to stderr so start.sh can show it.
        print("", file=sys.stderr)
        print("Reconciliation failed for:", file=sys.stderr)
        for f in failures:
            print(f"  - {f.reconciler_id}: {f.message}", file=sys.stderr)
        print(f"\nFull log: {log_path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
