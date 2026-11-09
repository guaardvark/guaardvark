"""Embedding through the GPU Embedding plugin (plugins/gpu_embedding).

EmbeddingRouter asks ``PluginEmbeddingGate`` before each call. The plugin is
used only when it is switched on in Settings, answers on its port, serves the
same Ollama model the router embeds with, and a probe text comes back as the
same vector (length, direction and norm) the router's own path produces.
Anything else leaves the router on its own path, and ``status()`` says why.
"""

import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

PLUGIN_ID = "gpu_embedding"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "plugins" / PLUGIN_ID / "plugin.json"
_STATE_FILE = _REPO_ROOT / "data" / "plugin_state.json"

RECHECK_S = 30.0
ENABLED_TTL_S = 5.0
PROBE_TEXT = "Index path check: a red fox crosses a frozen river at dawn."
# Same model, same input, same Ollama: anything below this is another model,
# another prefix or another normalisation.
MIN_PROBE_COSINE = 0.9999
MAX_PROBE_NORM_DRIFT = 1e-3


class PluginEmbeddingError(RuntimeError):
    """The plugin did not return usable vectors for a call."""


def _manifest() -> dict:
    try:
        with open(_MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def plugin_enabled() -> bool:
    """The Settings toggle for the plugin, else its manifest default (off)."""
    try:
        from backend.plugins.plugin_state_store import PluginStateStore
        prefs = PluginStateStore(_STATE_FILE).get_user_enabled()
        if PLUGIN_ID in prefs:
            return bool(prefs[PLUGIN_ID])
    except Exception:
        pass
    return bool(_manifest().get("config", {}).get("default_enabled", False))


def _service_url() -> str:
    manifest = _manifest()
    port = os.environ.get("PLUGIN_GPU_EMBEDDING_PORT") or manifest.get("port", 8204)
    return f"http://127.0.0.1:{int(port)}"


def _limits() -> Dict[str, int]:
    cfg = _manifest().get("config", {})
    return {
        "batch_size": int(os.environ.get("PLUGIN_GPU_EMBEDDING_BATCH_SIZE") or cfg.get("batch_size", 32)),
        "max_text_length": int(os.environ.get("PLUGIN_GPU_EMBEDDING_MAX_TEXT_LENGTH")
                               or cfg.get("max_text_length", 8192)),
        "timeout": int(cfg.get("timeout", 30)),
    }


def _same_model(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    try:
        from backend.utils.ollama_resource_manager import same_model
        return same_model(a, b) is True
    except Exception:
        def full(n):
            return n if ":" in n else f"{n}:latest"
        return full(a) == full(b)


def _norm(v: List[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def compare_vectors(a: List[float], b: List[float]) -> Optional[str]:
    """None when ``a`` and ``b`` are the same embedding, else what differs."""
    if len(a) != len(b):
        return f"dimension {len(a)} vs {len(b)}"
    na, nb = _norm(a), _norm(b)
    if na == 0 or nb == 0 or not math.isfinite(na) or not math.isfinite(nb):
        return "empty or non-finite vector"
    if abs(na - nb) / max(na, nb) > MAX_PROBE_NORM_DRIFT:
        return f"norm {na:.4f} vs {nb:.4f}"
    cos = sum(x * y for x, y in zip(a, b)) / (na * nb)
    if cos < MIN_PROBE_COSINE:
        return f"cosine {cos:.6f} (needs {MIN_PROBE_COSINE})"
    return None


class PluginEmbeddingGate:
    """Decides, per router model, whether the plugin may embed for the index."""

    def __init__(self):
        self._lock = threading.RLock()
        self._enabled = False
        self._enabled_at = -ENABLED_TTL_S
        self._checked_at = -RECHECK_S
        self._checked_model: Optional[str] = None
        self._verified: Optional[tuple] = None
        self._usable = False
        self._reason = "not checked yet"
        self._served_model: Optional[str] = None
        self._dim: Optional[int] = None
        self._last_logged: Optional[tuple] = None
        self.texts_via_plugin = 0
        self.texts_via_fallback = 0

    def enabled(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if now - self._enabled_at >= ENABLED_TTL_S:
                self._enabled = plugin_enabled()
                self._enabled_at = now
                if not self._enabled:
                    self._set(False, "plugin is switched off in Settings")
            return self._enabled

    def _set(self, usable: bool, reason: str) -> None:
        self._usable, self._reason = usable, reason
        key = (usable, reason)
        if key != self._last_logged:
            self._last_logged = key
            if usable:
                logger.info("Embedding path: GPU Embedding plugin (%s)", reason)
            elif self._enabled:
                logger.warning("Embedding path: indexer's own Ollama path; GPU Embedding plugin not used: %s",
                               reason)

    def usable(self, model_name: str, reference: Callable[[List[str]], List[List[float]]],
               formatter: Callable[[str], str]) -> bool:
        """True when the plugin may embed for ``model_name`` right now.

        ``reference`` embeds through the router's own path and ``formatter``
        applies the router's text prefix; both are used once per model to probe.
        """
        if not self.enabled():
            return False
        now = time.monotonic()
        with self._lock:
            if now - self._checked_at < RECHECK_S and self._checked_model == model_name:
                return self._usable
            self._checked_at, self._checked_model = now, model_name
            try:
                import requests
                r = requests.get(f"{_service_url()}/models", timeout=2)
                info = r.json() if r.ok else {}
            except Exception:
                self._set(False, f"plugin not answering at {_service_url()}")
                return False
            served = info.get("current_model")
            self._served_model = served
            if not info.get("loaded") or not served:
                self._set(False, "plugin has no model loaded")
                return False
            if not _same_model(served, model_name):
                self._set(False, f"plugin serves {served}, the index uses {model_name}; "
                                 "restart the plugin after changing the embedding model")
                return False
            key = (model_name, served)
            if self._verified != key:
                try:
                    mine = reference([PROBE_TEXT])[0]
                    theirs = self._post([formatter(PROBE_TEXT)], served)[0]
                except Exception as e:
                    self._set(False, f"probe failed: {e}")
                    return False
                diff = compare_vectors(theirs, mine)
                if diff:
                    self._set(False, f"plugin vectors differ from the indexer's ({diff})")
                    return False
                self._verified, self._dim = key, len(mine)
            self._set(True, f"{served}, {self._dim}-d, probe matched")
            return True

    def _post(self, formatted: List[str], served: str) -> List[List[float]]:
        import requests
        limits = _limits()
        if len(formatted) == 1:
            r = requests.post(f"{_service_url()}/embed", json={"text": formatted[0]},
                              timeout=limits["timeout"])
            body = r.json()
            vectors = [body.get("embedding")] if r.ok else None
        else:
            r = requests.post(f"{_service_url()}/embed_batch", json={"texts": formatted},
                              timeout=limits["timeout"])
            body = r.json()
            vectors = body.get("embeddings") if r.ok else None
        if not vectors:
            raise PluginEmbeddingError(body.get("error") or f"HTTP {r.status_code}")
        if not _same_model(body.get("model"), served):
            raise PluginEmbeddingError(f"plugin answered with {body.get('model')}, expected {served}")
        return vectors

    def embed(self, texts: List[str], formatter: Callable[[str], str]) -> Optional[List[List[float]]]:
        """Vectors from the plugin, or None when a text is over its length limit.

        Raises PluginEmbeddingError when the plugin fails or returns a vector
        of another width; the gate then stays closed until the next check.
        """
        limits = _limits()
        formatted = [formatter(t) for t in texts]
        if any(len(t) > limits["max_text_length"] for t in formatted):
            return None
        with self._lock:
            served, dim = self._served_model, self._dim
        out: List[List[float]] = []
        try:
            for i in range(0, len(formatted), max(1, limits["batch_size"])):
                chunk = formatted[i:i + limits["batch_size"]]
                vectors = self._post(chunk, served)
                if len(vectors) != len(chunk) or any(not v or len(v) != dim for v in vectors):
                    raise PluginEmbeddingError(f"plugin returned vectors that are not {dim}-d")
                out.extend(vectors)
        except Exception as e:
            self.fail(str(e))
            raise PluginEmbeddingError(str(e)) from e
        return out

    def fail(self, reason: str) -> None:
        with self._lock:
            self._verified = None
            self._checked_at = time.monotonic()
            self._set(False, f"last call failed: {reason}")

    def status(self) -> dict:
        with self._lock:
            return {
                "plugin": PLUGIN_ID,
                "enabled": self._enabled,
                "in_use": bool(self._enabled and self._usable),
                "reason": self._reason,
                "served_model": self._served_model,
                "dimension": self._dim,
                "texts_via_plugin": self.texts_via_plugin,
                "texts_via_fallback": self.texts_via_fallback,
            }
