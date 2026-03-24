"""
GPU Memory Orchestrator — Unified model lifecycle management.

Tracks all GPU-resident models (Ollama LLMs, embeddings, SD pipelines, Whisper)
in a single registry. Evicts intelligently based on weighted scoring (priority,
recency, frequency, size). Responds to frontend navigation intent to predictively
preload models before the user needs them.

Sits above the existing gpu_resource_coordinator (video exclusive locks) and
ollama_resource_manager (adaptive context windows) — delegates to both.
"""

import gc
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & Data Structures
# ---------------------------------------------------------------------------

class ModelType(Enum):
    OLLAMA_LLM = "ollama_llm"
    OLLAMA_EMBEDDING = "ollama_embedding"
    SD_PIPELINE = "sd_pipeline"
    VIDEO_PIPELINE = "video_pipeline"
    WHISPER = "whisper"


class SlotState(Enum):
    LOADED = "loaded"
    LOADING = "loading"
    UNLOADING = "unloading"
    UNLOADED = "unloaded"


@dataclass
class ModelSlot:
    """Tracks a single GPU-resident model."""
    slot_id: str                        # e.g. "ollama:llama3", "sd:sd-1.5"
    model_type: ModelType
    vram_mb: int                        # Estimated VRAM consumption
    loaded_at: float = 0.0              # time.time() when loaded
    last_used: float = 0.0              # time.time() of last inference
    use_count: int = 0                  # Total inferences since load
    priority: int = 50                  # 0-100, higher = harder to evict
    state: SlotState = SlotState.LOADED
    preloaded_for: Optional[str] = None # Route hint that triggered preload

    def to_dict(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "model_type": self.model_type.value,
            "vram_mb": self.vram_mb,
            "loaded_at": self.loaded_at,
            "last_used": self.last_used,
            "last_used_ago_s": round(time.time() - self.last_used, 1) if self.last_used else None,
            "use_count": self.use_count,
            "priority": self.priority,
            "state": self.state.value,
            "preloaded_for": self.preloaded_for,
        }


@dataclass
class ModelNeed:
    """Describes a model requirement for a route."""
    slot_prefix: str    # e.g. "ollama:llm", "sd:pipeline"
    priority: int
    required: bool = True
    exclusive: bool = False   # If True, ALL other models must be evicted


# ---------------------------------------------------------------------------
# Route → Model Intent Map
# ---------------------------------------------------------------------------

ROUTE_MODEL_MAP: Dict[str, List[ModelNeed]] = {
    "/chat":            [ModelNeed("ollama:llm", priority=90)],
    "/voice-chat":      [ModelNeed("ollama:llm", priority=90),
                         ModelNeed("whisper:stt", priority=80)],
    "/images":          [ModelNeed("sd:pipeline", priority=85)],
    "/batch-images":    [ModelNeed("sd:pipeline", priority=85)],
    "/video":           [ModelNeed("video:pipeline", priority=95, exclusive=True)],
    "/documents":       [ModelNeed("ollama:embedding", priority=60, required=False)],
    "/settings":        [],
    "/":                [],  # Dashboard — no models needed, good time to idle-evict
}

# ---------------------------------------------------------------------------
# Quality Tiers
# ---------------------------------------------------------------------------

QUALITY_TIERS = {
    "speed": {
        "sd_steps": 10,
        "sd_max_resolution": 512,
        "llm_num_ctx": 4096,
        "ollama_keep_alive": "60s",
        "keep_alive_seconds": 60,
    },
    "balanced": {
        "sd_steps": 20,
        "sd_max_resolution": 768,
        "llm_num_ctx": 8192,
        "ollama_keep_alive": "300s",
        "keep_alive_seconds": 300,
    },
    "quality": {
        "sd_steps": 35,
        "sd_max_resolution": 1024,
        "llm_num_ctx": 16384,
        "ollama_keep_alive": "600s",
        "keep_alive_seconds": 600,
    },
}

DEFAULT_TIER = "balanced"


# ---------------------------------------------------------------------------
# The Orchestrator
# ---------------------------------------------------------------------------

