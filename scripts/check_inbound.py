#!/usr/bin/env python3
"""Read code coming in and say whether it may land.

The counterpart of check_portable.sh: that guard watches what leaves this clone,
this one reads what arrives. Stdlib only, so it runs from git hooks and CI with
any python3.

Usage:
  scripts/check_inbound.py scan --range A..B      what B adds over A (A...B: since they split)
  scripts/check_inbound.py scan --staged          what the index adds over HEAD
  scripts/check_inbound.py scan --worktree        uncommitted edits to tracked files
  scripts/check_inbound.py scan --text NEW --as PATH
                                                  NEW as the new content of PATH
      [--json | --github] [--record] [--exit-zero] [--source NAME]
  scripts/check_inbound.py approve DIGEST --note TEXT [--override-block]
                                                  let exactly that held change land
  scripts/check_inbound.py log [-n N] [--json]    recent verdicts and approvals
  scripts/check_inbound.py mode                   the mode git hooks use

Git hooks (installed by scripts/install_hooks.sh, which pins a copy of this
engine in the git directory so incoming code is judged by the engine you had):
  check_inbound.py hook pre-merge-commit
  check_inbound.py hook reference-transaction STATE   (ref updates on stdin)

The mode comes from GUAARDVARK_INBOUND_GUARD, then `git config inboundguard.mode`,
and is off when neither is set:
  off       hooks do nothing
  observe   hooks record verdicts and print findings; nothing is refused
  enforce   a merge that would be held or blocked is refused until approved

Exit codes for scan: 0 allow, 1 hold or block, 2 usage or git error. The
pre-merge-commit hook exits 3 to refuse; its shell wrapper turns any other
failure into a pass, so a fault in the guard never stops git.
"""
from __future__ import annotations

import argparse
import fnmatch
import getpass
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inbound_guard import RuleSet, Verdict, change_from_texts, git_mode, scan, scan_diff  # noqa: E402
from inbound_guard import ledger  # noqa: E402
from inbound_guard.sources import ZERO_OID, GitError, git, git_common_dir, git_ok, repo_root  # noqa: E402

DEFAULT_REFS = ("refs/heads/main", "refs/remotes/origin/main")
# A hook's "refuse" answer. Distinct from 1, which a crashing interpreter also
# returns: the shell hooks map only this code to a refusal and let anything else
# through, so a broken engine never stops git.
EXIT_REFUSE = 3
ENGINE_FILES = ("check_inbound.py", "inbound_guard/*.py", "inbound_guard/*.json")
_MARK = {"critical": "✗✗", "high": "✗", "medium": "!", "low": "·", "info": "·"}


# -- output ------------------------------------------------------------------

def render_text(v: Verdict, *, verbose: bool = True) -> str:
    lines = [f"inbound guard — {v.source}: {v.subject}"]
    acted = {"off": "guard off; shown only", "observe": "observe: recorded, not enforced",
             "enforce": "enforced"}.get(v.mode, v.mode)
    lines.append(f"  verdict: {v.verdict.upper()} ({acted}) · {v.files} file(s), +{v.added_lines} line(s)"
                 f" · digest {v.digest}")
    if verbose:
        for f in v.findings:
            where = f"{f.path}:{f.line}" if f.line else f.path
            lines.append(f"  {_MARK.get(f.severity, '?')} {f.severity:<8} {f.rule:<26} {where}")
            if f.excerpt:
                lines.append(f"        {f.excerpt}")
            lines.append(f"        {f.why}")
    for err in v.errors:
        lines.append(f"  note: {err}")
    return "\n".join(lines)


def _gh_escape(text: str, prop: bool = False) -> str:
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        text = text.replace(":", "%3A").replace(",", "%2C")
    return text


def render_github(v: Verdict) -> str:
    """Workflow-command annotations. Every value is escaped: excerpts are PR content."""
    level = {"critical": "error", "high": "warning", "medium": "warning", "low": "notice", "info": "notice"}
    out = []
    for f in v.findings:
        props = f"file={_gh_escape(f.path, True)}"
        if f.line:
            props += f",line={f.line}"
        props += f",title={_gh_escape(f'{f.severity} {f.rule}', True)}"
        message = f.why + (f" — {f.excerpt}" if f.excerpt else "")
        out.append(f"::{level[f.severity]} {props}::{_gh_escape(message)}")
    out.append(f"Inbound check: {v.verdict} ({len(v.findings)} finding(s), {v.files} file(s), +{v.added_lines} lines)")
    return "\n".join(out)


