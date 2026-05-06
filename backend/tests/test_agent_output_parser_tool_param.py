"""Regression tests for parse_tool_calls_xml when a tool's schema contains
a parameter literally named "tool" (e.g. mcp_execute).

Background: the XML wire format wraps each tool call as
    <tool_call>
      <tool>tool_name_here</tool>
      <param_a>...</param_a>
      ...
    </tool_call>
so <tool> doubles as the structural tool-name marker. Before the fix, the
parser dropped *every* <tool> element from the params dict, which meant
mcp_execute always lost its required `tool` parameter and the LLM's MCP
calls failed validation downstream.

The fix: only the FIRST <tool> element is structural; subsequent <tool>
elements are real parameters.
"""
from backend.utils.agent_output_parser import parse_tool_calls_xml


def test_param_named_tool_is_preserved_in_direct_format():
    xml = (
        "<tool_call>\n"
        "  <tool>mcp_execute</tool>\n"
        "  <server>filesystem</server>\n"
        "  <tool>list_directory</tool>\n"
        "  <arguments>{\"path\": \"/tmp\"}</arguments>\n"
        "</tool_call>"
    )
    parsed = parse_tool_calls_xml(xml)
    assert len(parsed.tool_calls) == 1
    tc = parsed.tool_calls[0]
    assert tc.tool_name == "mcp_execute"
    assert tc.parameters.get("server") == "filesystem"
    assert tc.parameters.get("tool") == "list_directory"
    assert tc.parameters.get("arguments") == '{"path": "/tmp"}'


def test_param_named_tool_is_preserved_in_nested_format():
    # nested format uses <parameter>name</parameter><value>val</value>; the
    # parser falls into this branch when the first encountered tag is
    # <parameter> or <parameter_name>. We mix in a stray <tool> to confirm
    # the second-<tool> rescue logic also fires here.
    xml = (
        "<tool_call>\n"
        "  <tool>mcp_execute</tool>\n"
        "  <parameter>server</parameter><value>filesystem</value>\n"
        "  <tool>list_directory</tool>\n"
        "  <parameter>arguments</parameter><value>{\"path\":\"/tmp\"}</value>\n"
        "</tool_call>"
    )
    parsed = parse_tool_calls_xml(xml)
    assert len(parsed.tool_calls) == 1
    tc = parsed.tool_calls[0]
    assert tc.tool_name == "mcp_execute"
    assert tc.parameters.get("server") == "filesystem"
    assert tc.parameters.get("tool") == "list_directory"
    assert tc.parameters.get("arguments") == '{"path":"/tmp"}'


def test_first_tool_remains_structural_for_normal_tools():
    # Make sure the fix didn't regress the common case.
    xml = (
        "<tool_call>\n"
        "  <tool>web_search</tool>\n"
        "  <query>weather in tokyo</query>\n"
        "</tool_call>"
    )
    parsed = parse_tool_calls_xml(xml)
    assert len(parsed.tool_calls) == 1
    tc = parsed.tool_calls[0]
    assert tc.tool_name == "web_search"
    assert tc.parameters == {"query": "weather in tokyo"}
    assert "tool" not in tc.parameters  # only the structural one — no spurious leak


def test_reasoning_and_tool_call_tags_still_filtered():
    # Direct format: <reasoning> and <tool_call> tags must never become params.
    xml = (
        "<tool_call>\n"
        "  <tool>web_search</tool>\n"
        "  <reasoning>need fresh weather data</reasoning>\n"
        "  <query>weather in tokyo</query>\n"
        "</tool_call>"
    )
    parsed = parse_tool_calls_xml(xml)
    tc = parsed.tool_calls[0]
    assert "reasoning" not in tc.parameters
    assert "tool_call" not in tc.parameters
    assert tc.parameters == {"query": "weather in tokyo"}
