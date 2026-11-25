"""Integration tests for the Agent Executor ReACT loop."""
import os
import sys
import json
import pytest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tests.conftest_sandbox import sandbox_dir, requires_llm
from backend.utils.agent_output_parser import (
    parse_tool_calls_json,
    parse_tool_calls_structured,
    format_tool_result_for_llm,
)


@pytest.fixture(autouse=True)
def _no_saved_memories(monkeypatch):
    # The executor recalls saved memories first. Outside an app context
    # that imports backend.app, which boots the whole app inside the test
    # (and against DATABASE_URL, would count recalls in that database) and
    # leaves it running for every test after this file.
    # test_agent_memory_hints.py covers the recall itself.
    monkeypatch.setattr("backend.api.memory_api.search_memories", lambda *a, **k: [])


def _make_code_tool_registry(project_root):
    """Create a minimal tool registry with code tools pointed at sandbox."""
    from backend.services.agent_tools import ToolRegistry, BaseTool, ToolParameter, ToolResult
    import backend.tools.llama_code_tools as code_tools

    original_root = code_tools.PROJECT_ROOT
    code_tools.PROJECT_ROOT = Path(project_root)

    registry = ToolRegistry()

    class ReadCodeTool(BaseTool):
        name = "read_code"
        description = "Read a source code file. Args: filepath (relative path to file)"
        parameters = {
            "filepath": ToolParameter(name="filepath", type="string", required=True,
                                       description="Relative path to file to read")
        }
        def execute(self, **kwargs):
            result = code_tools.read_code(kwargs["filepath"])
            return ToolResult(success=True, output=result)

    class SearchCodeTool(BaseTool):
        name = "search_code"
        description = "Search for a text pattern across code files. Args: pattern, file_glob (optional)"
        parameters = {
            "pattern": ToolParameter(name="pattern", type="string", required=True,
                                      description="Text or regex pattern to search for"),
            "file_glob": ToolParameter(name="file_glob", type="string", required=False,
                                        description="File glob pattern", default="**/*.py")
        }
        def execute(self, **kwargs):
            result = code_tools.search_code(kwargs["pattern"], kwargs.get("file_glob", "**/*.py"))
            return ToolResult(success=True, output=result)

    class EditCodeTool(BaseTool):
        name = "edit_code"
        description = "Edit a source code file by replacing exact text with new text. Creates automatic backup. Args: filepath, old_text, new_text"
        parameters = {
            "filepath": ToolParameter(name="filepath", type="string", required=True,
                                       description="Relative path to file"),
            "old_text": ToolParameter(name="old_text", type="string", required=True,
                                       description="Exact text to find and replace"),
            "new_text": ToolParameter(name="new_text", type="string", required=True,
                                       description="New text to insert")
        }
        def execute(self, **kwargs):
            result = code_tools.edit_code(kwargs["filepath"], kwargs["old_text"], kwargs["new_text"])
            return ToolResult(success="error" not in result.lower(), output=result)

    class VerifyChangeTool(BaseTool):
        name = "verify_change"
        description = "Verify that a code change was applied correctly by checking if text exists in file. Args: filepath, expected_text, should_exist"
        parameters = {
            "filepath": ToolParameter(name="filepath", type="string", required=True,
                                       description="Relative path to file"),
            "expected_text": ToolParameter(name="expected_text", type="string", required=True,
                                            description="Text to check for"),
            "should_exist": ToolParameter(name="should_exist", type="bool", required=False,
                                           description="True if text should exist, False if should be gone",
                                           default=True)
        }
        def execute(self, **kwargs):
            result = code_tools.verify_change(kwargs["filepath"], kwargs["expected_text"],
                                               kwargs.get("should_exist", True))
            return ToolResult(success=True, output=result)

    for tool_cls in [ReadCodeTool, SearchCodeTool, EditCodeTool, VerifyChangeTool]:
        registry.register(tool_cls())

    return registry, original_root


