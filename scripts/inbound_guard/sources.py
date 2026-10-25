"""Turn git diffs and in-memory edits into ``Change`` lists.

Everything here only reads: diffs, blobs and ignore rules. Nothing touches the
index, the working tree or a ref, which is what makes it safe to call from a
reference-transaction hook.
"""
from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .model import Change

ZERO_OID = "0" * 40
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Pinned so a user's diff configuration (external diff drivers, textconv,
# noprefix, colour) can never change what the rules read.
_DIFF_FLAGS = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "-M",
)


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, stdin: Optional[bytes] = None, check: bool = True) -> bytes:
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=str(repo),
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def git_ok(repo: Path, *args: str) -> bool:
    """Whether a git command succeeds, for questions answered by exit status."""
    return subprocess.run(["git", *args], cwd=str(repo), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def repo_root(start: Optional[Path] = None) -> Path:
    out = git(start or Path.cwd(), "rev-parse", "--show-toplevel")
    return Path(out.decode().strip())


def git_common_dir(repo: Path) -> Path:
    out = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(out.decode().strip())


def _parse_raw(raw: bytes) -> List[Change]:
    """Parse ``git diff --raw -z`` into Changes carrying modes, blobs and paths."""
    parts = raw.split(b"\0")
    changes: List[Change] = []
    i = 0
    while i < len(parts):
        head = parts[i]
        if not head.startswith(b":"):
            i += 1
            continue
        fields = head[1:].decode().split()
        old_mode, new_mode, old_oid, new_oid, status = fields[:5]
        letter = status[0]
        if letter in ("R", "C"):
            old_path = parts[i + 1].decode("utf-8", "replace")
            path = parts[i + 2].decode("utf-8", "replace")
            i += 3
        else:
            old_path = None
            path = parts[i + 1].decode("utf-8", "replace")
            i += 2
        changes.append(
            Change(
                path=path,
                status=letter,
                old_path=old_path,
                old_mode=None if old_mode == "000000" else old_mode,
                new_mode=None if new_mode == "000000" else new_mode,
                new_blob=None if letter == "D" else new_oid,
                old_blob=None if letter == "A" or old_oid == ZERO_OID else old_oid,
            )
        )
    return changes


def _fill_from_patch(changes: List[Change], patch: str, max_added: int) -> int:
    """Attach added/removed lines to each Change. Returns the added-line count.

    Patch sections come out in the same order as ``--raw`` entries for the same
    arguments, so the n-th ``diff --git`` header belongs to the n-th Change. Lines
    are only read as headers outside a hunk: an added line whose text starts with
    "++" looks exactly like a "+++" header otherwise.
    """
    index = -1
    current: Optional[Change] = None
    in_hunk = False
    new_line = 0
    total = 0
    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            index += 1
            current = changes[index] if index < len(changes) else None
            in_hunk = False
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            match = _HUNK.match(line)
            new_line = int(match.group(1)) if match else 0
            in_hunk = True
            continue
        if not in_hunk:
            if line.startswith("Binary files ") or line == "GIT binary patch":
                current.binary = True
            continue
        if line.startswith("+"):
            if total >= max_added:
                current.truncated = True
            else:
                current.added.append((new_line, line[1:]))
                total += 1
            new_line += 1
        elif line.startswith("-"):
            if total < max_added:
                current.removed.append(line[1:])
    return total


def changes_from_diff(repo: Path, diff_args: Sequence[str], max_added: int = 1000000) -> Tuple[List[Change], int]:
    """Changes for ``git diff <diff_args>``, e.g. ``["A...B"]`` or ``["--cached", "HEAD"]``."""
    raw = git(repo, "diff", "--raw", "-z", "--no-abbrev", "-M", *diff_args)
    changes = _parse_raw(raw)
    if not changes:
        return [], 0
    patch = git(repo, "diff", "--unified=0", *_DIFF_FLAGS, *diff_args).decode("utf-8", "replace")
    total = _fill_from_patch(changes, patch, max_added)
    return changes, total


def change_from_texts(path: str, old_text: Optional[str], new_text: Optional[str]) -> Change:
    """A Change for one in-memory edit (self-code applies, CLI --text)."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    if old_text is None:
        status = "A"
    elif new_text is None:
        status = "D"
    else:
        status = "M"
    change = Change(path=path, status=status, new_mode=None if status == "D" else "100644",
                    old_text=old_text, new_text=new_text)
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            change.removed.extend(old_lines[a0:a1])
        if tag in ("replace", "insert"):
            change.added.extend((n + 1, new_lines[n]) for n in range(b0, b1))
    return change


def blob_text(repo: Optional[Path], change: Change, which: str = "new") -> Optional[str]:
    """The full old or new content of a changed file, for rules that need a whole file.

    In-memory texts win. A zero new blob id means the content is only in the
    working tree (an unstaged diff), so it is read from disk.
    """
    if change.binary:
        return None
    if which == "old":
        if change.old_text is not None or change.status == "A":
            return change.old_text
        oid = change.old_blob
    else:
        if change.new_text is not None or change.status == "D":
            return change.new_text
        oid = change.new_blob
    if repo is None:
        return None
    try:
        if oid and oid != ZERO_OID:
            return git(repo, "cat-file", "blob", oid).decode("utf-8", "replace")
        if which == "new":
            target = repo / change.path
            if target.is_file() and not target.is_symlink():
                return target.read_text(encoding="utf-8", errors="replace")
    except (GitError, OSError):
        return None
    return None


def ignored_paths(repo: Path, paths: Iterable[str]) -> Set[str]:
    """Which of ``paths`` this clone's ignore rules cover, tracked or not.

    An incoming file at such a path replaces a local file git treats as
    expendable: the clone's private notes or its own CLAUDE.md.
    """
    listed = [p for p in paths if p]
    if not listed:
        return set()
    out = git(
        repo,
        "check-ignore",
        "--no-index",
        "--stdin",
        "-z",
        stdin=b"\0".join(p.encode() for p in listed) + b"\0",
        check=False,
    )
    return {p.decode("utf-8", "replace") for p in out.split(b"\0") if p}
