"""
OpenAI-compatible cloud LLM provider for normal chat.

Selectable at runtime via the ``llm_provider`` setting (see
:mod:`backend.services.llm_provider`). Because it speaks the OpenAI chat
completions protocol, ONE client covers OpenAI, OpenRouter, Groq, Together,
vLLM, LM Studio, and Ollama's OpenAI-compatible endpoint — only
``GUAARDVARK_OPENAI_BASE_URL`` changes between them.

Two surfaces are exposed so both call sites in the codebase work unchanged:

1. :func:`chat` — mimics ``ollama.chat``'s streaming interface, yielding chunks
   shaped like ``{"message": {"content": "..."}}`` with a final ``done=True``
   chunk carrying token counts. ``unified_chat_engine._call_llm_streaming``
   dispatches to this, so its token-emit / XML-tool-call / token-count loop runs
   untouched.
2. :class:`OpenAIChatLLM` — a LlamaIndex ``CustomLLM`` exposing ``.chat()`` /
   ``.complete()``, so the ``llm_service`` helpers route through too.

Tool calling in this codebase is XML-in-the-prompt (see
``backend.utils.agent_output_parser.parse_tool_calls_xml``), not native
function-calling, so the provider only has to stream text.

Only the standard library + ``requests`` are used; no OpenAI SDK is pulled in.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterator, List, Optional

import requests

from backend import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Availability + config helpers
# ---------------------------------------------------------------------------
def available() -> bool:
    """True when an OpenAI-compatible endpoint is usable (key set, or a custom base URL)."""
    if config.OPENAI_API_KEY:
        return True
    return bool(config.OPENAI_BASE_URL and config.OPENAI_BASE_URL != "https://api.openai.com/v1")


def _headers() -> Dict[str, str]:
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if config.OPENAI_API_KEY:
        headers["Authorization"] = f"Bearer {config.OPENAI_API_KEY}"
    return headers


def _map_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Translate Ollama-style sampling options to OpenAI chat params.

    Ollama-only knobs (num_ctx, num_keep, top_k, repeat_penalty, ...) have no
    OpenAI-compatible equivalent and are dropped.
    """
    out: Dict[str, Any] = {}
    if not options:
        return out
    if options.get("temperature") is not None:
        out["temperature"] = options["temperature"]
    if options.get("top_p") is not None:
        out["top_p"] = options["top_p"]
    np = options.get("num_predict")
    if isinstance(np, int) and np > 0:
        out["max_tokens"] = np
    return out