def github_summary(v: Verdict) -> str:
    def cell(s: str) -> str:
        return s.replace("|", "\\|").replace("`", "'").replace("\n", " ")

    rows = [f"### Inbound check: **{v.verdict}**", "",
            f"{v.files} file(s), +{v.added_lines} added line(s), {len(v.findings)} finding(s). "
            "Findings are for a maintainer to read; this check does not fail the pull request.", ""]
    if v.findings:
        rows += ["| severity | rule | where | why |", "|---|---|---|---|"]
        for f in v.findings:
            where = f"{f.path}:{f.line}" if f.line else f.path
            rows.append(f"| {f.severity} | {cell(f.rule)} | {cell(where)} | {cell(f.why)} |")
    return "\n".join(rows) + "\n"


# -- helpers -------------------------------------------------------------------

def _ledger(repo: Path) -> Path:
    return ledger.ledger_path(git_common_dir(repo))


def _record(repo: Path, v: Verdict, **extra) -> None:
    """Append a verdict to the ledger.

    Git records carry ``old_sha`` and ``new_sha`` when both commits are known,
    so a reader can list exactly what arrived with ``git log old_sha..new_sha``.
    """
    data = v.to_dict()
    data["kind"] = "verdict"
    data.update(extra)
    try:
        ledger.append(_ledger(repo), data)
    except OSError as exc:
        print(f"inbound guard: could not write the ledger: {exc}", file=sys.stderr)


def _who() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "unknown")


def _engine_hint() -> str:
    return f"python3 {Path(__file__).resolve()}"


def engine_fingerprint(scripts_dir: Path) -> str:
    """Hash of the engine files under a scripts directory (tracked or installed)."""
    h = hashlib.sha256()
    for pattern in ENGINE_FILES:
        for path in sorted(scripts_dir.glob(pattern)):
            h.update(path.relative_to(scripts_dir).as_posix().encode() + b"\0")
            h.update(path.read_bytes())
    return h.hexdigest()


def _warn_if_engine_drifted(repo: Path) -> None:
    """Say so when the tracked guard no longer matches the copy that is judging."""
    installed = Path(__file__).resolve().parent
    tracked = repo / "scripts"
    if installed == tracked.resolve() or not (tracked / "check_inbound.py").is_file():
        return
    if engine_fingerprint(installed) != engine_fingerprint(tracked):
        print("inbound guard: the tracked guard differs from the installed copy that judged this; "
              "after reading the change, run scripts/install_hooks.sh", file=sys.stderr)


def _rev(repo: Path, rev: str) -> Optional[str]:
    if not rev or rev.startswith("-"):
        return None
    out = git(repo, "rev-parse", "--verify", "--quiet", rev, check=False).decode().strip()
    return out or None


def _range_shas(repo: Path, revs: str) -> dict:
    """``{"old_sha", "new_sha"}`` for an ``A..B`` or ``A...B`` range whose sides resolve, else {}."""
    sides = revs.split("...", 1) if "..." in revs else revs.split("..", 1)
    if len(sides) != 2:
        return {}
    old, new = _rev(repo, sides[0] or "HEAD"), _rev(repo, sides[1] or "HEAD")
    return {"old_sha": old, "new_sha": new} if old and new else {}


# -- commands ------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> int:
    repo = repo_root()
    rules = RuleSet.load()
    mode = args.mode or "observe"
    try:
        if args.text:
            target = repo / args.as_path
            old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
            new = Path(args.text).read_text(encoding="utf-8", errors="replace")
            change = change_from_texts(args.as_path, old, new)
            v = scan([change], source=args.source, subject=f"{args.as_path} (from {args.text})", mode=mode,
                     rules=rules, repo=repo, budget_seconds=args.budget)
        else:
            if args.range:
                diff_args, subject = [args.range], args.range
            elif args.staged:
                diff_args, subject = ["--cached"], "staged changes"
            else:
                diff_args, subject = ["HEAD"], "working tree"
            v = scan_diff(repo, diff_args, source=args.source, subject=subject, mode=mode, rules=rules,
                          budget_seconds=args.budget, max_added_lines=args.max_lines)
    except (GitError, OSError) as exc:
        print(f"check_inbound.py: {exc}", file=sys.stderr)
        return 2

    if args.record:
        _record(repo, v, **(_range_shas(repo, args.range) if args.range else {}))
    if args.json:
        print(json.dumps(v.to_dict(), indent=2))
    elif args.github:
        print(render_github(v))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(github_summary(v))
    else:
        print(render_text(v))
        if v.verdict != "allow":
            print(f"\n  To let exactly this change land: {_engine_hint()} approve {v.digest} --note \"why\"")
    return 0 if args.exit_zero or v.verdict == "allow" else 1


