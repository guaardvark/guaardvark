"""Per-session Socket.IO bridge for cluster-routed chat streaming.

When a chat request routes to a remote node, the local node opens an
outbound socketio.Client() to the remote node's Socket.IO endpoint,
relays `chat:send` events upstream, and forwards streaming events
(chat:token, chat:complete, etc.) back down to the user's session.

Lives on whichever node receives the browser connection — NOT on master.
Each node self-bridges so master isn't a streaming bottleneck.
"""
from __future__ import annotations

import logging
import threading

import socketio as sio

from backend.services.cluster_proxy import NodeTarget

log = logging.getLogger(__name__)


class SocketIOChatBridge:
    RELAYED_EVENTS = (
        "chat:thinking", "chat:tool_call", "chat:tool_result",
        "chat:token", "chat:complete", "chat:error",
    )

    def __init__(self, user_session_id: str, target: NodeTarget):
        self._sid = user_session_id
        self._target = target
        self._client: sio.Client | None = None

    def open(self) -> None:
        if self._client is not None:
            return
        self._client = sio.Client(reconnection=False)
        for event in self.RELAYED_EVENTS:
            # Bind each event to _relay with the event name captured
            self._client.on(event, lambda data, e=event: self._relay(e, data))
        self._client.connect(
            self._target.base_url,
            headers={"X-Guaardvark-API-Key": self._target.api_key},
            auth={"api_key": self._target.api_key},
            wait_timeout=3,
        )
        log.info("[BRIDGE] opened session=%s → node=%s",
                 self._sid, self._target.node_id)

    def _relay(self, event: str, data) -> None:
        try:
            from backend.socketio_instance import socketio
            socketio.emit(event, data, to=self._sid)
        except Exception as e:
            log.warning("[BRIDGE] relay failed: %s", e)

    def forward_send(self, payload: dict) -> None:
        if self._client is None:
            self.open()
        self._client.emit("chat:send", payload)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
            log.info("[BRIDGE] closed session=%s → node=%s",
                     self._sid, self._target.node_id)


class SocketIOBridgeRegistry:
    _bridges: dict[str, SocketIOChatBridge] = {}
    _lock = threading.Lock()

    @classmethod
    def get_or_create(cls, session_id: str, target: NodeTarget) -> SocketIOChatBridge:
        with cls._lock:
            bridge = cls._bridges.get(session_id)
            if bridge is None:
                bridge = SocketIOChatBridge(session_id, target)
                cls._bridges[session_id] = bridge
            return bridge

    @classmethod
    def close_for_session(cls, session_id: str) -> None:
        with cls._lock:
            bridge = cls._bridges.pop(session_id, None)
        if bridge is not None:
            try:
                bridge.close()
            except Exception as e:
                log.warning("[BRIDGE] close failed for %s: %s", session_id, e)
