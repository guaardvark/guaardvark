"""
Structured audit logging for inbound MCP calls.

Phase 1 writes to the standard ``logger.info("mcp.call", extra={...})`` stream.
A proper ``mcp_calls`` DB table lands in Phase 2 when bearer auth arrives —
this shape is designed to pour straight into it without churn.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("guaardvark.mcp.audit")


@contextmanager
def audit_call(method: str, target: str, principal: str = "stdio-local") -> Iterator[dict]:
    """
    Wrap an inbound RPC and emit a structured audit record on exit.

    Usage::

        with audit_call("tools/call", tool_name) as rec:
            result = do_the_work()
            rec["bytes_out"] = len(str(result))
            if not result.success:
                rec["outcome"] = "error"

    The caller can mutate ``rec`` mid-flight; ``latency_ms`` is always stamped
    on exit. Exactly one audit record is emitted per call.
    """
    start = time.monotonic()
    rec: dict[str, Any] = {
        "method": method,
        "target": target,
        "principal": principal,
        "outcome": "ok",
        "error_code": None,
        "bytes_in": 0,
        "bytes_out": 0,
    }
    try:
        yield rec
    except Exception as exc:
        rec["outcome"] = "exception"
        rec["error_code"] = exc.__class__.__name__
        raise
    finally:
        rec["latency_ms"] = int((time.monotonic() - start) * 1000)
        logger.info("mcp.call %s", rec["method"], extra={"mcp": rec})
