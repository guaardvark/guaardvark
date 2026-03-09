# Self-Improvement Coding Test Suite — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a 4-layer automated test suite that validates Guaardvark's self-improvement coding abilities (code tools, codegen, agent loop, E2E self-improvement).

**Architecture:** Bottom-up: sandbox unit tests for deterministic code tools, then LLM-backed tests for codegen, agent executor, and full self-improvement pipeline. Each layer depends on the one below it.

**Tech Stack:** pytest, unittest.mock, Flask test client, Ollama (llama3:latest)

---

### Task 1: Create test fixtures and sandbox infrastructure

**Files:**
- Create: `backend/tests/fixtures/sandbox_code/buggy_calculator.py`
- Create: `backend/tests/fixtures/sandbox_code/messy_utils.py`
- Create: `backend/tests/fixtures/sandbox_code/minimal_module.py`
- Create: `backend/tests/conftest_sandbox.py`

**Step 1: Create intentionally buggy fixture file**

```python
# backend/tests/fixtures/sandbox_code/buggy_calculator.py
"""A calculator with an obvious bug for the agent to find and fix."""

def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    return a * b  # BUG: should be a / b
```

**Step 2: Create messy code fixture**

```python
# backend/tests/fixtures/sandbox_code/messy_utils.py
"""Poorly structured code for the agent to improve."""

def processData(x):
    if x==None:
        return None
    if type(x)==str:
        return x.strip().lower()
    if type(x)==int or type(x)==float:
        return x*2
    return x
```

**Step 3: Create minimal module fixture**

```python
# backend/tests/fixtures/sandbox_code/minimal_module.py
"""Minimal module for testing feature addition."""

def greet(name):
    return f"Hello, {name}!"
```

**Step 4: Create sandbox conftest with pytest fixtures**

```python
# backend/tests/conftest_sandbox.py
"""Shared fixtures for self-improvement test suite."""
import os
import shutil
import tempfile
import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "sandbox_code")


@pytest.fixture
def sandbox_dir(tmp_path):
    """Create a temporary sandbox with copies of fixture files."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    # Copy all fixture files into sandbox
    for f in os.listdir(FIXTURES_DIR):
        src = os.path.join(FIXTURES_DIR, f)
        if os.path.isfile(src):
            shutil.copy2(src, sandbox / f)
    return sandbox


@pytest.fixture
def sandbox_file(sandbox_dir):
    """Create a single temporary Python file for simple tests."""
    p = sandbox_dir / "test_target.py"
    p.write_text('def hello():\n    return "world"\n')
    return p


def ollama_available():
    """Check if Ollama is running and has a model loaded."""
    try:
        import urllib.request
        import json
        resp = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        data = json.loads(resp.read())
        return len(data.get("models", [])) > 0
    except Exception:
        return False


requires_llm = pytest.mark.skipif(
    not ollama_available(),
    reason="Ollama not available or no models loaded"
)
```

**Step 5: Create `__init__.py` for fixtures**

```bash
touch backend/tests/fixtures/__init__.py
touch backend/tests/fixtures/sandbox_code/__init__.py
```

**Step 6: Verify fixture files exist**

```bash
ls -la backend/tests/fixtures/sandbox_code/
# Expected: buggy_calculator.py, messy_utils.py, minimal_module.py, __init__.py
python3 -c "import backend.tests.conftest_sandbox as cs; print('sandbox fixtures OK')"
```

**Step 7: Commit**

```bash
git add backend/tests/fixtures/ backend/tests/conftest_sandbox.py
git commit -m "test: add sandbox fixtures for self-improvement test suite"
```

---

### Task 2: Code tools unit tests — read_code and search_code

**Files:**
- Create: `backend/tests/test_code_tools.py`
- Reference: `backend/tools/llama_code_tools.py` (lines 29-147)

**Step 1: Write failing tests for read_code**

