#!/usr/bin/env python3
"""MCP (Model Context Protocol) client service.

Built on the official ``mcp`` SDK, which handles JSON-RPC framing, request/
response correlation, notifications and pings. Servers are local programs
spoken to over stdio; remote transports are not used.

Threading model: the service owns one asyncio event loop on a daemon thread.
Every connected server has a long-lived *holder task* on that loop which
keeps the stdio transport + ``ClientSession`` context open (anyio requires those
to be entered and exited by the same task), pings the server periodically,
and tears everything down on disconnect or failure. Synchronous callers
(Flask handlers, agent tools, Celery) use the public methods below, which
submit coroutines to the loop with ``run_coroutine_threadsafe``.

Public API is synchronous and returns plain dicts:
``connect``, ``disconnect``, ``list_tools``, ``call_tool``, ``list_resources``,
``read_resource``, ``list_prompts``, ``get_prompt``, ``list_configured_servers``,
``get_server``, ``get_state``, ``get_audit_log``, ``reload_config``,
``upsert_server``, ``remove_server``, ``autoconnect``, ``shutdown``.
"""

from __future__ import annotations

import asyncio
import atexit
import collections
import concurrent.futures
import json
import logging
import os
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from backend import config as app_config
from backend.services import mcp_policy
from backend.services.mcp_config import (
    MCPConfigError,
    MCPServerConfig,
    build_child_env,
    load_server_configs,
    parse_server,
    read_config_file,
    write_config_file,
)

logger = logging.getLogger(__name__)

# How long an on-demand connect leaves a server alone after it failed to
# start. Unmeasured: long enough that one chat turn does not wait on it twice.
RECONNECT_COOLDOWN_S = 60

# Kept as module attributes for backwards compatibility with existing imports.
MCP_ENABLED = app_config.MCP_ENABLED
MCP_TIMEOUT = app_config.MCP_TIMEOUT

PING_INTERVAL_SECONDS = 30
PING_TIMEOUT_SECONDS = 10
STDERR_TAIL_BYTES = 4000
AUDIT_MAX_ENTRIES = 500
CLIENT_NAME = "guaardvark"

try:  # the SDK is optional at import time so the app still boots without it
    from mcp import ClientSession, StdioServerParameters
    from mcp import types as mcp_types
    from mcp.client.stdio import stdio_client
    MCP_SDK_AVAILABLE = True
    MCP_SDK_ERROR = None
    try:
        import importlib.metadata as _md

        MCP_SDK_VERSION = _md.version("mcp")
    except Exception:  # pragma: no cover
        MCP_SDK_VERSION = "unknown"
except Exception as _e:  # pragma: no cover - exercised only without the SDK
    MCP_SDK_AVAILABLE = False
    MCP_SDK_ERROR = str(_e)
    MCP_SDK_VERSION = None


def _sdk_timeout(seconds: float):
    """mcp 1.x takes a timedelta for read timeouts, mcp 2.x takes float seconds."""
    if MCP_SDK_VERSION and MCP_SDK_VERSION.split(".")[0].isdigit() and int(MCP_SDK_VERSION.split(".")[0]) < 2:
        from datetime import timedelta

        return timedelta(seconds=seconds)
    return float(seconds)


_CLIENT_VERSION: Optional[str] = None


def _client_version() -> str:
    """Backend version from backend/app.py, read as text: importing the app
    module would boot the whole Flask application."""
    global _CLIENT_VERSION
    if _CLIENT_VERSION is None:
        import re

        _CLIENT_VERSION = "unknown"
        try:
            app_py = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
            with open(app_py, encoding="utf-8") as f:
                m = re.search(r'^__version__\s*=\s*["\']([^"\']+)', f.read(), re.MULTILINE)
            if m:
                _CLIENT_VERSION = m.group(1)
        except OSError:
            pass
    return _CLIENT_VERSION


def _dump(model: Any) -> Any:
    """SDK model -> JSON-safe dict using wire (camelCase) field names."""
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump(by_alias=True, mode="json", exclude_none=True)
    return model


class MCPError(Exception):
    pass