def _normalize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Coerce incoming messages to the OpenAI ``[{role, content}]`` shape."""
    norm: List[Dict[str, str]] = []
    for m in messages:
        role = (m.get("role") or "user").strip()
        if role not in ("system", "user", "assistant", "tool"):
            role = "user"
        content = m.get("content", "")
        if content is None:
            content = ""
        norm.append({"role": role, "content": str(content)})
    return norm


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------
def list_models() -> List[Dict[str, Any]]:
    """Return chat models as ``[{"name", "id"}]``, or a small static fallback."""
    fallback = [
        {"name": config.OPENAI_DEFAULT_MODEL, "id": config.OPENAI_DEFAULT_MODEL},
        {"name": "gpt-4o", "id": "gpt-4o"},
        {"name": "gpt-4o-mini", "id": "gpt-4o-mini"},
    ]
    if not available():
        return []
    try:
        resp = requests.get(
            f"{config.OPENAI_BASE_URL}/models",
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        models = []
        for entry in data:
            mid = entry.get("id")
            if not mid:
                continue
            if "embed" in mid.lower() or "whisper" in mid.lower() or "tts" in mid.lower():
                continue
            models.append({"name": mid, "id": mid})
        models.sort(key=lambda m: m["name"])
        return models or fallback
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not list OpenAI-compatible models, using fallback list: %s", e)
        return fallback


# ---------------------------------------------------------------------------
# Streaming chat — Ollama-shaped, drop-in for ollama.chat()
# ---------------------------------------------------------------------------
def chat(
    model: str,
    messages: List[Dict[str, Any]],
    stream: bool = True,
    options: Optional[Dict[str, Any]] = None,
    **_kwargs: Any,
):
    """Call the OpenAI-compatible chat completions endpoint.

    Returns chunks shaped exactly like ``ollama.chat``:
      - streaming: a generator of ``{"message": {"content": tok}, "done": bool}``
        with a final ``done=True`` chunk carrying ``prompt_eval_count`` /
        ``eval_count``.
      - non-streaming: a single dict in the same shape with ``done=True``.
    """
    if not available():
        raise RuntimeError("OpenAI-compatible provider selected but not configured.")

    model = model or config.OPENAI_DEFAULT_MODEL
    payload: Dict[str, Any] = {
        "model": model,
        "messages": _normalize_messages(messages),
        "stream": bool(stream),
        **_map_options(options),
    }

    if not stream:
        resp = requests.post(
            f"{config.OPENAI_BASE_URL}/chat/completions",
            headers=_headers(),
            json=payload,
            timeout=config.OPENAI_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        content = ""
        try:
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            content = ""
        usage = body.get("usage", {}) or {}
        return {
            "message": {"content": content},
            "done": True,
            "prompt_eval_count": usage.get("prompt_tokens", 0) or 0,
            "eval_count": usage.get("completion_tokens", 0) or 0,
        }

    return _stream_chat(payload)


def _stream_chat(payload: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Yield Ollama-shaped chunks from the OpenAI-compatible SSE stream."""
    prompt_tokens = 0
    completion_tokens = 0
    with requests.post(
        f"{config.OPENAI_BASE_URL}/chat/completions",
        headers=_headers(),
        json=payload,
        stream=True,
        timeout=config.OPENAI_REQUEST_TIMEOUT,
    ) as resp:
        resp.raise_for_status()
        # Force UTF-8: the SSE body is JSON. requests defaults resp.encoding to
        # ISO-8859-1 when the server sends no charset, which mangles every
        # non-ASCII UTF-8 char (em-dash —, smart quotes) into mojibake.
        resp.encoding = "utf-8"
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if not raw.startswith("data:"):
                continue
            data = raw[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            usage = obj.get("usage")
            if usage:
                prompt_tokens = usage.get("prompt_tokens", prompt_tokens) or prompt_tokens
                completion_tokens = usage.get("completion_tokens", completion_tokens) or completion_tokens
            try:
                delta = obj["choices"][0].get("delta", {}) or {}
            except (KeyError, IndexError, TypeError):
                delta = {}
            token = delta.get("content") or ""
            if token:
                yield {"message": {"content": token}, "done": False}
    yield {
        "message": {"content": ""},
        "done": True,
        "prompt_eval_count": prompt_tokens,
        "eval_count": completion_tokens,
    }


def complete(prompt: str, model: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> str:
    """Non-streaming single-prompt convenience wrapper returning plain text."""
    result = chat(
        model=model or config.OPENAI_DEFAULT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        options=options,
    )
    return (result.get("message", {}) or {}).get("content", "") or ""


# ---------------------------------------------------------------------------
# LlamaIndex-compatible wrapper (for llm_service / self.llm callers)
# ---------------------------------------------------------------------------
def make_llamaindex_llm(model: Optional[str] = None):
    """Build a LlamaIndex ``CustomLLM`` backed by the OpenAI-compatible endpoint,
    or None if unavailable. Imported lazily (no hard LlamaIndex dependency)."""
    if not available():
        return None
    try:
        from llama_index.core.llms import (
            CustomLLM,
            CompletionResponse,
            CompletionResponseGen,
            LLMMetadata,
        )
        from llama_index.core.llms.callbacks import llm_completion_callback
        from llama_index.core.base.llms.types import ChatMessage, ChatResponse, MessageRole
    except Exception as e:  # noqa: BLE001
        logger.error("LlamaIndex not available for OpenAIChatLLM wrapper: %s", e)
        return None

    resolved_model = model or config.OPENAI_DEFAULT_MODEL

    class OpenAIChatLLM(CustomLLM):
        model: str = resolved_model
        context_window: int = 64000
        num_output: int = 4096

        @property
        def metadata(self) -> "LLMMetadata":
            return LLMMetadata(
                context_window=self.context_window,
                num_output=self.num_output,
                model_name=self.model,
                is_chat_model=True,
            )

        @llm_completion_callback()
        def complete(self, prompt: str, **kwargs: Any) -> "CompletionResponse":
            text = complete(prompt, model=self.model)
            return CompletionResponse(text=text)

        @llm_completion_callback()
        def stream_complete(self, prompt: str, **kwargs: Any) -> "CompletionResponseGen":
            acc = ""
            for chunk in chat(self.model, [{"role": "user", "content": prompt}], stream=True):
                tok = (chunk.get("message", {}) or {}).get("content", "")
                if tok:
                    acc += tok
                    yield CompletionResponse(text=acc, delta=tok)

        def chat(self, messages, **kwargs: Any) -> "ChatResponse":
            dict_messages = [
                {"role": getattr(m.role, "value", str(m.role)), "content": m.content or ""}
                for m in messages
            ]
            result = globals()["chat"](self.model, dict_messages, stream=False)
            content = (result.get("message", {}) or {}).get("content", "") or ""
            return ChatResponse(
                message=ChatMessage(role=MessageRole.ASSISTANT, content=content)
            )

    return OpenAIChatLLM()
