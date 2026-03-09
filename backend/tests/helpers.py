import types
from typing import ClassVar

try:
    from llama_index.core.base.llms.types import ChatResponse
    from llama_index.core.llms import LLM, ChatMessage, MessageRole
except Exception:  # pragma: no cover - llama_index not installed

    class LLM:  # type: ignore
        pass

    class ChatMessage:  # type: ignore
        def __init__(self, role=None, content=None):
            self.role = role
            self.content = content

    class MessageRole:  # type: ignore
        SYSTEM = "system"
        USER = "user"

    class ChatResponse:  # type: ignore
        def __init__(self, message=None):
            self.message = message


def make_mock_llm(response_text: str = "ok") -> LLM:
    class MockLLM(LLM):  # type: ignore
        model: ClassVar[str] = "mock"

        def __init__(self) -> None:
            # Initialize the underlying BaseModel so attributes such as
            # ``__pydantic_fields_set__`` are created. Without this the
            # LlamaIndex utilities raise AttributeError during tests.
            super().__init__()
            self.callback_manager = None

        def chat(self, messages, **_) -> ChatResponse:  # type: ignore
            msg = types.SimpleNamespace(content=response_text)
            return ChatResponse(msg)

        def stream_chat(self, messages, **_):  # pragma: no cover - streaming unused
            for _ in range(1):
                yield ChatResponse(types.SimpleNamespace(content=response_text))

        def complete(self, prompt, **_) -> types.SimpleNamespace:
            return types.SimpleNamespace(text=response_text)

        def stream_complete(self, prompt, **_):  # pragma: no cover
            for _ in range(1):
                yield types.SimpleNamespace(text=response_text)

        # Async variants for compatibility with newer LlamaIndex versions
        async def achat(self, messages, **_):  # pragma: no cover - simple echo
            return self.chat(messages)

        async def astream_chat(self, messages, **_):  # pragma: no cover
            for r in self.stream_chat(messages):
                yield r

        async def acomplete(self, prompt, **_):  # pragma: no cover
            return self.complete(prompt)

        async def astream_complete(self, prompt, **_):  # pragma: no cover
            for r in self.stream_complete(prompt):
                yield r

        @property
        def metadata(self):  # pragma: no cover - minimal metadata
            return {}

        @property
        def callback_manager(self):  # pragma: no cover - minimal
            return None

    return MockLLM()