def cmd_approve(args: argparse.Namespace) -> int:
    repo = repo_root()
    path = _ledger(repo)
    last = None
    for record in ledger.records(path):
        if record.get("kind") == "verdict" and record.get("digest") == args.digest:
            last = record
    if last is None:
        print(f"check_inbound.py: no verdict recorded for {args.digest}; run the scan with --record first",
              file=sys.stderr)
        return 2
    if last.get("verdict") == "block" and not args.override_block:
        print("check_inbound.py: that change was blocked, not held. Read the findings; if you are certain,"
              " repeat with --override-block.", file=sys.stderr)
        return 1
    ledger.append(path, {"kind": "approval", "digest": args.digest, "by": _who(), "note": args.note,
                         "subject": last.get("subject"), "verdict": last.get("verdict")})
    print(f"✓ approved {args.digest} ({last.get('subject')}) — {args.note}")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    items = ledger.recent(_ledger(repo_root()), args.n)
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    if not items:
        print("No inbound verdicts recorded yet.")
        return 0
    for r in items:
        if r.get("kind") == "approval":
            print(f"{r.get('ts', '')}  approval  {r.get('digest')}  by {r.get('by')}: {r.get('note')}")
            continue
        top = r.get("top_severity") or "-"
        print(f"{r.get('ts', '')}  {r.get('verdict', '?'):<5} {r.get('mode', ''):<8} {r.get('source', ''):<12}"
              f" {len(r.get('findings', [])):>3} finding(s), worst {top:<8} {r.get('subject', '')}"
              f"  [{r.get('digest', '')}]")
    return 0


def cmd_mode(_args: argparse.Namespace) -> int:
    print(git_mode(repo_root()))
    return 0


# -- hooks ---------------------------------------------------------------------

def hook_pre_merge_commit() -> int:
    """Judge a merge whose result sits in the index, before its commit exists."""
    repo = repo_root()
    mode = git_mode(repo)
    if mode == "off":
        return 0
    # Git writes MERGE_HEAD only after this hook passes, so during an automatic
    # merge the incoming side is named by the reflog action ("merge <branch>").
    # When a stopped merge is concluded through pre-commit, MERGE_HEAD exists.
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).decode().strip() or "HEAD"
    merge_head = _rev(repo, "MERGE_HEAD")
    incoming = merge_head[:9] if merge_head else os.environ.get("GIT_REFLOG_ACTION", "merge").replace("merge ", "", 1)
    subject = f"merge {incoming} into {branch}"
    v = scan_diff(repo, ["--cached"], source="git-merge", subject=subject, mode=mode)
    approval = ledger.approval_for(_ledger(repo), v.digest)
    shas = {"old_sha": _rev(repo, "HEAD"), "new_sha": merge_head or _rev(repo, incoming)}
    _record(repo, v, approved=bool(approval), **{k: sha for k, sha in shas.items() if sha})

    if v.verdict == "allow" and not v.findings:
        print(f"✓ inbound guard: {subject} — nothing to hold ({v.files} file(s))", file=sys.stderr)
        return 0
    print(render_text(v), file=sys.stderr)
    _warn_if_engine_drifted(repo)
    if mode != "enforce" or v.verdict == "allow":
        return 0
    if approval:
        print(f"  approved by {approval.get('by')}: {approval.get('note')}", file=sys.stderr)
        return 0
    print(
        "\nRefusing the merge commit: the inbound guard holds it for review. Nothing was committed and the"
        "\nmerge is still in progress. Read the findings above, then either"
        f"\n  approve exactly this change:  {_engine_hint()} approve {v.digest} --note \"why\""
        "\n                               and finish with: git commit --no-edit"
        "\n  or drop it:                   git merge --abort",
        file=sys.stderr,
    )
    return EXIT_REFUSE


def _wanted_refs(repo: Path) -> List[str]:
    out = git(repo, "config", "--get-all", "inboundguard.ref", check=False).decode().split()
    return out or list(DEFAULT_REFS)


def _parse_updates(lines: Sequence[str]) -> List[Tuple[str, str, str]]:
    updates = []
    for line in lines:
        parts = line.split()
        if len(parts) == 3 and not parts[0].startswith("ref:") and not parts[1].startswith("ref:"):
            updates.append((parts[0], parts[1], parts[2]))
    return updates


