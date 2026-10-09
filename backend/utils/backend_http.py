"""HTTP calls from a tool to the running Guaardvark backend.

The MCP server runs tools in a process with no Flask app (see
``backend/mcp/server.py``). A tool that needs the database, a plugin or a job
queue asks the backend over its REST API through ``request_json`` instead, so
the backend keeps owning validation, scoping and side effects.

Writes are never retried here: a caller that retries must bring its own
deduplication.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 30.0
API_KEY_HEADER = "X-API-Key"


class BackendError(RuntimeError):
    """A backend call that did not produce a usable answer.

    ``kind`` is one of ``unreachable`` (nothing answering), ``timeout``,
    ``auth`` (401/403), ``plugin_offline`` (503), ``http`` (any other 4xx/5xx)
    or ``malformed`` (a success status whose body is not JSON).
    """

    def __init__(self, kind: str, message: str, status: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.body = body


@dataclass
class BackendResponse:
    status: int
    body: Any
    # ``body["data"]`` for the standard success envelope, otherwise the body.
    data: Any


def _checkout_env_value(key: str, env_file: Optional[Path] = None) -> str:
    """``key`` from this checkout's ``.env`` (last line wins), or ""."""
    env_file = env_file or Path(__file__).resolve().parents[2] / ".env"
    value = ""
    try:
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{key}="):
                value = line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return value


def backend_base_url() -> str:
    """Where the backend answers HTTP. ``GUAARDVARK_URL`` wins; otherwise the
    port ``start.sh`` recorded in ``.env`` (macOS writes 5055); otherwise 5000."""
    url = (os.environ.get("GUAARDVARK_URL") or "").strip()
    if url:
        return url.rstrip("/")
    port = (os.environ.get("FLASK_PORT") or "").strip() or _checkout_env_value("FLASK_PORT")
    return f"http://127.0.0.1:{port or '5000'}"


def backend_api_key() -> str:
    """The API key to send, or "".

    For this checkout's own backend (no ``GUAARDVARK_URL``), the key in its
    ``.env`` comes first and is read on every call: that is the key the backend
    runs with, and Settings → Access rewrites it, so a key created or replaced
    there works here without restarting the MCP client. Otherwise, and when
    ``.env`` has none, ``GUAARDVARK_API_KEY`` from this process's environment.
    """
    if not (os.environ.get("GUAARDVARK_URL") or "").strip():
        saved = _checkout_env_value("GUAARDVARK_API_KEY")
        if saved:
            return saved
    return (os.environ.get("GUAARDVARK_API_KEY") or "").strip()


def in_mcp_process() -> bool:
    """True inside the MCP server process (``backend/mcp/__main__.py`` sets it)."""
    return os.environ.get("GUAARDVARK_MCP_PROCESS") == "1"


def is_mcp_transport(tool: Any) -> bool:
    """True when the MCP adapter is the one calling this tool."""
    context = getattr(tool, "_context", None) or {}
    return context.get("transport") == "mcp"


# Body field run_tool_in_backend adds so POST /api/tools/execute knows the call
# came from an MCP client.
CALLER_TRANSPORT_FIELD = "caller_transport"

_caller = threading.local()


@contextlib.contextmanager
def calls_for_mcp_client(active: bool = True):
    """Treat tool calls made on this thread as calls from an MCP client.

    POST /api/tools/execute enters this for calls that run_tool_in_backend
    forwarded, so a tool the MCP server hands to the backend keeps the input
    rules it has over MCP. The mark is thread-local and only ever makes those
    rules stricter, so a caller that sets it on purpose gains nothing.
    """
    previous = getattr(_caller, "mcp", False)
    _caller.mcp = previous or bool(active)
    try:
        yield
    finally:
        _caller.mcp = previous


def is_mcp_caller(tool: Any) -> bool:
    """True when an MCP client asked for this call, in either process.

    ``is_mcp_transport`` answers only "am I in the MCP server, so hand the
    work to the backend?" and is false once the backend runs the tool. Rules
    that are stricter for MCP clients (which files a tool may read) check this
    instead: it is also true inside the backend for a call run_tool_in_backend
    forwarded.
    """
    return is_mcp_transport(tool) or bool(getattr(_caller, "mcp", False))


