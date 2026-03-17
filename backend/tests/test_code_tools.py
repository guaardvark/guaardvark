"""Unit tests for code manipulation tools (sandbox-based, no LLM)."""

import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tests.conftest_sandbox import sandbox_dir, sandbox_file


def _patch_root(mod, new_root):
    """Patch PROJECT_ROOT and return original for cleanup."""
    original = mod.PROJECT_ROOT
    mod.PROJECT_ROOT = Path(new_root)
    return original


class TestReadCode:
    """Tests for read_code() — backend/tools/llama_code_tools.py:29"""

    def test_read_existing_file(self, sandbox_file):
        from backend.tools.llama_code_tools import read_code
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_file.parent)
        try:
            result = read_code(sandbox_file.name)
            assert "hello" in result
            assert "world" in result
        finally:
            mod.PROJECT_ROOT = orig

    def test_read_nonexistent_file(self, sandbox_dir):
        from backend.tools.llama_code_tools import read_code
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_dir)
        try:
            result = read_code("nonexistent_file_xyz.py")
            assert (
                "error" in result.lower()
                or "not found" in result.lower()
                or "does not exist" in result.lower()
            )
        finally:
            mod.PROJECT_ROOT = orig

    def test_read_rejects_path_traversal(self, sandbox_dir):
        from backend.tools.llama_code_tools import read_code
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_dir)
        try:
            result = read_code("../../etc/passwd")
            # Should either reject or not return /etc/passwd content
            assert "root:" not in result
        finally:
            mod.PROJECT_ROOT = orig


class TestSearchCode:
    """Tests for search_code() — backend/tools/llama_code_tools.py:77"""

    def test_search_finds_pattern(self, sandbox_dir):
        from backend.tools.llama_code_tools import search_code
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_dir)
        try:
            result = search_code("def hello", "**/*.py")
            assert "hello" in result
        finally:
            mod.PROJECT_ROOT = orig

    def test_search_no_matches(self, sandbox_dir):
        from backend.tools.llama_code_tools import search_code
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_dir)
        try:
            result = search_code("xyznonexistentpattern123", "**/*.py")
            assert (
                "no matches" in result.lower()
                or "0 match" in result.lower()
                or result.strip() == ""
            )
        finally:
            mod.PROJECT_ROOT = orig


class TestEditCode:
    """Tests for edit_code() — backend/tools/llama_code_tools.py:150"""

    def test_edit_replaces_text(self, sandbox_file):
        from backend.tools.llama_code_tools import edit_code
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_file.parent)
        try:
            result = edit_code(sandbox_file.name, 'return "world"', 'return "universe"')
            assert "error" not in result.lower() or "success" in result.lower()
            content = sandbox_file.read_text()
            assert 'return "universe"' in content
            assert 'return "world"' not in content
        finally:
            mod.PROJECT_ROOT = orig

    def test_edit_creates_backup(self, sandbox_file):
        from backend.tools.llama_code_tools import edit_code
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_file.parent)
        try:
            edit_code(sandbox_file.name, 'return "world"', 'return "backup_test"')
            backup = sandbox_file.parent / (sandbox_file.name + ".backup")
            assert backup.exists(), f"Backup file not created at {backup}"
            backup_content = backup.read_text()
            assert 'return "world"' in backup_content
        finally:
            mod.PROJECT_ROOT = orig

    def test_edit_rejects_nonexistent_text(self, sandbox_file):
        from backend.tools.llama_code_tools import edit_code
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_file.parent)
        try:
            result = edit_code(
                sandbox_file.name, "THIS TEXT DOES NOT EXIST", "replacement"
            )
            assert "error" in result.lower() or "not found" in result.lower()
        finally:
            mod.PROJECT_ROOT = orig

    def test_edit_multiline(self, sandbox_dir):
        target = sandbox_dir / "multiline.py"
        target.write_text("def foo():\n    x = 1\n    y = 2\n    return x + y\n")
        from backend.tools.llama_code_tools import edit_code
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_dir)
        try:
            edit_code("multiline.py", "    x = 1\n    y = 2", "    x = 10\n    y = 20")
            content = target.read_text()
            assert "x = 10" in content
            assert "y = 20" in content
        finally:
            mod.PROJECT_ROOT = orig


class TestVerifyChange:
    """Tests for verify_change() — backend/tools/llama_code_tools.py:347"""

    def test_verify_text_present(self, sandbox_file):
        from backend.tools.llama_code_tools import verify_change
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_file.parent)
        try:
            result = verify_change(sandbox_file.name, "hello", should_exist=True)
            assert "error" not in result.lower()
        finally:
            mod.PROJECT_ROOT = orig

    def test_verify_text_absent(self, sandbox_file):
        from backend.tools.llama_code_tools import verify_change
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_file.parent)
        try:
            result = verify_change(
                sandbox_file.name, "nonexistent_text_xyz", should_exist=False
            )
            assert "error" not in result.lower()
        finally:
            mod.PROJECT_ROOT = orig


class TestListFiles:
    """Tests for list_files() — backend/tools/llama_code_tools.py:278"""

    def test_list_files_shows_contents(self, sandbox_dir):
        from backend.tools.llama_code_tools import list_files
        import backend.tools.llama_code_tools as mod

        orig = _patch_root(mod, sandbox_dir.parent)
        try:
            result = list_files(sandbox_dir.name)
            assert ".py" in result
        finally:
            mod.PROJECT_ROOT = orig
