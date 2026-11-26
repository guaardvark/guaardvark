from .base_backend import BaseBackend, AgentProcess, AgentStatus
from .claude_backend import ClaudeBackend
from .cline_backend import ClineBackend
from .guaardvark_backend import GuaardvarkBackend


def backend_classes() -> dict:
    """Backend name in config.yaml -> the class that runs it."""
    return {"guaardvark": GuaardvarkBackend, "claude": ClaudeBackend, "cline": ClineBackend}


__all__ = [
    "BaseBackend", "AgentProcess", "AgentStatus",
    "ClaudeBackend", "ClineBackend", "GuaardvarkBackend", "backend_classes",
]
