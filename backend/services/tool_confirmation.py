"""Who may run a tool that insists on a human answer.

Some tools (MCP tools the server policy gates) must not run unless a person
said yes. In the streaming chat that answer comes from the approval card
(``chat:tool_approval_request``), after which the tool loop runs the call
inside ``trusted_caller("chat_approval")``. A person driving a tool directly
(the Tools page) is trusted the same way. Every other path, such as sync chat,
the agent executor, Celery or the screen loop, has no one to ask, so those
tools refuse there with ``approval_required_message``.

The mark is thread-local: it covers only calls made on the thread that set it.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Optional

_thread_state = threading.local()


@contextlib.contextmanager
def trusted_caller(label: str):
    """Run tools on this thread as approved by, or driven by, a person."""
    prev = getattr(_thread_state, "trusted", None)
    _thread_state.trusted = label
    try:
        yield
    finally:
        _thread_state.trusted = prev


def current_trusted_caller() -> Optional[str]:
    return getattr(_thread_state, "trusted", None)


def approval_required_message(tool_name: str) -> str:
    return (
        f"'{tool_name}' requires the user's approval before it can run, and this "
        f"request path cannot ask for it. Use the streaming chat (web UI or `guaardvark chat`) "
        f"to be prompted, or run it yourself from the Tools page."
    )
