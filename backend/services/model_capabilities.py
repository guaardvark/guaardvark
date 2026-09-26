"""One capability record per Ollama model tag.

Built from Ollama's own description of the model (``/api/show``, through
``ollama_resource_manager.get_model_info`` and its cache), then any declared
row for the tag (``model_capability_data.MODEL_CAPABILITY_ROWS`` and, on this
machine, ``data/config/model_capabilities.json``). Vision is the capability
resolver's answer (``model_capability_resolver.sees_natively``), which has its
own authority order.

Where a name rule decides something Ollama also reports, the record keeps both
answers side by side instead of choosing: ``thinking`` is what Ollama says,
``thinks_by_name`` is the THINKING_NAME_PATTERNS rule, and the helpers that
send ``think:false`` still use either, as they did before. Each field names
where it came from in ``evidence``.

Importable from Flask, Celery and the MCP server alike: no app context.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.services.model_capability_data import (
    MODEL_CAPABILITY_ROWS,
    OVERRIDABLE_FIELDS,
    THINKING_NAME_PATTERNS,
)

logger = logging.getLogger(__name__)

LOCAL_ROWS_PATH = Path(__file__).resolve().parents[2] / "data" / "config" / "model_capabilities.json"

_local_lock = threading.Lock()
_local_cache: Dict[str, Any] = {"mtime": None, "rows": {}}


@dataclass(frozen=True)
class ModelRecord:
    tag: str
    exists: bool                  # Ollama described the model
    capabilities: Tuple[str, ...]  # /api/show "capabilities", as reported
    completion: bool
    tools: bool
    thinking: bool                # Ollama lists "thinking"
    thinks_by_name: bool          # THINKING_NAME_PATTERNS matches the tag
    vision: bool
    embedding: bool
    native_context: int           # <arch>.context_length (0 when unknown)
    embedding_dim: int            # <arch>.embedding_length (0 when unknown)
    parameter_count: int
    size_mb: float
    architecture: str
    evidence: Dict[str, str] = field(default_factory=dict)

    @property
    def sends_think_flag(self) -> bool:
        """Whether think:false is sent to this model (model_supports_thinking)."""
        return self.thinks_by_name or self.thinking


def thinks_by_name(tag: Optional[str]) -> bool:
    lower = (tag or "").lower()
    return bool(lower) and any(re.search(p, lower) for p in THINKING_NAME_PATTERNS)


def _local_rows() -> Dict[str, Dict[str, Any]]:
    """data/config/model_capabilities.json, re-read when it changes."""
    try:
        mtime = LOCAL_ROWS_PATH.stat().st_mtime
    except OSError:
        return {}
    with _local_lock:
        if _local_cache["mtime"] == mtime:
            return _local_cache["rows"]
        try:
            rows = json.loads(LOCAL_ROWS_PATH.read_text(encoding="utf-8"))
            if not isinstance(rows, dict):
                raise ValueError("expected an object keyed by model tag")
        except Exception as e:  # noqa: BLE001 - a broken file must not break chat
            logger.warning("Ignoring %s: %s", LOCAL_ROWS_PATH, e)
            rows = {}
        _local_cache.update(mtime=mtime, rows=rows)
        return rows


def declared_row(tag: str) -> Tuple[Dict[str, Any], str]:
    """The declared fields for ``tag`` and where they came from."""
    local = _local_rows().get(tag)
    if isinstance(local, dict):
        return {k: v for k, v in local.items() if k in OVERRIDABLE_FIELDS}, "local_row"
    shipped = MODEL_CAPABILITY_ROWS.get(tag)
    if isinstance(shipped, dict):
        return {k: v for k, v in shipped.items() if k in OVERRIDABLE_FIELDS}, "declared_row"
    return {}, ""


def _info(tag: str) -> Optional[dict]:
    try:
        from backend.utils.ollama_resource_manager import get_model_info
        return get_model_info(tag)
    except Exception as e:  # noqa: BLE001
        logger.debug("model info lookup failed for %r: %s", tag, e)
        return None


def _vision(tag: str) -> Tuple[bool, str]:
    try:
        from backend.services.model_capability_resolver import _vision_with_evidence
        return _vision_with_evidence(tag)
    except Exception as e:  # noqa: BLE001
        logger.debug("vision lookup failed for %r: %s", tag, e)
        return False, "unavailable"


def capabilities_for(tag: Optional[str], *, with_vision: bool = True) -> ModelRecord:
    """The record for ``tag``. Cheap to call: /api/show is cached by
    get_model_info (5 minutes; 15 s for a model Ollama could not describe)."""
    tag = tag or ""
    return record_from_info(tag, _info(tag) if tag else None, with_vision=with_vision)


def record_from_info(tag: str, info: Optional[dict], *, with_vision: bool = True) -> ModelRecord:
    """The record for ``tag`` from a get_model_info result the caller already has."""
    caps = tuple(str(c) for c in ((info or {}).get("capabilities") or []))
    source = "api_show" if info is not None else "ollama_unreachable"
    values: Dict[str, Any] = {
        "completion": "completion" in caps,
        "tools": "tools" in caps,
        "thinking": "thinking" in caps,
        "embedding": "embedding" in caps,
        "native_context": int((info or {}).get("native_context") or 0),
        "embedding_dim": int((info or {}).get("embedding_length") or 0),
    }
    evidence = {k: source for k in values}
    row, row_source = declared_row(tag)
    for key, value in row.items():
        values[key] = value
        evidence[key] = row_source

    by_name = thinks_by_name(tag)
    evidence["thinks_by_name"] = "name_pattern"
    if with_vision:
        vision, evidence["vision"] = _vision(tag)
    else:
        vision, evidence["vision"] = False, "not_asked"

    return ModelRecord(
        tag=tag,
        exists=info is not None,
        capabilities=caps,
        completion=bool(values["completion"]),
        tools=bool(values["tools"]),
        thinking=bool(values["thinking"]),
        thinks_by_name=by_name,
        vision=bool(vision),
        embedding=bool(values["embedding"]),
        native_context=int(values["native_context"]),
        embedding_dim=int(values["embedding_dim"]),
        parameter_count=int((info or {}).get("parameter_count") or 0),
        size_mb=float((info or {}).get("size_mb") or 0.0),
        architecture=(info or {}).get("architecture") or "unknown",
        evidence=evidence,
    )