```python
# backend/tests/test_code_tools.py
"""Unit tests for code manipulation tools (sandbox-based, no LLM)."""
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tests.conftest_sandbox import sandbox_dir, sandbox_file


class TestReadCode:
    """Tests for read_code() — backend/tools/llama_code_tools.py:29"""

    def test_read_existing_file(self, sandbox_file):
        from backend.tools.llama_code_tools import read_code
        # Patch PROJECT_ROOT to sandbox parent so relative paths work
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_file.parent
        try:
            result = read_code(sandbox_file.name)
            assert "hello" in result
            assert "world" in result
        finally:
            mod.PROJECT_ROOT = original_root

    def test_read_nonexistent_file(self):
        from backend.tools.llama_code_tools import read_code
        result = read_code("nonexistent_file_xyz.py")
        assert "error" in result.lower() or "not found" in result.lower() or "does not exist" in result.lower()

    def test_read_rejects_path_traversal(self):
        from backend.tools.llama_code_tools import read_code
        result = read_code("../../etc/passwd")
        # Should either reject or not return /etc/passwd content
        assert "root:" not in result


class TestSearchCode:
    """Tests for search_code() — backend/tools/llama_code_tools.py:77"""

    def test_search_finds_pattern(self, sandbox_dir):
        from backend.tools.llama_code_tools import search_code
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_dir
        try:
            result = search_code("def hello", "**/*.py")
            assert "hello" in result
        finally:
            mod.PROJECT_ROOT = original_root

    def test_search_no_matches(self, sandbox_dir):
        from backend.tools.llama_code_tools import search_code
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_dir
        try:
            result = search_code("xyznonexistentpattern123", "**/*.py")
            assert "no matches" in result.lower() or "0 match" in result.lower() or result.strip() == ""
        finally:
            mod.PROJECT_ROOT = original_root
```

**Step 2: Run tests to verify they fail (tools may not be importable yet in test env)**

```bash
cd /home/llamax1/LLAMAX7
python3 -m pytest backend/tests/test_code_tools.py -vv --tb=short 2>&1 | head -40
```

Expected: Tests run (may pass or fail depending on import paths).

**Step 3: Fix any import issues found**

Adjust `sys.path` or `PROJECT_ROOT` patching as needed based on Step 2 output.

**Step 4: Run tests and verify they pass**

```bash
python3 -m pytest backend/tests/test_code_tools.py::TestReadCode -vv
python3 -m pytest backend/tests/test_code_tools.py::TestSearchCode -vv
```

Expected: All PASS.

**Step 5: Commit**

```bash
git add backend/tests/test_code_tools.py
git commit -m "test: add read_code and search_code unit tests"
```

---

### Task 3: Code tools unit tests — edit_code, verify_change, list_files

**Files:**
- Modify: `backend/tests/test_code_tools.py`
- Reference: `backend/tools/llama_code_tools.py` (lines 150-386)

**Step 1: Write failing tests for edit_code**

Append to `backend/tests/test_code_tools.py`:

```python
class TestEditCode:
    """Tests for edit_code() — backend/tools/llama_code_tools.py:150"""

    def test_edit_replaces_text(self, sandbox_file):
        from backend.tools.llama_code_tools import edit_code
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_file.parent
        try:
            result = edit_code(sandbox_file.name, 'return "world"', 'return "universe"')
            assert "success" in result.lower() or "updated" in result.lower() or "changed" in result.lower()
            # Verify file was actually changed
            content = sandbox_file.read_text()
            assert 'return "universe"' in content
            assert 'return "world"' not in content
        finally:
            mod.PROJECT_ROOT = original_root

    def test_edit_creates_backup(self, sandbox_file):
        from backend.tools.llama_code_tools import edit_code
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_file.parent
        try:
            edit_code(sandbox_file.name, 'return "world"', 'return "backup_test"')
            backup = sandbox_file.parent / (sandbox_file.name + ".backup")
            assert backup.exists(), f"Backup file not created at {backup}"
            backup_content = backup.read_text()
            assert 'return "world"' in backup_content
        finally:
            mod.PROJECT_ROOT = original_root

    def test_edit_rejects_nonexistent_text(self, sandbox_file):
        from backend.tools.llama_code_tools import edit_code
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_file.parent
        try:
            result = edit_code(sandbox_file.name, "THIS TEXT DOES NOT EXIST", "replacement")
            assert "error" in result.lower() or "not found" in result.lower()
        finally:
            mod.PROJECT_ROOT = original_root

    def test_edit_multiline(self, sandbox_dir):
        target = sandbox_dir / "multiline.py"
        target.write_text("def foo():\n    x = 1\n    y = 2\n    return x + y\n")
        from backend.tools.llama_code_tools import edit_code
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_dir
        try:
            result = edit_code("multiline.py", "    x = 1\n    y = 2", "    x = 10\n    y = 20")
            content = target.read_text()
            assert "x = 10" in content
            assert "y = 20" in content
        finally:
            mod.PROJECT_ROOT = original_root


class TestVerifyChange:
    """Tests for verify_change() — backend/tools/llama_code_tools.py:347"""

    def test_verify_text_present(self, sandbox_file):
        from backend.tools.llama_code_tools import verify_change
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_file.parent
        try:
            result = verify_change(sandbox_file.name, "hello", should_exist=True)
            assert "verified" in result.lower() or "found" in result.lower() or "success" in result.lower()
        finally:
            mod.PROJECT_ROOT = original_root

    def test_verify_text_absent(self, sandbox_file):
        from backend.tools.llama_code_tools import verify_change
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_file.parent
        try:
            result = verify_change(sandbox_file.name, "nonexistent_text_xyz", should_exist=False)
            assert "verified" in result.lower() or "confirmed" in result.lower() or "success" in result.lower()
        finally:
            mod.PROJECT_ROOT = original_root


class TestListFiles:
    """Tests for list_files() — backend/tools/llama_code_tools.py:278"""

    def test_list_files_shows_contents(self, sandbox_dir):
        from backend.tools.llama_code_tools import list_files
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_dir.parent
        try:
            result = list_files(sandbox_dir.name)
            assert ".py" in result  # Should show python files
        finally:
            mod.PROJECT_ROOT = original_root
```