class TestAgentExecutorLLMInitialization:
    """Unit tests for AgentExecutor LLM fallback behavior (no live LLM required)."""

    def test_none_llm_uses_default_loader(self, monkeypatch):
        from backend.services.agent_executor import AgentExecutor
        from backend.services.agent_tools import ToolRegistry
        from backend.utils import llm_service

        class FakeLLM:
            def chat(self, *_args, **_kwargs):
                raise AssertionError("chat should not be called during initialization")

        fake_llm = FakeLLM()
        monkeypatch.setattr(llm_service, "get_default_llm", lambda: fake_llm)

        executor = AgentExecutor(ToolRegistry(), None, max_iterations=1)

        assert executor.llm is fake_llm

    def test_execute_fails_cleanly_when_default_llm_unavailable(self, monkeypatch):
        from backend.services.agent_executor import AgentExecutor
        from backend.services.agent_tools import ToolRegistry
        from backend.utils import llm_service

        monkeypatch.setattr(llm_service, "get_default_llm", lambda: None)

        executor = AgentExecutor(ToolRegistry(), None, max_iterations=1)
        result = executor.execute("do something")

        assert result.success is False
        assert result.iterations == 0
        assert "no chat-capable LLM" in result.error


def _approval_registry():
    """A registry that records execute_tool calls instead of running tools."""
    from backend.services.agent_tools import ToolRegistry, BaseTool, ToolParameter, ToolResult

    class RecordingRegistry(ToolRegistry):
        def __init__(self):
            super().__init__()
            self.executed = []

        def execute_tool(self, tool_name, /, agent_context=None, **kwargs):
            self.executed.append(tool_name)
            return ToolResult(success=True, output=f"{tool_name} ran")

    class RunPythonTool(BaseTool):
        name = "execute_python"
        description = "Run Python code."
        requires_approval = True
        parameters = {"code": ToolParameter(name="code", type="string", description="Code to run")}

    class EditTool(BaseTool):
        name = "edit_code"
        description = "Replace exact text in a file."
        requires_approval = True
        parameters = {
            "filepath": ToolParameter(name="filepath", type="string", description="File"),
            "old_text": ToolParameter(name="old_text", type="string", description="Old text"),
            "new_text": ToolParameter(name="new_text", type="string", description="New text"),
        }

    class FindTool(BaseTool):
        name = "find_files"
        description = "Find files; asks only outside the project's own folders."
        requires_approval = True
        parameters = {"path": ToolParameter(name="path", type="string", description="Folder")}

        def needs_approval(self, params):
            return params.get("path") != "inside"

    registry = RecordingRegistry()
    for tool_cls in (RunPythonTool, EditTool, FindTool):
        registry.register(tool_cls())
    return registry


