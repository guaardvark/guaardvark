import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("PYTEST_SKIP_MIGRATION_CHECK", "1")
os.environ.setdefault("PYTEST_SKIP_LLAMA_CHECK", "1")

from types import SimpleNamespace

# Provide lightweight stand-ins for LlamaIndex classes during tests
try:
    from backend.api import enhanced_chat_api as chat_api
except ImportError:
    # Create a mock module if import fails
    import types
    chat_api = types.SimpleNamespace()
    chat_api.MessageRole = None
    chat_api.ChatMessage = None
    chat_api.ChatMemoryBuffer = None

if getattr(chat_api, 'MessageRole', None) is None:

    class _Role:
        def __init__(self, val):
            self.value = val

    class RoleMeta(type):
        def __iter__(cls):
            return iter([cls.USER, cls.ASSISTANT, cls.SYSTEM])

    class DummyMessageRole(metaclass=RoleMeta):
        USER = _Role("user")
        ASSISTANT = _Role("assistant")
        SYSTEM = _Role("system")

    chat_api.MessageRole = DummyMessageRole

if getattr(chat_api, 'ChatMessage', None) is None:

    class DummyChatMsg:
        def __init__(self, role=None, content=None):
            self.role = role
            self.content = content

    chat_api.ChatMessage = DummyChatMsg

if getattr(chat_api, 'ChatMemoryBuffer', None) is None:

    class DummyBuffer:
        @classmethod
        def from_defaults(cls, **_kwargs):
            return cls()

    chat_api.ChatMemoryBuffer = DummyBuffer

# ---- Test logging hooks ----
import json
from datetime import datetime, timezone

_results = []
_timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
_log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs", "test_results")
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, f"{_timestamp}_testlog.json")


def pytest_runtest_logreport(report):
    if report.when != "call":
        return
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "file": report.location[0],
        "test": report.nodeid,
        "status": report.outcome,
    }
    if report.failed:
        entry["traceback"] = getattr(report, "longreprtext", "")
        for name, content in report.sections:
            lname = name.lower()
            if "stdout" in lname:
                entry["stdout"] = content
            if "stderr" in lname:
                entry["stderr"] = content
    _results.append(entry)


def pytest_sessionfinish(session, exitstatus):
    with open(_log_file, "w", encoding="utf-8") as f:
        json.dump(_results, f, indent=2)
    summary = os.path.join(_log_dir, "test_summary.log")
    with open(summary, "a", encoding="utf-8") as f:
        sym = "\u2713" if exitstatus == 0 else "\u2717"
        f.write(f"{_timestamp} {sym} exit={exitstatus}\n")