**Step 2: Run tests**

```bash
python3 -m pytest backend/tests/test_code_tools.py -vv --tb=short
```

Expected: All PASS.

**Step 3: Commit**

```bash
git add backend/tests/test_code_tools.py
git commit -m "test: add edit_code, verify_change, list_files unit tests"
```

---

### Task 4: Code generation tests (LLM-backed)

**Files:**
- Create: `backend/tests/test_code_generation.py`
- Reference: `backend/tools/code_tools.py` (CodeGeneratorTool, line 17)

**Step 1: Write tests**

```python
# backend/tests/test_code_generation.py
"""Tests for LLM-powered code generation. Requires Ollama."""
import os
import sys
import py_compile
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tests.conftest_sandbox import sandbox_dir, requires_llm


@requires_llm
class TestCodeGeneration:
    """Tests for CodeGeneratorTool — backend/tools/code_tools.py:17"""

    @pytest.mark.timeout(120)
    def test_generate_python_function(self, sandbox_dir):
        """Generate a Python function and verify it compiles."""
        from backend.tools.code_tools import CodeGeneratorTool
        tool = CodeGeneratorTool()
        result = tool.execute(
            output_filename=str(sandbox_dir / "generated_func.py"),
            instructions="Write a Python function called 'fibonacci' that returns the nth Fibonacci number using iteration.",
            language="python"
        )
        assert result.success, f"Codegen failed: {result.error}"
        output_path = sandbox_dir / "generated_func.py"
        assert output_path.exists(), "Generated file not created"
        # Verify it compiles
        py_compile.compile(str(output_path), doraise=True)

    @pytest.mark.timeout(120)
    def test_generate_valid_syntax(self, sandbox_dir):
        """Generate code and verify syntax is valid Python."""
        from backend.tools.code_tools import CodeGeneratorTool
        tool = CodeGeneratorTool()
        result = tool.execute(
            output_filename=str(sandbox_dir / "syntax_test.py"),
            instructions="Write a Python class called 'Stack' with push, pop, and peek methods.",
            language="python"
        )
        assert result.success
        content = (sandbox_dir / "syntax_test.py").read_text()
        compile(content, "syntax_test.py", "exec")  # Raises SyntaxError if invalid

    @pytest.mark.timeout(120)
    def test_modify_existing_file(self, sandbox_dir):
        """Modify an existing file with instructions."""
        from backend.tools.code_tools import CodeGeneratorTool
        # Create input file
        input_file = sandbox_dir / "to_modify.py"
        input_file.write_text("def greet(name):\n    return f'Hello, {name}!'\n")

        tool = CodeGeneratorTool()
        result = tool.execute(
            input_file=str(input_file),
            output_filename=str(sandbox_dir / "modified.py"),
            instructions="Add a function called 'farewell' that takes a name and returns 'Goodbye, {name}!'",
            language="python",
            preserve_structure=True
        )
        assert result.success
        content = (sandbox_dir / "modified.py").read_text()
        assert "farewell" in content
        assert "greet" in content  # Original should be preserved
```

