# backend/services/comfyui_progress_bridge.py
#
# Layer 1 of the film-orchestrator plan (docs/plans/2026-06-02-film-production-orchestrator.md).
#
# THE PROBLEM: comfyui_video_generator._wait_for_completion() polls /history/{id},
# which stays silent until the whole job is DONE. Meanwhile ComfyUI is shouting
# per-step progress into its /ws websocket that nobody was listening to. So the UI
# showed a spinner and a prayer for 20 minutes.
#
# THE FIX (additive, flag-gated): connect to that websocket with the same client_id
# we send on the /prompt POST, translate ComfyUI's "progress"/"executing" messages
# into the EXISTING unified progress rail (emit_progress_event -> Redis
# guaardvark:progress -> app.py relay -> 'job_progress' socket event), and let the
# frontend's UnifiedProgressContext (already listening) render it.
#
# This NEVER replaces the /history poll — that stays the source of truth for
# completion + results. The bridge is progress-only and self-terminating: if the
# socket dies, or ComfyUI says "executing: null" (idle/done), or it outlives its
# max lifetime, the thread ends on its own. Worst case it adds nothing; it can't
# wedge a generation.
#
# Protocol verified against the bundled ComfyUI source (2026-06-02):
#   server.py:249   -> ws connects at /ws?clientId=<id>
#   server.py:883   -> /prompt accepts "client_id" in the JSON body
#   server.py:1143  -> envelope is {"type": event, "data": data}
#   main.py:296,299 -> progress: {"value": k, "max": N, "prompt_id": ..., "node": <id>}
#   server.py:266   -> executing: {"node": <id or None>}

import json
import logging
import os
import struct
import threading
import time
from typing import Dict, Optional, Tuple

import websocket  # websocket-client 1.8.0 — already in backend venv

from backend.utils.preview_emitter import emit_preview_event
from backend.utils.progress_emitter import emit_progress_event

logger = logging.getLogger(__name__)

# ComfyUI protocol.BinaryEventTypes — keep numeric to avoid importing ComfyUI.
_PREVIEW_IMAGE = 1
_PREVIEW_IMAGE_WITH_METADATA = 4
_PREVIEW_THROTTLE_S = 0.25


def ws_progress_enabled() -> bool:
    """Master switch. ON by default; set GUAARDVARK_COMFYUI_WS_PROGRESS=0 to fall
    back to poll-only (the pre-bridge behavior). This is the ROLLBACK lever."""
    return os.environ.get("GUAARDVARK_COMFYUI_WS_PROGRESS", "1") not in ("0", "false", "False", "")


def ws_preview_enabled() -> bool:
    """Sampler thumbnails ride the same /ws as progress. Off if progress is off,
    or when GUAARDVARK_COMFYUI_WS_PREVIEW=0 (percent rail stays)."""
    if not ws_progress_enabled():
        return False
    return os.environ.get("GUAARDVARK_COMFYUI_WS_PREVIEW", "1") not in ("0", "false", "False", "")


def parse_comfy_preview_frame(raw: bytes) -> Optional[Tuple[str, bytes]]:
    """Decode a ComfyUI binary /ws message into (mime, image_bytes).

    PREVIEW_IMAGE (1): [>I event][>I type 1=jpeg|2=png][payload]
    PREVIEW_IMAGE_WITH_METADATA (4): [>I event][>I meta_len][json][payload]
    Anything else, truncated, or empty payload → None.
    """
    if not raw or len(raw) < 8:
        return None
    event = struct.unpack_from(">I", raw, 0)[0]
    if event == _PREVIEW_IMAGE:
        type_num = struct.unpack_from(">I", raw, 4)[0]
        payload = bytes(raw[8:])
        if not payload:
            return None
        mime = "image/png" if type_num == 2 else "image/jpeg"
        return mime, payload
    if event == _PREVIEW_IMAGE_WITH_METADATA:
        meta_len = struct.unpack_from(">I", raw, 4)[0]
        header = 8 + meta_len
        if meta_len < 0 or header > len(raw):
            return None
        payload = bytes(raw[header:])
        if not payload:
            return None
        mime = "image/jpeg"
        try:
            meta = json.loads(bytes(raw[8:header]).decode("utf-8"))
            if isinstance(meta, dict) and meta.get("image_type"):
                mime = str(meta["image_type"])
        except (ValueError, UnicodeDecodeError):
            pass
        return mime, payload
    return None


# class_type substring -> human stage label. ComfyUI only tells us the node *id*
# over the wire, so we resolve id -> class_type via the workflow, then class_type
# -> label here. First match wins, so order matters (specific before generic).
_STAGE_LABELS = [
    ("VHS", "encoding video"),
    ("VideoCombine", "encoding video"),
    ("RIFE", "interpolating"),
    ("FILM", "interpolating"),
    ("Interpolat", "interpolating"),
    ("Upscale", "upscaling"),
    ("VAEDecode", "decoding"),
    ("VAEEncode", "encoding latents"),
    # MoE Wan graphs often use two KSamplerAdvanced nodes; class_type alone
    # cannot tell HN vs LN — extra workflow label overrides fill that gap.
    ("KSamplerAdvanced", "denoising"),
    ("KSampler", "denoising"),
    ("Sampler", "denoising"),
    ("TextEncode", "encoding prompt"),
    ("LoraLoader", "loading LoRA"),
    ("CheckpointLoader", "loading model"),
    ("UNETLoader", "loading model"),
    ("UnetLoaderGGUF", "loading model"),
    ("CLIPLoader", "loading model"),
    ("Loader", "loading model"),
]


