"""The backend's side of the inbound guard.

Every path where Guaardvark writes code into its own checkout — guarded edits,
staged fixes, the code editor's file routes, swarm merges, the agent's own notes
— asks this module whether the change may land. The judging is done by the
stdlib engine in ``scripts/inbound_guard`` (the same one git hooks and CI use);
this module adds the mode setting, the database ledger, approvals, and two
registries an extension can plug into:

- ``register_inbound_scanner(name, fn)`` — ``fn(changes, context) -> [Finding]``.
  Runs inside every scan; may add findings, never remove them. A scanner that
  raises is recorded on the verdict and skipped.
- ``register_inbound_listener(name, fn)`` — ``fn(event)`` for every verdict and
  every change that landed. Called on one background thread so a slow listener
  never delays a write.

Mode (``inbound_guard_mode``): off, observe or enforce, read from the setting,
then GUAARDVARK_INBOUND_GUARD, then `git config inboundguard.mode`. Saving it from
Settings writes the git config as well, so the hooks and the swarm follow the
same switch. With off, callers return before anything here imports the engine,
so behaviour is exactly as before.
"""
from __future__ import annotations

import json
import logging
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

MODE_KEY = "inbound_guard_mode"
MODES = ("off", "observe", "enforce")
REPO_ROOT = Path(__file__).resolve().parents[2]

Scanner = Callable[[list, dict], Iterable[Any]]
Listener = Callable[[dict], None]

_scanners: Dict[str, Scanner] = {}
_listeners: Dict[str, Listener] = {}
_registry_lock = threading.Lock()

_events: "queue.Queue[tuple]" = queue.Queue(maxsize=512)
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()

_mode: Optional[str] = None


class InboundRefused(Exception):
    """A change the guard will not let land while enforcing.

    ``held`` is True when a person can approve it (a hold), False when it was
    blocked outright. ``scan_id`` points at the ledger row with the findings.
    """

    def __init__(self, message: str, *, held: bool, verdict: Any = None, scan_id: Optional[int] = None,
                 pending_fix_id: Optional[int] = None):
        super().__init__(message)
        self.held = held
        self.verdict = verdict
        self.scan_id = scan_id
        self.pending_fix_id = pending_fix_id

    @property
    def code(self) -> str:
        return "INBOUND_HELD" if self.held else "INBOUND_BLOCKED"


# -- mode --------------------------------------------------------------------------

def _normalize(value: Optional[str]) -> str:
    mode = (value or "").strip().lower()
    if not mode:
        return "off"
    if mode in MODES:
        return mode
    logger.warning("inbound guard: unknown mode %r; using observe", value)
    return "observe"


def get_mode() -> str:
    """The mode, cached for threads with no app context (tools run in workers)."""
    global _mode
    from flask import has_app_context

    if has_app_context() or _mode is None:
        from backend.utils.settings_utils import get_setting

        value = get_setting(MODE_KEY, default=None)
        _mode = _normalize(value if value is not None else _git_config_mode())
    return _mode


def _git_config_mode() -> Optional[str]:
    try:
        engine()
        from scripts.inbound_guard.sources import git

        return git(REPO_ROOT, "config", "--get", "inboundguard.mode", check=False).decode().strip() or None
    except Exception:
        return None


def set_mode(mode: str) -> str:
    """Save the mode for the backend and for this clone's git hooks, so one switch sets both."""
    global _mode
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    from backend.utils.settings_utils import save_setting

    save_setting(MODE_KEY, mode)
    _mode = mode
    try:
        engine()
        from scripts.inbound_guard.sources import git

        git(REPO_ROOT, "config", "inboundguard.mode", mode)
    except Exception as exc:
        logger.warning("inbound guard: saved the mode, but not into git config: %s", exc)
    if mode != "off":
        try:
            from flask import current_app

            from backend.services import inbound_guard_watch

            inbound_guard_watch.start_background(current_app._get_current_object())
        except Exception as exc:
            logger.warning("inbound guard: could not start the source watch: %s", exc)
    return mode


def is_on() -> bool:
    return get_mode() != "off"


