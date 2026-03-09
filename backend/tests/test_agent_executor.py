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
