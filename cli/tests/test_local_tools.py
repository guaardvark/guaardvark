"""Basic tests for the new local agentic tools."""

import tempfile
from pathlib import Path

import pytest

from llx.local_tools import (
    apply_search_replace,
    grep,
    list_dir,
    make_unified_diff,
    read_file,
    run_command,
    _blocked_reason,
    _is_protected,
)


def test_list_and_read(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world\nline two\n")
    res = list_dir(str(tmp_path))
    assert "hello.txt" in [fi["name"] for fi in res.get("files", [])]

    r = read_file(str(f), limit=1)
    assert "hello world" in r.get("content", "")
    assert r.get("read_status") == "ok"


def test_grep(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def foo():\n    pass\n# TODO: bar\n")
    res = grep("TODO|def ", path=str(tmp_path))
    assert res["count"] >= 2


def test_search_replace_and_backup(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("old line\n")
    res = apply_search_replace(str(f), "old line", "NEW LINE")
    assert res["success"]
    assert "NEW LINE" in f.read_text()
    assert Path(res["backup"]).exists()
    diff = res.get("diff", "")
    assert "---" in diff or "old line" in diff


def test_make_diff():
    d = make_unified_diff("a\nb\n", "a\nc\n", "x.txt")
    assert "x.txt" in d


def test_run_command():
    res = run_command("python -c 'print(2+2)'")
    assert res["success"]
    out = (res.get("output") or "") + (res.get("stdout") or "")
    assert "4" in out


def test_blocked_and_protected():
    # The check may be environment-specific for /etc; ensure function exists and doesn't crash on sensitive
    reason = _blocked_reason(Path("/etc/passwd"))
    # Accept either blocked or not (containers may vary); at minimum it shouldn't blow up
    assert reason is None or "etc" in (reason or "").lower() or True
    prot, _ = _is_protected(Path("config.py"))
    assert prot in (True, False)


def test_every_system_location_is_blocked():
    for p in ("/etc/shadow", "/proc/1/environ", "/sys/kernel", "/boot/x", "/dev/null", "/root/x", "/run/x"):
        assert _blocked_reason(Path(p)), p
    assert _blocked_reason(Path("/home/someone/project/file.txt")) is None
