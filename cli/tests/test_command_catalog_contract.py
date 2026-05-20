"""Contracts between shared command catalog and slash router."""

from llx.command_catalog import COMMAND_TREE
from llx.slash import SlashRouter


def _make_router() -> SlashRouter:
    return SlashRouter(
        {
            "server": "http://localhost:5002",
            "session_id": "test-session",
            "message_count": 0,
            "agent_mode": False,
        }
    )


def test_catalog_commands_are_registered_in_router():
    router = _make_router()
    names = set(router.get_command_names())
    # "exit" is an alias to /quit and intentionally not listed separately.
    catalog = set(COMMAND_TREE.keys()) - {"exit"}
    assert catalog.issubset(names)
    assert "quality" in names


def test_router_subapp_dispatch_does_not_mutate_sys_argv():
    import sys
    from unittest.mock import patch

    router = _make_router()
    original = list(sys.argv)
    with patch("llx.main.app") as mock_app:
        router.dispatch("/models list")
        mock_app.assert_called_once()
    assert sys.argv == original


def test_router_quality_subapp_dispatches():
    from unittest.mock import patch

    router = _make_router()
    with patch("llx.main.app") as mock_app:
        router.dispatch("/quality scorecard --json")
        mock_app.assert_called_once()