def _error_message(body: Any, fallback: str) -> str:
    if isinstance(body, dict):
        for key in ("error", "message", "detail"):
            value = body.get(key)
            if isinstance(value, dict):
                value = value.get("message") or value.get("detail")
            if value:
                return str(value)
    return fallback


def request_json(
    method: str,
    path: str,
    *,
    payload: Any = None,
    params: Optional[Mapping[str, Any]] = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    headers: Optional[Mapping[str, str]] = None,
) -> BackendResponse:
    """One call to the backend REST API. Raises ``BackendError`` on failure."""
    import requests

    base = backend_base_url()
    send_headers = dict(headers or {})
    api_key = backend_api_key()
    if api_key:
        send_headers[API_KEY_HEADER] = api_key

    try:
        resp = requests.request(
            method,
            base + path,
            json=payload,
            params=params,
            headers=send_headers,
            timeout=(connect_timeout, read_timeout),
        )
    except requests.exceptions.ConnectionError as exc:
        logger.debug("backend call %s %s could not connect: %s", method, path, exc)
        raise BackendError(
            "unreachable",
            f"The Guaardvark backend is not answering at {base}. Start it with ./start.sh.",
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise BackendError(
            "timeout",
            f"The Guaardvark backend did not answer {method} {path} within {read_timeout:.0f} s.",
        ) from exc

    try:
        body = resp.json()
    except ValueError:
        body = None

    if resp.status_code >= 400:
        message = _error_message(body, (resp.text or "").strip()[:200] or f"HTTP {resp.status_code}")
        if resp.status_code in (401, 403):
            kind = "auth"
            message += (
                " (the backend needs its API key: set GUAARDVARK_API_KEY in this client's"
                " environment, or in the .env of the Guaardvark checkout it runs from)"
            )
        elif resp.status_code == 503:
            kind = "plugin_offline"
            message += " (start the plugin from the Studio Plugins page or POST /api/plugins/<id>/start)"
        else:
            kind = "http"
        raise BackendError(kind, message, status=resp.status_code, body=body)

    if body is None and resp.content:
        raise BackendError(
            "malformed",
            f"{method} {path} answered HTTP {resp.status_code} with a body that is not JSON.",
            status=resp.status_code,
        )

    data = body.get("data", body) if isinstance(body, dict) else body
    return BackendResponse(status=resp.status_code, body=body, data=data)


def http_json(method: str, path: str, payload: Any = None, timeout: float = DEFAULT_READ_TIMEOUT) -> Any:
    """``request_json`` returning only ``data``."""
    return request_json(method, path, payload=payload, read_timeout=timeout).data


def run_tool_in_backend(tool_name: str, arguments: Mapping[str, Any], read_timeout: float = DEFAULT_READ_TIMEOUT):
    """Run a registered tool inside the backend process and return its ToolResult.

    For tools whose work belongs to the backend: its database session, Flask
    config, Celery dispatch or GPU queue. POST /api/tools/execute runs the
    same tool through the backend's registry, so behaviour matches chat,
    except where a tool is stricter with MCP clients: the call is marked as
    coming from one (``CALLER_TRANSPORT_FIELD``), and ``is_mcp_caller`` is
    true while the backend runs it. Arguments starting with ``_`` are
    internal to the calling process and are not sent.
    """
    from backend.services.agent_tools import ToolResult

    parameters = {k: v for k, v in arguments.items() if not str(k).startswith("_")}
    try:
        body = request_json(
            "POST", "/api/tools/execute",
            payload={"tool_name": tool_name, "parameters": parameters, CALLER_TRANSPORT_FIELD: "mcp"},
            read_timeout=read_timeout,
        ).body or {}
    except BackendError as e:
        return ToolResult(success=False, error=str(e), metadata={"backend_error": e.kind})
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        return ToolResult(success=False, error=f"The backend returned no result for {tool_name}.")
    return ToolResult(
        success=bool(result.get("success")),
        output=result.get("output"),
        error=result.get("error"),
        metadata=result.get("metadata") or {},
    )
