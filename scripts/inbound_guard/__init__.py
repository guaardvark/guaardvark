"""Inbound guard: judge code before it lands.

The portability guard (``scripts/check_portable.sh``) watches what leaves this
clone; this watches what comes in — merges, fetched branches, pull requests, and
edits the product makes to its own code. It reads only the lines a change adds
(and, for guards, the lines it removes) and returns a verdict:

- allow: nothing at or above the hold threshold
- hold:  something a person should read before it lands
- block: something no review should be needed to refuse

Stdlib only, so git hooks, CI and the backend run the same rules. Callers decide
what a verdict means for them through the mode (off, observe, enforce).

    from inbound_guard import scan, change_from_texts
    verdict = scan([change_from_texts("app.py", old, new)], source="self_code",
                   subject="edit_code app.py", mode="enforce")
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from .model import MODES, SEVERITIES, SEVERITY_RANK, VERDICTS, Change, Finding, Verdict
from .policy import decide, git_mode, normalize_mode
from .rules import RuleSet
from .sources import blob_text, change_from_texts, changes_from_diff, ignored_paths

ENGINE_VERSION = "1"

# A scanner contributed by an extension: (changes, context) -> findings. It may
# add findings, never remove them, and a failure is recorded, not raised.
Scanner = Callable[[Sequence[Change], dict], Iterable[Finding]]

__all__ = [
    "ENGINE_VERSION", "MODES", "SEVERITIES", "SEVERITY_RANK", "VERDICTS",
    "Change", "Finding", "Verdict", "RuleSet", "Scanner",
    "scan", "scan_diff", "change_from_texts", "change_digest", "git_mode", "normalize_mode",
]


def change_digest(changes: Sequence[Change]) -> str:
    """A stable id for exactly this set of changes, used to record an approval."""
    h = hashlib.sha256()
    for c in sorted(changes, key=lambda c: c.path):
        h.update(f"{c.path}\0{c.status}\0{c.new_mode or ''}\0{c.new_blob or ''}\0".encode())
        if not c.new_blob:
            for number, text in c.added:
                h.update(f"+{number}:{text}\n".encode("utf-8", "surrogatepass"))
            for text in c.removed:
                h.update(f"-{text}\n".encode("utf-8", "surrogatepass"))
    return h.hexdigest()[:20]


def scan(
    changes: Sequence[Change],
    *,
    source: str,
    subject: str,
    mode: str,
    rules: Optional[RuleSet] = None,
    repo: Optional[Path] = None,
    budget_seconds: Optional[float] = None,
    scanners: Sequence[Tuple[str, Scanner]] = (),
    context: Optional[dict] = None,
) -> Verdict:
    rules = rules or RuleSet.load()
    deadline = time.monotonic() + budget_seconds if budget_seconds else None
    ignored = ignored_paths(repo, (c.path for c in changes)) if repo else set()

    def loader(change: Change, which: str = "new") -> Optional[str]:
        return blob_text(repo, change, which)

    findings, notes = rules.evaluate(changes, blob_loader=loader, ignored=ignored, deadline=deadline,
                                     source=source)

    errors: List[str] = list(notes)
    providers: List[str] = []
    for name, scanner in scanners:
        try:
            extra = list(scanner(changes, dict(context or {}, source=source, subject=subject)))
        except Exception as exc:  # a provider never decides whether the core rules ran
            errors.append(f"provider {name} failed: {exc}")
            continue
        providers.append(name)
        findings.extend(f for f in extra if isinstance(f, Finding) and f.severity in SEVERITY_RANK)

    findings.sort(key=lambda f: (-SEVERITY_RANK[f.severity], f.path, f.line or 0))
    return Verdict(
        verdict=decide(findings, rules.policy, source),
        mode=normalize_mode(mode),
        source=source,
        subject=subject,
        digest=change_digest(changes),
        findings=findings,
        providers=providers,
        errors=errors,
        files=len(changes),
        added_lines=sum(len(c.added) for c in changes),
        engine_version=f"{ENGINE_VERSION}.{rules.version}",
    )


def scan_diff(repo: Path, diff_args: Sequence[str], **kwargs) -> Verdict:
    """Scan ``git diff <diff_args>`` in ``repo``."""
    rules = kwargs.pop("rules", None) or RuleSet.load()
    max_added = kwargs.pop("max_added_lines", None) or int(rules.caps.get("max_added_lines", 1000000))
    changes, _ = changes_from_diff(repo, diff_args, max_added)
    return scan(changes, rules=rules, repo=repo, **kwargs)
