"""Async Socket.IO client for POST /api/chat/unified.

Connect and join the session room before the HTTP POST — the backend can emit
chat:thinking before join_room finishes. Approval requests are queued off the
Socket.IO handler so a Discord button wait cannot stall the event stream.
"""
from engineio import payload as _engineio_payload
_engineio_payload.Payload.max_decode_packets = 10000

import asyncio
import logging
import time

import socketio

logger = logging.getLogger("discord_bot")

IDLE_TIMEOUT = 300.0
HARD_TIMEOUT = 1800.0
JOIN_TIMEOUT = 10.0


class StreamError(Exception):
    """Raised when the unified-chat stream fails or times out."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class UnifiedChatStreamer:
    """One-shot Socket.IO listener for a single unified-chat turn."""

    def __init__(self, server_url: str, sio=None):
        self.server_url = server_url.rstrip("/")
        self.sio = sio or socketio.AsyncClient(
            reconnection=False, logger=False, engineio_logger=False
        )
        self._done = asyncio.Event()
        self._joined = asyncio.Event()
        self._tokens: list[str] = []
        self._complete: dict = {}
        self._error: str | None = None
        self._images: list[dict] = []
        self._session_id: str | None = None
        self._approval_handler = None
        self._approval_queue: asyncio.Queue = asyncio.Queue()
        self._last_activity = time.monotonic()
        self._closing = False
        self._handlers_registered = False

    def _touch(self):
        self._last_activity = time.monotonic()

    def _register_handlers(self):
        if self._handlers_registered:
            return
        self._handlers_registered = True

        @self.sio.on("chat:joined")
        async def _joined(_data):
            self._touch()
            self._joined.set()

        @self.sio.on("chat:token")
        async def _token(data):
            self._touch()
            content = (data or {}).get("content", "")
            if content:
                self._tokens.append(content)

        @self.sio.on("chat:image")
        async def _image(data):
            self._touch()
            payload = data or {}
            self._images.append({
                "url": payload.get("image_url") or payload.get("url"),
                "type": "image",
                "alt": payload.get("alt") or "",
            })

        @self.sio.on("chat:video")
        async def _video(data):
            self._touch()
            payload = data or {}
            self._images.append({
                "url": payload.get("video_url") or payload.get("url"),
                "type": "video",
                "alt": payload.get("alt") or "",
            })

        @self.sio.on("chat:complete")
        async def _complete(data):
            self._touch()
            self._complete = data or {}
            self._done.set()

        @self.sio.on("chat:error")
        async def _err(data):
            self._touch()
            self._error = (data or {}).get("error", "Unknown error")
            self._done.set()

        @self.sio.on("chat:aborted")
        async def _aborted(_data):
            self._touch()
            self._error = "Chat aborted"
            self._done.set()

        @self.sio.on("chat:tool_approval_request")
        async def _approval(data):
            self._touch()
            self._approval_queue.put_nowait(data or {})

        @self.sio.event
        async def disconnect():
            if self._closing or self._done.is_set():
                return
            self._error = "Stream disconnected before completion"
            self._done.set()

    async def run(
        self,
        *,
        session_id: str,
        message: str,
        post_fn,
        image: str | None = None,
        options: dict | None = None,
        approval_handler=None,
        is_voice_message: bool = False,
    ) -> dict:
        """Join the session, POST the turn, wait for chat:complete.

        ``post_fn`` is an async callable that receives the JSON body and
        performs the HTTP POST (so retries and unwrap live in the API client).
        """
        self._session_id = session_id
        self._approval_handler = approval_handler
        self._register_handlers()
        try:
            try:
                await self.sio.connect(
                    self.server_url,
                    transports=["polling", "websocket"],
                )
            except Exception as e:
                raise StreamError(f"Failed to connect to Guaardvark: {e}", 503) from e
            await self.sio.emit("chat:join", {"session_id": session_id})
            try:
                await asyncio.wait_for(self._joined.wait(), timeout=JOIN_TIMEOUT)
            except asyncio.TimeoutError:
                raise StreamError(
                    "Timed out joining Guaardvark chat session", 504
                )

            body = {
                "session_id": session_id,
                "message": message,
                "options": options or {},
            }
            if image:
                body["image"] = image
            if is_voice_message:
                body["is_voice_message"] = True

            await post_fn(body)
            completed = await self._wait_for_completion()
            if not completed:
                try:
                    await self.sio.emit("chat:abort", {"session_id": session_id})
                except Exception:
                    pass
                raise StreamError("Chat timed out waiting for Guaardvark", 504)

            if self._error:
                raise StreamError(self._error, 502)

            response = self._complete.get("response") or "".join(self._tokens)
            images = list(self._images)
            for img in self._complete.get("generated_images") or []:
                url = (img or {}).get("url") or (img or {}).get("image_url")
                if url and not any((existing or {}).get("url") == url for existing in images):
                    images.append(img)
            return {
                "response": response or "No response received.",
                "session_id": session_id,
                "generated_images": images,
            }
        finally:
            await self.close()

    async def _handle_approval(self, data: dict):
        approved = False
        if self._approval_handler:
            try:
                approved = bool(await self._approval_handler(data))
            except Exception:
                logger.exception("Approval handler failed; rejecting tool call")
                approved = False
        try:
            await self.sio.emit("chat:tool_approval_response", {
                "session_id": self._session_id,
                "approved": approved,
            })
        except Exception:
            logger.exception("Failed to send tool approval response")

    async def _wait_for_completion(self) -> bool:
        started = time.monotonic()
        self._touch()
        while True:
            now = time.monotonic()
            if now - started >= HARD_TIMEOUT:
                return False
            idle_for = now - self._last_activity
            if idle_for >= IDLE_TIMEOUT:
                return False
            remaining = min(
                0.25,
                IDLE_TIMEOUT - idle_for,
                HARD_TIMEOUT - (now - started),
            )
            if remaining <= 0:
                return False

            while True:
                try:
                    data = self._approval_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await self._handle_approval(data)
                self._touch()

            try:
                await asyncio.wait_for(self._done.wait(), timeout=remaining)
                return True
            except asyncio.TimeoutError:
                continue

    async def close(self):
        self._closing = True
        connected = getattr(self.sio, "connected", False)
        if connected:
            try:
                await self.sio.disconnect()
            except Exception:
                pass