# -- engine ------------------------------------------------------------------------

def engine():
    """The stdlib engine, imported on first use."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import scripts.inbound_guard as guard  # noqa: WPS433 - deliberate late import

    return guard


_rules_cache: Dict[str, Any] = {}


def rules():
    """The rule data, reloaded when the file changes."""
    guard_engine = engine()
    path = guard_engine.rules.RULES_FILE
    stamp = path.stat().st_mtime
    if _rules_cache.get("stamp") != stamp:
        _rules_cache.update(stamp=stamp, rules=guard_engine.RuleSet.load())
    return _rules_cache["rules"]


def guard_file_reason(relative_path: str) -> Optional[str]:
    """While the guard is on, its own files and the portability guard's are off limits to autonomous edits."""
    if not is_on():
        return None
    if rules().matches(relative_path, ["@outbound_guard", "@inbound_guard"]):
        return (f"'{relative_path}' is part of a guard. While the inbound guard is on, it can only be "
                "changed through git, where the change is judged before it lands.")
    return None


def relative(path: str | Path) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except (ValueError, OSError):
        return p.as_posix()


def change_for_file(path: str | Path, old_text: Optional[str], new_text: Optional[str]):
    """A whole-file change: create (old None), delete (new None) or rewrite."""
    return engine().change_from_texts(relative(path), old_text, new_text)


def change_for_replacement(path: str | Path, current: str, old_text: str, new_text: str):
    """The change one exact replacement makes, read as whole lines.

    Only the lines around the replaced span are diffed, so a one-line edit in a
    large file costs one line of work; the full before and after texts ride
    along for the rules that compare whole files.
    """
    guard = engine()
    start = current.find(old_text)
    if start < 0:
        return change_for_file(path, current, current.replace(old_text, new_text, 1))
    end = start + len(old_text)
    line_start = current.rfind("\n", 0, start) + 1
    line_end = current.find("\n", end)
    line_end = len(current) if line_end < 0 else line_end
    updated = current[:start] + new_text + current[end:]
    new_end = line_end + (len(new_text) - len(old_text))
    block = guard.change_from_texts(relative(path), current[line_start:line_end], updated[line_start:new_end])
    offset = current.count("\n", 0, line_start)
    block.added = [(number + offset, text) for number, text in block.added]
    block.old_text = current
    block.new_text = updated
    return block


# -- scanning and the gate ---------------------------------------------------------------

def check(changes: List[Any], *, source: str, subject: str, mode: Optional[str] = None,
          payload: Optional[dict] = None, pending_fix_id: Optional[int] = None, keep: bool = True):
    """Judge ``changes`` and record the verdict. Returns None when the guard is off.

    ``keep=False`` judges without recording, for dry runs that must answer the
    same as the real apply. An engine fault is a refusal in enforce mode (this
    is the in-process gate, which fails closed) and a logged skip otherwise.
    """
    mode = mode or get_mode()
    if mode == "off":
        return None
    try:
        verdict = engine().scan(changes, source=source, subject=subject, mode=mode, repo=REPO_ROOT,
                                scanners=scanners())
    except Exception as exc:
        logger.error("inbound guard: scan failed for %s: %s", subject, exc, exc_info=True)
        if mode == "enforce":
            raise InboundRefused(f"The inbound guard could not read this change ({exc}); refusing while "
                                 "it enforces.", held=False) from exc
        return None
    if not keep:
        verdict.scan_id = None
        return verdict
    verdict.scan_id = record(verdict, payload=payload, pending_fix_id=pending_fix_id)
    _emit({"kind": "verdict", "verdict": verdict.to_dict(), "scan_id": verdict.scan_id})
    return verdict


