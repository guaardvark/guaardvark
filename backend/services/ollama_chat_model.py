"""Resolve a preferred chat model against what Ollama has actually pulled.

Several callers carry a preferred model tag — the Film Crew agents want a
Gemma4, the music-video director a small JSON-reliable one — while the
installer's hardware policy decides which tag a machine actually gets
(``gemma4:e2b`` on most boxes, ``llama3.2:1b`` on small ones). A caller that
sends its preferred tag straight to Ollama fails with a 404 on every machine
where the policy chose differently, which is most clean installs.

``resolve_chat_model`` keeps the caller's preference when it is available and
otherwise picks the closest thing that is installed, so a hard-coded tag is a
preference rather than a requirement.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Embedding models are pulled for RAG but reject /api/chat, so they must never
# be chosen as a chat fallback.
_EMBEDDING_MARKERS = ("embed", "embedding", "bge", "nomic", "snowflake", "e5", "gte", "minilm")


def is_embedding_model(name: Optional[str]) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(marker in n for marker in _EMBEDDING_MARKERS)


def installed_chat_tags() -> set[str]:
    """Tags Ollama reports as pulled, minus embedding models. Empty on any failure.

    Tolerates both ollama-lib shapes: ``ListResponse`` with ``Model`` objects
    (tag under ``.model``) and the older list of dicts (``model``/``name``).
    """
    try:
        import ollama
        resp = ollama.list()
    except Exception as e:  # Ollama down, library missing — caller keeps its preference
        logger.debug("ollama.list() unavailable: %s", e)
        return set()
    models = resp.get("models", []) if hasattr(resp, "get") else getattr(resp, "models", [])
    tags: set[str] = set()
    for m in models or []:
        tag = getattr(m, "model", None)
        if tag is None and hasattr(m, "get"):
            tag = m.get("model") or m.get("name")
        if tag is None:
            tag = getattr(m, "name", None)
        if tag and not is_embedding_model(tag):
            tags.add(tag)
    return tags


def _family(tag: str) -> str:
    return tag.split(":", 1)[0].lower()


def _saved_active_model() -> Optional[str]:
    try:
        from backend.utils.llm_service import get_saved_active_model_name
        return get_saved_active_model_name()
    except Exception:
        return None


def _policy_model() -> Optional[str]:
    """The chat model the hardware policy installs on this machine."""
    try:
        from backend.services.hardware_policy import _load_hardware, model_tier
        hw = _load_hardware() or {}
        gpu = hw.get("gpu", {}) or {}
        ram_gb = (hw.get("ram", {}) or {}).get("total_gb", 0)
        return model_tier(ram_gb, gpu, hw.get("arch", ""))["chat"]
    except Exception:
        return None


def resolve_chat_model(preferred: str, *, installed: Optional[Iterable[str]] = None) -> str:
    """Return an installed chat model, honouring ``preferred`` when possible.

    Order:
      1. ``preferred`` itself, if installed.
      2. An installed tag of the same family (``gemma4:e4b`` -> ``gemma4:e2b``).
      3. The saved active chat model, if installed.
      4. The hardware policy's tier model for this machine, if installed.
      5. Any installed Gemma, then any installed chat model.
      6. ``preferred`` unchanged — nothing is installed or Ollama is unreachable,
         and the call that follows fails with Ollama's own error.

    ``installed`` is injectable for tests; production reads Ollama.
    """
    tags = set(installed) if installed is not None else installed_chat_tags()
    tags = {t for t in tags if not is_embedding_model(t)}
    if not tags:
        return preferred

    if preferred and preferred in tags:
        return preferred

    def _log(choice: str, why: str) -> str:
        logger.info("chat model %r not installed; using %r (%s)", preferred, choice, why)
        return choice

    if preferred:
        family = _family(preferred)
        same_family = sorted(t for t in tags if _family(t) == family)
        if same_family:
            return _log(same_family[0], "same family")

    active = _saved_active_model()
    if active and active in tags:
        return _log(active, "saved active model")

    policy = _policy_model()
    if policy and policy in tags:
        return _log(policy, "hardware policy tier")

    gemmas = sorted(t for t in tags if "gemma" in t.lower())
    if gemmas:
        return _log(gemmas[0], "installed gemma")

    return _log(sorted(tags)[0], "first installed chat model")