class _JsonToolCallLLM:
    """Answers every prompt-path turn with one JSON tool call."""
    model = ""

    def __init__(self, tool_name, params):
        self._text = json.dumps({
            "thoughts": "calling a tool",
            "tool_calls": [{"tool_name": tool_name, "parameters": params}],
            "final_answer": None,
        })

    def chat(self, messages, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(message=SimpleNamespace(content=self._text))


class _NativeToolCallLLM:
    """Answers every native-tools turn with one function call."""
    model = ""

    def __init__(self, tool_name, params):
        self._tool_name = tool_name
        self._params = params

    def chat(self, messages, **kwargs):
        raise AssertionError("native path should not use chat()")

    def chat_with_tools(self, tools, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(message=SimpleNamespace(content=""))

    def get_tool_calls_from_response(self, response, error_on_no_tool_call=False):
        from types import SimpleNamespace
        return [SimpleNamespace(tool_name=self._tool_name, tool_kwargs=dict(self._params))]


def _ready_executor(registry, llm, **tool_context):
    from backend.services.agent_executor import AgentExecutor
    from backend.services.tool_execution_guard import ToolExecutionGuard

    executor = AgentExecutor(registry, llm, max_iterations=1)
    executor.coordinator = None
    executor._guard = ToolExecutionGuard(max_failures_per_tool=2, scope="for the rest of this task")
    executor._tool_history = []
    executor._li_tools = []
    executor.original_query = "run it"
    if tool_context:
        executor.set_tool_context(**tool_context)
    return executor


def _run_one_iteration(executor, native):
    fn = executor._execute_iteration_native if native else executor._execute_iteration
    return fn("run it", "system", 1, None)


_EDIT = {"filepath": "a.py", "old_text": "x = 1", "new_text": "x = 2"}


class TestApprovalRequiredTools:
    """The executor has no approval card, so it refuses calls that need one."""

    @pytest.mark.parametrize("native", [False, True])
    def test_approval_tool_is_refused_not_run(self, native):
        registry = _approval_registry()
        llm_cls = _NativeToolCallLLM if native else _JsonToolCallLLM
        executor = _ready_executor(registry, llm_cls("execute_python", {"code": "print(2+2)"}))

        out = _run_one_iteration(executor, native)

        assert registry.executed == []
        obs = out["step"].observations
        assert len(obs) == 1
        assert obs[0]["result"]["success"] is False
        assert obs[0]["result"]["error"] == "execute_python needs your approval in chat and was not run"
        assert "needs your approval in chat" in out["next_prompt"]

    @pytest.mark.parametrize("native", [False, True])
    def test_edit_code_refused_outside_self_improvement(self, native):
        registry = _approval_registry()
        llm_cls = _NativeToolCallLLM if native else _JsonToolCallLLM
        executor = _ready_executor(registry, llm_cls("edit_code", _EDIT))

        out = _run_one_iteration(executor, native)

        assert registry.executed == []
        assert "edit_code needs your approval" in out["step"].observations[0]["result"]["error"]

    @pytest.mark.parametrize("native", [False, True])
    def test_self_improvement_edit_code_still_runs(self, native):
        registry = _approval_registry()
        llm_cls = _NativeToolCallLLM if native else _JsonToolCallLLM
        executor = _ready_executor(registry, llm_cls("edit_code", _EDIT),
                                   _self_improvement_context=True)

        out = _run_one_iteration(executor, native)

        assert registry.executed == ["edit_code"]
        assert out["step"].observations[0]["result"]["success"] is True

    def test_self_improvement_does_not_exempt_other_tools(self):
        registry = _approval_registry()
        executor = _ready_executor(registry, _JsonToolCallLLM("execute_python", {"code": "1"}),
                                   _self_improvement_context=True)

        out = _run_one_iteration(executor, native=False)

        assert registry.executed == []
        assert out["step"].observations[0]["result"]["success"] is False

    def test_per_call_needs_approval_is_honoured(self):
        registry = _approval_registry()
        executor = _ready_executor(registry, _JsonToolCallLLM("find_files", {"path": "inside"}))

        _run_one_iteration(executor, native=False)

        assert registry.executed == ["find_files"]


class _ScriptedLLM:
    """Plays back one reply per chat() call and keeps each prompt it was sent."""
    model = ""

    def __init__(self, *replies):
        self._replies = list(replies)
        self.prompts = []

    def chat(self, messages, **kwargs):
        from types import SimpleNamespace
        self.prompts.append(messages[-1].content)
        return SimpleNamespace(message=SimpleNamespace(content=self._replies.pop(0)))


def _notes_registry():
    """One tool whose output is a fact unrelated to the question asked."""
    from backend.services.agent_tools import ToolRegistry, BaseTool, ToolParameter, ToolResult

    class NotesRegistry(ToolRegistry):
        def execute_tool(self, tool_name, /, agent_context=None, **kwargs):
            return ToolResult(
                success=True,
                output="The team meeting is on Tuesday at noon in room four.",
            )

    class ReadNotesTool(BaseTool):
        name = "read_notes"
        description = "Read the user's notes."
        parameters = {"topic": ToolParameter(name="topic", type="string", description="Topic")}

    registry = NotesRegistry()
    registry.register(ReadNotesTool())
    return registry


def _tool_call_then_answer(answer):
    return (
        json.dumps({
            "thoughts": "check the notes",
            "tool_calls": [{"tool_name": "read_notes", "parameters": {"topic": "storage"}}],
            "final_answer": None,
        }),
        json.dumps({"thoughts": "done", "tool_calls": [], "final_answer": answer}),
    )


def _run_notes_agent(llm):
    from backend.services.agent_executor import AgentExecutor

    executor = AgentExecutor(_notes_registry(), llm, max_iterations=3)
    executor.coordinator = None
    return executor.execute("where are my notes stored?")


class TestFinalAnswerFactsCheck:
    """The model's final answer is returned as written; the facts check labels it."""

    def test_unrelated_fact_keeps_the_answer_and_marks_it_unverified(self):
        answer = "Your notes are stored in Dropbox."
        llm = _ScriptedLLM(
            *_tool_call_then_answer(answer),
            "CORRECTED: The facts only mention a team meeting.",
        )

        result = _run_notes_agent(llm)

        assert result.success is True
        assert result.final_answer == answer
        assert result.verified is False
        assert not any("Based ONLY on these verified facts" in p for p in llm.prompts)

    def test_answer_with_no_unsupported_claim_is_verified(self):
        answer = "I checked your notes; they do not say where they are kept."
        llm = _ScriptedLLM(*_tool_call_then_answer(answer))

        result = _run_notes_agent(llm)

        assert result.final_answer == answer
        assert result.verified is True
        assert len(llm.prompts) == 2


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
            assert len(result.steps) > 0, "Agent should have used at least one tool"
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
            result = executor.execute("Search for any hardcoded secrets or passwords in the code files.")
            assert result.success
            assert len(result.steps) > 0
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
                "You MUST use your tools to complete this task. Follow these exact steps:\n"
                "Step 1: Use the read_code tool with filepath='config.py' to read the file.\n"
                "Step 2: Use the edit_code tool with filepath='config.py', old_text='DEBUG = True', new_text='DEBUG = False' to change DEBUG from True to False.\n"
                "Step 3: Use the verify_change tool with filepath='config.py', expected_text='DEBUG = False', should_exist=true to confirm the edit worked.\n"
                "Do not skip any step. Use the tools exactly as described."
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
            result = executor.execute("Do a very complex multi-step analysis of every file in the project.")
            assert result.iterations <= 2
        finally:
            import backend.tools.llama_code_tools as mod
            mod.PROJECT_ROOT = orig_root


class TestJSONToolCallParsing:
    """Unit tests for JSON tool call parsing (no LLM needed)."""

    def test_parse_single_tool_call(self):
        response = '{"thoughts": "need to read", "tool_calls": [{"tool_name": "read_code", "parameters": {"filepath": "test.py"}}], "final_answer": null}'
        result = parse_tool_calls_json(response)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "read_code"
        assert result.tool_calls[0].parameters == {"filepath": "test.py"}
        assert result.final_answer is None

    def test_parse_final_answer(self):
        response = '{"thoughts": null, "tool_calls": [], "final_answer": "The answer is 42."}'
        result = parse_tool_calls_json(response)
        assert result.final_answer == "The answer is 42."
        assert len(result.tool_calls) == 0

    def test_parse_multiple_tool_calls(self):
        response = '{"thoughts": "multi", "tool_calls": [{"tool_name": "search_code", "parameters": {"pattern": "TODO"}}, {"tool_name": "read_code", "parameters": {"filepath": "main.py"}}], "final_answer": null}'
        result = parse_tool_calls_json(response)
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].tool_name == "search_code"
        assert result.tool_calls[1].tool_name == "read_code"

    def test_json_with_markdown_fences(self):
        response = '```json\n{"thoughts": null, "tool_calls": [{"tool_name": "read_code", "parameters": {"filepath": "x.py"}}], "final_answer": null}\n```'
        result = parse_tool_calls_json(response)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].parameters == {"filepath": "x.py"}

    def test_empty_on_non_json(self):
        result = parse_tool_calls_json("This is plain text with no JSON")
        assert len(result.tool_calls) == 0
        assert result.final_answer is None

    def test_xml_fallback_still_works(self):
        xml = '<tool_call><tool>read_code</tool><filepath>test.py</filepath></tool_call>'
        result = parse_tool_calls_structured(xml)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "read_code"

    def test_json_priority_over_xml(self):
        json_response = '{"thoughts": "test", "tool_calls": [{"tool_name": "web_search", "parameters": {"query": "test"}}], "final_answer": null}'
        result = parse_tool_calls_structured(json_response)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "web_search"

    def test_json_observation_format_success(self):
        from backend.services.agent_tools import ToolResult
        result = ToolResult(success=True, output="found 3", metadata={"count": 3})
        formatted = format_tool_result_for_llm("search", result, format='json')
        parsed = json.loads(formatted)
        assert parsed["tool"] == "search"
        assert parsed["status"] == "success"
        assert "found 3" in parsed["output"]

    def test_json_observation_format_error(self):
        from backend.services.agent_tools import ToolResult
        result = ToolResult(success=False, error="timeout")
        formatted = format_tool_result_for_llm("search", result, format='json')
        parsed = json.loads(formatted)
        assert parsed["status"] == "failed"
        assert parsed["error"] == "timeout"

    def test_xml_observation_format_backward_compat(self):
        from backend.services.agent_tools import ToolResult
        result = ToolResult(success=True, output="data here")
        formatted = format_tool_result_for_llm("tool", result, format='xml')
        assert "<observation tool='tool'>" in formatted
        assert "data here" in formatted
