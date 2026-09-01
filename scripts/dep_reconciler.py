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
    p.add_argument("--repo-root", default="", help="override repository root (mainly for tests)")
    return p.parse_args(argv)


def _entries_match(stored: dict, current_hash: str, current_extra: dict) -> bool:
    if stored.get("manifest_hash") != current_hash:
        return False
    return stored.get("extra", {}) == current_extra


def _reexec_with_venv_python(repo: Path) -> None:
    """PEP 668 guard: the reconcilers pip-install via sys.executable, so any
    invoker using the SYSTEM interpreter makes every install fail with
    'externally-managed-environment' on Debian/Ubuntu (Observed: client box heal
    run 2026-08-04 — backend_venv + cli_venv both exit 1, sentinel set).
    Re-exec with the backend venv python whenever we aren't already inside it.

    Compare sys.prefix to the venv DIR — never resolved executable paths:
    venv/bin/python is a symlink to the system python3.12, so resolving both
    sides makes system and venv interpreters look identical.
    """
    if os.environ.get("GUAARDVARK_RECONCILER_REEXEC") == "1":
        return  # already re-exec'd once; never loop
    venv_dir = repo / "backend" / "venv"
    venv_py = venv_dir / "bin" / "python"
    if not venv_py.is_file() or not os.access(venv_py, os.X_OK):
        return  # bootstrap case: no venv yet — run with whatever invoked us
    try:
        if Path(sys.prefix).resolve() == venv_dir.resolve():
            return  # already running inside the backend venv
    except OSError:
        return
    os.environ["GUAARDVARK_RECONCILER_REEXEC"] = "1"
    print(f"[reconciler] re-exec via {venv_py} (invoked with {sys.executable})", flush=True)
    os.execv(str(venv_py), [str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]])


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    log = _setup_logging(args.quiet)

    if os.environ.get("GUAARDVARK_DEP_RECONCILER") == "disabled":
        log.info("kill switch set; skipping reconciliation")
        return 0

    repo = Path(args.repo_root).resolve() if args.repo_root else _REPO_ROOT
    if argv is None:
        # Real CLI invocation only — a test calling main([...]) must never have
        # its process replaced (and sys.argv wouldn't be ours to replay anyway).
        _reexec_with_venv_python(repo)

    # Sync-in-progress sentinel: refuse to run.
    sentinel = repo / "data" / "dep_reconciler" / ".sync_in_progress"
    if sentinel.exists():
        log.error("sync in progress; refusing to reconcile (retry on next boot)")
        return 2

    state_path = Path(args.state_file) if args.state_file else default_state_path(repo)
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

    # Failure sentinel consumed by scripts/preflight_check.py: while it exists,
    # preflight fails RED instead of printing "All checks passed" over a venv a
    # reconciler couldn't fully install. Owned here (not in start.sh) so any
    # invoking layer (start.sh, heal_backend_venv.sh, system-manager) that later
    # succeeds clears it.
    fail_sentinel = repo / "logs" / ".dep_reconcile_failed"

    def _sentinel_failed_ids() -> set[str]:
        """Reconciler ids named in the sentinel, or an empty set if unreadable."""
        try:
            body = fail_sentinel.read_text()
        except OSError:
            return set()
        found = set()
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("- ") or ":" not in line:
                continue
            # Each entry is "<id>: <message>". Split on the first ": " rather
            # than the first ":" — isolated plugin ids carry a colon of their
            # own ("isolated_plugin_venv:<plugin>"), and cutting at it left an
            # id no run could ever cover.
            head = line[2:]
            rid = head.split(": ", 1)[0] if ": " in head else head.rstrip(":")
            found.add(rid.strip())
        return found

    def _clear_sentinel_if_covered(covered: set[str] | None = None) -> None:
        # The sentinel names the reconcilers that failed, and a run clears the
        # ones it just proved good. A scoped run has to be able to clear a
        # failure in its own scope: gating the clear on backend_venv alone left
        # a plugin_bundle failure jamming preflight until some unrelated run
        # happened to cover the venv.
        if not fail_sentinel.exists():
            return
        recorded = _sentinel_failed_ids()
        # Unparseable or pre-format sentinel: only a full run can vouch for it.
        outstanding = (recorded - (covered or set())) if recorded else (set() if not only else {"?"})
        try:
            if outstanding:
                if recorded:
                    fail_sentinel.write_text(
                        "dep_reconciler failed at "
                        + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        + "\n"
                        + "".join(f"  - {rid}: (still unreconciled)\n" for rid in sorted(outstanding))
                        + f"Full log: {log_path}\n"
                    )
                return
            fail_sentinel.unlink(missing_ok=True)
        except OSError:
            pass

    # Trust-on-upgrade: state file is empty AND we detect a populated venv.
    # Write current hashes as initial state without re-installing.
    if not state.reconcilers:
        venv_marker = repo / "backend" / "venv" / "bin" / "flask"
        trust_on_upgrade = (
            os.environ.get("GUAARDVARK_TRUST_ON_UPGRADE") == "1"
            or venv_marker.is_file()
        )
        if trust_on_upgrade:
            log.info("trust-on-upgrade: existing venv detected, snapshotting current state")
            preliminary_reconcilers = build_active_reconcilers(repo)
            for recon in preliminary_reconcilers:
                if recon.id == "torch_venv_detector":
                    continue
                if not recon.is_active():
                    continue
                state.reconcilers[recon.id] = {
                    "manifest_hash": recon.compute_hash(),
                    "extra": recon.extra_state(),
                    "last_installed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            save_state(state_path, state)
            log.info("trust-on-upgrade: snapshot saved; no installers run")
            snapshot_ids = {r.id for r in preliminary_reconcilers if getattr(r, "id", None)}
            snapshot_ids |= {
                m for r in preliminary_reconcilers for m in (getattr(r, "members", None) or [])
            }
            _clear_sentinel_if_covered(snapshot_ids)
            return 0

    reconcilers = build_active_reconcilers(repo)
    if only:
        reconcilers = [r for r in reconcilers if r.id in only]
    active_ids = {r.id for r in reconcilers if hasattr(r, "id") and r.id}

    # Always look up the plugin_bundle (used for per-plugin state propagation later).
    plugin_bundle = next((r for r in reconcilers if r.id == "plugin_bundle"), None)
    member_ids = set(getattr(plugin_bundle, "members", []) or [])

    # Orphan pruning: drop state entries for reconcilers no longer active.
    # Skip when --only is used: a partial run shouldn't mutate state for
    # reconcilers it didn't even consider.
    if not only:
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
        try:
            fail_sentinel.parent.mkdir(parents=True, exist_ok=True)
            fail_sentinel.write_text(
                "dep_reconciler failed at "
                + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                + "\n"
                + "".join(f"  - {f.reconciler_id}: {f.message}\n" for f in failures)
                + f"Full log: {log_path}\n"
            )
        except OSError:
            pass
        return 1
    _clear_sentinel_if_covered(active_ids | member_ids)
    return 0


if __name__ == "__main__":
    sys.exit(main())