def _free_vram_mb() -> Optional[int]:
    """Best-effort free VRAM for progress diagnostics. Never raises."""
    try:
        from backend.services.gpu_resource_coordinator import get_available_vram
        info = get_available_vram()
        if not info.get("success"):
            return None
        free = info.get("available_mb") or info.get("free_mb")
        return int(free) if free is not None else None
    except Exception:
        return None


def _label_for(class_type: str) -> str:
    for needle, label in _STAGE_LABELS:
        if needle.lower() in class_type.lower():
            return label
    return class_type or "working"


class ComfyUIProgressBridge:
    """One bridge per generation. start() spins a daemon ws-listener thread;
    stop() asks it to quit. The thread also self-terminates, so a missed stop()
    (e.g. an early return in the caller) never leaks."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ws: Optional[websocket.WebSocket] = None
        self._last_preview_emit = 0.0

    def start(
        self,
        client_id: str,
        process_id: str,
        comfy_url: str,
        workflow: Dict,
        *,
        max_seconds: int = 7200,
        extra: Optional[Dict] = None,
    ) -> None:
        if not ws_progress_enabled():
            return
        self._last_preview_emit = 0.0
        # Build node_id -> friendly label map from the workflow we're about to queue.
        # Wan MoE: two KSamplerAdvanced nodes in step order → high/low noise labels
        # so UnifiedProgress shows which expert is running (architecture visibility).
        node_labels: Dict[str, str] = {}
        try:
            sampler_ids = []
            for nid, node in (workflow or {}).items():
                ct = node.get("class_type", "") or ""
                base = _label_for(ct)
                node_labels[str(nid)] = base
                if "KSampler" in ct or ct.endswith("Sampler"):
                    sampler_ids.append(str(nid))
            if len(sampler_ids) >= 2:
                # Stable order by node id (workflows use "10" then "11" for HN→LN).
                sampler_ids.sort(key=lambda x: int(x) if x.isdigit() else x)
                node_labels[sampler_ids[0]] = "denoising (high noise)"
                node_labels[sampler_ids[1]] = "denoising (low noise)"
                for sid in sampler_ids[2:]:
                    node_labels[sid] = "denoising"
        except Exception:
            pass  # a weird workflow just means generic labels; not worth failing for

        ws_url = comfy_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
        ws_url = f"{ws_url}/ws?clientId={client_id}"

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(ws_url, process_id, node_labels, max_seconds, extra or {}),
            name=f"comfy-ws-{process_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"ComfyUI ws progress bridge started for process {process_id}")

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    # ── the listener thread ──────────────────────────────────────────────────
    def _run(self, ws_url: str, process_id: str, node_labels: Dict[str, str],
             max_seconds: int, extra: Dict) -> None:
        deadline = time.time() + max_seconds
        try:
            # connect timeout small; recv timeout lets us check _stop / deadline.
            self._ws = websocket.create_connection(ws_url, timeout=10)
            self._ws.settimeout(5)
        except Exception as e:
            logger.warning(f"ws progress bridge could not connect ({e}); falling back to poll-only")
            return

        last_pct = -1
        previews = ws_preview_enabled()
        try:
            while not self._stop.is_set() and time.time() < deadline:
                try:
                    raw = self._ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue  # no message this tick — loop to re-check stop/deadline
                except Exception as e:
                    logger.debug(f"ws progress bridge recv ended: {e}")
                    break
                if not raw:
                    continue
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    if previews:
                        self._maybe_emit_preview(process_id, bytes(raw))
                    continue
                if not isinstance(raw, str):
                    continue
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                mtype = msg.get("type")
                data = msg.get("data", {}) or {}

                if mtype == "progress":
                    value = data.get("value", 0)
                    total = data.get("max", 0) or 0
                    node = str(data.get("node", ""))
                    stage = node_labels.get(node, "working")
                    # Clamp to 1..99 — completion is owned by the /history poll,
                    # never by the bridge (avoids a premature "100%" race).
                    pct = int(value / total * 100) if total else 0
                    pct = max(1, min(99, pct))
                    if pct != last_pct:
                        last_pct = pct
                        msg = f"{stage} {value}/{total}"
                        free_mb = _free_vram_mb()
                        add = {"stage": stage, "node": node, **extra}
                        # Surface offload thrash risk on the UnifiedProgress rail
                        # (free near zero while denoising ≈ CPU weight thrash).
                        if free_mb is not None:
                            add["free_vram_mb"] = free_mb
                            if free_mb < 512 and "denois" in stage:
                                msg = (
                                    f"{msg} — low free VRAM (~{free_mb} MB); "
                                    "likely CPU offload (slow)"
                                )
                        emit_progress_event(
                            process_id=process_id,
                            progress=pct,
                            message=msg,
                            status="processing",
                            process_type="video_render",
                            additional_data=add,
                        )

                elif mtype == "executing":
                    # node == None means the prompt finished / queue went idle.
                    if data.get("node") is None:
                        logger.debug(f"ws progress bridge: ComfyUI idle for {process_id}, stopping")
                        break
        finally:
            try:
                if self._ws is not None:
                    self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _maybe_emit_preview(self, process_id: str, raw: bytes) -> None:
        parsed = parse_comfy_preview_frame(raw)
        if not parsed:
            return
        now = time.monotonic()
        if self._last_preview_emit and (now - self._last_preview_emit) < _PREVIEW_THROTTLE_S:
            return
        mime, image_bytes = parsed
        if emit_preview_event(process_id, mime, image_bytes):
            self._last_preview_emit = now
        else:
            # Redis/Socket both down: still stamp so we do not retry the same
            # burst every recv; the next frame after the window is enough.
            self._last_preview_emit = now
