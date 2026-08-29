"""Tests for UnifiedChatStreamer."""
import pytest
from unittest.mock import AsyncMock

from core.chat_streamer import StreamError, UnifiedChatStreamer


class FakeSio:
    def __init__(self):
        self.handlers = {}
        self.connected = False
        self.emitted = []

    def on(self, event):
        def deco(fn):
            self.handlers[event] = fn
            return fn
        return deco

    def event(self, fn):
        self.handlers[fn.__name__] = fn
        return fn

    async def connect(self, *args, **kwargs):
        self.connected = True

    async def emit(self, event, data=None):
        self.emitted.append((event, data))
        if event == "chat:join":
            handler = self.handlers.get("chat:joined")
            if handler:
                await handler({"session_id": (data or {}).get("session_id"), "status": "ok"})

    async def disconnect(self):
        self.connected = False


@pytest.mark.asyncio
async def test_joins_before_post():
    sio = FakeSio()
    streamer = UnifiedChatStreamer("http://localhost:5002", sio=sio)
    order = []

    async def post_fn(body):
        order.append("post")
        assert streamer._joined.is_set()
        await sio.handlers["chat:complete"]({"response": "hi from gv"})

    result = await streamer.run(
        session_id="discord_user_1",
        message="hello",
        post_fn=post_fn,
    )
    assert order == ["post"]
    assert sio.emitted[0][0] == "chat:join"
    assert result["response"] == "hi from gv"
    assert result["session_id"] == "discord_user_1"


@pytest.mark.asyncio
async def test_collects_generated_images():
    sio = FakeSio()
    streamer = UnifiedChatStreamer("http://localhost:5002", sio=sio)

    async def post_fn(_body):
        await sio.handlers["chat:image"]({"image_url": "/api/img/a.png"})
        await sio.handlers["chat:complete"]({
            "response": "here",
            "generated_images": [{"url": "/api/img/b.png", "type": "image"}],
        })

    result = await streamer.run(
        session_id="discord_user_1", message="draw", post_fn=post_fn
    )
    urls = [img["url"] for img in result["generated_images"]]
    assert "/api/img/a.png" in urls
    assert "/api/img/b.png" in urls


@pytest.mark.asyncio
async def test_approval_emits_response():
    sio = FakeSio()
    streamer = UnifiedChatStreamer("http://localhost:5002", sio=sio)
    seen = {}

    async def approval_handler(data):
        seen["tools"] = data.get("tools")
        await sio.handlers["chat:complete"]({"response": "ok"})
        return True

    async def post_fn(_body):
        streamer._approval_queue.put_nowait({"tools": ["edit_image"]})

    result = await streamer.run(
        session_id="discord_user_1",
        message="edit",
        post_fn=post_fn,
        approval_handler=approval_handler,
    )
    assert result["response"] == "ok"
    assert seen["tools"] == ["edit_image"]
    events = [e[0] for e in sio.emitted]
    assert "chat:tool_approval_response" in events
    approval = [d for e, d in sio.emitted if e == "chat:tool_approval_response"][0]
    assert approval["approved"] is True


@pytest.mark.asyncio
async def test_error_event_raises():
    sio = FakeSio()
    streamer = UnifiedChatStreamer("http://localhost:5002", sio=sio)

    async def post_fn(_body):
        await sio.handlers["chat:error"]({"error": "LLM not available"})

    with pytest.raises(StreamError) as exc:
        await streamer.run(session_id="s", message="hi", post_fn=post_fn)
    assert "LLM not available" in str(exc.value)
