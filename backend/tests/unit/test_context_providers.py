import inspect

import pytest

from backend.services import context_providers as cp


@pytest.fixture(autouse=True)
def restore_registry():
    """Leave the process-wide registry exactly as the test found it."""
    snapshot = dict(cp._providers)
    yield
    cp._providers.clear()
    cp._providers.update(snapshot)


def test_page_provider_is_registered_at_import():
    assert "page" in cp.list_context_providers()


def test_register_and_unregister():
    cp.register_context_provider("crew", lambda pc, o: "crew block")

    assert "crew" in cp.list_context_providers()
    assert "crew block" in cp.build_context_block(None, {})

    assert cp.unregister_context_provider("crew") is True
    assert "crew" not in cp.list_context_providers()
    assert cp.unregister_context_provider("crew") is False


def test_register_rejects_bad_arguments():
    with pytest.raises(ValueError):
        cp.register_context_provider("", lambda pc, o: None)
    with pytest.raises(ValueError):
        cp.register_context_provider("nope", "not callable")


def test_blocks_render_in_registration_order_separated_by_blank_lines():
    cp.unregister_context_provider("page")
    cp.register_context_provider("first", lambda pc, o: "one")
    cp.register_context_provider("second", lambda pc, o: "two")
    cp.register_context_provider("third", lambda pc, o: "three")

    assert cp.list_context_providers() == ["first", "second", "third"]
    assert cp.build_context_block(None, {}) == "one\n\ntwo\n\nthree"


def test_empty_and_none_results_are_skipped():
    cp.unregister_context_provider("page")
    cp.register_context_provider("none", lambda pc, o: None)
    cp.register_context_provider("blank", lambda pc, o: "   ")
    cp.register_context_provider("real", lambda pc, o: "kept")

    assert cp.build_context_block(None, {}) == "kept"


def test_a_failing_provider_does_not_break_the_others():
    cp.unregister_context_provider("page")

    def boom(page_context, options):
        raise RuntimeError("provider exploded")

    cp.register_context_provider("before", lambda pc, o: "before")
    cp.register_context_provider("boom", boom)
    cp.register_context_provider("after", lambda pc, o: "after")

    assert cp.build_context_block(None, {}) == "before\n\nafter"


def test_providers_receive_page_context_and_options():
    cp.unregister_context_provider("page")
    seen = {}

    def spy(page_context, options):
        seen["page_context"] = page_context
        seen["options"] = options
        return None

    cp.register_context_provider("spy", spy)
    cp.build_context_block({"page": "Clients"}, {"use_rag": True})

    assert seen["page_context"] == {"page": "Clients"}
    assert seen["options"] == {"use_rag": True}


def test_page_provider_renders_page_with_entity():
    rendered = cp.page_context_provider(
        {"page": "Clients", "entityType": "client", "entityId": 42}, {}
    )
    assert rendered == "The user is viewing the Clients page (client 42)."


def test_page_provider_renders_page_without_entity():
    rendered = cp.page_context_provider(
        {"page": "Dashboard", "entityType": None, "entityId": None}, {}
    )
    assert rendered == "The user is viewing the Dashboard page."


@pytest.mark.parametrize(
    "page_context",
    [
        None,
        "not a dict",
        {},
        {"page": "Unknown", "entityType": None, "entityId": None},
        {"page": "   "},
        {"page": 7},
    ],
)
def test_page_provider_returns_nothing_without_a_usable_page(page_context):
    assert cp.page_context_provider(page_context, {}) is None


def test_engine_turn_context_includes_the_provider_block():
    """Guard the one call site in the engine's per-turn context assembly.

    The assembly is inline in ``UnifiedChatEngine._run_chat``, which cannot be
    run without a model and a database, so assert the wiring, not the output.
    """
    from backend.services.unified_chat_engine import UnifiedChatEngine

    source = inspect.getsource(UnifiedChatEngine._run_chat)
    assert "build_context_block" in source
    assert 'options if isinstance(options, dict) else {}' in source
    assert '"page_context"' in source
    assert "Current context:" in source