def gate(verdict, *, human_approved: bool = False) -> None:
    """Raise InboundRefused when an enforced verdict says the change may not land.

    A hold passes when a person already approved this change elsewhere — a fix
    approved in the fixes dialog is that approval. A block passes only on an
    approval recorded for exactly this change.
    """
    if verdict is None or verdict.mode != "enforce" or verdict.verdict == "allow":
        return
    if is_approved(verdict.digest):
        return
    if verdict.verdict == "hold" and human_approved:
        return
    worst = verdict.findings[0] if verdict.findings else None
    detail = f"{worst.severity} {worst.rule}: {worst.why}" if worst else verdict.verdict
    held = verdict.verdict == "hold"
    raise InboundRefused(
        ("Held for approval by the inbound guard" if held else "Blocked by the inbound guard")
        + f" ({len(verdict.findings)} finding(s); worst: {detail}). "
        + "Review it under Settings → Agents → Inbound guard.",
        held=held, verdict=verdict, scan_id=getattr(verdict, "scan_id", None),
    )


def check_and_gate(changes: List[Any], *, source: str, subject: str, human_approved: bool = False,
                   payload: Optional[dict] = None):
    verdict = check(changes, source=source, subject=subject, payload=payload)
    gate(verdict, human_approved=human_approved)
    return verdict


def landed(paths: Iterable[str], *, source: str, subject: str, verdict: Any = None) -> None:
    """Tell listeners a change is now on disk (re-index, correlate, alert)."""
    if not is_on():
        return
    _emit({"kind": "landed", "paths": [relative(p) for p in paths], "source": source, "subject": subject,
           "digest": getattr(verdict, "digest", None), "scan_id": getattr(verdict, "scan_id", None)})


# -- ledger -------------------------------------------------------------------------------

def record(verdict, *, payload: Optional[dict] = None, pending_fix_id: Optional[int] = None) -> Optional[int]:
    """Store a verdict: the database when there is an app context, else the git ledger."""
    from flask import has_app_context

    if has_app_context():
        try:
            from backend.models import InboundScan, db

            row = InboundScan(
                source=verdict.source,
                subject=verdict.subject[:500],
                mode=verdict.mode,
                verdict=verdict.verdict,
                top_severity=verdict.top_severity,
                digest=verdict.digest,
                findings=json.dumps([f.to_dict() for f in verdict.findings]),
                providers=json.dumps(verdict.providers),
                errors=json.dumps(verdict.errors),
                files=verdict.files,
                added_lines=verdict.added_lines,
                status="open" if verdict.verdict != "allow" else "clear",
                payload=json.dumps(payload) if payload else None,
                pending_fix_id=pending_fix_id,
            )
            db.session.add(row)
            db.session.commit()
            return row.id
        except Exception as exc:
            logger.warning("inbound guard: could not store the verdict in the database: %s", exc)
            try:
                from backend.models import db

                db.session.rollback()
            except Exception:
                pass
    try:
        data = verdict.to_dict()
        data["kind"] = "verdict"
        _git_ledger_append(data)
    except Exception as exc:
        logger.warning("inbound guard: could not write the git ledger either: %s", exc)
    return None


def _git_ledger_path() -> Path:
    engine()
    from scripts.inbound_guard import ledger
    from scripts.inbound_guard.sources import git_common_dir

    return ledger.ledger_path(git_common_dir(REPO_ROOT))


def _git_ledger_append(record_: dict) -> None:
    path = _git_ledger_path()
    from scripts.inbound_guard import ledger

    ledger.append(path, record_)


def link_pending_fix(scan_id: Optional[int], pending_fix_id: int) -> None:
    if not scan_id:
        return
    from backend.models import InboundScan, db

    row = db.session.get(InboundScan, scan_id)
    if row:
        row.pending_fix_id = pending_fix_id
        db.session.commit()


def is_approved(digest: str) -> bool:
    """Whether a person approved exactly this change, here or through git."""
    from flask import has_app_context

    if has_app_context():
        try:
            from backend.models import InboundScan, db

            if db.session.query(InboundScan.id).filter_by(digest=digest, status="approved").first():
                return True
        except Exception as exc:
            logger.warning("inbound guard: approval lookup failed: %s", exc)
    try:
        path = _git_ledger_path()
        from scripts.inbound_guard import ledger

        return ledger.approval_for(path, digest) is not None
    except Exception:
        return False