def hook_reference_transaction(state: str, lines: Sequence[str]) -> int:
    """Record what a ref update brought in. Runs after the update; never refuses one.

    Only the "committed" state is used: git ignores this hook's exit status there,
    so a fault here can never wedge a commit, fetch or checkout in any worktree.
    """
    if state != "committed":
        return 0
    repo = repo_root()
    mode = git_mode(repo)
    if mode == "off":
        return 0
    wanted = _wanted_refs(repo)
    rules = RuleSet.load()
    budget = float(rules.caps.get("hook_seconds", 2.0))
    for old, new, ref in _parse_updates(lines):
        if not any(fnmatch.fnmatchcase(ref, pattern) for pattern in wanted):
            continue
        if new == ZERO_OID:
            continue
        if old == ZERO_OID:
            old = _rev(repo, f"{ref}@{{1}}") or ""
        if not old or old == new:
            continue
        if git_ok(repo, "merge-base", "--is-ancestor", new, old):
            continue  # moved backwards: nothing new arrived
        short = ref.replace("refs/heads/", "").replace("refs/remotes/", "")
        source = "fetch" if ref.startswith("refs/remotes/") else "branch-update"
        v = scan_diff(repo, [old, new], source=source, subject=f"{short} {old[:9]}..{new[:9]}", mode=mode,
                      rules=rules, budget_seconds=budget)
        already_shown = v.digest in ledger.recent_digests(_ledger(repo))
        _record(repo, v, ref=ref, old_sha=old, new_sha=new)
        if v.verdict != "allow" and not already_shown:
            worst = v.findings[0]
            where = f"{worst.path}:{worst.line}" if worst.line else worst.path
            print(f"inbound guard: {short} {old[:9]}..{new[:9]} — {v.verdict.upper()}, "
                  f"{len(v.findings)} finding(s); worst {worst.severity} {worst.rule} at {where}\n"
                  f"  details: python3 scripts/check_inbound.py log  ·  "
                  f"rescan: python3 scripts/check_inbound.py scan --range {old[:12]}..{new[:12]}",
                  file=sys.stderr)
            _warn_if_engine_drifted(repo)
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    print(engine_fingerprint(Path(args.scripts_dir)))
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    """Hooks fail open: an error in the guard must never stop git working."""
    try:
        if args.name == "pre-merge-commit":
            return hook_pre_merge_commit()
        if args.name == "reference-transaction":
            lines = sys.stdin.read().splitlines()
            return hook_reference_transaction(args.state or "", lines)
        print(f"check_inbound.py: unknown hook {args.name}", file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001 - see docstring
        print(f"inbound guard: skipped ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="check_inbound.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="judge a range, the index, the working tree or one file")
    what = p.add_mutually_exclusive_group(required=True)
    what.add_argument("--range", metavar="REVS")
    what.add_argument("--staged", action="store_true")
    what.add_argument("--worktree", action="store_true")
    what.add_argument("--text", metavar="NEW")
    p.add_argument("--as", dest="as_path", metavar="PATH")
    p.add_argument("--source", default="cli")
    p.add_argument("--mode", choices=["off", "observe", "enforce"])
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--github", action="store_true")
    p.add_argument("--record", action="store_true", help="append the verdict to the ledger")
    p.add_argument("--exit-zero", action="store_true", help="report only; always exit 0")
    p.add_argument("--budget", type=float, default=None, metavar="SECONDS")
    p.add_argument("--max-lines", type=int, default=None, metavar="N", help="added lines to read (default from the rules)")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("approve", help="let one held change land")
    p.add_argument("digest")
    p.add_argument("--note", required=True)
    p.add_argument("--override-block", action="store_true")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("log", help="recent verdicts and approvals")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("mode", help="print the mode git hooks use")
    p.set_defaults(func=cmd_mode)

    p = sub.add_parser("fingerprint", help=argparse.SUPPRESS)
    p.add_argument("scripts_dir")
    p.set_defaults(func=cmd_fingerprint)

    p = sub.add_parser("hook", help=argparse.SUPPRESS)
    p.add_argument("name")
    p.add_argument("state", nargs="?")
    p.set_defaults(func=cmd_hook)

    args = parser.parse_args(argv)
    if args.command == "scan" and args.text and not args.as_path:
        parser.error("--text needs --as PATH")
    try:
        return args.func(args)
    except GitError as exc:
        print(f"check_inbound.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