@dataclass
class ServerRuntime:
    config: MCPServerConfig
    status: str = "disconnected"  # disconnected | connecting | connected | error
    session: Any = None
    task: Optional[asyncio.Task] = None
    stop_event: Optional[asyncio.Event] = None
    lock: Optional[asyncio.Lock] = None
    tools: List[Dict[str, Any]] = field(default_factory=list)
    resources: List[Dict[str, Any]] = field(default_factory=list)
    resource_templates: List[Dict[str, Any]] = field(default_factory=list)
    prompts: List[Dict[str, Any]] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    server_info: Dict[str, Any] = field(default_factory=dict)
    protocol_version: Optional[str] = None
    instructions: Optional[str] = None
    connected_at: Optional[float] = None
    last_error: Optional[str] = None
    call_count: int = 0
    failure: Optional[str] = None  # set by callers that observed a dead connection

    @property
    def connected(self) -> bool:
        return self.status == "connected" and self.session is not None


class MCPClientService:
    _instance: Optional["MCPClientService"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._initialized = False
                cls._instance = inst
            return cls._instance

    @classmethod
    def get_instance(cls) -> "MCPClientService":
        return cls()

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_lock = threading.Lock()
        self._runtimes: Dict[str, ServerRuntime] = {}
        self._config_errors: List[str] = []
        self._errors: collections.deque = collections.deque(maxlen=50)
        self._audit: collections.deque = collections.deque(maxlen=AUDIT_MAX_ENTRIES)
        self._audit_lock = threading.Lock()
        self._listeners: List[Callable[[str, str], None]] = []
        self._total_calls = 0
        self._started_at = time.time()
        self._shutdown = False
        self._load_configs()
        atexit.register(self._atexit_shutdown)
        logger.info(
            f"MCPClientService initialized (enabled={MCP_ENABLED}, sdk={MCP_SDK_AVAILABLE}, "
            f"servers={list(self._runtimes)})"
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @property
    def config_file(self) -> str:
        return app_config.MCP_CONFIG_FILE

    def _load_configs(self) -> None:
        configs, errors = load_server_configs(self.config_file, app_config.MCP_SERVERS_CONFIG)
        self._config_errors = errors
        for name, cfg in configs.items():
            rt = self._runtimes.get(name)
            if rt:
                rt.config = cfg
            else:
                self._runtimes[name] = ServerRuntime(config=cfg)
        for name in [n for n in self._runtimes if n not in configs]:
            rt = self._runtimes[name]
            if rt.connected or rt.status == "connecting":
                self._stop_runtime_sync(name)
            self._runtimes.pop(name, None)
            self._notify("removed", name)

    def reload_config(self) -> Dict[str, Any]:
        before = {n: rt.config.to_json() for n, rt in self._runtimes.items()}
        self._load_configs()
        changed = []
        for name, rt in list(self._runtimes.items()):
            if name in before and before[name] != rt.config.to_json() and rt.connected:
                changed.append(name)
                self._stop_runtime_sync(name)
            elif rt.connected:
                self._notify("tools_changed", name)  # policy may have changed
        # A server whose entry changed is started again with the new entry;
        # stopped only, its tools were gone from chat until something called it.
        restarted, failed = [], {}
        for name in changed:
            rt = self._runtimes.get(name)
            if rt is None or rt.config.disabled:
                continue
            res = self.connect(name)
            if res.get("success"):
                restarted.append(name)
            else:
                failed[name] = res.get("error") or "could not reconnect"
        return {
            "success": True,
            "servers": sorted(self._runtimes),
            "restarted": restarted,
            "stopped": [n for n in changed if n not in restarted],
            "reconnect_errors": failed,
            "errors": list(self._config_errors),
        }

    def upsert_server(self, name: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and persist one server entry to the config file."""
        try:
            cfg = parse_server(name, raw)
        except MCPConfigError as e:
            return {"success": False, "error": str(e)}
        try:
            servers = read_config_file(self.config_file)
        except Exception as e:
            return {"success": False, "error": f"Existing config file is unreadable: {e}"}
        existing = servers.get(name) or {}
        new_entry = cfg.to_json()
        # Clients only ever see env *names*. An empty, null or "***"
        # value means "keep the existing secret"; omitted names are removed.
        for key in ("env",):
            incoming = raw.get(key) or {}
            old = existing.get(key) or {}
            merged = {}
            for k, v in incoming.items():
                if v in (None, "", "***"):
                    if k in old:
                        merged[k] = old[k]
                else:
                    merged[k] = str(v)
            if merged:
                new_entry[key] = merged
            else:
                new_entry.pop(key, None)
        servers[name] = new_entry
        write_config_file(self.config_file, servers)
        result = self.reload_config()
        return {"success": True, "server": self._runtimes[name].config.to_public()
                if name in self._runtimes else None, "errors": result["errors"]}

    def remove_server(self, name: str) -> Dict[str, Any]:
        servers = read_config_file(self.config_file)
        if name not in servers:
            if name in self._runtimes:
                return {"success": False, "error": f"'{name}' is defined via GUAARDVARK_MCP_SERVERS, "
                                                   "not the config file; remove it there"}
            return {"success": False, "error": f"Unknown server: {name}"}
        servers.pop(name)
        write_config_file(self.config_file, servers)
        self.reload_config()
        return {"success": True, "server": name}

    # ------------------------------------------------------------------
    # Event loop plumbing
    # ------------------------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is not None and self._loop_thread and self._loop_thread.is_alive():
                return self._loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run():
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()

            t = threading.Thread(target=_run, name="mcp-client-loop", daemon=True)
            t.start()
            ready.wait(5)
            self._loop, self._loop_thread = loop, t
            return loop

    def _run(self, coro, timeout: float):
        loop = self._ensure_loop()
        if threading.current_thread() is self._loop_thread:
            coro.close()
            raise RuntimeError("MCP service called from its own event loop thread")
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise MCPError(f"Timed out after {timeout:.0f}s")

    # ------------------------------------------------------------------
    # Listeners (tool proxy registration hooks into these)
    # ------------------------------------------------------------------
    def add_listener(self, fn: Callable[[str, str], None]) -> None:
        if fn not in self._listeners:
            self._listeners.append(fn)

    def _notify(self, event: str, server: str) -> None:
        for fn in list(self._listeners):
            try:
                fn(event, server)
            except Exception as e:
                logger.error(f"MCP listener failed for {event}/{server}: {e}")

    def _record_error(self, server: str, message: str) -> None:
        self._errors.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "server": server,
            "error": message[:500],
        })

    # ------------------------------------------------------------------
    # Connection lifecycle (runs on the service loop)
    # ------------------------------------------------------------------
    def _errlog_path(self, name: str) -> str:
        directory = os.path.join(app_config.LOG_DIR, "mcp")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{name}.log")

    def _stderr_tail(self, name: str) -> str:
        path = self._errlog_path(name)
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - STDERR_TAIL_BYTES))
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def _timeout_for(self, rt: ServerRuntime) -> float:
        return float(rt.config.timeout or MCP_TIMEOUT)

    async def _open_transport(self, rt: ServerRuntime, stack: AsyncExitStack):
        cfg = rt.config
        errlog = stack.enter_context(open(self._errlog_path(cfg.name), "a", encoding="utf-8"))
        errlog.write(f"\n--- {datetime.now().isoformat()} starting {cfg.name} ---\n")
        errlog.flush()
        params = StdioServerParameters(
            command=cfg.command,
            args=list(cfg.args),
            env=build_child_env(cfg),
            # Relative commands and paths in an entry mean "inside this
            # install", wherever the backend process was started from.
            cwd=cfg.cwd or str(app_config.GUAARDVARK_ROOT),
        )
        read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
        return read, write

    def _make_message_handler(self, rt: ServerRuntime):
        async def handler(message: Any) -> None:
            if isinstance(message, Exception):
                logger.warning(f"MCP '{rt.config.name}' transport error: {message}")
                return
            root = getattr(message, "root", message)
            method = getattr(root, "method", "")
            if method == "notifications/tools/list_changed":
                asyncio.get_running_loop().create_task(self._refresh_after_change(rt, "tools"))
            elif method in ("notifications/resources/list_changed",
                            "notifications/prompts/list_changed"):
                asyncio.get_running_loop().create_task(self._refresh_after_change(rt, method.split("/")[1]))

        return handler

    async def _refresh_after_change(self, rt: ServerRuntime, what: str) -> None:
        try:
            await self._refresh_catalog(rt)
            logger.info(f"MCP '{rt.config.name}' {what} list changed; refreshed")
            if what == "tools":
                self._notify("tools_changed", rt.config.name)
        except Exception as e:
            logger.warning(f"MCP '{rt.config.name}' refresh failed: {e}")

    async def _paginate(self, fn, attr: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        cursor = None
        for _ in range(100):  # hard stop against a server that never ends pagination
            if cursor:
                res = await fn(params=mcp_types.PaginatedRequestParams(cursor=cursor))
            else:
                res = await fn()
            items.extend(_dump(x) for x in getattr(res, attr, []) or [])
            cursor = getattr(res, "next_cursor", None) or getattr(res, "nextCursor", None)
            if not cursor:
                break
        return items

    async def _refresh_catalog(self, rt: ServerRuntime) -> None:
        s = rt.session
        caps = rt.capabilities
        tools = await self._paginate(s.list_tools, "tools") if caps.get("tools") is not None else []
        for t in tools:
            t["description"] = mcp_policy.sanitize_description(t.get("description"))
        rt.tools = tools
        if caps.get("resources") is not None:
            rt.resources = await self._paginate(s.list_resources, "resources")
            try:
                rt.resource_templates = await self._paginate(s.list_resource_templates, "resource_templates")
            except Exception:
                rt.resource_templates = []
        else:
            rt.resources, rt.resource_templates = [], []
        rt.prompts = await self._paginate(s.list_prompts, "prompts") if caps.get("prompts") is not None else []

    async def _holder(self, rt: ServerRuntime, ready: asyncio.Future) -> None:
        name = rt.config.name
        error: Optional[str] = None
        try:
            async with AsyncExitStack() as stack:
                read, write = await self._open_transport(rt, stack)
                session = await stack.enter_async_context(ClientSession(
                    read, write,
                    read_timeout_seconds=_sdk_timeout(self._timeout_for(rt)),
                    message_handler=self._make_message_handler(rt),
                    client_info=mcp_types.Implementation(name=CLIENT_NAME, version=_client_version()),
                ))
                init = await asyncio.wait_for(session.initialize(), app_config.MCP_CONNECT_TIMEOUT)
                rt.session = session
                rt.capabilities = _dump(getattr(init, "capabilities", None)) or {}
                rt.server_info = _dump(getattr(init, "server_info", None) or getattr(init, "serverInfo", None)) or {}
                rt.protocol_version = getattr(init, "protocol_version", None) or getattr(init, "protocolVersion", None)
                rt.instructions = mcp_policy.sanitize_description(getattr(init, "instructions", None) or "") or None
                await self._refresh_catalog(rt)
                rt.status = "connected"
                rt.connected_at = time.time()
                rt.last_error = None
                rt.failure = None
                if not ready.done():
                    ready.set_result(True)
                logger.info(f"MCP '{name}' connected: {len(rt.tools)} tools, "
                            f"{len(rt.resources)} resources, {len(rt.prompts)} prompts")
                self._notify("connected", name)

                while not rt.stop_event.is_set():
                    try:
                        await asyncio.wait_for(rt.stop_event.wait(), PING_INTERVAL_SECONDS)
                    except asyncio.TimeoutError:
                        if rt.failure:
                            raise MCPError(rt.failure)
                        try:
                            await asyncio.wait_for(session.send_ping(), PING_TIMEOUT_SECONDS)
                        except Exception as e:
                            raise MCPError(f"Health check failed: {e or type(e).__name__}")
                if rt.failure:
                    raise MCPError(rt.failure)
        except asyncio.CancelledError:
            error = error or None
        except BaseException as e:  # includes anyio ExceptionGroups from transports
            error = self._describe_exception(e)
        finally:
            was_connected = rt.status == "connected"
            rt.session = None
            rt.connected_at = None
            if error:
                rt.status = "error"
                rt.last_error = error
                self._record_error(name, error)
                logger.error(f"MCP '{name}' {'lost' if was_connected else 'failed to connect'}: {error}")
            else:
                rt.status = "disconnected"
            if not ready.done():
                ready.set_exception(MCPError(error or "Connection closed during startup"))
            if was_connected:
                self._notify("disconnected", name)

    @staticmethod
    def _describe_exception(e: BaseException) -> str:
        inner = getattr(e, "exceptions", None)
        if inner:
            return "; ".join(MCPClientService._describe_exception(x) for x in inner[:3])
        if isinstance(e, FileNotFoundError):
            return f"Command not found: {e.filename or e}"
        msg = str(e) or type(e).__name__
        return msg[:500]

    async def _connect_async(self, name: str) -> Dict[str, Any]:
        rt = self._runtimes[name]
        if rt.lock is None:
            rt.lock = asyncio.Lock()
        async with rt.lock:
            if rt.connected:
                return {"already": True}
            rt.status = "connecting"
            rt.stop_event = asyncio.Event()
            ready = asyncio.get_running_loop().create_future()
            rt.task = asyncio.get_running_loop().create_task(self._holder(rt, ready), name=f"mcp:{name}")
            try:
                await asyncio.wait_for(asyncio.shield(ready), app_config.MCP_CONNECT_TIMEOUT + 5)
            except asyncio.TimeoutError:
                await self._stop_async(rt)
                rt.status, rt.last_error = "error", "Timed out connecting"
                raise MCPError(f"Timed out connecting to '{name}'")
            return {"already": False}

    async def _stop_async(self, rt: ServerRuntime) -> None:
        if rt.stop_event:
            rt.stop_event.set()
        task = rt.task
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), 10)
            except (asyncio.TimeoutError, Exception):
                task.cancel()
                try:
                    await asyncio.wait_for(task, 5)
                except BaseException:
                    pass
        rt.task = None

    def _stop_runtime_sync(self, name: str) -> None:
        rt = self._runtimes.get(name)
        if not rt or self._loop is None:
            return
        try:
            self._run(self._stop_async(rt), timeout=20)
        except Exception as e:
            logger.warning(f"MCP '{name}' stop error: {e}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def _precheck(self, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not MCP_ENABLED:
            return {"success": False, "error": "MCP is disabled (set GUAARDVARK_MCP_ENABLED=true)"}
        if not MCP_SDK_AVAILABLE:
            return {"success": False, "error": f"The 'mcp' Python package is not installed ({MCP_SDK_ERROR})"}
        if name is not None and name not in self._runtimes:
            return {"success": False, "error": f"Unknown server: {name}. Configured: {sorted(self._runtimes)}"}
        return None

    @property
    def _connect_failed_at(self) -> Dict[str, float]:
        """When an on-demand connect to each server last failed."""
        if not hasattr(self, "_connect_failures"):
            self._connect_failures = {}
        return self._connect_failures

    def connect(self, name: str) -> Dict[str, Any]:
        err = self._precheck(name)
        if err:
            return err
        rt = self._runtimes[name]
        if rt.config.disabled:
            return {"success": False, "error": f"Server '{name}' is disabled in its configuration"}
        t0 = time.time()
        try:
            res = self._run(self._connect_async(name), timeout=app_config.MCP_CONNECT_TIMEOUT + 15)
        except Exception as e:
            return {"success": False, "server": name, "error": rt.last_error or str(e),
                    "stderr_tail": self._stderr_tail(name)[-1500:] if rt.config.transport == "stdio" else ""}
        return {
            "success": True,
            "server": name,
            "message": "Already connected" if res.get("already") else "Connected",
            "tools": len(rt.tools),
            "resources": len(rt.resources),
            "prompts": len(rt.prompts),
            "tool_names": [t.get("name") for t in rt.tools],
            "duration_ms": int((time.time() - t0) * 1000),
        }

    # Backwards-compatible async aliases (older call sites awaited these).
    async def connect_server(self, name: str) -> Dict[str, Any]:
        return await asyncio.get_running_loop().run_in_executor(None, self.connect, name)

    async def disconnect_server(self, name: str) -> Dict[str, Any]:
        return await asyncio.get_running_loop().run_in_executor(None, self.disconnect, name)

    def disconnect(self, name: str) -> Dict[str, Any]:
        if name not in self._runtimes:
            return {"success": False, "error": f"Unknown server: {name}"}
        rt = self._runtimes[name]
        if not rt.connected and rt.status != "connecting":
            return {"success": False, "error": f"Not connected to {name}"}
        self._stop_runtime_sync(name)
        rt.tools, rt.resources, rt.prompts, rt.resource_templates = [], [], [], []
        return {"success": True, "server": name}

    def _ensure_connected(self, name: str) -> Optional[Dict[str, Any]]:
        """Connect on demand if configured; return an error dict on failure."""
        rt = self._runtimes[name]
        if rt.connected:
            return None
        # A server that just failed to start is not tried again on every call:
        # each attempt can wait out the whole connect timeout, in the middle
        # of a chat turn.
        failed_at = self._connect_failed_at.get(name)
        if failed_at and time.time() - failed_at < RECONNECT_COOLDOWN_S:
            wait = int(RECONNECT_COOLDOWN_S - (time.time() - failed_at)) + 1
            return {"success": False, "server": name,
                    "error": (f"MCP server '{name}' did not start a moment ago ({rt.last_error or 'no detail'}); "
                              f"not retried for another {wait} s. Connect it from MCP Servers to retry now.")}
        res = self.connect(name)
        if res.get("success"):
            self._connect_failed_at.pop(name, None)
            return None
        self._connect_failed_at[name] = time.time()
        return res

    def get_tool(self, server: str, tool: str) -> Optional[Dict[str, Any]]:
        rt = self._runtimes.get(server)
        if not rt:
            return None
        return next((t for t in rt.tools if t.get("name") == tool), None)

    def evaluate_policy(self, server: str, tool: Dict[str, Any]) -> mcp_policy.PolicyDecision:
        rt = self._runtimes.get(server)
        return mcp_policy.evaluate(rt.config if rt else None, tool)

    def list_tools(self, server: Optional[str] = None) -> Dict[str, Any]:
        err = self._precheck(server)
        if err:
            return err

        def describe(name: str, rt: ServerRuntime) -> List[Dict[str, Any]]:
            out = []
            for t in rt.tools:
                d = self.evaluate_policy(name, t)
                out.append({**t, "policy": d.action, "policyReason": d.reason,
                            "proxyName": mcp_policy.sanitize_tool_name(name, t.get("name", ""))})
            return out

        if server:
            rt = self._runtimes[server]
            if not rt.connected:
                return {"success": False, "error": f"Not connected to {server}"}
            tools = describe(server, rt)
            return {"success": True, "server": server, "tools": tools, "count": len(tools)}
        all_tools = {n: describe(n, rt) for n, rt in self._runtimes.items() if rt.connected}
        return {
            "success": True,
            "servers": list(all_tools),
            "tools": all_tools,
            "total_count": sum(len(v) for v in all_tools.values()),
        }

    def cached_tools_for_prompt(self) -> Dict[str, List[Dict[str, Any]]]:
        """Connected server name -> its cached tool list, minus denied tools.

        Cache-only (no RPC), so the chat engine can call it every turn to show
        the LLM the real tool inventory.
        """
        out: Dict[str, List[Dict[str, Any]]] = {}
        for name, rt in self._runtimes.items():
            if not rt.connected:
                continue
            out[name] = [t for t in rt.tools if self.evaluate_policy(name, t).action != "deny"]
        return out

    def call_tool(
        self,
        server: str,
        tool: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        approved: bool = False,
        caller: str = "agent",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Call a tool. ``approved`` must be True for tools the policy marks
        ``confirm``; callers are responsible for having obtained approval."""
        err = self._precheck(server)
        if err:
            return err
        t0 = time.time()
        arguments = arguments or {}
        entry = {"caller": caller, "session_id": session_id, "server": server, "tool": tool,
                 "args": _audit_args(arguments)}

        err = self._ensure_connected(server)
        if err:
            self._audit_entry({**entry, "decision": "error", "success": False, "error": err.get("error")})
            return err
        rt = self._runtimes[server]
        tool_def = self.get_tool(server, tool)
        if not tool_def:
            names = [t.get("name") for t in rt.tools]
            return {"success": False, "error": f"Unknown tool '{tool}' on {server}. Available: {names}"}
        if not isinstance(arguments, dict):
            return {"success": False, "error": "arguments must be a JSON object"}

        decision = self.evaluate_policy(server, tool_def)
        if decision.action == mcp_policy.DENY:
            self._audit_entry({**entry, "decision": "deny", "success": False, "error": decision.reason})
            return {"success": False, "error": f"Tool '{tool}' on {server} is blocked: {decision.reason}",
                    "policy": decision.action}
        if decision.action == mcp_policy.CONFIRM and not approved:
            self._audit_entry({**entry, "decision": "needs_approval", "success": False})
            return {
                "success": False,
                "requires_confirmation": True,
                "policy": decision.action,
                "proxy_tool": mcp_policy.sanitize_tool_name(server, tool),
                "error": (f"Tool '{server}/{tool}' requires the user's approval ({decision.reason}). "
                          f"Call '{mcp_policy.sanitize_tool_name(server, tool)}' so the user is asked."),
            }

        validation_error = _validate_arguments(tool_def.get("inputSchema"), arguments)
        if validation_error:
            self._audit_entry({**entry, "decision": decision.action, "success": False, "error": validation_error})
            return {"success": False, "error": f"Invalid arguments for {server}/{tool}: {validation_error}"}

        timeout = self._timeout_for(rt)

        async def _call():
            session = rt.session
            if session is None:
                raise MCPError(f"{server} is not connected")
            return await session.call_tool(tool, arguments, read_timeout_seconds=_sdk_timeout(timeout))

        try:
            result = self._run(_call(), timeout=timeout + 10)
        except Exception as e:
            msg = self._describe_exception(e)
            if "connection closed" in msg.lower() or "not connected" in msg.lower():
                rt.failure = msg
                if rt.stop_event and self._loop:
                    self._loop.call_soon_threadsafe(rt.stop_event.set)
            self._record_error(server, f"{tool}: {msg}")
            self._audit_entry({**entry, "decision": decision.action, "success": False, "error": msg,
                               "duration_ms": int((time.time() - t0) * 1000)})
            return {"success": False, "server": server, "tool": tool, "error": msg}

        rt.call_count += 1
        self._total_calls += 1
        data = _dump(result) or {}
        is_error = bool(data.get("isError"))
        text = mcp_policy.content_to_text(data.get("content"), data.get("structuredContent"))
        duration = int((time.time() - t0) * 1000)
        self._audit_entry({**entry, "decision": decision.action, "success": not is_error,
                           "duration_ms": duration, **({"error": text[:300]} if is_error else {})})
        out = {
            "success": not is_error,
            "server": server,
            "tool": tool,
            "result": data,
            "text": text,
            "duration_ms": duration,
        }
        if is_error:
            out["error"] = text[:2000] or "Tool reported an error"
        return out

    def list_resources(self, server: Optional[str] = None) -> Dict[str, Any]:
        err = self._precheck(server)
        if err:
            return err
        names = [server] if server else [n for n, rt in self._runtimes.items() if rt.connected]
        out = {}
        for n in names:
            rt = self._runtimes[n]
            if not rt.connected:
                return {"success": False, "error": f"Not connected to {n}"}
            out[n] = {"resources": rt.resources, "templates": rt.resource_templates}
        return {"success": True, "resources": out}

    def read_resource(self, server: str, uri: str, *, caller: str = "agent") -> Dict[str, Any]:
        err = self._precheck(server) or self._ensure_connected(server)
        if err:
            return err
        rt = self._runtimes[server]
        if rt.capabilities.get("resources") is None:
            return {"success": False, "error": f"{server} does not expose resources"}

        async def _read():
            return await rt.session.read_resource(uri)

        t0 = time.time()
        try:
            res = _dump(self._run(_read(), timeout=self._timeout_for(rt) + 10)) or {}
        except Exception as e:
            msg = self._describe_exception(e)
            self._audit_entry({"caller": caller, "server": server, "tool": f"resource:{uri}",
                               "decision": "allow", "success": False, "error": msg})
            return {"success": False, "error": msg}
        contents = res.get("contents") or []
        text = "\n".join(
            c.get("text") if "text" in c else f"[binary {c.get('mimeType', '')} content omitted]"
            for c in contents
        )
        self._audit_entry({"caller": caller, "server": server, "tool": f"resource:{uri}", "decision": "allow",
                           "success": True, "duration_ms": int((time.time() - t0) * 1000)})
        return {"success": True, "server": server, "uri": uri, "contents": contents, "text": text}

    def list_prompts(self, server: Optional[str] = None) -> Dict[str, Any]:
        err = self._precheck(server)
        if err:
            return err
        names = [server] if server else [n for n, rt in self._runtimes.items() if rt.connected]
        out = {}
        for n in names:
            rt = self._runtimes[n]
            if not rt.connected:
                return {"success": False, "error": f"Not connected to {n}"}
            out[n] = rt.prompts
        return {"success": True, "prompts": out}

    def get_prompt(self, server: str, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        err = self._precheck(server) or self._ensure_connected(server)
        if err:
            return err
        rt = self._runtimes[server]
        if rt.capabilities.get("prompts") is None:
            return {"success": False, "error": f"{server} does not expose prompts"}
        args = {str(k): str(v) for k, v in (arguments or {}).items()}

        async def _get():
            return await rt.session.get_prompt(name, args)

        try:
            res = _dump(self._run(_get(), timeout=self._timeout_for(rt) + 10)) or {}
        except Exception as e:
            return {"success": False, "error": self._describe_exception(e)}
        return {"success": True, "server": server, "prompt": name, **res}

    def _server_summary(self, name: str, rt: ServerRuntime) -> Dict[str, Any]:
        return {
            **rt.config.to_public(),
            "status": rt.status,
            "connected": rt.connected,
            "tool_count": len(rt.tools) if rt.connected else 0,
            "resource_count": len(rt.resources) if rt.connected else 0,
            "prompt_count": len(rt.prompts) if rt.connected else 0,
            "connected_at": rt.connected_at,
            "last_error": rt.last_error,
            "call_count": rt.call_count,
            "server_info": rt.server_info,
        }

    def list_configured_servers(self) -> Dict[str, Any]:
        servers = [self._server_summary(n, rt) for n, rt in sorted(self._runtimes.items())]
        return {"success": True, "servers": servers, "total": len(servers),
                "config_errors": list(self._config_errors), "config_file": self.config_file}

    def get_server(self, name: str) -> Dict[str, Any]:
        rt = self._runtimes.get(name)
        if not rt:
            return {"success": False, "error": f"Unknown server: {name}"}
        detail = self._server_summary(name, rt)
        detail.update({
            "capabilities": rt.capabilities,
            "protocol_version": rt.protocol_version,
            "instructions": rt.instructions,
            "tools": self.list_tools(name).get("tools", []) if rt.connected else [],
            "resources": rt.resources,
            "resource_templates": rt.resource_templates,
            "prompts": rt.prompts,
            "stderr_tail": self._stderr_tail(name) if rt.config.transport == "stdio" else "",
            "definition": rt.config.to_editable(),
        })
        return {"success": True, "server": detail}

    def get_state(self) -> Dict[str, Any]:
        connected = [n for n, rt in self._runtimes.items() if rt.connected]
        return {
            "mcp_enabled": MCP_ENABLED,
            "sdk_available": MCP_SDK_AVAILABLE,
            "sdk_version": MCP_SDK_VERSION,
            "initialized": True,
            "servers_configured": len(self._runtimes),
            "servers_connected": len(connected),
            "total_tools_available": sum(len(self._runtimes[n].tools) for n in connected),
            "total_calls": self._total_calls,
            "connected_servers": connected,
            "errors": list(self._errors)[-10:],
            "config_errors": list(self._config_errors),
        }

    def autoconnect(self) -> Dict[str, Any]:
        results = {}
        if self._precheck():
            return {"success": False, "results": results}
        for name, rt in list(self._runtimes.items()):
            if rt.config.auto_connect and not rt.config.disabled and not rt.connected:
                res = self.connect(name)
                results[name] = res.get("success", False)
                if not res.get("success"):
                    logger.warning(f"MCP autoconnect '{name}' failed: {res.get('error')}")
        return {"success": True, "results": results}

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    def _audit_entry(self, entry: Dict[str, Any]) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
        with self._audit_lock:
            self._audit.append(entry)
            try:
                os.makedirs(app_config.LOG_DIR, exist_ok=True)
                with open(os.path.join(app_config.LOG_DIR, "mcp_audit.log"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, default=str) + "\n")
            except OSError as e:
                logger.debug(f"MCP audit write failed: {e}")

    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._audit_lock:
            items = list(self._audit)
        return items[-max(1, min(limit, AUDIT_MAX_ENTRIES)):][::-1]

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def shutdown_sync(self) -> None:
        if self._loop is None:
            return
        for name, rt in list(self._runtimes.items()):
            if rt.task is not None:
                self._stop_runtime_sync(name)
        loop = self._loop
        loop.call_soon_threadsafe(loop.stop)
        if self._loop_thread:
            self._loop_thread.join(timeout=5)
        self._loop = None
        self._loop_thread = None
        logger.info("MCPClientService shut down")

    async def shutdown(self) -> None:  # backwards-compatible async form
        await asyncio.get_running_loop().run_in_executor(None, self.shutdown_sync)

    def _atexit_shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        try:
            self.shutdown_sync()
        except Exception:
            pass


def _audit_args(arguments: Any) -> Any:
    try:
        s = json.dumps(arguments, default=str)
    except Exception:
        s = repr(arguments)
    return s if len(s) <= 500 else s[:500] + "…"


def _validate_arguments(schema: Optional[Dict[str, Any]], arguments: Dict[str, Any]) -> Optional[str]:
    if not schema or not isinstance(schema, dict):
        return None
    try:
        import jsonschema
    except ImportError:
        missing = [k for k in schema.get("required", []) if k not in arguments]
        return f"missing required argument(s): {', '.join(missing)}" if missing else None
    try:
        jsonschema.validate(arguments, schema)
    except jsonschema.ValidationError as e:
        path = ".".join(str(p) for p in e.path)
        return f"{path + ': ' if path else ''}{e.message}"[:500]
    except jsonschema.SchemaError:
        return None  # the server's schema is broken; let the server decide
    return None


def get_mcp_service() -> MCPClientService:
    return MCPClientService.get_instance()


def run_mcp_async(coro):
    """Deprecated shim: run a coroutine to completion from sync code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result(timeout=MCP_TIMEOUT + 5)
