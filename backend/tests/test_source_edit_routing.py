import ast
from pathlib import Path

from backend.services.agent_router import AgentRouter, RouteType


def test_source_code_edit_routes_to_agent_loop_with_edit_code():
    router = AgentRouter()

    decision = router.route(
        "Modify the Clear Cache button within frontend/src/pages/SettingsPage.jsx"
    )

    assert decision.route_type == RouteType.AGENT_LOOP
    assert decision.tool_name == "edit_code"
    assert decision.reasoning == "Matched pattern: source_code_edit"


def test_code_tool_group_includes_edit_workflow_tools():
    source_path = Path(__file__).parents[1] / "services" / "unified_chat_engine.py"
    module = ast.parse(source_path.read_text())
    code_tools = None

    for node in module.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if "CODE_TOOLS" in names:
                code_tools = ast.literal_eval(node.value)
                break

    assert code_tools is not None
    assert "read_code" in code_tools
    assert "edit_code" in code_tools
    assert "verify_change" in code_tools
