"""Changes Guaardvark makes to its own code: restore point, write, check, undo.

Every edit to the install's own source keeps a copy of each file it touches
(backups/self_edits/<id>/), is written exactly (no near-match), and is then
checked: syntax, an import of the changed backend modules, and the tests
that import them. A failed check puts the files back at once. A passed edit
can be reverted later from the chat or the Self-improvement page, and one
that stops the backend from starting is put back by start.sh through
scripts/self_edit_guard.py, which reads the same records with the standard
library only.

Edits made for one plan step or one chat turn share a group; Revert undoes
the whole group, newest first. Git plays no part: on a git checkout the
edits are ordinary uncommitted changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How long each check may take, in seconds. Tests run twice (before and after
# the edit) so a test that was already failing does not undo a good edit.
IMPORT_CHECK_TIMEOUT_S = 60
TEST_TIMEOUT_S = 180
MAX_TEST_FILES = 4

_lock = threading.Lock()

# The form apply_change gives every id; anything else is not a stored edit.
_EDIT_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")


def repo_root() -> Path:
    from backend.services.guarded_code_service import default_repo_root
    return Path(default_repo_root()).resolve()


def store_dir() -> Path:
    try:
        from backend.config import BACKUP_DIR
        base = Path(BACKUP_DIR)
    except Exception:
        base = repo_root() / "backups"
    return base / "self_edits"


def pending_path() -> Path:
    """Edits not yet proven by a backend start; scripts/self_edit_guard.py
    reads this when a start fails."""
    return store_dir() / "pending.json"


def is_own_code(path: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(repo_root())
        return True
    except (ValueError, OSError):
        return False


def _sha(text: Optional[str]) -> Optional[str]:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest_path(edit_id: str) -> Path:
    return store_dir() / edit_id / "manifest.json"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def load(edit_id: str) -> Optional[Dict[str, Any]]:
    if not _EDIT_ID.fullmatch(edit_id or ""):
        return None
    try:
        return json.loads(_manifest_path(edit_id).read_text())
    except (OSError, ValueError):
        return None


def list_edits(limit: int = 50) -> List[Dict[str, Any]]:
    root = store_dir()
    if not root.is_dir():
        return []
    records = [load(p.name) for p in root.iterdir() if (p / "manifest.json").is_file()]
    records = [r for r in records if r]
    records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return records[:limit]


def _needs_restart(rel: str) -> bool:
    """A backend or CLI file runs from memory; the web app reloads itself."""
    return rel.endswith(".py") and not rel.startswith(("frontend/", "scripts/"))


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _syntax(path: Path) -> tuple[bool, Optional[str]]:
    """(checked, first syntax error). ``checked`` is False for a file type
    nothing on this machine can parse."""
    from backend.services.guarded_code_service import syntax_check

    return syntax_check(path)


def _module_of(rel: str) -> Optional[str]:
    if not (rel.startswith("backend/") and rel.endswith(".py")) or "/tests/" in rel:
        return None
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _isolated_env() -> Dict[str, str]:
    """Environment for checks: nothing they run reaches the live database or
    the task queue."""
    from backend.utils.isolated_env import isolated_env
    return isolated_env(PYTHONDONTWRITEBYTECODE="1")


def _import_check(modules: List[str]) -> Optional[str]:
    if not modules:
        return None
    code = "import importlib, sys\nfor m in sys.argv[1:]:\n    importlib.import_module(m)\n"
    try:
        proc = subprocess.run([sys.executable, "-c", code, *modules], cwd=str(repo_root()),
                              env=_isolated_env(), capture_output=True, text=True,
                              timeout=IMPORT_CHECK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f"importing {', '.join(modules)} took longer than {IMPORT_CHECK_TIMEOUT_S} s"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        return " | ".join(tail)[:600]
    return None


def impact_tests(rels: List[str]) -> List[str]:
    """Test files that import the changed modules, from the code map."""
    wanted = set(rels)
    try:
        from backend.services.repository_analysis_service import project_code_map
        graph = project_code_map("self", str(repo_root())).get("dependency_graph") or {}
    except Exception:
        logger.debug("code map unavailable for test selection", exc_info=True)
        return []
    tests = sorted(src for src, targets in graph.items()
                   if src.startswith("backend/tests/") and Path(src).name.startswith("test_")
                   and wanted & set(targets))
    tests += [r for r in rels if r.startswith("backend/tests/") and Path(r).name.startswith("test_")]
    return sorted(set(tests))[:MAX_TEST_FILES]


def _run_tests(tests: List[str]) -> Dict[str, Any]:
    """{test file: passed} for the given files, each run on its own."""
    outcome: Dict[str, Any] = {}
    for test in tests:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-m", "not integration", test],
                cwd=str(repo_root()), env=_isolated_env(), capture_output=True, text=True,
                timeout=TEST_TIMEOUT_S)
            # 5: no tests collected (all marked integration) counts as no signal.
            outcome[test] = proc.returncode in (0, 5)
        except subprocess.TimeoutExpired:
            outcome[test] = None
    return outcome


def tests_enabled() -> bool:
    try:
        from backend.utils.settings_utils import get_setting
        return str(get_setting("self_edit_run_tests", default="true")).strip().lower() not in ("false", "0", "no", "off")
    except Exception:
        return True


def network_findings(path: str, old_text: str, new_text: str) -> Optional[List[Dict[str, Any]]]:
    """What a replacement in Guaardvark's own code adds that reaches outside
    the machine: the inbound guard's outbound-network findings (a new outside
    host, a hosted AI or telemetry client, TLS checks off, a raw socket),
    whatever mode the guard is in. None when the change cannot be judged, so a
    caller can ask instead of assuming it is clean."""
    from backend.services import inbound_guard_service as guard

    try:
        file_path = Path(path).resolve()
        current = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        change = guard.change_for_replacement(file_path, current, old_text, new_text or "")
        verdict = guard.engine().scan([change], source="self_code", subject=f"network check: {file_path.name}",
                                      mode="observe", repo=guard.REPO_ROOT)
    except Exception as exc:  # noqa: BLE001 - unknown means ask
        logger.info("self-edit network check could not judge %s: %s", path, exc)
        return None
    return [f.to_dict() for f in verdict.findings if f.pack == "outbound-network"]


# ---------------------------------------------------------------------------
# Apply and revert
# ---------------------------------------------------------------------------

def apply_change(path: str, old_text: str, new_text: str, *, group: str, title: str = "",
                 expected_hash: Optional[str] = None, expected_mtime: Optional[float] = None,
                 origin: str = "self_edit", human_approved: bool = False) -> Dict[str, Any]:
    """Make one exact replacement in Guaardvark's own code, safely.

    ``human_approved`` says a person approved exactly this change (an approved
    pending fix); it answers an inbound-guard hold, not a block.

    Returns the edit's record: status "applied" (checks passed),
    "failed_checks" (put back, with what failed) or raises GuardedCodeError
    when the edit could not be made at all.
    """
    from backend.services.guarded_code_service import apply_exact_replacement

    root = repo_root()
    file_path = Path(path).resolve()
    rel = str(file_path.relative_to(root))
    edit_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    edit_dir = store_dir() / edit_id
    with _lock:
        before = file_path.read_text(encoding="utf-8") if file_path.exists() else None
        # Refused before the baseline tests run: they can take minutes.
        _require_one_exact_match(before, old_text)
        edit_dir.mkdir(parents=True, exist_ok=True)
        if before is not None:
            (edit_dir / "before_0").write_text(before, encoding="utf-8")
        tests = impact_tests([rel]) if tests_enabled() else []
        baseline = _run_tests(tests) if tests else {}

        try:
            apply_exact_replacement(str(file_path), old_text, new_text, expected_hash=expected_hash,
                                    expected_mtime=expected_mtime, origin=origin,
                                    human_approved=human_approved, allow_reindent=False)
        except Exception:
            shutil.rmtree(edit_dir, ignore_errors=True)
            raise
        after = file_path.read_text(encoding="utf-8")
        record = {
            "id": edit_id, "group": group, "title": title or f"Change {rel}",
            # The install the paths are relative to; the start guard restores
            # only its own install's changes.
            "root": str(root),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "checking",
            "files": [{"path": rel, "before": "before_0" if before is not None else None,
                       "before_sha": _sha(before), "after_sha": _sha(after), "created": before is None}],
            "restart_needed": _needs_restart(rel),
            "checks": [],
        }
        _write_json(_manifest_path(edit_id), record)
        # Listed for the start guard from the moment the file changes: a stop
        # during the checks must not leave an unchecked edit in place.
        _list_pending(record)

        checks = []
        syntax_checked, error = _syntax(file_path)
        checks.append({"check": "syntax", "ok": error is None, "checked": syntax_checked,
                       "detail": error or ("" if syntax_checked else "not checked for this file type")})
        module = _module_of(rel)
        if error is None and module:
            error = _import_check([module])
            checks.append({"check": "import", "ok": error is None, "detail": error or ""})
        if error is None and tests:
            after_tests = _run_tests(tests)
            broke = [t for t, ok in after_tests.items() if ok is False and baseline.get(t) is not False]
            hung = [t for t, ok in after_tests.items() if ok is None and baseline.get(t) is True]
            detail = "; ".join(part for part in (
                f"newly failing: {', '.join(broke)}" if broke else "",
                f"no longer finishing within {TEST_TIMEOUT_S} s: {', '.join(hung)}" if hung else "",
            ) if part)
            checks.append({"check": "tests", "ok": not (broke or hung),
                           "detail": detail or f"{len(after_tests)} test file(s) pass as before"})
            if broke or hung:
                error = f"tests that passed before the change do not pass now ({detail})"
        record["checks"] = checks

        if error is not None:
            _restore(record, edit_dir)
            record["status"] = "failed_checks"
            record["error"] = error
            _write_json(_manifest_path(edit_id), record)
            _unlist_pending(edit_id)
            logger.warning("Self-edit %s on %s undone: %s", edit_id, rel, error)
            return record

        record["status"] = "applied"
        _write_json(_manifest_path(edit_id), record)
        _mark_pending(record)
        logger.info("Self-edit %s on %s applied (%s)", edit_id, rel,
                    "restart to apply" if record["restart_needed"] else "live now")
        return record


def _require_one_exact_match(content: Optional[str], old_text: str) -> None:
    """An edit to Guaardvark's own code applies to the text that was quoted,
    once, or not at all: the guarded write would otherwise fall back to a
    near match."""
    from backend.services.guarded_code_service import GuardedCodeError

    text = (content or "").replace("\r\n", "\n")
    count = text.count((old_text or "").replace("\r\n", "\n")) if old_text else 0
    if count == 0:
        raise GuardedCodeError("Exact text was not found; edit was not applied.", "TEXT_NOT_FOUND", 409)
    if count > 1:
        raise GuardedCodeError("Exact text is not unique; edit was not applied.", "TEXT_NOT_UNIQUE", 409)


def _pending_ids() -> List[str]:
    try:
        return list(json.loads(pending_path().read_text()).get("edits", []))
    except (OSError, ValueError):
        return []


def _list_pending(record: Dict[str, Any]) -> None:
    """Put a backend edit on the list the start guard reads. A frontend edit
    cannot stop the backend starting, so it is never listed."""
    if not record.get("restart_needed"):
        return
    _write_json(pending_path(), {"edits": [e for e in _pending_ids() if e != record["id"]] + [record["id"]]})


def _unlist_pending(edit_id: str) -> None:
    ids = _pending_ids()
    if edit_id not in ids:
        return
    left = [e for e in ids if e != edit_id]
    if left:
        _write_json(pending_path(), {"edits": left})
    else:
        # No list at all when nothing waits: the start guard and the tests
        # read "no file" as "nothing to undo".
        pending_path().unlink(missing_ok=True)


def _mark_pending(record: Dict[str, Any]) -> None:
    """Note a backend edit no start has proven yet, and tell the UI a restart
    is due."""
    if not record.get("restart_needed"):
        return
    _list_pending(record)
    try:
        from backend.utils.update_state import record_update_applied
        record_update_applied(str(repo_root()), [f["path"] for f in record["files"]])
    except Exception:
        logger.debug("restart banner not set", exc_info=True)


def _restore(record: Dict[str, Any], edit_dir: Path) -> None:
    root = repo_root()
    for f in reversed(record["files"]):
        target = root / f["path"]
        if f.get("created"):
            if target.exists():
                target.unlink()
        elif f.get("before"):
            shutil.copyfile(edit_dir / f["before"], target)


def revert(edit_or_group: str, *, force: bool = False) -> Dict[str, Any]:
    """Put back the files of an edit, or of every applied edit in a group,
    newest first. Without ``force`` a file changed again since the edit is
    left alone and named in ``conflicts``."""
    with _lock:
        records = [r for r in list_edits(limit=1000)
                   if (r["id"] == edit_or_group or r.get("group") == edit_or_group)
                   and r.get("status") in ("applied", "live")]
        if not records:
            return {"reverted": [], "conflicts": [], "error": "Nothing to revert: no applied change by that id."}
        root = repo_root()
        conflicts = []
        if not force:
            # Newest first, each file is expected to hold what the edit left
            # and then what it replaced, so a later edit in the same group is
            # not mistaken for an outside change.
            expected: Dict[str, Optional[str]] = {}
            for r in records:
                for f in r["files"]:
                    if f["path"] not in expected:
                        target = root / f["path"]
                        expected[f["path"]] = _sha(target.read_text(encoding="utf-8") if target.exists() else None)
                    if expected[f["path"]] != f["after_sha"]:
                        conflicts.append(f["path"])
                    expected[f["path"]] = f.get("before_sha")
            if conflicts:
                return {"reverted": [], "conflicts": sorted(set(conflicts)),
                        "error": "These files changed again after the edit; revert anyway to lose those changes."}
        reverted = []
        restart = False
        for r in records:  # newest first
            _restore(r, store_dir() / r["id"])
            r["status"] = "reverted"
            r["reverted_at"] = datetime.now().isoformat(timespec="seconds")
            _write_json(_manifest_path(r["id"]), r)
            reverted.append(r["id"])
            restart = restart or r.get("restart_needed", False)
        if restart:
            try:
                from backend.utils.update_state import record_update_applied
                record_update_applied(str(root), sorted({f["path"] for r in records for f in r["files"]}))
            except Exception:
                logger.debug("restart banner not set", exc_info=True)
        return {"reverted": reverted, "conflicts": [], "restart_needed": restart}


# An edit still "checking" this long after it was written has lost the process
# that was checking it: the import check and the after-tests are bounded by
# IMPORT_CHECK_TIMEOUT_S + MAX_TEST_FILES * TEST_TIMEOUT_S (13 minutes).
INTERRUPTED_AFTER_S = 30 * 60


def recover_interrupted() -> int:
    """Put back edits whose checks never finished (the process stopped
    mid-check). A file changed again since the edit is left alone and the
    record says so."""
    recovered = 0
    with _lock:
        for record in list_edits(limit=1000):
            if record.get("status") != "checking":
                continue
            try:
                age = (datetime.now() - datetime.fromisoformat(record["created_at"])).total_seconds()
            except (KeyError, ValueError):
                age = INTERRUPTED_AFTER_S
            if age < INTERRUPTED_AFTER_S:
                continue
            root = repo_root()
            if record.get("root") and Path(record["root"]).resolve() != root:
                continue
            untouched = True
            for f in record["files"]:
                target = root / f["path"]
                now = target.read_text(encoding="utf-8") if target.exists() else None
                untouched = untouched and _sha(now) == f["after_sha"]
            if untouched:
                _restore(record, store_dir() / record["id"])
                record["error"] = "Guaardvark stopped before this change was checked, so it was undone."
            else:
                record["error"] = ("Guaardvark stopped before this change was checked. The file has changed "
                                   "since, so it was left as it is.")
            record["status"] = "failed_checks"
            _write_json(_manifest_path(record["id"]), record)
            _unlist_pending(record["id"])
            recovered += 1
    if recovered:
        logger.warning("Self-edit: %d change(s) interrupted mid-check were closed out", recovered)
    return recovered


def confirm_boot() -> int:
    """At backend start: edits waiting for a start have now been through one.
    Called by the server process after it is up."""
    try:
        recover_interrupted()
    except Exception:
        logger.warning("could not close out interrupted self-edits", exc_info=True)
    try:
        data = json.loads(pending_path().read_text())
    except (OSError, ValueError):
        return 0
    confirmed = 0
    for edit_id in data.get("edits", []):
        record = load(edit_id)
        if record and record.get("status") == "applied":
            record["status"] = "live"
            record["live_at"] = datetime.now().isoformat(timespec="seconds")
            _write_json(_manifest_path(edit_id), record)
            confirmed += 1
    try:
        pending_path().unlink()
    except OSError:
        pass
    return confirmed