def git_ledger(limit: int = 50) -> List[dict]:
    """Verdicts the git hooks recorded (fetches, merges, cherry-picks)."""
    try:
        path = _git_ledger_path()
        from scripts.inbound_guard import ledger

        return ledger.recent(path, limit)
    except Exception as exc:
        logger.warning("inbound guard: could not read the git ledger: %s", exc)
        return []


def approve_git(digest: str, by: str, note: str) -> None:
    _git_ledger_append({"kind": "approval", "digest": digest, "by": by, "note": note})


# -- registries -----------------------------------------------------------------------------

def register_inbound_scanner(name: str, fn: Scanner) -> None:
    if not name or not callable(fn):
        raise ValueError("an inbound scanner needs a name and a callable")
    with _registry_lock:
        _scanners[name] = fn


def unregister_inbound_scanner(name: str) -> bool:
    with _registry_lock:
        return _scanners.pop(name, None) is not None


def register_inbound_listener(name: str, fn: Listener) -> None:
    if not name or not callable(fn):
        raise ValueError("an inbound listener needs a name and a callable")
    with _registry_lock:
        _listeners[name] = fn


def unregister_inbound_listener(name: str) -> bool:
    with _registry_lock:
        return _listeners.pop(name, None) is not None


def scanners() -> list:
    """Registered extension scanners as (name, fn), for any caller that runs a scan."""
    with _registry_lock:
        return list(_scanners.items())


def registered() -> dict:
    with _registry_lock:
        return {"scanners": sorted(_scanners), "listeners": sorted(_listeners)}


def _emit(event: dict) -> None:
    with _registry_lock:
        if not _listeners:
            return
    _ensure_worker()
    app = None
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            app = current_app._get_current_object()
    except Exception:
        app = None
    try:
        _events.put_nowait((event, app))
    except queue.Full:
        logger.warning("inbound guard: listener queue full; dropped a %s event", event.get("kind"))


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_deliver, name="inbound-guard-listeners", daemon=True)
            _worker.start()


def _deliver() -> None:
    while True:
        event, app = _events.get()
        with _registry_lock:
            listeners = list(_listeners.items())
        for name, fn in listeners:
            try:
                if app is not None:
                    with app.app_context():
                        fn(event)
                else:
                    fn(event)
            except Exception as exc:
                logger.warning("inbound guard: listener %s failed on %s: %s", name, event.get("kind"), exc)
        _events.task_done()


# -- built-in listener: keep the code index current ---------------------------------------------

_zvec_lock = threading.Lock()
_zvec_pending = threading.Event()


def _refresh_code_index(event: dict) -> None:
    """After code lands, drop cached views of the checkout so self-reading sees it."""
    if event.get("kind") != "landed":
        return
    from backend.services import guarded_code_service

    guarded_code_service.invalidate_lifecycle_cache()
    guarded_code_service._ANALYSIS_CACHE.clear()
    if (REPO_ROOT / ".zvec-grep").is_dir():
        _zvec_pending.set()
        threading.Thread(target=_refresh_zvec, name="inbound-guard-zvec", daemon=True).start()


def _refresh_zvec() -> None:
    """Run the code-search plugin's incremental index once for any burst of landings."""
    import subprocess

    if not _zvec_lock.acquire(blocking=False):
        return  # a run is in progress; it picks up the pending flag when it ends
    try:
        script = REPO_ROOT / "plugins" / "zvec_grep" / "scripts" / "index.sh"
        zg = REPO_ROOT / "plugins" / "zvec_grep" / "node_modules" / ".bin" / "zg"
        if not script.is_file() or not zg.exists():
            return
        while _zvec_pending.is_set():
            _zvec_pending.clear()
            proc = subprocess.run(["bash", str(script)], cwd=str(REPO_ROOT), capture_output=True, text=True,
                                  timeout=600)
            if proc.returncode != 0:
                logger.warning("inbound guard: code-search reindex failed: %s", proc.stderr.strip()[-300:])
                return
            logger.info("inbound guard: code-search index refreshed")
    except Exception as exc:
        logger.warning("inbound guard: code-search reindex failed: %s", exc)
    finally:
        _zvec_lock.release()


register_inbound_listener("code-index", _refresh_code_index)


# -- decisions on held changes ------------------------------------------------------------------

