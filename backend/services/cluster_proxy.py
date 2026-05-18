"""HTTP proxy layer for cluster-routed workloads — pure logic half.

Three classes here (classifier, loop detector, target resolver) + NodeTarget
dataclass. HttpProxyForwarder lands in Task 19; Flask middleware in Task 20.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator

log = logging.getLogger(__name__)


CLASSIFIER_RULES: list[tuple[str, str, str]] = [
    # Chat
    ("POST", "/api/chat/unified",             "llm_chat"),
    ("POST", "/api/enhanced-chat",            "llm_chat"),
    # Batch generation (prefix match — trailing slash means prefix)
    ("POST", "/api/batch-video/generate/",    "video_generation"),
    ("POST", "/api/batch-image/generate/",    "image_generation"),
    # Embeddings (document indexing — prefix match)
    ("POST", "/api/index/",                   "embeddings"),
    ("POST", "/api/entity-indexing/",         "embeddings"),
    # RAG search — only semantic; by-tag/by-project stay local
    ("POST", "/api/search/semantic",          "rag_search"),
    # Voice
    ("POST", "/api/voice/speech-to-text",     "voice_stt"),
    ("POST", "/api/voice/text-to-speech",     "voice_tts"),
]

ALWAYS_LOCAL_PREFIXES: tuple[str, ...] = (
    "/api/health",
    "/api/settings/",
    "/api/files/",
    "/api/auth/",
    "/api/projects/",
    "/api/clients/",
    "/api/folders/",
    "/api/documents/",
    "/api/memories/",
    "/api/interconnector/",
    "/api/node/",
    "/api/cluster/",
    "/api/metadata-indexing/",
    "/api/search/by-tag/",
    "/api/search/by-project/",
    "/socket.io/",
)


class WorkloadClassifier:
    """Maps a request to a workload tag, or None if it stays local."""

    def classify(self, method: str, path: str) -> str | None:
        # Fast-path: always-local prefixes checked before anything else
        for prefix in ALWAYS_LOCAL_PREFIXES:
            if path.startswith(prefix):
                return None
        # Workload rules — exact or prefix match (trailing slash = prefix)
        for rule_method, rule_path, workload in CLASSIFIER_RULES:
            if method != rule_method:
                continue
            if rule_path.endswith("/"):
                if path.startswith(rule_path):
                    return workload
            else:
                if path == rule_path:
                    return workload
        return None


class LoopDetector:
    MAX_HOPS = 2

    def should_force_local(self, request) -> bool:
        try:
            hops = int(request.headers.get("X-Guaardvark-Hops", "0"))
        except (ValueError, TypeError):
            hops = 0
        return hops >= self.MAX_HOPS


@dataclass
class NodeTarget:
    node_id: str
    host: str
    port: int
    api_key: str = ""  # InterconnectorNode has no api_key column yet; v1 sends node_id

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class ProxyTargetResolver:
    """Yields the primary target, then fallbacks (skipping offline + local),
    then None. Callers iterate until they get a successful forward or None."""

    def resolve(self, workload: str, routing_table,
                local_node_id: str, model_hint: str | None = None) -> Iterator[NodeTarget | None]:
        route = routing_table.routes.get(workload) if routing_table else None
        if route is None or route.mode == "local" or route.primary is None:
            yield None
            return

        chain = [route.primary] + list(route.fallback)
        for node_id in chain:
            if node_id == local_node_id:
                continue  # this is us — caller handles locally
            target = self._get_target(node_id)
            if target is None:
                continue
            yield target
        yield None  # chain exhausted

    def _get_target(self, node_id: str) -> NodeTarget | None:
        try:
            from backend.models import InterconnectorNode
            node = InterconnectorNode.query.filter_by(node_id=node_id).first()
        except Exception:
            return None
        if node is None or not node.online:
            return None
        # api_key column doesn't exist yet — fall back to node_id for handshake
        api_key = getattr(node, "api_key", None) or node.node_id
        return NodeTarget(node_id=node.node_id, host=node.host,
                          port=node.port, api_key=api_key)


# ---- HTTP forwarder ------------------------------------------------

HOP_BY_HOP_HEADERS = frozenset([
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
])


class HttpProxyForwarder:
    TIMEOUT_PROFILES: dict[str, tuple[int, int]] = {
        "llm_chat":         (1, 30),
        "embeddings":       (1, 20),
        "rag_search":       (1, 20),
        "video_generation": (2, 300),
        "image_generation": (2, 180),
        "voice_stt":        (1, 60),
        "voice_tts":        (1, 60),
    }

    def forward(self, target, workload: str, request):
        """Forward the Flask `request` to `target` as a remote HTTP call.

        Strips hop-by-hop headers, adds loop-prevention headers, streams the
        response back. Raises requests.ConnectionError / Timeout on failure
        so the middleware can iterate to the next fallback.
        """
        import os
        import requests as _rq
        from flask import Response

        headers = self._sanitize_headers(dict(request.headers), target, request)
        timeout = self.TIMEOUT_PROFILES.get(workload, (1, 30))
        url = f"{target.base_url}{request.path}"
        log.info("[ROUTE] %s → %s (primary) forwarding to %s", workload, target.node_id, url)

        upstream = _rq.request(
            request.method,
            url,
            headers=headers,
            params=request.args,
            data=request.get_data(),
            stream=True,
            timeout=timeout,
            allow_redirects=False,
        )

        resp_headers = {k: v for k, v in upstream.headers.items()
                        if k.lower() not in HOP_BY_HOP_HEADERS}
        return Response(
            upstream.iter_content(chunk_size=8192),
            status=upstream.status_code,
            headers=resp_headers,
        )

    def _sanitize_headers(self, incoming: dict, target, request) -> dict:
        import os
        out = {k: v for k, v in incoming.items() if k.lower() not in HOP_BY_HOP_HEADERS}
        out.pop("Host", None)  # let requests set it
        try:
            prev_hops = int(incoming.get("X-Guaardvark-Hops", "0"))
        except (ValueError, TypeError):
            prev_hops = 0
        out["X-Guaardvark-Proxy"] = "1"
        out["X-Guaardvark-Hops"] = str(prev_hops + 1)
        out["X-Guaardvark-Source-Node"] = os.environ.get("CLUSTER_NODE_ID", "unknown")
        out["X-Guaardvark-API-Key"] = target.api_key
        return out
