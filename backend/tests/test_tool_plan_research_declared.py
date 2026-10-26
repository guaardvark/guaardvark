"""Every tool says whether it changes anything, so plan mode can tell research from action.

Plan mode runs only research tools before a plan is approved. A tool that
declares nothing counts as an action (``is_plan_research_tool`` fails closed),
which would silently keep a harmless lookup out of research; a tool declared
read-only by mistake would let research change things. These tests read the
class bodies, so they need no app, database or model.
"""
import ast
from pathlib import Path

from backend.services.agent_tools import BaseTool, is_plan_research_tool

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"


def _tool_classes():
    """(file, class name, {attr: value}) for every BaseTool subclass with a name."""
    for path in sorted(TOOLS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(getattr(b, "id", getattr(b, "attr", "")) == "BaseTool" for b in node.bases):
                continue
            attrs = {}
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    try:
                        attrs[stmt.targets[0].id] = ast.literal_eval(stmt.value)
                    except (ValueError, SyntaxError):
                        attrs[stmt.targets[0].id] = None
            # A tool that decides per call (asking only for some arguments, as
            # find_files does outside Guaardvark's folders) can still research.
            attrs["_per_call_approval"] = any(
                isinstance(stmt, ast.FunctionDef) and stmt.name == "needs_approval" for stmt in node.body
            )
            if "name" in attrs:
                yield path.relative_to(TOOLS_DIR.parent.parent), node.name, attrs


def test_every_tool_declares_read_only():
    undeclared = [f"{path}:{cls} ({attrs['name']})"
                  for path, cls, attrs in _tool_classes() if "read_only" not in attrs]
    assert not undeclared, "declare read_only on: " + ", ".join(undeclared)


def test_tools_that_need_approval_are_never_research():
    """A tool that always shows an approval card does something worth asking about."""
    leaks = []
    for path, cls, attrs in _tool_classes():
        if not attrs.get("requires_approval") or attrs["_per_call_approval"]:
            continue
        research = attrs.get("plan_research")
        if research is None:
            research = attrs.get("read_only") is True
        if research:
            leaks.append(f"{path}:{cls}")
    assert not leaks, "approval-gated tools marked as research: " + ", ".join(leaks)


def test_third_party_mcp_calls_are_never_research():
    research = {attrs["name"] for _, _, attrs in _tool_classes()
                if attrs.get("plan_research", attrs.get("read_only") is True)}
    for name in ("mcp_proxy", "mcp_execute", "mcp_read_resource", "mcp_get_prompt", "mcp_list_tools"):
        assert name not in research


def _tool(**flags):
    cls = type("T", (BaseTool,), {"name": "t", "description": "d", "parameters": {},
                                  "execute": lambda self, **kw: None, **flags})
    return cls()


def test_research_follows_read_only_unless_declared():
    assert is_plan_research_tool(_tool(read_only=True))
    assert not is_plan_research_tool(_tool(read_only=False))
    assert not is_plan_research_tool(_tool())  # undeclared counts as an action
    assert not is_plan_research_tool(_tool(read_only=True, plan_research=False))
    assert not is_plan_research_tool(None)