class GPUMemoryOrchestrator:
    """
    Singleton orchestrator for all GPU model lifecycle management.

    - Maintains a registry of every GPU-resident model
    - Evicts based on weighted scoring (recency, priority, frequency, size)
    - Responds to frontend route intents for predictive preloading
    - Delegates to gpu_resource_coordinator for exclusive video locks
    - Delegates to ollama_resource_manager for model metadata
    """

    _instance = None
    _creation_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._creation_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._lock = threading.RLock()

        # Model registry: slot_id → ModelSlot
        self._registry: Dict[str, ModelSlot] = {}

        # Quality tier
        self._quality_tier = self._load_quality_tier()

        # Config
        self._eviction_grace_s = int(os.environ.get("GUAARDVARK_GPU_EVICTION_GRACE", "30"))
        self._idle_timeout_s = int(os.environ.get("GUAARDVARK_GPU_IDLE_TIMEOUT", "300"))
        self._sync_interval_s = 30

        # Background thread
        self._stop_event = threading.Event()
        self._bg_thread = threading.Thread(
            target=self._background_loop,
            name="gpu-orchestrator-bg",
            daemon=True,
        )
        self._bg_thread.start()

        # Initial sync from hardware
        self._sync_from_hardware()

        logger.info(
            f"GPU Memory Orchestrator initialized (tier={self._quality_tier}, "
            f"idle_timeout={self._idle_timeout_s}s, grace={self._eviction_grace_s}s)"
        )

    # ------------------------------------------------------------------
    # Public API: Model Lifecycle
    # ------------------------------------------------------------------

    def request_model(
        self,
        slot_id: str,
        vram_estimate_mb: int,
        priority: int = 50,
        model_type: Optional[ModelType] = None,
        exclusive: bool = False,
    ) -> ModelSlot:
        """
        Request GPU resources for a model. Evicts other models if needed.

        Args:
            slot_id: Unique model identifier (e.g. "sd:pipeline", "ollama:llama3")
            vram_estimate_mb: Estimated VRAM the model will consume
            priority: 0-100, higher = harder to evict later
            model_type: Auto-inferred from slot_id prefix if not given
            exclusive: If True, evict ALL other models first

        Returns:
            The ModelSlot for the requested model.
        """
        if model_type is None:
            model_type = self._infer_model_type(slot_id)

        with self._lock:
            # Already loaded?
            existing = self._registry.get(slot_id)
            if existing and existing.state == SlotState.LOADED:
                existing.last_used = time.time()
                existing.use_count += 1
                existing.priority = max(existing.priority, priority)
                logger.debug(f"Model {slot_id} already loaded, use_count={existing.use_count}")
                return existing

            # Exclusive mode: evict everything
            if exclusive:
                self._evict_all(exclude=[slot_id])
            else:
                # Evict enough to fit this model
                vram = self._get_vram_info()
                if vram.get("success"):
                    available = vram["available_mb"]
                    if available < vram_estimate_mb:
                        needed = vram_estimate_mb - available
                        self._evict_until_free(needed, exclude=[slot_id])

            # Register the slot
            now = time.time()
            slot = ModelSlot(
                slot_id=slot_id,
                model_type=model_type,
                vram_mb=vram_estimate_mb,
                loaded_at=now,
                last_used=now,
                use_count=1,
                priority=priority,
                state=SlotState.LOADING,
            )
            self._registry[slot_id] = slot
            logger.info(f"Model {slot_id} registered (type={model_type.value}, ~{vram_estimate_mb}MB, priority={priority})")

            return slot

    def mark_model_loaded(self, slot_id: str):
        """Mark a model as fully loaded (call after the actual load completes)."""
        with self._lock:
            slot = self._registry.get(slot_id)
            if slot:
                slot.state = SlotState.LOADED
                slot.loaded_at = time.time()
                logger.debug(f"Model {slot_id} marked LOADED")

    def release_model(self, slot_id: str):
        """
        Mark a model as no longer in active use. Does NOT unload —
        just updates last_used so the eviction timer starts.
        """
        with self._lock:
            slot = self._registry.get(slot_id)
            if slot:
                slot.last_used = time.time()
                logger.debug(f"Model {slot_id} released (still in VRAM, eviction timer started)")

    def force_evict(self, slot_id: str) -> bool:
        """Force-evict a specific model from GPU."""
        with self._lock:
            slot = self._registry.get(slot_id)
            if not slot or slot.state == SlotState.UNLOADED:
                return False
            return self._unload_model(slot)

    # ------------------------------------------------------------------
    # Public API: Route Intent
    # ------------------------------------------------------------------

    def prepare_for_route(self, route: str) -> Dict[str, Any]:
        """
        Prepare GPU resources for a frontend route. Evicts unneeded
        models and starts preloading needed ones.

        Args:
            route: The frontend route path (e.g. "/images", "/chat")

        Returns:
            Summary of actions taken.
        """
        # Normalize route: strip project IDs from parameterized routes
        normalized = self._normalize_route(route)
        needs = ROUTE_MODEL_MAP.get(normalized, [])

        actions = []
        with self._lock:
            # Check if any need is exclusive
            exclusive_need = next((n for n in needs if n.exclusive), None)
            if exclusive_need:
                # Evict everything except what this route needs
                needed_prefixes = {n.slot_prefix for n in needs}
                for sid, slot in list(self._registry.items()):
                    if slot.state in (SlotState.LOADED, SlotState.LOADING):
                        if not any(sid.startswith(p.replace(":pipeline", ":").replace(":llm", ":").replace(":stt", ":").replace(":embedding", ":")) or sid.startswith(p) for p in needed_prefixes):
                            if self._unload_model(slot):
                                actions.append({"action": "evict", "slot_id": sid, "reason": f"exclusive route {normalized}"})

            # For each needed model, ensure it's loaded or queued
            for need in needs:
                matching = self._find_matching_slot(need.slot_prefix)
                if matching and matching.state == SlotState.LOADED:
                    matching.priority = max(matching.priority, need.priority)
                    actions.append({"action": "already_loaded", "slot_id": matching.slot_id})
                elif need.required:
                    actions.append({
                        "action": "preload_needed",
                        "slot_prefix": need.slot_prefix,
                        "priority": need.priority,
                    })

        logger.info(f"Route intent: {route} → {len(actions)} actions")
        return {"route": route, "normalized": normalized, "actions": actions}

    # ------------------------------------------------------------------
    # Public API: Quality Tiers
    # ------------------------------------------------------------------

    def get_quality_tier(self) -> str:
        return self._quality_tier

    def get_tier_config(self) -> dict:
        return QUALITY_TIERS.get(self._quality_tier, QUALITY_TIERS[DEFAULT_TIER])

    def set_quality_tier(self, tier: str) -> Dict[str, Any]:
        """Change the quality tier. Adjusts Ollama keep_alive for loaded models."""
        if tier not in QUALITY_TIERS:
            return {"success": False, "error": f"Unknown tier: {tier}. Valid: {list(QUALITY_TIERS.keys())}"}

        old_tier = self._quality_tier
        self._quality_tier = tier
        self._save_quality_tier(tier)

        changes = [f"tier changed from {old_tier} to {tier}"]

        # Update idle timeout based on tier keep_alive
        tier_config = QUALITY_TIERS[tier]
        self._idle_timeout_s = tier_config["keep_alive_seconds"]
        changes.append(f"idle_timeout set to {self._idle_timeout_s}s")

        logger.info(f"Quality tier changed: {old_tier} → {tier}")
        return {"success": True, "tier": tier, "changes": changes, "config": tier_config}

    # ------------------------------------------------------------------
    # Public API: Status
    # ------------------------------------------------------------------

    def get_registry_snapshot(self) -> Dict[str, Any]:
        """Full state snapshot for the GPU status API/widget."""
        vram = self._get_vram_info()

        with self._lock:
            models = []
            for slot in self._registry.values():
                d = slot.to_dict()
                d["eviction_score"] = round(self._compute_eviction_score(slot), 3)
                models.append(d)

            # Sort by eviction score descending (most likely to be evicted first)
            models.sort(key=lambda m: m["eviction_score"], reverse=True)

            # Compute tracked vs actual VRAM
            tracked_vram = sum(
                s.vram_mb for s in self._registry.values()
                if s.state in (SlotState.LOADED, SlotState.LOADING)
            )

        snapshot = {
            "vram": {
                "total_mb": vram.get("total_mb", 0),
                "used_mb": vram.get("used_mb", 0),
                "free_mb": vram.get("available_mb", 0),
                "gpu_name": vram.get("gpu_name", "Unknown"),
                "utilization_percent": vram.get("utilization_percent", 0),
            },
            "models": models,
            "tracked_vram_mb": tracked_vram,
            "untracked_vram_mb": max(0, vram.get("used_mb", 0) - tracked_vram),
            "quality_tier": self._quality_tier,
            "tier_config": QUALITY_TIERS.get(self._quality_tier, {}),
            "idle_timeout_s": self._idle_timeout_s,
            "eviction_grace_s": self._eviction_grace_s,
            "timestamp": datetime.utcnow().isoformat(),
        }
        return snapshot

    def on_exclusive_lock_released(self):
        """Called by gpu_resource_coordinator when video gen finishes.
        Re-syncs the registry so the status widget updates immediately."""
        logger.info("Exclusive lock released — syncing registry from hardware")
        self._sync_from_hardware()

    # ------------------------------------------------------------------
    # Internal: Eviction Engine
    # ------------------------------------------------------------------

    def _compute_eviction_score(self, slot: ModelSlot) -> float:
        """
        Weighted eviction score. Higher = more likely to be evicted.

        Components:
            40% — time since last use (normalized to 0-1 over 30 min)
            30% — inverse priority (lower priority → higher score)
            20% — inverse use frequency (less used → higher score)
            10% — VRAM size (bigger → slightly more likely to evict)
        """
        now = time.time()

        # Grace period: recently loaded models are immune
        if (now - slot.loaded_at) < self._eviction_grace_s:
            return -1.0  # Negative = immune

        # Time component: normalize to 30 min
        idle_s = now - slot.last_used if slot.last_used else now - slot.loaded_at
        time_score = min(idle_s / 1800.0, 1.0)

        # Priority component: invert
        priority_score = 1.0 - (slot.priority / 100.0)

        # Frequency component: normalize over max 100 uses
        freq_score = 1.0 - min(slot.use_count / 100.0, 1.0)

        # Size component: normalize over 16GB
        size_score = min(slot.vram_mb / 16384.0, 1.0)

        return (
            0.4 * time_score +
            0.3 * priority_score +
            0.2 * freq_score +
            0.1 * size_score
        )

    def _evict_until_free(self, needed_mb: int, exclude: List[str] = None):
        """Evict models until at least needed_mb is free."""
        exclude = exclude or []
        freed = 0

        # Build eviction candidates sorted by score (highest first)
        candidates = [
            s for s in self._registry.values()
            if s.slot_id not in exclude
            and s.state == SlotState.LOADED
            and self._compute_eviction_score(s) >= 0  # Not in grace period
        ]
        candidates.sort(key=self._compute_eviction_score, reverse=True)

        for slot in candidates:
            if freed >= needed_mb:
                break
            logger.info(f"Evicting {slot.slot_id} ({slot.vram_mb}MB, score={self._compute_eviction_score(slot):.3f}) to free {needed_mb}MB")
            if self._unload_model(slot):
                freed += slot.vram_mb

        if freed < needed_mb:
            logger.warning(f"Could only free {freed}MB of {needed_mb}MB requested")

    def _evict_all(self, exclude: List[str] = None):
        """Evict all loaded models except excluded ones."""
        exclude = exclude or []
        for slot in list(self._registry.values()):
            if slot.slot_id not in exclude and slot.state == SlotState.LOADED:
                self._unload_model(slot)

    # ------------------------------------------------------------------
    # Internal: Model Unloading
    # ------------------------------------------------------------------

    def _unload_model(self, slot: ModelSlot) -> bool:
        """Dispatch unload to the correct backend."""
        slot.state = SlotState.UNLOADING
        success = False

        try:
            if slot.model_type in (ModelType.OLLAMA_LLM, ModelType.OLLAMA_EMBEDDING):
                success = self._unload_ollama_model(slot.slot_id)
            elif slot.model_type == ModelType.SD_PIPELINE:
                success = self._unload_sd_pipeline()
            elif slot.model_type == ModelType.VIDEO_PIPELINE:
                success = self._unload_video_pipeline()
            elif slot.model_type == ModelType.WHISPER:
                success = self._unload_whisper()
            else:
                logger.warning(f"Unknown model type for unload: {slot.model_type}")
        except Exception as e:
            logger.error(f"Error unloading {slot.slot_id}: {e}")

        if success:
            slot.state = SlotState.UNLOADED
            # Remove from registry
            self._registry.pop(slot.slot_id, None)
            logger.info(f"Unloaded {slot.slot_id} (~{slot.vram_mb}MB freed)")
        else:
            slot.state = SlotState.LOADED  # Revert
            logger.warning(f"Failed to unload {slot.slot_id}")

        return success

    def _unload_ollama_model(self, slot_id: str) -> bool:
        """Unload an Ollama model by setting keep_alive=0."""
        try:
            from backend.utils.ollama_resource_manager import get_ollama_base_url
            base_url = get_ollama_base_url()

            # Extract model name from slot_id (e.g. "ollama:llama3" → "llama3")
            model_name = slot_id.split(":", 1)[1] if ":" in slot_id else slot_id

            resp = requests.post(
                f"{base_url}/api/generate",
                json={"model": model_name, "prompt": "", "keep_alive": 0, "options": {"num_ctx": 1}},
                timeout=15,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to unload Ollama model {slot_id}: {e}")
            return False

    def _unload_sd_pipeline(self) -> bool:
        """Unload the Stable Diffusion pipeline from GPU."""
        try:
            from backend.services.offline_image_generator import get_image_generator
            gen = get_image_generator()
            if hasattr(gen, '_pipeline') and gen._pipeline is not None:
                import torch
                gen._pipeline.to('cpu')
                del gen._pipeline
                gen._pipeline = None
                gen._current_model = None
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
                return True
            return True  # Already unloaded
        except Exception as e:
            logger.error(f"Failed to unload SD pipeline: {e}")
            return False

    def _unload_video_pipeline(self) -> bool:
        """Unload video generation pipeline. Delegates to force_clear_gpu_memory."""
        try:
            from backend.services.offline_video_generator import force_clear_gpu_memory
            force_clear_gpu_memory()
            return True
        except Exception as e:
            logger.error(f"Failed to unload video pipeline: {e}")
            return False

    def _unload_whisper(self) -> bool:
        """Whisper runs as an external process — nothing to unload from GPU."""
        # whisper.cpp is a subprocess, not in-process GPU memory
        return True

    # ------------------------------------------------------------------
    # Internal: Hardware Sync
    # ------------------------------------------------------------------

    def _sync_from_hardware(self):
        """Rebuild registry from actual GPU state (Ollama + in-process models)."""
        with self._lock:
            discovered = {}
            now = time.time()

            # 1. Sync Ollama models
            try:
                from backend.utils.ollama_resource_manager import get_ollama_base_url
                base_url = get_ollama_base_url()
                resp = requests.get(f"{base_url}/api/ps", timeout=3)
                if resp.status_code == 200:
                    for m in resp.json().get("models", []):
                        name = m.get("name", "")
                        size_bytes = m.get("size", 0)
                        vram_mb = size_bytes // (1024 * 1024) if size_bytes else 4000

                        # Determine if it's an embedding or LLM
                        is_embed = any(kw in name.lower() for kw in ("embed", "retrieval", "minilm"))
                        model_type = ModelType.OLLAMA_EMBEDDING if is_embed else ModelType.OLLAMA_LLM
                        prefix = "ollama:" + name

                        # Preserve existing slot data if we already track it
                        existing = self._registry.get(prefix)
                        if existing:
                            existing.vram_mb = vram_mb
                            existing.state = SlotState.LOADED
                            discovered[prefix] = existing
                        else:
                            discovered[prefix] = ModelSlot(
                                slot_id=prefix,
                                model_type=model_type,
                                vram_mb=vram_mb,
                                loaded_at=now,
                                last_used=now,
                                priority=70 if model_type == ModelType.OLLAMA_LLM else 50,
                                state=SlotState.LOADED,
                            )
            except Exception as e:
                logger.debug(f"Ollama sync failed (non-critical): {e}")

            # 2. Sync SD pipeline
            try:
                from backend.services.offline_image_generator import _generator_instance
                if _generator_instance is not None and hasattr(_generator_instance, '_pipeline') and _generator_instance._pipeline is not None:
                    prefix = f"sd:{_generator_instance._current_model or 'pipeline'}"
                    existing = self._registry.get(prefix)
                    if existing:
                        existing.state = SlotState.LOADED
                        discovered[prefix] = existing
                    else:
                        discovered[prefix] = ModelSlot(
                            slot_id=prefix,
                            model_type=ModelType.SD_PIPELINE,
                            vram_mb=3500,
                            loaded_at=now,
                            last_used=now,
                            priority=60,
                            state=SlotState.LOADED,
                        )
            except Exception as e:
                logger.debug(f"SD pipeline sync failed (non-critical): {e}")

            # Merge: keep anything in discovered, drop anything not found
            self._registry = discovered

        logger.debug(f"Hardware sync complete: {len(self._registry)} models tracked")

    # ------------------------------------------------------------------
    # Internal: Background Thread
    # ------------------------------------------------------------------

    def _background_loop(self):
        """Periodic sync + idle eviction."""
        while not self._stop_event.is_set():
            try:
                self._sync_from_hardware()
                self._evict_idle_models()
                self._emit_status_if_subscribers()
            except Exception as e:
                logger.error(f"Background loop error: {e}")

            self._stop_event.wait(self._sync_interval_s)

    def _evict_idle_models(self):
        """Evict models that have been idle longer than the timeout."""
        now = time.time()
        with self._lock:
            for slot in list(self._registry.values()):
                if slot.state != SlotState.LOADED:
                    continue
                idle_s = now - slot.last_used
                if idle_s > self._idle_timeout_s:
                    # Don't evict high-priority models that are recently used frequently
                    if slot.priority >= 90 and slot.use_count > 10:
                        continue
                    logger.info(f"Idle eviction: {slot.slot_id} (idle {idle_s:.0f}s > timeout {self._idle_timeout_s}s)")
                    self._unload_model(slot)

    def _emit_status_if_subscribers(self):
        """Emit gpu:status to Socket.IO room if anyone is subscribed."""
        try:
            from backend.socketio_instance import socketio
            # Only emit if there are clients in the gpu_status room
            snapshot = self.get_registry_snapshot()
            socketio.emit("gpu:status", snapshot, room="gpu_status")
        except Exception:
            pass  # Socket.IO not available or no subscribers — silent

    # ------------------------------------------------------------------
    # Internal: Helpers
    # ------------------------------------------------------------------

    def _get_vram_info(self) -> dict:
        """Get current VRAM status via the existing coordinator."""
        try:
            from backend.services.gpu_resource_coordinator import get_gpu_coordinator
            return get_gpu_coordinator().get_available_vram()
        except Exception as e:
            logger.debug(f"VRAM query failed: {e}")
            return {"success": False, "available_mb": 0, "total_mb": 0, "used_mb": 0}

    def _infer_model_type(self, slot_id: str) -> ModelType:
        """Infer ModelType from slot_id prefix convention."""
        lower = slot_id.lower()
        if lower.startswith("sd:"):
            return ModelType.SD_PIPELINE
        elif lower.startswith("video:"):
            return ModelType.VIDEO_PIPELINE
        elif lower.startswith("whisper:"):
            return ModelType.WHISPER
        elif lower.startswith("ollama:"):
            name = slot_id.split(":", 1)[1] if ":" in slot_id else ""
            if any(kw in name.lower() for kw in ("embed", "retrieval", "minilm")):
                return ModelType.OLLAMA_EMBEDDING
            return ModelType.OLLAMA_LLM
        return ModelType.OLLAMA_LLM  # Default

    def _normalize_route(self, route: str) -> str:
        """Strip parameterized segments from routes for intent matching."""
        # /chat/abc123 → /chat, /projects/xyz → /projects
        parts = route.strip("/").split("/")
        if len(parts) >= 2:
            # Check if second part looks like an ID (contains digits or is very long)
            second = parts[1]
            if any(c.isdigit() for c in second) or len(second) > 20:
                return f"/{parts[0]}"
        return f"/{parts[0]}" if parts and parts[0] else "/"

    def _find_matching_slot(self, prefix: str) -> Optional[ModelSlot]:
        """Find a loaded slot that matches a prefix pattern."""
        for slot in self._registry.values():
            if slot.slot_id.startswith(prefix.split(":")[0] + ":"):
                if slot.state == SlotState.LOADED:
                    return slot
        return None

    def _load_quality_tier(self) -> str:
        """Load quality tier from DB setting or env var."""
        try:
            from backend.utils.settings_utils import get_setting
            tier = get_setting("gpu_quality_tier", default=None)
            if tier and tier in QUALITY_TIERS:
                return tier
        except Exception:
            pass
        return os.environ.get("GUAARDVARK_GPU_QUALITY_TIER", DEFAULT_TIER)

    def _save_quality_tier(self, tier: str):
        """Persist quality tier to DB."""
        try:
            from backend.utils.settings_utils import save_setting
            save_setting("gpu_quality_tier", tier)
        except Exception as e:
            logger.debug(f"Could not persist quality tier: {e}")

    def shutdown(self):
        """Stop the background thread."""
        self._stop_event.set()
        if self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5)
        logger.info("GPU Memory Orchestrator shut down")


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------

_orchestrator_instance: Optional[GPUMemoryOrchestrator] = None


def get_orchestrator() -> GPUMemoryOrchestrator:
    """Get the global GPU Memory Orchestrator instance."""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = GPUMemoryOrchestrator()
    return _orchestrator_instance
