"""Per-turn context providers for the unified chat engine.

A distribution built on this engine registers a provider to inject its own
awareness — the page the user is on, the record they have open, the job that is
running — into each chat turn without forking the engine.

A provider is ``fn(page_context, options) -> str | None`` and is called once per
turn, so it must be cheap and must not block. A provider that raises is skipped:
awareness is an enhancement, and a broken one must never break chat.

    from backend.services.context_providers import register_context_provider

    def crew_provider(page_context, options):
        crew = options.get("crew_id")
        return f"The user is dispatching crew {crew}." if crew else None

    register_context_provider("crew", crew_provider)
"""

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ContextProvider = Callable[[Optional[Dict[str, Any]], Dict[str, Any]], Optional[str]]

# Insertion-ordered: providers render in the order they were registered.
_providers: Dict[str, ContextProvider] = {}
_lock = threading.Lock()


def register_context_provider(name: str, fn: ContextProvider) -> None:
    """Register ``fn`` under ``name``, replacing any provider of the same name.

    Re-registering a name keeps the original position in the render order.
    """
    if not name or not isinstance(name, str):
        raise ValueError("context provider name must be a non-empty string")
    if not callable(fn):
        raise ValueError(f"context provider {name!r} must be callable")
    with _lock:
        _providers[name] = fn


def unregister_context_provider(name: str) -> bool:
    """Remove the provider registered under ``name``. Returns whether one existed."""
    with _lock:
        return _providers.pop(name, None) is not None


def list_context_providers() -> List[str]:
    """Return registered provider names in render order."""
    with _lock:
        return list(_providers)


PAGE_PROVIDER_NAME = "page"


def build_context_entries(
    page_context: Optional[Dict[str, Any]], options: Dict[str, Any]
) -> List[Tuple[str, str]]:
    """Render every registered provider as ``(name, text)`` in render order.

    Empty results are skipped and a provider that raises is logged and dropped,
    so the result degrades to whatever the healthy providers produced. The
    names let a caller tell the built-in page hint from facts a distribution
    supplied.
    """
    with _lock:
        providers = list(_providers.items())

    entries: List[Tuple[str, str]] = []
    for name, fn in providers:
        try:
            rendered = fn(page_context, options)
        except Exception as e:
            logger.warning(f"[CONTEXT_PROVIDERS] provider {name!r} failed: {e}")
            continue
        if isinstance(rendered, str) and rendered.strip():
            entries.append((name, rendered.strip()))
    return entries


def build_context_block(page_context: Optional[Dict[str, Any]], options: Dict[str, Any]) -> str:
    """Render every registered provider into one block, blank-line separated."""
    return "\n\n".join(text for _, text in build_context_entries(page_context, options))


def page_context_provider(
    page_context: Optional[Dict[str, Any]], options: Dict[str, Any]
) -> Optional[str]:
    """Render the ``{page, entityType, entityId}`` context the frontend sends.

    ``page`` of ``"Unknown"`` is the frontend's sentinel for an unmapped route
    and yields no block.
    """
    if not isinstance(page_context, dict):
        return None

    page = page_context.get("page")
    if not isinstance(page, str):
        return None
    page = page.strip()
    if not page or page == "Unknown":
        return None

    entity_type = page_context.get("entityType") or page_context.get("entity_type")
    entity_id = page_context.get("entityId")
    if entity_id is None:
        entity_id = page_context.get("entity_id")

    if entity_type and entity_id is not None and str(entity_id).strip():
        return f"The user is viewing the {page} page ({entity_type} {entity_id})."
    return f"The user is viewing the {page} page."


register_context_provider(PAGE_PROVIDER_NAME, page_context_provider)

try:
    # Prompt-format awareness for models that need a structured prompt; the
    # provider itself decides when it applies (video pages, or an H3 model in
    # the options) and costs nothing otherwise.
    from backend.services.h3_prompt_compiler import chat_context_provider as _h3_provider
    register_context_provider("h3_prompting", _h3_provider)
except Exception as _e:  # pragma: no cover - awareness is an enhancement
    logger.debug(f"[CONTEXT_PROVIDERS] h3 provider not registered: {_e}")