**Step 2: Run tests (skip if no LLM)**

```bash
python3 -m pytest backend/tests/test_code_generation.py -vv --timeout=300
```

Expected: PASS if Ollama running, SKIP otherwise.

**Step 3: Commit**

```bash
git add backend/tests/test_code_generation.py
git commit -m "test: add LLM-backed code generation tests"
```

---

### Task 5: Agent executor integration tests

**Files:**
- Create: `backend/tests/test_agent_executor.py`
- Reference: `backend/services/agent_executor.py` (AgentExecutor, line 262)
- Reference: `backend/services/agent_tools.py` (ToolRegistry, line 127)

**Step 1: Write tests**

```python
# backend/tests/test_agent_executor.py
"""Integration tests for the Agent Executor ReACT loop."""
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tests.conftest_sandbox import sandbox_dir, requires_llm


def _make_code_tool_registry(project_root):
    """Create a minimal tool registry with code tools pointed at sandbox."""
    from backend.services.agent_tools import ToolRegistry, BaseTool, ToolParameter, ToolResult
    import backend.tools.llama_code_tools as code_tools

    # Temporarily point tools at sandbox
    original_root = code_tools.PROJECT_ROOT
    code_tools.PROJECT_ROOT = project_root

    registry = ToolRegistry()

    class ReadCodeTool(BaseTool):
        name = "read_code"
        description = "Read a source code file"
        parameters = {
            "filepath": ToolParameter(name="filepath", type="string", required=True,
                                       description="Path to file")
        }
        def execute(self, **kwargs):
            result = code_tools.read_code(kwargs["filepath"])
            return ToolResult(success=True, output=result)

    class SearchCodeTool(BaseTool):
        name = "search_code"
        description = "Search for pattern in code files"
        parameters = {
            "pattern": ToolParameter(name="pattern", type="string", required=True,
                                      description="Pattern to search for"),
            "file_glob": ToolParameter(name="file_glob", type="string", required=False,
                                        description="File glob", default="**/*.py")
        }
        def execute(self, **kwargs):
            result = code_tools.search_code(kwargs["pattern"], kwargs.get("file_glob", "**/*.py"))
            return ToolResult(success=True, output=result)

    class EditCodeTool(BaseTool):
        name = "edit_code"
        description = "Edit a source code file by replacing exact text"
        parameters = {
            "filepath": ToolParameter(name="filepath", type="string", required=True,
                                       description="Path to file"),
            "old_text": ToolParameter(name="old_text", type="string", required=True,
                                       description="Text to replace"),
            "new_text": ToolParameter(name="new_text", type="string", required=True,
                                       description="Replacement text")
        }
        def execute(self, **kwargs):
            result = code_tools.edit_code(kwargs["filepath"], kwargs["old_text"], kwargs["new_text"])
            return ToolResult(success="error" not in result.lower(), output=result)

    class VerifyChangeTool(BaseTool):
        name = "verify_change"
        description = "Verify a code change was applied correctly"
        parameters = {
            "filepath": ToolParameter(name="filepath", type="string", required=True,
                                       description="Path to file"),
            "expected_text": ToolParameter(name="expected_text", type="string", required=True,
                                            description="Text to check for"),
            "should_exist": ToolParameter(name="should_exist", type="bool", required=False,
                                           description="Whether text should exist", default=True)
        }
        def execute(self, **kwargs):
            result = code_tools.verify_change(kwargs["filepath"], kwargs["expected_text"],
                                               kwargs.get("should_exist", True))
            return ToolResult(success=True, output=result)

    for tool_cls in [ReadCodeTool, SearchCodeTool, EditCodeTool, VerifyChangeTool]:
        registry.register(tool_cls())

    return registry, original_root


@requires_llm
class TestAgentExecutor:
    """Tests for AgentExecutor — backend/services/agent_executor.py:262"""

    @pytest.mark.timeout(120)
    def test_agent_reads_file(self, sandbox_dir):
        """Agent can read a file when asked."""
        from backend.services.agent_executor import AgentExecutor
        from backend.utils.llm_service import get_default_llm

        target = sandbox_dir / "readme.py"
        target.write_text("# This module handles user authentication\ndef login(user, pwd):\n    pass\n")

        registry, orig_root = _make_code_tool_registry(sandbox_dir)
        try:
            llm = get_default_llm()
            executor = AgentExecutor(registry, llm, max_iterations=5)
            result = executor.execute("Read the file readme.py and tell me what it does.")
            assert result.success
            assert "authentication" in result.final_answer.lower() or "login" in result.final_answer.lower()
        finally:
            import backend.tools.llama_code_tools as mod
            mod.PROJECT_ROOT = orig_root

    @pytest.mark.timeout(120)
    def test_agent_searches_code(self, sandbox_dir):
        """Agent can search for patterns."""
        from backend.services.agent_executor import AgentExecutor
        from backend.utils.llm_service import get_default_llm

        target = sandbox_dir / "app.py"
        target.write_text("SECRET_KEY = 'abc123'\ndef get_config():\n    return {'key': SECRET_KEY}\n")

        registry, orig_root = _make_code_tool_registry(sandbox_dir)
        try:
            llm = get_default_llm()
            executor = AgentExecutor(registry, llm, max_iterations=5)
            result = executor.execute("Search for any hardcoded secrets in the code.")
            assert result.success
            assert len(result.steps) > 0  # Agent should have used at least one tool
        finally:
            import backend.tools.llama_code_tools as mod
            mod.PROJECT_ROOT = orig_root

    @pytest.mark.timeout(180)
    def test_agent_edit_sequence(self, sandbox_dir):
        """Agent performs read-edit-verify sequence."""
        from backend.services.agent_executor import AgentExecutor
        from backend.utils.llm_service import get_default_llm

        target = sandbox_dir / "config.py"
        target.write_text('DEBUG = True\nPORT = 8080\nHOST = "localhost"\n')

        registry, orig_root = _make_code_tool_registry(sandbox_dir)
        try:
            llm = get_default_llm()
            executor = AgentExecutor(registry, llm, max_iterations=10)
            result = executor.execute(
                "Read config.py, change DEBUG from True to False, and verify the change."
            )
            assert result.success
            content = target.read_text()
            assert "DEBUG = False" in content or "DEBUG=False" in content
        finally:
            import backend.tools.llama_code_tools as mod
            mod.PROJECT_ROOT = orig_root

    @pytest.mark.timeout(120)
    def test_agent_respects_max_iterations(self, sandbox_dir):
        """Agent stops after max iterations."""
        from backend.services.agent_executor import AgentExecutor
        from backend.utils.llm_service import get_default_llm

        registry, orig_root = _make_code_tool_registry(sandbox_dir)
        try:
            llm = get_default_llm()
            executor = AgentExecutor(registry, llm, max_iterations=2)
            result = executor.execute("Do a very complex multi-step analysis of all files.")
            assert result.iterations <= 2
        finally:
            import backend.tools.llama_code_tools as mod
            mod.PROJECT_ROOT = orig_root
```

