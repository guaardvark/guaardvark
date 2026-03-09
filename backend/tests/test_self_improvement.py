"""End-to-end self-improvement tests. Full pipeline through agent executor."""
import os
import sys
import shutil
import pytest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tests.conftest_sandbox import sandbox_dir, requires_llm

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "sandbox_code")


def _make_sandbox_agent(sandbox_dir):
    """Create an agent executor with code tools pointed at sandbox."""
    from backend.tests.test_agent_executor import _make_code_tool_registry
    from backend.services.agent_executor import AgentExecutor
    from backend.utils.llm_service import get_default_llm

    registry, orig_root = _make_code_tool_registry(sandbox_dir)
    llm = get_default_llm()
    executor = AgentExecutor(registry, llm, max_iterations=15)
    return executor, orig_root


@requires_llm
class TestSelfImprovement:
    """E2E tests: agent finds and fixes bugs autonomously."""

    @pytest.mark.timeout(180)
    def test_planted_bug_fix(self, sandbox_dir):
        """Agent finds and fixes the divide bug in buggy_calculator.py."""
        shutil.copy2(
            os.path.join(FIXTURES_DIR, "buggy_calculator.py"),
            sandbox_dir / "buggy_calculator.py"
        )

        executor, orig_root = _make_sandbox_agent(sandbox_dir)
        try:
            result = executor.execute(
                "Read buggy_calculator.py. The divide function has a bug — it returns a * b "
                "instead of a / b. Fix the bug using edit_code and verify the fix."
            )
            assert result.success
            content = (sandbox_dir / "buggy_calculator.py").read_text()
            # The bug: divide returns a * b instead of a / b
            # After fix, the divide function body should contain a / b
            divide_section = content.split("def divide")[1] if "def divide" in content else ""
            assert "/" in divide_section, f"divide function should use / operator. Content: {divide_section[:200]}"
        finally:
            import backend.tools.llama_code_tools as mod
            mod.PROJECT_ROOT = orig_root

    @pytest.mark.timeout(180)
    def test_feature_addition(self, sandbox_dir):
        """Agent fixes the farewell stub in minimal_module.py."""
        shutil.copy2(
            os.path.join(FIXTURES_DIR, "minimal_module.py"),
            sandbox_dir / "minimal_module.py"
        )

        executor, orig_root = _make_sandbox_agent(sandbox_dir)
        try:
            result = executor.execute(
                "Read minimal_module.py. The farewell function has a bug — it returns nothing "
                "(just 'pass') instead of returning a farewell message. Fix the farewell function "
                "so it returns f\"Goodbye, {name}!\" using the edit_code tool. "
                "Replace the line 'pass  # BUG: should return f\"Goodbye, {name}!\" but returns nothing' "
                "with 'return f\"Goodbye, {name}!\"'. Then verify the fix."
            )
            assert result.success
            content = (sandbox_dir / "minimal_module.py").read_text()
            assert "def farewell" in content
            assert "def greet" in content  # Original preserved
            assert "Goodbye" in content
            # The stub should be fixed
            assert "pass  # BUG" not in content
        finally:
            import backend.tools.llama_code_tools as mod
            mod.PROJECT_ROOT = orig_root

    @pytest.mark.timeout(60)
    def test_backup_and_rollback(self, sandbox_dir):
        """Verify backup is created and original can be restored."""
        target = sandbox_dir / "rollback_test.py"
        original_content = "def original():\n    return 42\n"
        target.write_text(original_content)

        from backend.tools.llama_code_tools import edit_code
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = Path(sandbox_dir)
        try:
            # Make an edit — should create backup
            edit_code("rollback_test.py", "return 42", "return 99")
            backup = sandbox_dir / "rollback_test.py.backup"
            assert backup.exists(), "Backup file should exist"
            assert "return 42" in backup.read_text()
            assert "return 99" in target.read_text()

            # Restore from backup
            shutil.copy2(backup, target)
            assert "return 42" in target.read_text()
        finally:
            mod.PROJECT_ROOT = original_root


@requires_llm
class TestRealFileModification:
    """Final validation: modify a real non-critical file with rollback."""

    @pytest.mark.timeout(60)
    def test_real_fixture_file_modification(self):
        """Modify the buggy_calculator fixture and verify rollback."""
        real_file = os.path.join(FIXTURES_DIR, "buggy_calculator.py")
        backup_file = real_file + ".test_backup"

        # Save original
        shutil.copy2(real_file, backup_file)
        original_content = open(real_file).read()

        try:
            from backend.tools.llama_code_tools import edit_code, verify_change
            import backend.tools.llama_code_tools as mod
            original_root = mod.PROJECT_ROOT
            # Point PROJECT_ROOT so the relative path works
            mod.PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(FIXTURES_DIR)))

            try:
                rel_path = os.path.relpath(real_file, str(mod.PROJECT_ROOT))
                result = edit_code(rel_path, "return a * b  # BUG", "return a / b  # FIXED")
                # Check edit succeeded
                assert "error" not in result.lower() or "success" in result.lower(), f"Edit failed: {result}"

                # Verify the fix applied
                v_result = verify_change(rel_path, "a / b", should_exist=True)
                assert "error" not in v_result.lower(), f"Verify failed: {v_result}"
            finally:
                mod.PROJECT_ROOT = original_root
        finally:
            # ALWAYS restore original — this is a real file
            shutil.copy2(backup_file, real_file)
            os.remove(backup_file)
            restored = open(real_file).read()
            assert restored == original_content, "Real file was not properly restored!"
