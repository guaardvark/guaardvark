"""Source watch: Guaardvark notices when its own code changes, however it changed.

The inbound guard judges changes at the doors it knows about — merges, its own
edits, the editor. This sweeps the code itself, so a change that came in some
other way is seen too. It covers the checkout's code (tracked, and untracked
files git does not ignore), every file the Interconnector serves to other
machines, and the surfaces listed under "watch" in inbound_rules.json (ComfyUI
custom nodes, extensions, the agent's notes and MCP server list).

Each file keeps its last judged state in ``inbound_baselines``. A sweep hashes
what changed (size and mtime first) and attributes each change:

- git: the file now matches HEAD. Merges are judged by the git hooks.
- product: the product wrote it through a guarded path, which judged it and
  updated the baseline when it landed.
- out-of-band: anything else. The file is read whole and only findings that
  were not there before are raised; a hold goes to the review list.

The first sweep reads everything once as an audit, recorded for a person to
read; it holds nothing. On the master, files still held are withheld from what
the Interconnector serves while the guard enforces, so only swept code leaves
this machine.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from backend.services import inbound_guard_service as guard

logger = logging.getLogger(__name__)

_sweep_lock = threading.Lock()
_last_summary: Dict = {}
_thread: Optional[threading.Thread] = None


def _rules():
    return guard.engine().RuleSet.load()


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _fingerprint(finding, sha: str) -> str:
    """Line findings are known by what they quote; file-level ones by the content they judged.

    The engine's "and N more like this" summary is known by its rule alone, so
    a file with many old findings of one kind does not re-raise them on every edit.
    """
    if finding.line is None and not finding.excerpt and finding.why.startswith("and "):
        anchor = "summary"
    else:
        anchor = finding.excerpt if finding.line else sha
    return hashlib.sha256(f"{finding.rule}\0{finding.path}\0{anchor}".encode("utf-8", "replace")).hexdigest()[:24]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(path: Path, max_bytes: int) -> Tuple[Optional[str], bool]:
    """(text, is_binary). Text is None when the file is too large or unreadable."""
    try:
        if path.stat().st_size > max_bytes:
            return None, False
        data = path.read_bytes()
    except OSError:
        return None, False
    if b"\0" in data[:8192]:
        return None, True
    return data.decode("utf-8", "replace"), False


# -- what is watched -------------------------------------------------------------------------

def _sync_files() -> Dict[str, Optional[str]]:
    """Files the Interconnector would serve: relative path -> sha256 (as it hashes them)."""
    try:
        from backend.services.interconnector_file_sync_service import get_file_sync_service

        files = get_file_sync_service().scan_files(include_content=False)
    except Exception as exc:
        logger.warning("source watch: could not list the sync set: %s", exc)
        return {}
    return {f["path"]: f.get("hash") for f in files if f.get("path")}


def _checkout_files(rules) -> Dict[str, Optional[str]]:
    """Everything git tracks plus untracked files it does not ignore, limited to code-like files.

    The sync set alone misses code that never travels (a loose module beside
    the packages it imports, a test, a frontend file).
    """
    suffixes = tuple(rules.data.get("watch", {}).get("suffixes", [".py", ".js"]))
    # Only vendored trees here: git already leaves out what the clone ignores,
    # and a tracked directory named data/ or build/ can hold real code.
    skip = {"node_modules", "venv", ".venv", "__pycache__"}
    guard.engine()
    from scripts.inbound_guard.sources import git

    try:
        out = git(guard.REPO_ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    except Exception as exc:
        logger.warning("source watch: could not list the checkout: %s", exc)
        return {}
    found: Dict[str, Optional[str]] = {}
    for raw in out.split(b"\0"):
        rel = raw.decode("utf-8", "replace")
        if rel and rel.endswith(suffixes) and not (set(rel.split("/")[:-1]) & skip):
            found[rel] = None
    return found


def _surface_files(rules) -> Dict[str, Optional[str]]:
    """Untracked code surfaces from the rule data, walked without leaving them."""
    watch = rules.data.get("watch", {})
    suffixes = tuple(watch.get("suffixes", [".py", ".js"]))
    skip = set(watch.get("skip_dirs", []))
    found: Dict[str, Optional[str]] = {}
    seen_dirs: Set[str] = set()
    for surface in watch.get("surfaces", []):
        root = guard.REPO_ROOT / surface
        if root.is_file():
            found[surface] = None
            continue
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            real = os.path.realpath(dirpath)
            if real in seen_dirs:
                dirnames[:] = []
                continue
            seen_dirs.add(real)
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            for name in filenames:
                if name.endswith(suffixes):
                    rel = (Path(dirpath) / name).relative_to(guard.REPO_ROOT).as_posix()
                    found[rel] = None
    return found


# -- attribution ----------------------------------------------------------------------------

def _matches_head(paths: List[str]) -> Set[str]:
    """Which of these files are byte-identical to their HEAD version."""
    if not paths:
        return set()
    guard.engine()
    from scripts.inbound_guard.sources import git

    out = set()
    try:
        tree = git(guard.REPO_ROOT, "ls-tree", "-z", "HEAD", "--", *paths, check=False).split(b"\0")
        head = {}
        for entry in tree:
            if b"\t" in entry:
                meta, name = entry.split(b"\t", 1)
                head[name.decode("utf-8", "replace")] = meta.split()[2].decode()
        existing = [p for p in paths if p in head and (guard.REPO_ROOT / p).is_file()]
        if existing:
            blobs = git(guard.REPO_ROOT, "hash-object", "--stdin-paths",
                        stdin="\n".join(existing).encode() + b"\n").decode().split()
            out = {p for p, blob in zip(existing, blobs) if head.get(p) == blob}
    except Exception as exc:
        logger.warning("source watch: could not compare with HEAD: %s", exc)
    return out


# -- the sweep ---------------------------------------------------------------------------------

_GIT_BUSY = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "index.lock")


def _git_busy() -> bool:
    """A merge, cherry-pick, rebase or commit is under way.

    Git rewrites the working tree before it moves HEAD — here up to half a
    minute apart while hooks run and the commit is signed — so a file read in
    between matches neither side and would look out-of-band.
    """
    guard.engine()
    from scripts.inbound_guard.sources import git

    git_dir = Path(git(guard.REPO_ROOT, "rev-parse", "--path-format=absolute", "--git-dir", check=False)
                   .decode().strip() or ".")
    return any((git_dir / name).exists() for name in _GIT_BUSY)


def sweep(paths: Optional[Iterable[str]] = None) -> Dict:
    """Bring the baseline up to date and judge out-of-band changes. Needs an app context."""
    if not guard.is_on():
        return {"skipped": "guard off"}
    if _git_busy():
        # Shown in Settings, so a lock git left behind is noticed rather than silently stopping sweeps.
        skipped = {"skipped": "a git operation is in progress; the next sweep reads it",
                   "at": _now_iso()}
        _last_summary.clear()
        _last_summary.update(skipped)
        return skipped
    if not _sweep_lock.acquire(timeout=300):
        return {"skipped": "another sweep is running"}
    try:
        return _sweep(paths)
    finally:
        _sweep_lock.release()


def _sweep(paths: Optional[Iterable[str]]) -> Dict:
    from backend.models import InboundBaseline, db

    started = time.monotonic()
    rules = _rules()
    max_bytes = int(rules.data.get("watch", {}).get("max_bytes", 2 * 1024 * 1024))
    # Posture snapshots share the table under "@posture/" paths; the file sweep leaves them alone.
    rows = {r.path: r for r in db.session.query(InboundBaseline).all() if not r.path.startswith("@")}
    seeding = not rows
    full = paths is None or seeding  # the first sweep always covers everything
    if full:
        files = _checkout_files(rules)
        files.update(_sync_files())
        files.update(_surface_files(rules))
    else:
        files = {p: None for p in paths}
    summary = {"files": len(files), "changed": 0, "new": 0, "held": 0, "deleted": 0,
               "seeded": seeding, "attribution": {}}

    changed: List[Tuple[str, Path, str, os.stat_result]] = []
    for rel, known_sha in files.items():
        path = guard.REPO_ROOT / rel
        try:
            st = path.stat()
        except OSError:
            continue
        row = rows.get(rel)
        if row is not None and row.size == st.st_size and row.mtime == st.st_mtime:
            continue
        sha = known_sha or _sha256(path)
        if row is not None and row.sha256 == sha:
            row.mtime = st.st_mtime
            continue
        changed.append((rel, path, sha, st))

    head_match = _matches_head([rel for rel, *_ in changed]) if not seeding else set()

    # A held file that now matches HEAD came back through git (reverted, or the
    # same change committed and judged there): it is no longer out-of-band.
    if full:
        held_rows = {rel: row for rel, row in rows.items() if row.status == "held"}
        for rel in _matches_head(list(held_rows)):
            _accept_current(held_rows[rel], max_bytes, note="the file now matches HEAD")

    # One engine pass over every changed file. No repo is passed on purpose: the
    # watch reads untracked surfaces (custom nodes, extensions) that this clone's
    # ignore rules cover by design, and that rule is for incoming git changes.
    readable = []
    for rel, path, sha, st in changed:
        text, binary = _read(path, max_bytes)
        if text is None and not binary:
            continue
        change = guard.engine().change_from_texts(rel, None, text or "")
        change.binary = binary
        readable.append((rel, sha, st, change))
    by_path: Dict[str, list] = {}
    if readable:
        whole = guard.engine().scan([c for *_, c in readable], source="watch", subject="sweep",
                                    mode=guard.get_mode())
        for finding in whole.findings:
            by_path.setdefault(finding.path, []).append(finding)

    audit_findings = []
    for rel, sha, st, change in readable:
        row = rows.get(rel)
        findings = by_path.get(rel, [])
        prints = {_fingerprint(f, sha): f for f in findings}
        if row is None:
            row = InboundBaseline(path=rel, accepted="[]")
            db.session.add(row)
            rows[rel] = row
            summary["new"] += 1
        accepted = set(json.loads(row.accepted or "[]"))
        if seeding:
            attribution = "seed"
            audit_findings.extend(findings)
        elif rel in head_match:
            attribution = "git"
        elif row.id is None:
            attribution = "new-file"
        else:
            attribution = "out-of-band"
        summary["attribution"][attribution] = summary["attribution"].get(attribution, 0) + 1
        summary["changed"] += 1

        fresh = [f for fp, f in prints.items() if fp not in accepted]
        hold_rank = guard.engine().SEVERITY_RANK[rules.policy.get("hold_at", "medium")]
        raise_now = attribution in ("out-of-band", "new-file") and any(
            guard.engine().SEVERITY_RANK[f.severity] >= hold_rank for f in fresh)
        was_held = row.status == "held" and row.scan_id
        row.sha256, row.size, row.mtime, row.attribution = sha, st.st_size, st.st_mtime, attribution
        if was_held and not raise_now:
            guard.mark(row.scan_id, "clear", by="source watch",
                       note="the file changed again and no longer holds what was flagged")
        if raise_now:
            verdict = guard.engine().scan([change], source="watch", subject=f"{attribution} change: {rel}",
                                          mode=guard.get_mode())
            verdict.findings = fresh
            verdict.verdict = guard.engine().policy.decide(fresh, rules.policy, "watch")
            scan_id = guard.record(verdict, payload=None)
            row.status, row.scan_id = "held", scan_id
            summary["held"] += 1
            guard._emit({"kind": "verdict", "verdict": verdict.to_dict(), "scan_id": scan_id})
        else:
            row.status = "clean"
            row.accepted = json.dumps(sorted(accepted | set(prints)))

    if full:
        for rel, row in list(rows.items()):
            if rel not in files and not (guard.REPO_ROOT / rel).exists():
                if row.status == "held" and row.scan_id:
                    guard.mark(row.scan_id, "clear", by="source watch", note="the file was removed")
                db.session.delete(row)
                summary["deleted"] += 1
    db.session.commit()

    if seeding and audit_findings:
        audit = guard.engine().scan([], source="watch-audit", subject="first sweep: existing code",
                                    mode=guard.get_mode())
        audit.findings = sorted(audit_findings, key=lambda f: -guard.engine().SEVERITY_RANK[f.severity])[:500]
        audit.verdict = guard.engine().policy.decide(audit.findings, rules.policy, "watch-audit")
        audit.files = summary["changed"]
        summary["audit_scan_id"] = guard.record(audit)
        summary["audit_findings"] = len(audit_findings)
    if full:
        from backend.services import inbound_guard_posture

        try:
            summary["posture"] = inbound_guard_posture.run()
        except Exception as exc:
            logger.warning("source watch: posture checks failed: %s", exc)
    summary["seconds"] = round(time.monotonic() - started, 2)
    _last_summary.clear()
    _last_summary.update(summary, at=_now_iso())
    return summary


def accept_landed(event: dict) -> None:
    """Listener: a guarded path landed these files; they are judged, so they become the baseline."""
    if event.get("kind") != "landed":
        return
    from backend.models import InboundBaseline, db

    rules = _rules()
    max_bytes = int(rules.data.get("watch", {}).get("max_bytes", 2 * 1024 * 1024))
    for rel in event.get("paths") or []:
        path = guard.REPO_ROOT / rel
        row = db.session.query(InboundBaseline).filter_by(path=rel).first()
        if row is not None and row.status == "held" and row.scan_id:
            guard.mark(row.scan_id, "clear", by="source watch",
                       note="replaced or removed through a guarded path, which judged the change")
        if not path.is_file():
            if row is not None:
                db.session.delete(row)
            continue
        text, binary = _read(path, max_bytes)
        st = path.stat()
        sha = _sha256(path)
        prints = set()
        if text is not None:
            verdict = guard.engine().scan([guard.engine().change_from_texts(rel, None, text)], source="watch",
                                          subject=rel, mode="observe")
            prints = {_fingerprint(f, sha) for f in verdict.findings}
        if row is None:
            row = InboundBaseline(path=rel, accepted="[]")
            db.session.add(row)
        accepted = set(json.loads(row.accepted or "[]"))
        row.sha256, row.size, row.mtime = sha, st.st_size, st.st_mtime
        row.status, row.attribution = "clean", "product"
        row.accepted = json.dumps(sorted(accepted | prints))
    db.session.commit()


def _accept_current(row, max_bytes: int, note: str) -> None:
    """Take what the file holds now as judged: clear its hold and remember its findings."""
    if row.scan_id:
        guard.mark(row.scan_id, "clear", by="source watch", note=note)
    path = guard.REPO_ROOT / row.path
    text, _binary = _read(path, max_bytes)
    accepted = set(json.loads(row.accepted or "[]"))
    if text is not None:
        sha = _sha256(path)
        verdict = guard.engine().scan([guard.engine().change_from_texts(row.path, None, text)],
                                      source="watch", subject=row.path, mode="observe")
        accepted |= {_fingerprint(f, sha) for f in verdict.findings}
        st = path.stat()
        row.sha256, row.size, row.mtime = sha, st.st_size, st.st_mtime
    row.accepted = json.dumps(sorted(accepted))
    row.status, row.attribution = "clean", "git"


def approve_held(scan_id: int) -> None:
    """A person approved a held sweep finding: what the file holds now becomes accepted."""
    from backend.models import InboundBaseline, db

    for row in db.session.query(InboundBaseline).filter_by(scan_id=scan_id, status="held").all():
        row.status = "approved"
        row.mtime = None  # re-read on the next sweep, which records the accepted findings
        accepted = set(json.loads(row.accepted or "[]"))
        path = guard.REPO_ROOT / row.path
        text, _binary = _read(path, 2 * 1024 * 1024)
        if text is not None:
            verdict = guard.engine().scan([guard.engine().change_from_texts(row.path, None, text)],
                                          source="watch", subject=row.path, mode="observe")
            accepted |= {_fingerprint(f, row.sha256) for f in verdict.findings}
        row.accepted = json.dumps(sorted(accepted))
    db.session.commit()


def held_paths(paths: Iterable[str]) -> Set[str]:
    from backend.models import InboundBaseline, db

    wanted = list(paths)
    held = set()
    for start in range(0, len(wanted), 500):
        chunk = wanted[start:start + 500]
        held |= {r.path for r in db.session.query(InboundBaseline.path)
                 .filter(InboundBaseline.path.in_(chunk), InboundBaseline.status == "held").all()}
    return held


def filter_for_sync(files: List[dict]) -> Tuple[List[dict], List[str]]:
    """What the master may serve. Returns (files to send, paths held back).

    The served files are swept first, so a change made since the last sweep is
    judged before it leaves. Observing, nothing is withheld but the held paths
    are still reported.
    """
    if not guard.is_on() or not files:
        return files, []
    paths = [f["path"] for f in files if f.get("path")]
    try:
        sweep(paths)
        held = held_paths(paths)
    except Exception as exc:
        logger.error("source watch: sweep before sync failed: %s", exc, exc_info=True)
        if guard.get_mode() == "enforce":
            return [], paths  # cannot vouch for anything; send nothing
        return files, []
    if guard.get_mode() != "enforce":
        return files, sorted(held)
    return [f for f in files if f.get("path") not in held], sorted(held)


def last_summary() -> Dict:
    return dict(_last_summary)


def start_background(app) -> None:
    """Sweep on an interval while the guard is on. One thread per process, started only once it is on."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with app.app_context():
        if not guard.is_on():
            return

    def loop():
        time.sleep(60)  # let startup finish
        while True:
            interval = 600
            try:
                with app.app_context():
                    interval = int(_rules().data.get("watch", {}).get("interval_seconds", 600))
                    if guard.is_on():
                        summary = sweep()
                        if summary.get("held"):
                            logger.warning("source watch: %s change(s) held for review", summary["held"])
            except Exception as exc:
                logger.error("source watch: sweep failed: %s", exc, exc_info=True)
            time.sleep(max(60, interval))

    _thread = threading.Thread(target=loop, name="inbound-guard-watch", daemon=True)
    _thread.start()


guard.register_inbound_listener("source-watch-baseline", accept_landed)