def decide(scan_id: int, decision: str, *, by: str, note: str = "", override_block: bool = False) -> dict:
    """Approve (and land) or reject an open verdict."""
    if decision == "approve":
        return approve_and_land(scan_id, by=by, note=note, override_block=override_block)
    if decision == "reject":
        return reject_held(scan_id, by=by, note=note)
    raise ValueError("decision must be approve or reject")


_land_lock = threading.Lock()


def _open_scan(scan_id: int):
    from backend.models import InboundScan, db

    row = db.session.get(InboundScan, scan_id)
    if row is None:
        raise LookupError("No such inbound verdict")
    if row.status != "open":
        raise ValueError(f"That verdict is already {row.status}")
    return row


def _settle(row, status: str, by: str, note: str) -> None:
    from datetime import datetime

    row.status = status
    row.decided_by = by[:80]
    row.decided_at = datetime.now()
    row.decision_note = note


def approve_and_land(scan_id: int, *, by: str, note: str = "", override_block: bool = False) -> dict:
    """Approve a held change and make it land, or change nothing.

    Every kind of held item lands here, so a person's "approve" means the same
    thing wherever they press it:
    - an edit waiting as a pending fix is applied (exact text only, and only if
      it still reads as it did when it was held);
    - a held file operation (create, delete, rename) is carried out;
    - a source-watch hold takes the file as it is now as judged.
    The change lands first and the approval is recorded after, so a failure
    leaves the item open with the reason.
    """
    from backend.models import db

    with _land_lock:
        row = _open_scan(scan_id)
        if row.verdict == "block" and not override_block:
            raise ValueError("That change was blocked, not held. Read the findings; approving it needs override_block.")
        landed: List[str] = []
        if row.pending_fix_id:
            landed = _land_pending_fix(row, by=by, note=note)
        elif row.payload:
            landed = _land_payload(json.loads(row.payload))
            _emit({"kind": "landed", "paths": landed, "source": row.source, "subject": row.subject,
                   "digest": row.digest, "scan_id": row.id})
        _settle(row, "approved", by, note)
        db.session.commit()
        if row.source == "watch":
            from backend.services import inbound_guard_watch

            inbound_guard_watch.approve_held(row.id)
        return {**row.to_dict(), "landed": landed}