**Step 2: Run tests**

```bash
python3 -m pytest backend/tests/test_agent_executor.py -vv --timeout=300
```

Expected: PASS if Ollama running, SKIP otherwise.

**Step 3: Commit**

```bash
git add backend/tests/test_agent_executor.py
git commit -m "test: add agent executor integration tests"
```

---

### Task 6: E2E self-improvement tests

**Files:**
- Create: `backend/tests/test_self_improvement.py`
- Reference: `backend/api/agents_api.py` (execute_agent, line 248)
- Reference: `backend/tests/fixtures/sandbox_code/buggy_calculator.py`

**Step 1: Write tests**

```python
# backend/tests/test_self_improvement.py
"""End-to-end self-improvement tests. Full pipeline through agent API."""
import os
import sys
import shutil
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tests.conftest_sandbox import sandbox_dir, requires_llm

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "sandbox_code")


def _make_sandbox_agent(sandbox_dir):
    """Create an agent executor with code tools pointed at sandbox."""
    # Reuse the helper from test_agent_executor
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
        # Copy buggy file to sandbox
        shutil.copy2(
            os.path.join(FIXTURES_DIR, "buggy_calculator.py"),
            sandbox_dir / "buggy_calculator.py"
        )

        executor, orig_root = _make_sandbox_agent(sandbox_dir)
        try:
            result = executor.execute(
                "Read buggy_calculator.py. The divide function has a bug. "
                "Find the bug, fix it, and verify the fix."
            )
            assert result.success
            content = (sandbox_dir / "buggy_calculator.py").read_text()
            # The bug: divide returns a * b instead of a / b
            assert "a / b" in content or "a/b" in content
            assert "a * b" not in content.split("def divide")[1]  # Fixed in divide function
        finally:
            import backend.tools.llama_code_tools as mod
            mod.PROJECT_ROOT = orig_root

    @pytest.mark.timeout(180)
    def test_feature_addition(self, sandbox_dir):
        """Agent adds a new function to minimal_module.py."""
        shutil.copy2(
            os.path.join(FIXTURES_DIR, "minimal_module.py"),
            sandbox_dir / "minimal_module.py"
        )

        executor, orig_root = _make_sandbox_agent(sandbox_dir)
        try:
            result = executor.execute(
                "Read minimal_module.py. Add a new function called 'farewell' "
                "that takes a name parameter and returns 'Goodbye, {name}!'. "
                "Keep the existing greet function. Verify the change."
            )
            assert result.success
            content = (sandbox_dir / "minimal_module.py").read_text()
            assert "def farewell" in content
            assert "def greet" in content  # Original preserved
            assert "Goodbye" in content
        finally:
            import backend.tools.llama_code_tools as mod
            mod.PROJECT_ROOT = orig_root

    @pytest.mark.timeout(180)
    def test_backup_and_rollback(self, sandbox_dir):
        """Verify backup is created and original can be restored."""
        target = sandbox_dir / "rollback_test.py"
        original_content = "def original():\n    return 42\n"
        target.write_text(original_content)

        from backend.tools.llama_code_tools import edit_code
        import backend.tools.llama_code_tools as mod
        original_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = sandbox_dir
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

    @pytest.mark.timeout(120)
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
            mod.PROJECT_ROOT = os.path.dirname(os.path.dirname(FIXTURES_DIR))

            try:
                # Calculate relative path from PROJECT_ROOT
                rel_path = os.path.relpath(real_file, mod.PROJECT_ROOT)
                result = edit_code(rel_path, "return a * b  # BUG", "return a / b  # FIXED")
                assert "success" in result.lower() or "updated" in result.lower() or "changed" in result.lower()

                # Verify the fix applied
                v_result = verify_change(rel_path, "a / b", should_exist=True)
                assert "verified" in v_result.lower() or "found" in v_result.lower() or "success" in v_result.lower()
            finally:
                mod.PROJECT_ROOT = original_root
        finally:
            # ALWAYS restore original — this is a real file
            shutil.copy2(backup_file, real_file)
            os.remove(backup_file)
            restored = open(real_file).read()
            assert restored == original_content, "Real file was not properly restored!"
```

**Step 2: Run tests**

```bash
python3 -m pytest backend/tests/test_self_improvement.py -vv --timeout=600
```

Expected: PASS if Ollama running.

**Step 3: Commit**

```bash
git add backend/tests/test_self_improvement.py
git commit -m "test: add E2E self-improvement tests with planted bug and rollback"
```

---

### Task 7: Run full test suite and verify

**Step 1: Run all self-improvement tests**

```bash
cd /home/llamax1/LLAMAX7
python3 -m pytest backend/tests/test_code_tools.py backend/tests/test_code_generation.py backend/tests/test_agent_executor.py backend/tests/test_self_improvement.py -vv --timeout=600
```

Expected: All PASS (LLM tests SKIP if no Ollama).

**Step 2: Run sandbox-only tests (fast, no LLM)**

```bash
python3 -m pytest backend/tests/test_code_tools.py -vv
```

Expected: All PASS in < 5 seconds.

**Step 3: Run existing test suite to verify no regressions**

```bash
python3 -m pytest backend/tests/ -vv --timeout=300 -x
```

Expected: No new failures.

**Step 4: Commit all**

```bash
git add -A backend/tests/
git commit -m "test: complete self-improvement coding test suite (4 layers)"
```
