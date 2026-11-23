"""A guarded edit lands on the text that was quoted, or not at all, and gives
the file back as it found it."""
import hashlib

import pytest

from backend.services import guarded_code_service as guarded
from backend.services.guarded_code_service import GuardedCodeError, apply_exact_replacement

SOURCE = (
    "def total(items):\n"
    "    out = 0\n"
    "    for item in items:\n"
    "        if item.refunded:\n"
    "            continue\n"
    "        out += item.price\n"
    "    return out\n"
)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(guarded, "is_codebase_locked", lambda: False)
    monkeypatch.setattr(guarded, "_inbound_gate", lambda *a, **k: None)
    (tmp_path / "calc.py").write_text(SOURCE)
    return tmp_path


def test_a_stale_quote_is_refused_not_matched_to_a_similar_block(repo):
    stale = "    for item in items:\n        out += item.price\n    return out\n"
    with pytest.raises(GuardedCodeError) as refused:
        apply_exact_replacement(str(repo / "calc.py"), stale, stale.replace("price", "price * 2"), repo_root=repo)
    assert refused.value.code == "TEXT_NOT_FOUND"
    assert (repo / "calc.py").read_text() == SOURCE


def test_a_quote_with_shifted_indentation_is_placed_and_reindented(repo):
    quoted = "if item.refunded:\n    continue\nout += item.price\n"
    result = apply_exact_replacement(
        str(repo / "calc.py"), quoted, quoted.replace("item.price", "item.price * 2"), repo_root=repo)
    assert result.verification["match"] == "whitespace"
    assert "        out += item.price * 2\n" in (repo / "calc.py").read_text()
    compile((repo / "calc.py").read_text(), "calc.py", "exec")


def test_approved_text_is_applied_exactly_or_not_at_all(repo):
    quoted = "if item.refunded:\n    continue\n"
    with pytest.raises(GuardedCodeError) as refused:
        apply_exact_replacement(str(repo / "calc.py"), quoted, "pass\n", repo_root=repo, allow_reindent=False)
    assert refused.value.code == "TEXT_NOT_FOUND"


def test_a_syntax_error_is_refused_before_the_file_is_touched(repo):
    with pytest.raises(GuardedCodeError) as refused:
        apply_exact_replacement(str(repo / "calc.py"), "    out = 0\n", "    out = = 0\n", repo_root=repo)
    assert refused.value.code == "SYNTAX_CHECK_FAILED"
    assert (repo / "calc.py").read_text() == SOURCE
    assert not (repo / "calc.py.backup").exists()


def test_crlf_files_keep_their_line_endings(repo):
    target = repo / "win.py"
    target.write_bytes(b"a = 1\r\nb = 2\r\n")
    apply_exact_replacement(str(target), "b = 2\n", "b = 3\n", repo_root=repo)
    assert target.read_bytes() == b"a = 1\r\nb = 3\r\n"
    assert (repo / "win.py.backup").read_bytes() == b"a = 1\r\nb = 2\r\n"


def test_drift_guards_fire(repo):
    target = repo / "calc.py"
    read_hash = hashlib.sha256(target.read_text().encode()).hexdigest()
    read_mtime = target.stat().st_mtime
    apply_exact_replacement(str(target), "out = 0", "out = 1", repo_root=repo, expected_hash=read_hash)
    with pytest.raises(GuardedCodeError) as stale_hash:
        apply_exact_replacement(str(target), "out = 1", "out = 2", repo_root=repo, expected_hash=read_hash)
    assert stale_hash.value.code == "FILE_DRIFT_DETECTED"
    with pytest.raises(GuardedCodeError) as stale_mtime:
        apply_exact_replacement(str(target), "out = 1", "out = 2", repo_root=repo, expected_mtime=read_mtime - 5)
    assert stale_mtime.value.code == "FILE_DRIFT_DETECTED"


def test_an_unparsed_file_type_says_syntax_was_not_checked(repo):
    (repo / "notes.md").write_text("hello world, this is text\n")
    result = apply_exact_replacement(str(repo / "notes.md"), "hello world", "goodbye", repo_root=repo, dry_run=True)
    assert result.verification["syntax_checked"] is False
    assert "not checked" in result.verification["output_summary"]