def _land_pending_fix(row, *, by: str, note: str) -> List[str]:
    """Apply the pending fix a held edit is waiting in, after checking it still reads as held."""
    import hashlib
    from datetime import datetime

    from backend.models import InboundScan, PendingFix, db
    from backend.services.guarded_code_service import GuardedCodeError, apply_exact_replacement

    fix = db.session.get(PendingFix, row.pending_fix_id)
    if fix is None:
        raise ValueError("The pending fix this change waits in no longer exists")
    if fix.status not in ("proposed", "triaged", "approved"):
        raise ValueError(f"The pending fix is already {fix.status}")
    if fix.original_content is None or fix.proposed_new_content is None:
        raise ValueError("The pending fix has no text to apply")
    path = Path(fix.file_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if current is None or current.count(fix.original_content) != 1:
        raise ValueError("Changed since it was held: the text it replaces is no longer in the file exactly once. "
                         "Reject it and propose the edit again.")

    # Judge it again as it would land now, with the core rules only (an
    # extension's view of a host can change over time). Anything new means the
    # change is not the one that was held.
    change = change_for_replacement(path, current, fix.original_content, fix.proposed_new_content)
    again = engine().scan([change], source="approved_fix", subject=row.subject, mode="observe", repo=REPO_ROOT)
    held = {(f.get("rule"), f.get("excerpt")) for f in json.loads(row.findings or "[]")}
    fresh = [f for f in again.findings if (f.rule, f.excerpt) not in held]
    if fresh or (again.verdict == "block" and row.verdict != "block"):
        worst = (fresh or again.findings)[0]
        raise ValueError(f"Changed since it was held: it now reads {worst.severity} {worst.rule}. "
                         "Reject it and propose the edit again.")

    pre_approved = []
    if row.verdict == "block":
        # The gate lets a block through only on an approval recorded for exactly
        # this change; the person's override is that approval.
        for twin in db.session.query(InboundScan).filter_by(digest=again.digest, status="open").all():
            _settle(twin, "approved", by, note or "approved despite the block")
            pre_approved.append(twin)
        db.session.commit()
    try:
        apply_exact_replacement(
            str(path), fix.original_content, fix.proposed_new_content,
            origin="approved_fix", human_approved=True,
            expected_hash=hashlib.sha256(current.encode("utf-8")).hexdigest(),
        )
    except GuardedCodeError:
        for twin in pre_approved:
            twin.status, twin.decided_by, twin.decided_at, twin.decision_note = "open", None, None, None
        db.session.commit()
        raise

    now = datetime.now()
    fix.status = "applied"
    fix.reviewed_by = (fix.reviewed_by or by)[:50]
    fix.reviewed_at = fix.reviewed_at or now
    fix.applied_at = now
    fix.review_notes = ((fix.review_notes or "") + f"\n\nApplied from the inbound guard's held list by {by}."
                        + (f" {note}" if note else "")).strip()
    for twin in db.session.query(InboundScan).filter_by(digest=again.digest, status="open").all():
        if twin.id != row.id:
            _settle(twin, "approved", by, f"applied with #{row.id}")
    db.session.commit()
    resolve_for_fix(fix.id, "approve", by=by, note=note)
    return [relative(path)]


def reject_held(scan_id: int, *, by: str, note: str = "") -> dict:
    """Reject a held change. An edit waiting as a pending fix is rejected with it.

    A source-watch hold is already on disk: rejecting it leaves the file there
    and keeps it out of what this machine serves to others until it changes.
    """
    from datetime import datetime

    from backend.models import PendingFix, db

    with _land_lock:
        row = _open_scan(scan_id)
        _settle(row, "rejected", by, note)
        if row.pending_fix_id:
            fix = db.session.get(PendingFix, row.pending_fix_id)
            if fix is not None and fix.status in ("proposed", "triaged", "approved"):
                fix.status = "rejected"
                fix.reviewed_by = by[:50]
                fix.reviewed_at = datetime.now()
                fix.review_notes = ((fix.review_notes or "") + "\n\nRejected from the inbound guard's held list."
                                    + (f" {note}" if note else "")).strip()
        db.session.commit()
        if row.pending_fix_id:
            resolve_for_fix(row.pending_fix_id, "reject", by=by, note=note)
        return {**row.to_dict(), "landed": []}


def _land_payload(payload: dict) -> List[str]:
    """Carry out a held file operation now that a person approved it.

    The same lock and protected-file checks as the route that was held apply
    again: approval answers the guard, not those.
    """
    import os

    from backend.services.guarded_code_service import (
        GuardedCodeError,
        is_codebase_locked,
        protected_file_reason,
        resolve_repo_path,
    )

    if is_codebase_locked():
        raise GuardedCodeError("Codebase is locked. Unlock it before landing this change.", "CODEBASE_LOCKED", 423)
    kind = payload.get("kind")
    resolved, rel = resolve_repo_path(payload["path"])
    reason = protected_file_reason(rel)
    if reason:
        raise GuardedCodeError(reason, "PROTECTED_FILE", 403)
    if kind == "write_file":
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(payload.get("content", ""), encoding="utf-8")
        return [rel]
    if kind == "delete_file":
        if resolved.exists():
            os.remove(resolved)
        return [rel]
    if kind == "rename_file":
        target, target_rel = resolve_repo_path(payload["new_path"])
        reason = protected_file_reason(target_rel)
        if reason:
            raise GuardedCodeError(reason, "PROTECTED_FILE", 403)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(resolved, target)
        return [rel, target_rel]
    raise ValueError(f"Unknown held change kind: {kind!r}")


def git_hooks_status() -> dict:
    """Whether this clone's inbound hooks are installed and current."""
    try:
        engine()
        from scripts.inbound_guard.sources import git, git_common_dir

        common = git_common_dir(REPO_ROOT)
        hooks = Path(git(REPO_ROOT, "rev-parse", "--path-format=absolute", "--git-path", "hooks").decode().strip())
        mode = git(REPO_ROOT, "config", "--get", "inboundguard.mode", check=False).decode().strip() or "off"
        pinned = common / "inbound-guard" / "engine" / "PINNED"
        return {
            "installed": all((hooks / h).is_file() for h in ("pre-merge-commit", "reference-transaction"))
            and pinned.is_file(),
            "mode": _normalize(mode),
        }
    except Exception as exc:
        return {"installed": False, "mode": "off", "error": str(exc)}


def mark(scan_id: int, status: str, *, by: str, note: str = "") -> None:
    from datetime import datetime

    from backend.models import InboundScan, db

    row = db.session.get(InboundScan, scan_id)
    if row is not None and row.status == "open":
        row.status = status
        row.decided_by = by[:80]
        row.decided_at = datetime.now()
        row.decision_note = note
        db.session.commit()


def resolve_for_fix(pending_fix_id: int, decision: str, *, by: str, note: str = "") -> None:
    """Settle open verdicts on a pending fix when a person approves or rejects the fix itself.

    The fixes dialog shows the guard's findings beside the diff, so its decision
    is the decision on them too; without this the review list would keep asking.
    """
    from datetime import datetime

    from backend.models import InboundScan, db

    status = "approved" if decision == "approve" else "rejected"
    rows = db.session.query(InboundScan).filter_by(pending_fix_id=pending_fix_id, status="open").all()
    for row in rows:
        if status == "approved" and row.verdict == "block":
            continue  # a block needs its own explicit approval
        row.status = status
        row.decided_by = f"{by} (fix #{pending_fix_id})"[:80]
        row.decided_at = datetime.now()
        row.decision_note = note
    if rows:
        db.session.commit()


def verdicts_for_fixes(fix_ids: List[int]) -> Dict[int, dict]:
    """The latest verdict per pending fix, for the fixes dialog."""
    if not fix_ids:
        return {}
    from backend.models import InboundScan, db

    out: Dict[int, dict] = {}
    rows = (db.session.query(InboundScan).filter(InboundScan.pending_fix_id.in_(fix_ids))
            .order_by(InboundScan.created_at.asc()).all())
    for row in rows:
        out[row.pending_fix_id] = row.to_dict()
    return out


def guard_file_write(path: str | Path, new_text: str, *, source: str, subject: str):
    """Judge a whole-file write a caller is about to make; None when off or outside the checkout.

    Raises InboundRefused when enforcing and the write may not land. Writes
    outside this checkout are not its code and are not read.
    """
    if not is_on():
        return None
    target = Path(path).resolve()
    try:
        target.relative_to(REPO_ROOT)
    except ValueError:
        return None
    old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
    return check_and_gate([change_for_file(target, old, new_text)], source=source, subject=subject,
                          payload={"kind": "write_file", "path": relative(target), "content": new_text})


RESTORE_GROUPS = ("code", "steering", "dependencies", "workflows")
RESTORE_MAX_BYTES = 2 * 1024 * 1024


def guard_restore(members: List[tuple], project_root: Path):
    """Judge the files a backup restore would write over this checkout's code.

    ``members`` are (archive name, extracted temp path). Only files the rules
    have something to say about are read: code, agent instructions,
    dependencies and workflows, up to 2 MB each.
    """
    if not is_on():
        return None
    rules = engine().RuleSet.load()
    changes = []
    for name, temp in members:
        rel = Path(name).as_posix()
        if not any(rules.in_group(rel, group) for group in RESTORE_GROUPS):
            continue
        temp = Path(temp)
        if not temp.is_file() or temp.stat().st_size > RESTORE_MAX_BYTES:
            continue
        new = temp.read_text(encoding="utf-8", errors="replace")
        dest = project_root / rel
        old = dest.read_text(encoding="utf-8", errors="replace") if dest.is_file() else None
        if old == new:
            continue
        changes.append(engine().change_from_texts(rel, old, new))
    if not changes:
        return None
    return check_and_gate(changes, source="backup_restore", subject=f"backup restore ({len(changes)} code file(s))")
