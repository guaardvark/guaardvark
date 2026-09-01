
import logging
import json
import threading
import subprocess
import time
import os
import tempfile
import shutil
import urllib.request
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import uuid

import requests

logger = logging.getLogger(__name__)

try:
    from backend.config import CACHE_DIR, COMFYUI_URL, COMFYUI_OUTPUT_DIR, COMFYUI_DIR
    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"
    COMFYUI_DIR = None

# Wan loader filenames are DERIVED from the shared registry (issue #36) so the
# generator always loads exactly what the downloader wrote — no third hand-edited
# copy to drift. Falls back to {} if the registry can't be imported; the loaders
# already tolerate a missing entry, so import never hard-fails over this.
try:
    from backend.services.video_model_registry import wan_comfyui_map as _wan_comfyui_map
    from backend.services.video_model_registry import ltx_comfyui_map as _ltx_comfyui_map
    from backend.services.video_model_registry import hunyuan_comfyui_map as _hunyuan_comfyui_map
    from backend.services.video_model_registry import minimax_comfyui_map as _minimax_comfyui_map
    from backend.services.video_model_registry import cogvideox_comfyui_map as _cogvideox_comfyui_map
except Exception:  # pragma: no cover - defensive
    def _wan_comfyui_map():
        return {}

    def _ltx_comfyui_map():
        return {}

    def _hunyuan_comfyui_map():
        return {}

    def _minimax_comfyui_map():
        return {}

    def _cogvideox_comfyui_map():
        return {}

from backend.services.comfyui_video_workflows import ComfyUIVideoWorkflowMixin
from backend.services.video_model_registry import FAMILY_SPECS as _FAMILY_SPECS


def _looks_like_blank_video(video_path) -> Optional[str]:
    """Zero-placebo guard for the ComfyUI/Wan path (issue #36 Phase 3).

    Returns a human-readable REASON string if the rendered file is obviously not a
    real video — missing, an empty/stub mux, or fully black for ~its whole
    duration — else None (looks fine). ComfyUI can emit a black clip when a loader
    silently fails (e.g. a missing model quant), and the old code reported that as
    success. This mirrors the offline path's no-placebo guard.

    FAIL-OPEN: if ffmpeg is unavailable or we can't decode/measure the file, return
    None. Never block a real render just because the *checker* couldn't run — the
    point is to catch the obvious blank, not to gate on inspection success.
    """
    import re
    import subprocess
    try:
        from pathlib import Path as _P
        p = _P(video_path)
        if not p.exists():
            return "render produced no output file"
        size = p.stat().st_size
        if size < 10 * 1024:  # a real clip is far larger; <10KB is a stub/empty mux
            return f"render output is only {size} bytes — an empty/failed clip"
        if p.suffix.lower() not in (".mp4", ".webm", ".avi", ".mov"):
            return None  # not a container we can black-scan; size check already passed

        # blackdetect with pic_th=0.98 flags ~fully-black frames only. A real clip —
        # even a dark/cinematic one — is not 98%-black pixels for ~its whole runtime,
        # so this only trips on a genuinely blank render (very low false-positive).
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
             "-vf", "blackdetect=d=0.1:pic_th=0.98", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        stderr = proc.stderr or ""
        dur_m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
        if not dur_m:
            return None  # couldn't read duration -> fail open
        h, m, s = dur_m.groups()
        total = int(h) * 3600 + int(m) * 60 + float(s)
        black = sum(float(x) for x in re.findall(r"black_duration:(\d+(?:\.\d+)?)", stderr))
        if total > 0 and black >= 0.95 * total:
            return f"render is black for {black:.1f}s of {total:.1f}s — a blank/failed clip"
        return None
    except Exception as e:  # noqa: BLE001 — fail open on a broken checker
        logger.debug(f"blank-video check skipped ({e})")
        return None


@dataclass
class VideoGenerationRequest:
    prompt: str = ""
    negative_prompt: str = ""
    model: str = "cogvideox-5b"
    duration_frames: int = 25
    fps: int = 24  # 24fps default — Wan 5B is 24fps-native (was 7 → choppy slow-motion)
    width: int = 512
    height: int = 512
    motion_strength: float = 1.0
    num_inference_steps: int = 25
    guidance_scale: float = 7.5
    seed: Optional[int] = None
    generate_frames_only: bool = False
    frames_per_batch: int = 1
    combine_frames: bool = False
    output_dir: Optional[Path] = None
    metadata: Dict[str, str] = field(default_factory=dict)
    interpolation_multiplier: int = 2  # 1 = no interpolation, 2 = double fps, 4 = quad fps
    prompt_style: str = "cinematic"   # Enhancement style: cinematic, realistic, artistic, anime, none
    enhance_prompt: bool = True       # Whether to run prompt through the enhancer
    fidelity_mode: bool = False       # Light enhancement only (Exact text / preserve fidelity mode)
    wan_sampler_profile: Optional[str] = None  # Wan 5B: "adaptive" | "official" (see WAN5B_SAMPLER_PROFILES)
    freeu: bool = False
    face_restore: bool = False
    lora_name: Optional[str] = None
    lora_strength: float = 1.0
    # Capability-contract inputs (backend/services/video_model_registry.py).
    # A model that does not declare the capability rejects the field with a
    # plain message instead of ignoring it.
    speed_profile: Optional[str] = None       # id from the model's speed_profiles
    style_embedding: Optional[str] = None     # id from the model's style_embeddings
    first_frame_path: Optional[str] = None    # alias of metadata["image_path"]
    last_frame_path: Optional[str] = None     # l2v / flf2v on models with those modes
    # Anchors for models with audio_in: [{"kind": "audio"|"image", "path": str,
    # "frame_idx": int, "seek_s": float, "duration_s": float}]
    guides: List[Dict] = field(default_factory=list)
    # Reference inputs for ref2v models (paths on this machine).
    ref_images: List[str] = field(default_factory=list)
    ref_videos: List[Dict] = field(default_factory=list)   # {"path": str, "audio_path": str|None}
    ref_audios: List[str] = field(default_factory=list)
    language: str = "English"                 # dialogue language for the prompt compiler
    h3_intent: Optional[Dict] = None          # structured intent, compiled by h3_prompt_compiler


@dataclass
class VideoGenerationResult:
    success: bool
    prompt_used: str = ""
    video_path: Optional[str] = None
    frame_paths: List[str] = field(default_factory=list)
    thumbnail_path: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)
    # True when the clip carries a soundtrack the model generated (H3 today;
    # LTX once its audio latent is decoded). Mirrored as metadata["has_audio"].
    has_audio: bool = False


class ComfyUIVideoGenerator(ComfyUIVideoWorkflowMixin):

    # Prompt ids this process has queued, newest last. Class-level because
    # generate and cancel do not always hold the same generator: the batch
    # runner uses the module singleton, the router keeps its own instance and
    # replaces it on every get_active_generator(). Bounded, and a stale id
    # costs nothing — ComfyUI matches it against what is actually running.
    _queued_prompts: deque = deque(maxlen=16)
    _queued_prompts_lock = threading.Lock()

    @classmethod
    def _track_prompt(cls, prompt_id: str) -> None:
        with cls._queued_prompts_lock:
            cls._queued_prompts.append(prompt_id)

    @classmethod
    def _forget_prompt(cls, prompt_id: str) -> None:
        with cls._queued_prompts_lock:
            try:
                cls._queued_prompts.remove(prompt_id)
            except ValueError:
                pass

    @classmethod
    def _known_prompts(cls) -> List[str]:
        with cls._queued_prompts_lock:
            return list(cls._queued_prompts)

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent

        self.comfy_url = COMFYUI_URL if config_available else os.environ.get("GUAARDVARK_COMFYUI_URL", "http://127.0.0.1:8188")

        self.templates_dir = project_root / "data" / "templates"
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        self.cache_dir = Path(CACHE_DIR) / "generated_videos"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Default output dir for standalone (non-batch) video generation
        try:
            from backend.config import UPLOAD_DIR as _upload_dir
            self.default_output_dir = Path(_upload_dir) / "Videos"
        except ImportError:
            self.default_output_dir = self.cache_dir
        self.default_output_dir.mkdir(parents=True, exist_ok=True)

        self.comfy_output_dir = Path(COMFYUI_OUTPUT_DIR if config_available else os.environ.get('COMFYUI_OUTPUT_DIR', os.path.join(os.environ.get('GUAARDVARK_ROOT', '.'), 'data', 'outputs', 'video')))

        self.service_available = self._check_comfyui_connection()
        self._object_info_cache: Optional[dict] = None

        if self.service_available:
            logger.info(f"ComfyUI video generator connected to {self.comfy_url}")
        else:
            logger.warning(f"ComfyUI not available at {self.comfy_url}. Video generation will fail unless ComfyUI is started.")

    def _get_object_info(self) -> dict:
        """Fetch ComfyUI /object_info once and cache (node class availability probe)."""
        if self._object_info_cache is not None:
            return self._object_info_cache
        try:
            response = requests.get(f"{self.comfy_url}/object_info", timeout=5)
            response.raise_for_status()
            self._object_info_cache = response.json()
        except Exception as e:
            logger.debug(f"Could not fetch ComfyUI object_info: {e}")
            self._object_info_cache = {}
        return self._object_info_cache

    def comfy_node_available(self, class_type: str) -> bool:
        if not self.service_available and not self._check_comfyui_connection():
            return False
        return class_type in self._get_object_info()

    def _check_comfyui_connection(self) -> bool:
        try:
            response = requests.get(self.comfy_url, timeout=2)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def _upload_input_file(self, path: str, kind: str = "image") -> Optional[str]:
        """Copy a local file into ComfyUI's input/ and return the name its
        loader nodes use. ComfyUI has only /upload/image, but it stores any
        file (server.py compares bytes, it does not decode), so audio guides
        travel the same road and LoadAudio finds them by name."""
        try:
            with open(path, 'rb') as f:
                files = {'image': f}
                data = {'type': 'input', 'overwrite': 'true'}
                response = requests.post(
                    f"{self.comfy_url}/upload/image",
                    files=files,
                    data=data,
                    timeout=60,
                )
                response.raise_for_status()

            result = response.json()
            uploaded_name = result.get("name")
            logger.info(f"Uploaded {kind} to ComfyUI as: {uploaded_name}")
            return uploaded_name

        except Exception as e:
            logger.error(f"Failed to upload {kind} to ComfyUI: {e}")
            return None

    def _upload_image_to_comfyui(self, image_path: str) -> Optional[str]:
        return self._upload_input_file(image_path, "image")

    def _prepare_guide_audio(
        self, path: str, seek_s: float = 0.0, duration_s: float = 0.0, max_s: Optional[float] = None
    ) -> str:
        """Return a WAV holding just the slice a guide needs. The encoder should
        never see a three-minute song when the clip is five seconds long, so the
        slice is cut with ffmpeg into the cache before upload. Returns the
        original path when there is nothing to cut."""
        limit = float(duration_s or 0)
        if max_s:
            limit = min(limit, float(max_s)) if limit else float(max_s)
        if not seek_s and not limit:
            return path
        base = Path(getattr(self, "cache_dir", None) or Path(tempfile.gettempdir())) / "h3_guides"
        base.mkdir(parents=True, exist_ok=True)
        out = base / f"{uuid.uuid4().hex}.wav"
        cmd = ["ffmpeg", "-y", "-i", str(path), "-ss", str(float(seek_s or 0))]
        if limit:
            cmd += ["-t", str(limit)]
        cmd += ["-vn", "-acodec", "pcm_s16le", str(out)]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            tail = (proc.stderr or b"").decode(errors="replace").strip().splitlines()[-1:] or [""]
            raise RuntimeError(f"ffmpeg could not cut the guide audio: {tail[0]}")
        return str(out)


    # ── CogVideoX model mapping ──────────────────────────────────────────────

    # cogvideox-5b is a diffusers snapshot the registry installs under
    # models/CogVideo/CogVideoX-5b; the wrapper's DownloadAndLoadCogVideoModel
    # resolves that hub id to the same directory and only reaches for Hugging
    # Face when the directory is absent (and the launch environment now forbids
    # that: HF_HUB_OFFLINE in plugins/comfyui/scripts/start.sh).
    COGVIDEOX_MODELS = {
        "cogvideox-5b": "THUDM/CogVideoX-5b",
        # I2V is deliberately NOT a hub id: it loads the registry's single file
        # through CogVideoXModelLoader. See COGVIDEOX_I2V_FILES.
        "cogvideox-5b-i2v": None,
    }
    # Single-file CogVideoX loader map — DERIVED from the registry like Wan/LTX.
    COGVIDEOX_I2V_FILES = _cogvideox_comfyui_map()
    # Both wrapper loaders emit COGVIDEOMODEL; the FreeU/LoRA hooks below key on them.
    _COG_MODEL_LOADER_NODES = ("DownloadAndLoadCogVideoModel", "CogVideoXModelLoader")

    # Conservative best-effort floor for the TOTAL VRAM a model needs to run at
    # all (not headroom-for-comfort). Used by the preflight in generate_video to
    # turn a silent OOM into an honest "this model needs ~N GB" message on the
    # install base. Keyed by the model id; family fallbacks below cover aliases.
    #
    # Note on real hardware (2026-06): "16 GB" consumer cards (e.g. 4070 Ti SUPER)
    # commonly report 15900-16400 MB total via pynvml/nvidia-smi/ComfyUI because
    # of driver/display reservation. The GGUF Q5 WAN 14B paths + music-video's
    # 832x480 preview res were explicitly built for this class of card.
    # Preflight therefore uses a small tolerance (see _vram_preflight) so it only
    # hard-blocks on truly under-spec hardware while still giving clear guidance.
    MODEL_MIN_VRAM_GB = {
        "cogvideox-2b": 8,
        "cogvideox-5b": 16,
        "cogvideox-5b-i2v": 16,
        "wan22-14b": 16,
        "wan22-14b-i2v": 16,
        "wan22-5b": 11,
        "ltx23-distilled-fp8": 16,
        "ltx25-distilled-int8": 16,
        "hunyuan-t2v": 16,
        "hunyuan-i2v": 16,
        "minimax-h3-int8": 16,
    }
    # Family floors when an exact id isn't in the table (aliases like "wan22").
    _FAMILY_MIN_VRAM_GB = {fam: spec["min_vram_gb"] for fam, spec in _FAMILY_SPECS.items()}

    # ── Wan 2.2 model mapping ────────────────────────────────────────────────
    # DERIVED from the shared registry (backend/services/video_model_registry.py)
    # so these loader paths can never drift from what the downloader writes
    # (issue #36). To change a Wan filename, edit the registry's `files` — not here.
    WAN22_MODELS = _wan_comfyui_map()
    # LTX-2.3 loader map — same SSOT pattern as Wan.
    LTX_MODELS = _ltx_comfyui_map()
    # HunyuanVideo loader map — same SSOT pattern.
    HUNYUAN_MODELS = _hunyuan_comfyui_map()
    # MiniMax H3 loader map — same SSOT pattern.
    MINIMAX_MODELS = _minimax_comfyui_map()

    # CogVideoX/Wan are 8x VAE × 2x patch → /16. SVD is U-Net only → /8.
    # LTX-2.3 spatial downscale is 32 (see EmptyLTXVLatentVideo).
    # Mirror of MODEL_OPTIONS[*].dimensionAlignment in VideoGeneratorPage.jsx —
    # the frontend should already snap, this is the defense-in-depth seam for
    # API/MCP/agent callers that go straight to the workflow builders.
    _DIMENSION_ALIGNMENT_BY_FAMILY = {
        "svd": 8,
        **{fam: spec["dimension_alignment"] for fam, spec in _FAMILY_SPECS.items()},
    }

    @classmethod
    def _ensure_wan_models(cls) -> dict:
        """The Wan loader map is derived from the registry at IMPORT time (`WAN22_MODELS`
        above); if that derivation froze to {} (a transient registry/circular-import hiccup
        at module load) Wan generation would be silently dead for the whole process. Re-resolve
        lazily here and cache the first non-empty result, so a one-time import-order problem
        can't permanently break Wan video. No-op once the map is populated."""
        if not cls.WAN22_MODELS:
            try:
                from backend.services.video_model_registry import wan_comfyui_map
                fresh = wan_comfyui_map() or {}
                if fresh:
                    cls.WAN22_MODELS = fresh
            except Exception:  # pragma: no cover - defensive
                pass
        return cls.WAN22_MODELS

    @classmethod
    def _ensure_ltx_models(cls) -> dict:
        """Same lazy re-resolve as `_ensure_wan_models` for the LTX-2.3 map."""
        if not cls.LTX_MODELS:
            try:
                from backend.services.video_model_registry import ltx_comfyui_map
                fresh = ltx_comfyui_map() or {}
                if fresh:
                    cls.LTX_MODELS = fresh
            except Exception:  # pragma: no cover - defensive
                pass
        return cls.LTX_MODELS

    @classmethod
    def _ensure_hunyuan_models(cls) -> dict:
        """Same lazy re-resolve as `_ensure_wan_models` for the HunyuanVideo map."""
        if not cls.HUNYUAN_MODELS:
            try:
                from backend.services.video_model_registry import hunyuan_comfyui_map
                fresh = hunyuan_comfyui_map() or {}
                if fresh:
                    cls.HUNYUAN_MODELS = fresh
            except Exception:  # pragma: no cover - defensive
                pass
        return cls.HUNYUAN_MODELS

    @classmethod
    def _ensure_minimax_models(cls) -> dict:
        """Same lazy re-resolve as `_ensure_wan_models` for the MiniMax H3 map."""
        if not cls.MINIMAX_MODELS:
            try:
                from backend.services.video_model_registry import minimax_comfyui_map
                fresh = minimax_comfyui_map() or {}
                if fresh:
                    cls.MINIMAX_MODELS = fresh
            except Exception:  # pragma: no cover - defensive
                pass
        return cls.MINIMAX_MODELS

    @classmethod
    def _model_family(cls, model: str) -> str:
        cls._ensure_wan_models()  # unfreeze the map if it froze empty at import
        cls._ensure_ltx_models()
        cls._ensure_hunyuan_models()
        cls._ensure_minimax_models()
        if model in cls.MINIMAX_MODELS or str(model).startswith("minimax"):
            return "minimax"
        if model in cls.HUNYUAN_MODELS or str(model).startswith("hunyuan"):
            return "hunyuan"
        if model in cls.LTX_MODELS or str(model).startswith("ltx"):
            return "ltx"
        if model in cls.WAN22_MODELS or model in ("wan22", "wan2.2"):
            return "wan"
        if model in cls.COGVIDEOX_MODELS:
            return "cogvideox"
        return "cogvideox"  # SVD retired; unknown models default to the cogvideox family

    @staticmethod
    def _hunyuan_frame_count(num_frames: int) -> int:
        """HunyuanVideo latent length is 4n+1 frames (1 = still image); snap to nearest."""
        n = max(1, int(num_frames or 73))
        return int((n - 1) / 4 + 0.5) * 4 + 1

    @staticmethod
    def _minimax_frame_count(num_frames: int) -> int:
        """MiniMax H3 samples on a 17k+5 frame grid at 24 fps (5, 22, 39, …, 124 ≈ 5s).
        Snap UP like the official template's Math Expression node does, so a
        requested duration is never silently shortened."""
        n = max(5, int(num_frames or 124))
        return n + (5 - n % 17) % 17

    @staticmethod
    def _ltx_frame_count(num_frames: int) -> int:
        """LTX-2.3 latent length must be 8n+1 (65, 97, 121, 161, …)."""
        n = max(9, int(num_frames or 65))
        snapped = ((n - 1) // 8) * 8 + 1
        if snapped < 9:
            snapped = 9
        return snapped

    # Timeout guard per family: ~1.0 MPx (1280×736) is proven on 16GB cards;
    # 3.7 MPx (1920×1920) never finished on either Wan. Aspect is preserved.
    _MAX_PIXEL_AREA_BY_FAMILY = {
        fam: spec["max_pixel_area"] for fam, spec in _FAMILY_SPECS.items() if spec.get("max_pixel_area")
    }

    # Ratio presets the UI offers, as width/height. Kept here so a clamp can name
    # the ratio it snapped to rather than emitting an arbitrary decimal.
    _ASPECT_RATIOS = {
        "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:3": 4 / 3, "3:2": 3 / 2,
        "21:9": 21 / 9, "3:4": 3 / 4,
    }

    @classmethod
    def _supported_aspect_ratios(cls, model: str) -> list:
        """Ratio keys a model declares in the registry, or [] when unconstrained."""
        try:
            from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
            entry = VIDEO_MODEL_REGISTRY.get(model) or {}
        except Exception:  # noqa: BLE001 — never block a render on a registry read
            return []
        return [r for r in (entry.get("aspect_ratios") or []) if r in cls._ASPECT_RATIOS]

    @classmethod
    def _clamp_aspect_ratio(cls, width: int, height: int, model: str) -> tuple[int, int]:
        """Reshape to the nearest aspect the model declares, keeping pixel area.

        The UI no longer offers an unsupported ratio, but the API still accepts
        one and old batch retry_data replays whatever it stored. A square frame
        on a model trained at 1280x704 comes back warped, so reshape instead of
        rendering it — area is preserved, and the clamp below still applies.
        """
        supported = cls._supported_aspect_ratios(model)
        if not supported or width <= 0 or height <= 0:
            return width, height

        # 6% covers alignment drift without reaching a different preset: a 32px
        # snap moves 1280x704 (native, 1.82) 2.3% off exact 16:9, while the
        # nearest wrong preset (3:2) sits 15.6% away.
        requested = width / height
        if any(abs(requested / cls._ASPECT_RATIOS[k] - 1.0) <= 0.06 for k in supported):
            return width, height

        import math

        # Snap within the requested orientation first. Log-distance alone stops
        # preserving orientation once square is a candidate: 4:3 (1.333) sits
        # exactly as far from 16:9 as from 1:1, so a landscape request could come
        # back square. Orientation is the part a caller notices, so it wins; the
        # nearest ratio is chosen within it.
        def _orient(r: float) -> int:
            return (r > 1.0) - (r < 1.0)

        candidates = [k for k in supported
                      if _orient(cls._ASPECT_RATIOS[k]) == _orient(requested)] or supported
        key = min(candidates, key=lambda k: abs(math.log(requested / cls._ASPECT_RATIOS[k])))
        target = cls._ASPECT_RATIOS[key]
        area = width * height
        new_w = int(round(math.sqrt(area * target)))
        new_h = int(round(new_w / target)) or 1
        logger.warning(
            "Reshaped %s video dims %dx%d (%.2f:1) → %dx%d (%s) — the model declares "
            "%s; off-native frames warp rather than crop",
            model, width, height, requested, new_w, new_h, key, "/".join(supported),
        )
        return new_w, new_h

    @classmethod
    def _clamp_pixel_area(cls, width: int, height: int, model: str) -> tuple[int, int]:
        """Scale (width, height) down to the family's pixel-area budget,
        preserving aspect ratio. No-op when unbudgeted or already within it."""
        cap = None
        try:
            from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
            cap = (VIDEO_MODEL_REGISTRY.get(model) or {}).get("max_pixel_area")
        except Exception:  # noqa: BLE001 — never block a render on a registry read
            cap = None
        cap = cap or cls._MAX_PIXEL_AREA_BY_FAMILY.get(cls._model_family(model))
        area = int(width) * int(height)
        if not cap or area <= cap:
            return width, height
        scale = (cap / area) ** 0.5
        new_w, new_h = int(width * scale), int(height * scale)
        logger.warning(
            "Clamped %s video dims %dx%d (%.1f MPx) → %dx%d to stay within the "
            "%.1f MPx budget — larger frames time out on this hardware",
            model, width, height, area / 1e6, new_w, new_h, cap / 1e6,
        )
        return new_w, new_h

    @classmethod
    def _align_dimensions(cls, width: int, height: int, model: str) -> tuple[int, int]:
        """Snap (width, height) to the model family's required alignment.

        Logs a WARNING when the input wasn't already aligned — that's our
        breadcrumb if a caller bypasses the frontend's snap.
        """
        align = cls._DIMENSION_ALIGNMENT_BY_FAMILY.get(cls._model_family(model), 16)
        new_w = max(align, round(width / align) * align)
        new_h = max(align, round(height / align) * align)
        if (new_w, new_h) != (width, height):
            logger.warning(
                "Aligned video dims for %s: %dx%d → %dx%d (must be multiple of %d)",
                model, width, height, new_w, new_h, align,
            )
        return new_w, new_h

    @classmethod
    def _min_vram_gb_for(cls, model: str) -> int:
        """Conservative TOTAL-VRAM floor (GB) for `model`. Exact id wins; falls
        back to the model family; 0 means 'no floor known' (don't block)."""
        try:
            from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
            declared = (VIDEO_MODEL_REGISTRY.get(model) or {}).get("min_vram_gb")
            if declared:
                return int(declared)
        except Exception:  # noqa: BLE001 — fall back to the tables below
            pass
        if model in cls.MODEL_MIN_VRAM_GB:
            return cls.MODEL_MIN_VRAM_GB[model]
        return cls._FAMILY_MIN_VRAM_GB.get(cls._model_family(model), 0)

    # Wan UMT5 TE is ~6.4 GB fully resident. On 16–20 GB cards that leaves the
    # ~10 GB GGUF UNet with ~300 MB usable → CPU offload thrash (~150 s/step).
    # CLIPLoader supports device="cpu" (same weights/math; encode is slower once,
    # sample runs at full GPU speed). Above this total we leave TE on GPU.
    # Override: GUAARDVARK_WAN_CLIP_DEVICE=cpu|default
    WAN_CLIP_CPU_TOTAL_VRAM_MB = 20 * 1024

    @classmethod
    def _wan_clip_device(cls, total_vram_mb: Optional[int] = None) -> str:
        """Where Wan CLIP/UMT5 should load: ``cpu`` on consumer VRAM, else default.

        Quality-preserving residency control (mirrors CogVideoX force_offload):
        TE on CPU frees the full card for UNet+VAE instead of stacking TE+UNet
        until Comfy partial-loads the diffusion model. Same fp/quant weights —
        no model downshift.
        """
        override = (os.environ.get("GUAARDVARK_WAN_CLIP_DEVICE") or "").strip().lower()
        if override in ("cpu", "default"):
            return override

        total_mb = total_vram_mb
        if total_mb is None:
            try:
                from backend.services.gpu_resource_coordinator import get_available_vram
                info = get_available_vram()
                if info.get("success"):
                    total_mb = int(info.get("total_mb") or 0) or None
            except Exception:  # noqa: BLE001
                total_mb = None

        # Unknown probe → prefer CPU (safe on 16 GB; harmless quality-wise on larger).
        if total_mb is None or total_mb <= 0:
            return "cpu"
        if total_mb <= cls.WAN_CLIP_CPU_TOTAL_VRAM_MB:
            return "cpu"
        return "default"

    # Wan sampling profiles, used by BOTH the 5B and the 14B MoE workflow.
    # "adaptive" is the in-house pairing (euler + resolution-scaled shift);
    # "official" mirrors ComfyUI's bundled template (uni_pc + fixed shift 8 at
    # every size). Per-job via the request field, default via
    # GUAARDVARK_WAN5B_SAMPLER. The name keeps its 5B spelling because it is the
    # public env var and the frontend key; it is not 5B-only.
    WAN5B_SAMPLER_PROFILES = {
        "adaptive": {"sampler": "euler", "shift": None},
        "official": {"sampler": "uni_pc", "shift": 8.0},
    }
    # "official" is the ComfyUI template's own config and is what actually renders
    # cleanly here — verified at both 1280x736 and 736x416. "adaptive" scales shift
    # linearly with pixel area, which floors at 3.0 by 736x416 against the 8.0 the
    # model is tuned for, and produced warping and colour bleed at every size.
    WAN5B_DEFAULT_SAMPLER_PROFILE = "official"

    @classmethod
    def _wan5b_sampler_profile(cls, requested: Optional[str] = None) -> str:
        """Resolve the Wan 5B sampling profile: request → env → the default."""
        for candidate in (requested, os.environ.get("GUAARDVARK_WAN5B_SAMPLER")):
            key = (candidate or "").strip().lower()
            if key in cls.WAN5B_SAMPLER_PROFILES:
                return key
        return cls.WAN5B_DEFAULT_SAMPLER_PROFILE

    @staticmethod
    def _wan_dynamic_shift(width: int, height: int) -> float:
        """ModelSamplingSD3 shift for Wan, scaled to resolution.

        SD3/flow-matching shift should scale with resolution/sequence-length.
        Base shift 8.0 is tuned for 1280x704; at lower resolutions (e.g. 736x416)
        8.0 is too strong, leading to blurry output and poor motion.
        """
        base_area = 1280 * 704
        return round(max(3.0, min(12.0, 8.0 * ((width * height) / base_area))), 1)

    def _resolve_wan_profile(self, request: VideoGenerationRequest, model_key: str) -> tuple:
        """The Wan speed profile a request names, resolved against the registry:
        (profile dict, None) with its LoRA filenames, (None, None) when none was
        asked for, or (None, message) when the model does not declare it or its
        LoRAs are not installed."""
        if not request.speed_profile or request.speed_profile == "standard":
            return None, None
        from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, speed_profile_for
        entry = VIDEO_MODEL_REGISTRY.get(model_key) or {}
        name = entry.get("name") or model_key
        profile = speed_profile_for(model_key, request.speed_profile)
        if profile is None:
            declared = ", ".join(entry.get("speed_profiles") or {}) or "none"
            return None, f"{name} declares no speed profile '{request.speed_profile}' (declared: {declared})."
        if (profile.get("loras") or profile.get("lora")) and not profile.get("lora_installed"):
            wanted = [VIDEO_MODEL_REGISTRY.get(pid, {}).get("name") or pid
                      for pid in ([profile["lora"]] if profile.get("lora") else profile["loras"].values())]
            return None, (
                f"Speed profile '{profile.get('label') or profile['id']}' needs "
                f"{' and '.join(wanted)}. Open Manage Video Models and install them."
            )
        return profile, None

    def _resolve_minimax_common(self, request: VideoGenerationRequest, model_key: str, caps: dict, entry_name: str) -> tuple:
        """Speed profile, LoRA, step count and prompt for either H3 build.
        Returns ((profile, lora_file, lora_strength, steps, prompt), None) or
        (None, message)."""
        from backend.services.video_model_registry import (
            VIDEO_MODEL_REGISTRY, speed_profile_for, style_embedding_token,
        )
        profile = None
        if request.speed_profile:
            profile = speed_profile_for(model_key, request.speed_profile)
            if profile is None:
                return None, (
                    f"Unknown speed profile '{request.speed_profile}' for {entry_name}; "
                    f"declared: {', '.join(caps.get('speed_profiles') or {}) or 'none'}."
                )
        lora_file = None
        lora_strength = 1.0
        if profile and profile.get("lora"):
            if not profile.get("lora_installed"):
                lora_name = (VIDEO_MODEL_REGISTRY.get(profile["lora"]) or {}).get("name") or profile["lora"]
                return None, (
                    f"Speed profile '{profile.get('label') or profile['id']}' needs "
                    f"'{lora_name}'. Open Manage Video Models and install it."
                )
            lora_file = profile.get("lora_file")
            lora_strength = float(profile.get("strength") or 1.0)
            min_edge = profile.get("min_short_edge")
            if min_edge and min(int(request.width), int(request.height)) < int(min_edge):
                return None, (
                    f"Speed profile '{profile.get('label') or profile['id']}' is tuned for a "
                    f"{min_edge}px short edge; requested {request.width}x{request.height}. "
                    f"Pick the 768p canvas or another profile."
                )

        floor = int((profile or {}).get("min_steps") or caps.get("min_steps") or 20)
        default_steps = int((profile or {}).get("steps") or caps.get("default_steps") or 20)
        requested = int(request.num_inference_steps or 0)
        explicit = str((request.metadata or {}).get("steps_explicit", "")).lower() in ("1", "true")
        if explicit and requested > 0:
            steps = requested
            if steps < floor:
                logger.info(
                    "MiniMax H3: keeping the %d steps typed by the person, below the %d-step floor",
                    steps, floor,
                )
        elif profile:
            steps = default_steps
        else:
            steps = max(requested or default_steps, floor)
            if requested and requested < floor:
                logger.info("MiniMax H3: raised %d preset steps to the %d-step floor", requested, floor)

        if not caps.get("cfg", True):
            if request.negative_prompt or (request.guidance_scale is not None and request.guidance_scale > 1.0):
                logger.info(
                    "MiniMax H3 runs without CFG (BasicGuider); negative prompt and "
                    "guidance_scale=%s are not used", request.guidance_scale,
                )

        prompt = request.prompt or ""
        if request.style_embedding:
            token = style_embedding_token(model_key, request.style_embedding)
            if not token:
                return None, (
                    f"Unknown style embedding '{request.style_embedding}' for {entry_name}; "
                    f"declared: {', '.join(e['id'] for e in caps.get('style_embeddings') or [])}."
                )
            prompt = f"{prompt} {token}".strip()

        return (profile, lora_file, lora_strength, steps, prompt), None

    def _build_minimax_ref_request(
        self, request: VideoGenerationRequest, model_key: str, seed: int, interpolation: int,
        caps: dict, entry_name: str,
    ) -> tuple:
        """The ref2va graph: reference images, clips (with or without their
        soundtrack, or a separate one) and standalone audio, counted against
        the registry's ref_limits, uploaded into input/, then wired in the
        order the prompt's <Picture N> / <Video N> / <Audio N> tags follow."""
        limits = caps.get("ref_limits") or {}
        images = list(request.ref_images or [])
        videos = list(request.ref_videos or [])
        audios = list(request.ref_audios or [])
        if request.first_frame_path or request.last_frame_path or request.guides:
            return None, (
                f"{entry_name} takes references, not first/last frames or guides; "
                f"pick MiniMax H3 (Int8) for those."
            )
        if not images and not videos:
            return None, (
                f"{entry_name} needs at least one reference image or clip; audio "
                f"cannot be the only reference."
            )
        for kind, items, key in (("image", images, "images"), ("clip", videos, "videos"), ("audio", audios, "audios")):
            cap = limits.get(key)
            if cap is not None and len(items) > cap:
                return None, f"{entry_name} takes at most {cap} reference {kind}s; {len(items)} given."
        total = len(images) + len(videos) + len(audios) + sum(
            1 for v in videos if isinstance((v or {}).get("audio"), str) and v.get("audio")
        )
        if limits.get("files") and total > limits["files"]:
            return None, f"{entry_name} takes at most {limits['files']} reference files; {total} given."

        common, err = self._resolve_minimax_common(request, model_key, caps, entry_name)
        if err:
            return None, err
        profile, lora_file, lora_strength, steps, prompt = common

        def _upload(path, kind):
            if not path or not Path(path).exists():
                return None, f"Reference {kind} not found: {path}"
            name = self._upload_input_file(path, kind)
            return (name, None) if name else (None, f"Failed to upload the reference {kind} to ComfyUI")

        image_names = []
        for path in images:
            name, err = _upload(path, "image")
            if err:
                return None, err
            image_names.append(name)
        video_specs = []
        for video in videos:
            video = video if isinstance(video, dict) else {"path": video}
            name, err = _upload(video.get("path"), "clip")
            if err:
                return None, err
            audio = video.get("audio_path")
            if audio:
                audio, err = _upload(audio, "audio")
                if err:
                    return None, err
            else:
                audio = bool(video.get("include_audio", True))
            video_specs.append({"filename": name, "audio": audio})
        audio_names = []
        for path in audios:
            name, err = _upload(path, "audio")
            if err:
                return None, err
            audio_names.append(name)

        try:
            workflow = self._create_minimax_ref_workflow(
                prompt=prompt,
                model_key=model_key,
                num_frames=request.duration_frames,
                num_inference_steps=steps,
                width=request.width,
                height=request.height,
                seed=seed,
                fps=float(request.fps or 24),
                interpolation_multiplier=interpolation,
                ref_images=image_names,
                ref_videos=video_specs,
                ref_audios=audio_names,
                ref_image_size=str((request.metadata or {}).get("ref_image_size") or "match"),
                lora_name=lora_file,
                lora_strength=lora_strength,
            )
        except ValueError as e:
            return None, str(e)
        logger.info(
            "Using MiniMax H3 reference-to-video (%s, %d steps%s; %d image(s), %d clip(s), %d audio) via ComfyUI",
            model_key, steps, f", profile {profile['id']}" if profile else "",
            len(image_names), len(video_specs), len(audio_names),
        )
        return workflow, None

    def _build_minimax_request(
        self, request: VideoGenerationRequest, model_key: str,
        image_path: Optional[str], seed: int, interpolation: int,
    ) -> tuple:
        """Turn a request into the H3 fl2va graph, or (None, message).

        Everything the request asks for is checked against the capability
        record the registry declares, so a wrong ask fails with one sentence
        naming the fix rather than as a ComfyUI validation dump:

        - speed profile → LoRA file (must be installed) and step count; a preset
          value below the profile's floor is raised to it, a value the person
          typed (metadata steps_explicit) is kept and logged;
        - style embedding → its token appended after enhancement;
        - first and last frame, image and audio guides → uploaded into input/;
        - reference inputs → refused on this build (they need the ref2va model);
        - negative prompt and guidance → logged once as ignored (no CFG).
        """
        from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, model_capabilities
        caps = model_capabilities(model_key)
        modes = caps.get("modes") or []
        entry_name = (VIDEO_MODEL_REGISTRY.get(model_key) or {}).get("name") or model_key

        if "ref2v" in modes and "t2v" not in modes:
            return self._build_minimax_ref_request(request, model_key, seed, interpolation, caps, entry_name)
        if request.ref_images or request.ref_videos or request.ref_audios:
            return None, (
                f"{entry_name} takes no reference images, clips or audio; those need "
                f"the MiniMax H3 Reference build."
            )

        common, err = self._resolve_minimax_common(request, model_key, caps, entry_name)
        if err:
            return None, err
        profile, lora_file, lora_strength, steps, prompt = common
        first_name = None
        first_path = request.first_frame_path or image_path
        if first_path:
            if not Path(first_path).exists():
                return None, f"First frame not found: {first_path}"
            first_name = self._upload_image_to_comfyui(first_path)
            if not first_name:
                return None, "Failed to upload the first frame to ComfyUI"
        last_name = None
        if request.last_frame_path:
            if "l2v" not in modes and "flf2v" not in modes:
                return None, f"{entry_name} takes no last frame."
            if not Path(request.last_frame_path).exists():
                return None, f"Last frame not found: {request.last_frame_path}"
            last_name = self._upload_image_to_comfyui(request.last_frame_path)
            if not last_name:
                return None, "Failed to upload the last frame to ComfyUI"

        frames = self._minimax_frame_count(request.duration_frames)
        fps = float(request.fps or 24)
        guide_specs = []
        for guide in request.guides or []:
            if not caps.get("audio_in"):
                return None, f"{entry_name} takes no guides."
            kind = (guide or {}).get("kind")
            path = (guide or {}).get("path")
            if kind not in ("audio", "image") or not path:
                return None, f"A guide needs kind audio|image and a path: {guide!r}"
            if not Path(path).exists():
                return None, f"Guide file not found: {path}"
            frame_idx = int((guide or {}).get("frame_idx") or 0)
            if kind == "audio":
                try:
                    remaining = (frames - frame_idx if frame_idx >= 0 else -frame_idx) / fps
                    path = self._prepare_guide_audio(
                        path, float(guide.get("seek_s") or 0), float(guide.get("duration_s") or 0),
                        max_s=max(0.1, remaining),
                    )
                except Exception as e:  # noqa: BLE001 — the message is the diagnosis
                    return None, str(e)
            name = self._upload_input_file(path, kind)
            if not name:
                return None, f"Failed to upload the {kind} guide to ComfyUI"
            guide_specs.append({"kind": kind, "filename": name, "frame_idx": frame_idx})

        try:
            workflow = self._create_minimax_workflow(
                prompt=prompt,
                model_key=model_key,
                num_frames=request.duration_frames,
                num_inference_steps=steps,
                width=request.width,
                height=request.height,
                seed=seed,
                fps=fps,
                interpolation_multiplier=interpolation,
                image_filename=first_name,
                last_frame_filename=last_name,
                lora_name=lora_file,
                lora_strength=lora_strength,
                guides=guide_specs,
            )
        except ValueError as e:
            return None, str(e)

        mode = (
            "first+last-frame" if (first_name and last_name) else
            "last-frame" if last_name else
            "first-frame I2V" if first_name else "T2V"
        )
        logger.info(
            "Using MiniMax H3 %s (%s, %d steps%s%s%s) via ComfyUI",
            mode, model_key, steps,
            f", profile {profile['id']}" if profile else "",
            f", {len(guide_specs)} guide(s)" if guide_specs else "",
            f", style {request.style_embedding}" if request.style_embedding else "",
        )
        return workflow, None

    def _vram_preflight(self, model: str) -> Optional[str]:
        """Read-only VRAM gate run BEFORE queuing a ComfyUI job, so an
        under-spec card gets an honest message instead of a silent OOM mid-render.

        Returns an error string to surface (caller turns it into a failed
        VideoGenerationResult), or None to proceed. Fail-OPEN: if the probe
        itself errors we return None — never block a working render because the
        probe threw. Reuses the coordinator's pynvml/nvidia-smi probe (READ
        ONLY, allocates nothing).
        """
        try:
            from backend.services.gpu_resource_coordinator import get_available_vram
            info = get_available_vram()
        except Exception as e:  # noqa: BLE001 — fail open on a broken probe
            logger.warning("VRAM preflight probe errored (%s); proceeding without gate", e)
            return None

        # Probe didn't succeed. Distinguish "no NVIDIA hardware at all" (an
        # honest hard error — video gen needs a GPU) from a transient/unknown
        # probe failure (fail open — don't block a card we just can't read).
        if not info.get("success"):
            reason = info.get("reason") or info.get("error") or ""
            if reason == "no_gpu_hardware" or "no NVIDIA" in str(reason):
                return "GPU required for video generation: no NVIDIA GPU detected on this host."
            logger.warning("VRAM preflight: probe unavailable (%s); proceeding", reason)
            return None

        total_mb = info.get("total_mb") or 0
        free_mb = info.get("available_mb") or info.get("free_mb") or 0
        if total_mb <= 0:
            return None  # unknown total → fail open
        total_gb = total_mb / 1024.0
        need = self._min_vram_gb_for(model)
        # Tolerance for real "16 GB" consumer cards (common 15.5-16.0 GB reported
        # total after driver/display reservation). The quantized GGUF paths and
        # music-video's 832x480 preview res target exactly this hardware class.
        # We still hard-block true under-spec cards (e.g. 12 GB or less) and any
        # probe failure is fail-open (existing behavior).
        # Use MB math for the tolerance check to avoid float edge cases.
        need_mb = need * 1024
        if need and total_mb + 512 < need_mb:  # ~0.5 GB grace
            return (
                f"{model} needs ~{need} GB VRAM; detected {total_gb:.2f} GB "
                f"({total_mb} MB total). "
                "Try a lighter model or preview resolution."
            )
        # Advisory only: free VRAM after callers should already have run
        # gpu_session reclaim. Low free means another consumer is still resident;
        # we do not hard-fail (staged TE-on-CPU is the thrash fix, not refuse).
        if free_mb and free_mb < 2048:
            logger.warning(
                "VRAM preflight: only ~%s MB free of %s MB total before queue "
                "(clip_device=%s). Ensure gpu_session reclaimed Ollama/Comfy.",
                free_mb, total_mb, self._wan_clip_device(total_mb),
            )
        return None















    def interrupt(self, prompt_id: Optional[str] = None) -> bool:
        """Stop the named prompt, or every prompt this process queued.

        ComfyUI is a shared sidecar: a bare ``/interrupt`` stops whatever is
        sampling no matter who queued it, and ``/queue {"clear": true}`` drops
        other clients' pending work with it. Both are scoped here — ComfyUI
        matches the id against what is actually running and no-ops otherwise,
        so a stale id is free.

        Falls back to the unscoped interrupt when this process queued nothing
        it knows of, which is how a cancel raised in Flask still reaches a clip
        queued by the Celery worker. The fallback skips the queue clear, and
        goes away once cancellation is a flag both processes can see.
        """
        targets = [prompt_id] if prompt_id else self._known_prompts()
        if not targets:
            return self._interrupt_unscoped()

        acked = False
        for pid in targets:
            try:
                requests.post(
                    f"{self.comfy_url}/interrupt",
                    json={"prompt_id": pid},
                    timeout=5,
                )
                acked = True
            except Exception as e:
                logger.warning(f"Failed to interrupt ComfyUI prompt {pid}: {e}")
                continue
            try:
                requests.post(
                    f"{self.comfy_url}/queue",
                    json={"delete": [pid]},
                    timeout=5,
                )
            except Exception as delete_err:
                logger.debug(f"Queue delete for {pid} failed (non-fatal): {delete_err}")
            self._forget_prompt(pid)

        if acked:
            logger.info(f"Sent scoped interrupt to ComfyUI for {len(targets)} prompt(s)")
        return acked

    def _interrupt_unscoped(self) -> bool:
        """Stop whatever ComfyUI is sampling, whoever queued it."""
        try:
            requests.post(f"{self.comfy_url}/interrupt", timeout=5)
            logger.info("Sent unscoped interrupt to ComfyUI (no prompt of ours tracked)")
            return True
        except Exception as e:
            logger.warning(f"Failed to interrupt ComfyUI: {e}")
            return False

    def _queue_prompt(self, workflow: dict, client_id: Optional[str] = None) -> Optional[str]:
        try:
            payload = {"prompt": workflow}
            # client_id scopes ComfyUI's /ws progress messages back to us so the
            # progress bridge can hear this generation. (server.py:883)
            if client_id:
                payload["client_id"] = client_id
            self._last_queue_error = None
            response = requests.post(
                f"{self.comfy_url}/prompt",
                json=payload,
                timeout=10
            )
            response.raise_for_status()

            result = response.json()
            prompt_id = result.get("prompt_id")
            if prompt_id:
                self._track_prompt(prompt_id)
            logger.info(f"Queued workflow in ComfyUI: {prompt_id}")
            return prompt_id

        except requests.HTTPError as e:
            detail = ""
            try:
                if e.response is not None:
                    body = e.response.json()
                    if isinstance(body, dict):
                        err = body.get("error", body)
                        if isinstance(err, dict):
                            detail = err.get("message") or str(err)
                        else:
                            detail = str(err)
                        # node_errors carry the actionable part (e.g. which
                        # model file failed a loader's value check).
                        node_errors = body.get("node_errors")
                        if isinstance(node_errors, dict) and node_errors:
                            specifics = []
                            for node in node_errors.values():
                                for ne in (node or {}).get("errors", []):
                                    msg = ne.get("message", "")
                                    det = ne.get("details", "")
                                    specifics.append(
                                        f"{msg}: {det}" if det else msg
                                    )
                            if specifics:
                                detail += " — " + "; ".join(s for s in specifics[:3] if s)
            except Exception:
                if e.response is not None:
                    detail = (e.response.text or "")[:500]
            self._last_queue_error = detail or str(e)
            logger.error(
                "Failed to queue workflow in ComfyUI: %s%s",
                e,
                f" — {detail}" if detail else "",
            )
            return None
        except Exception as e:
            self._last_queue_error = str(e)
            logger.error(f"Failed to queue workflow in ComfyUI: {e}")
            return None

    # Which ComfyUI models/ subfolder each LTX-2.5 loader input reads from.
    # audio_vae is checkpoints/ because LTXVAudioVAELoader only lists that
    # folder; the file's real home is vae/, bridged by a symlink.
    _LTX25_MODEL_SUBDIRS = {
        "unet": "diffusion_models",
        "clip": "text_encoders",
        "vae": "vae",
        "audio_vae": "checkpoints",
        "upscale_model": "latent_upscale_models",
    }

    def _ltx25_missing_files(self, model_key: str) -> List[str]:
        """Relative paths of required LTX-2.5 files absent from the local
        ComfyUI models tree. Empty when all present — or when the tree isn't
        local (remote ComfyUI), where ComfyUI's own validation is the check."""
        if not COMFYUI_DIR:
            return []
        models_root = Path(COMFYUI_DIR) / "models"
        if not models_root.is_dir():
            return []
        files = self._ltx25_loader_cfg(model_key)
        missing = []
        for key, subdir in self._LTX25_MODEL_SUBDIRS.items():
            name = files.get(key)
            if name and not (models_root / subdir / name).exists():
                missing.append(f"{subdir}/{name}")
        return missing

    _COG_I2V_CANONICAL_SUBDIR = "checkpoints"
    _COG_I2V_LOADER_SUBDIR = "diffusion_models"

    def _cogvideox_i2v_files(self, model_key: str) -> dict:
        if not self.COGVIDEOX_I2V_FILES:
            try:
                from backend.services.video_model_registry import cogvideox_comfyui_map
                fresh = cogvideox_comfyui_map() or {}
                if fresh:
                    type(self).COGVIDEOX_I2V_FILES = fresh
            except Exception:  # pragma: no cover - defensive
                pass
        cfg = self.COGVIDEOX_I2V_FILES.get(model_key, {})
        return {
            "unet": cfg.get("unet") or "CogVideoX_1_5_5b_I2V_bf16.safetensors",
            "vae": cfg.get("vae") or "cogvideox_vae_bf16.safetensors",
        }

    def _cogvideox_i2v_missing_files(self, model_key: str) -> List[str]:
        """Relative paths of CogVideoX I2V files the wrapper's loaders cannot see.

        The transformer's canonical home is models/checkpoints (where every
        existing install put it) but CogVideoXModelLoader enumerates
        models/diffusion_models, so this reconciles the link a pre-2026-08-28
        install never got. Empty when everything is in place, or when the
        models tree isn't local (remote ComfyUI validates for itself)."""
        if not COMFYUI_DIR:
            return []
        models_root = Path(COMFYUI_DIR) / "models"
        if not models_root.is_dir():
            return []
        files = self._cogvideox_i2v_files(model_key)
        missing: List[str] = []
        loader_path = models_root / self._COG_I2V_LOADER_SUBDIR / files["unet"]
        canonical = models_root / self._COG_I2V_CANONICAL_SUBDIR / files["unet"]
        if not loader_path.exists():
            if canonical.exists() and canonical.stat().st_size > 0:
                loader_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(str(canonical), str(loader_path))
                except OSError:
                    os.symlink(str(canonical), str(loader_path))
                logger.info("Linked %s into %s for CogVideoXModelLoader", canonical.name, loader_path.parent)
            else:
                missing.append(f"{self._COG_I2V_CANONICAL_SUBDIR}/{files['unet']}")
        vae_path = models_root / "vae" / files["vae"]
        if not vae_path.exists():
            missing.append(f"vae/{files['vae']}")
        return missing

    def _comfyui_alive(self) -> bool:
        """Quick liveness probe — distinct from service_available cache."""
        return self._check_comfyui_connection()

    def _prompt_in_queue(self, prompt_id: str) -> Optional[bool]:
        """Return True/False if prompt is running or pending; None if queue probe failed."""
        try:
            response = requests.get(f"{self.comfy_url}/queue", timeout=5)
            response.raise_for_status()
            data = response.json()
            ids: set[str] = set()
            for key in ("queue_running", "queue_pending"):
                for entry in data.get(key) or []:
                    if isinstance(entry, (list, tuple)) and len(entry) > 1:
                        ids.add(str(entry[1]))
                    elif isinstance(entry, dict) and entry.get("prompt_id"):
                        ids.add(str(entry["prompt_id"]))
            return prompt_id in ids
        except Exception as e:
            logger.debug(f"ComfyUI queue probe failed: {e}")
            return None

    @staticmethod
    def _history_execution_error(entry: dict) -> Optional[str]:
        """Extract a human error from a ComfyUI history entry, if any."""
        status = entry.get("status") or {}
        if status.get("completed") is True and status.get("status_str") != "error":
            return None
        messages = status.get("messages") or []
        for msg in messages:
            if not isinstance(msg, (list, tuple)) or len(msg) < 2:
                continue
            tag, payload = msg[0], msg[1]
            if tag in ("execution_error", "execution_interrupted"):
                if isinstance(payload, dict):
                    text = payload.get("exception_message") or payload.get("message") or str(payload)
                else:
                    text = str(payload)
                return f"ComfyUI {tag}: {text}"
        if status.get("status_str") == "error":
            return "ComfyUI reported execution error"
        return None

    def _gpu_activity_snapshot(self) -> dict:
        """Best-effort VRAM/memory-util snapshot for activity-aware waits. Never raises."""
        try:
            from backend.services.gpu_resource_coordinator import get_available_vram
            info = get_available_vram()
            if not info.get("success"):
                return {}
            return {
                "free_mb": int(info.get("available_mb") or 0),
                "used_mb": int(info.get("used_mb") or 0),
                "total_mb": int(info.get("total_mb") or 0),
                # Coordinator reports memory occupancy as utilization_percent.
                "util_pct": float(info.get("utilization_percent") or 0),
            }
        except Exception:
            return {}

    def _card_looks_busy(self, *, util_threshold: float = 50.0, free_ratio_threshold: float = 0.35) -> bool:
        """True when VRAM occupancy suggests a render/weights are still resident."""
        snap = self._gpu_activity_snapshot()
        if not snap:
            return False
        util = snap.get("util_pct") or 0.0
        free = snap.get("free_mb")
        total = snap.get("total_mb") or 0
        if util >= util_threshold:
            return True
        if total > 0 and free is not None and (free / total) < free_ratio_threshold:
            return True
        return False

    def _wait_for_completion(
        self,
        prompt_id: str,
        timeout: int = 600,
        *,
        hard_ceiling_s: Optional[int] = None,
    ) -> Optional[dict]:
        """Poll ComfyUI history until the prompt finishes.

        ``timeout`` is a *soft* budget. While the prompt is still active (in
        Comfy's queue, or the GPU looks busy), we extend in 5-minute slices up
        to ``hard_ceiling_s`` (default max(2× soft, 3h)). We only abandon when
        the soft budget has expired *and* the prompt is idle/orphan for a
        sustained window, or the hard ceiling is hit.
        """
        soft_budget = max(60, int(timeout))
        hard_ceiling = int(
            hard_ceiling_s
            if hard_ceiling_s is not None
            else max(soft_budget * 2, 10800)
        )
        start_time = time.time()
        effective_deadline = start_time + soft_budget
        last_log_time = start_time
        orphan_grace_s = 30
        idle_kill_s = 90
        idle_since: Optional[float] = None
        consecutive_dead = 0
        # ~4s per missed probe (HTTP timeout + 2s sleep). Loading a 20GB+
        # transformer on a 16GB card stalls Comfy's HTTP server well past 20s
        # (observed 247s renders with ~30s silent loads on an RTX 5080 / 30GB
        # RAM box), so tolerate ~2 minutes before declaring the prompt orphaned.
        dead_probe_limit = 30

        while True:
            now = time.time()
            elapsed = now - start_time

            if elapsed >= hard_ceiling:
                snap = self._gpu_activity_snapshot()
                logger.error(
                    "Generation hit hard ceiling after %ds (soft=%ds ceiling=%ds) "
                    "prompt_id=%s util=%s free_mb=%s",
                    int(elapsed),
                    soft_budget,
                    hard_ceiling,
                    prompt_id,
                    snap.get("util_pct"),
                    snap.get("free_mb"),
                )
                return None

            if not self._comfyui_alive():
                consecutive_dead += 1
                if consecutive_dead >= dead_probe_limit:
                    logger.error(
                        "ComfyUI unreachable (%d consecutive probes) while waiting for %s — prompt orphaned",
                        consecutive_dead,
                        prompt_id,
                    )
                    return None
                logger.warning(
                    "ComfyUI liveness probe missed (%d/%d) while waiting for %s — retrying",
                    consecutive_dead,
                    dead_probe_limit,
                    prompt_id,
                )
                time.sleep(2)
                continue
            consecutive_dead = 0

            history: dict = {}
            try:
                response = requests.get(
                    f"{self.comfy_url}/history/{prompt_id}",
                    timeout=5,
                )
                response.raise_for_status()
                history = response.json() or {}

                if prompt_id in history:
                    entry = history[prompt_id]
                    exec_err = self._history_execution_error(entry)
                    if exec_err:
                        logger.error("Generation failed for %s: %s", prompt_id, exec_err)
                        return None
                    outputs = entry.get("outputs") or {}
                    if outputs:
                        logger.info(f"Generation complete: {prompt_id}")
                        return outputs
            except Exception as e:
                logger.warning(f"Error checking generation status: {e}")

            in_queue = self._prompt_in_queue(prompt_id)
            card_busy = self._card_looks_busy()
            # Active if Comfy still lists the prompt, or the card looks occupied
            # while Comfy is reachable (weights / encode still resident).
            active = (in_queue is True) or (card_busy and self._comfyui_alive())
            # Transient queue probe failure: don't treat as idle if card is busy.
            if in_queue is None and card_busy:
                active = True

            if active:
                idle_since = None
                if now >= effective_deadline:
                    effective_deadline = now + 300  # +5 min slice
                    snap = self._gpu_activity_snapshot()
                    logger.info(
                        "still_active extending soft budget (+5m) prompt_id=%s "
                        "elapsed=%ds util=%s free_mb=%s in_queue=%s",
                        prompt_id,
                        int(elapsed),
                        snap.get("util_pct"),
                        snap.get("free_mb"),
                        in_queue,
                    )
            else:
                # Idle / orphan path.
                orphan_candidate = (
                    elapsed > orphan_grace_s
                    and in_queue is False
                    and prompt_id not in history
                )
                past_soft = now >= (start_time + soft_budget)
                if orphan_candidate or (past_soft and in_queue is not True):
                    if idle_since is None:
                        idle_since = now
                    elif (now - idle_since) >= idle_kill_s:
                        snap = self._gpu_activity_snapshot()
                        logger.error(
                            "ComfyUI lost/idle prompt %s after %.0fs idle "
                            "(elapsed=%ds soft=%ds in_queue=%s util=%s free_mb=%s)",
                            prompt_id,
                            now - idle_since,
                            int(elapsed),
                            soft_budget,
                            in_queue,
                            snap.get("util_pct"),
                            snap.get("free_mb"),
                        )
                        return None

            if now - last_log_time > 10:
                snap = self._gpu_activity_snapshot()
                logger.info(
                    "Waiting for generation... (%ds elapsed, prompt_id=%s, "
                    "in_queue=%s, util=%s, free_mb=%s)",
                    int(elapsed),
                    prompt_id,
                    in_queue,
                    snap.get("util_pct"),
                    snap.get("free_mb"),
                )
                last_log_time = now

            time.sleep(2)


    def _download_result(self, outputs: dict, destination_dir: Path) -> List[str]:
        downloaded_files = []

        try:
            for node_id, node_output in outputs.items():
                if 'gifs' in node_output:
                    for item in node_output['gifs']:
                        filename = item.get('filename')
                        if filename:
                            downloaded_files.extend(
                                self._download_file(filename, destination_dir, file_type='output', subfolder=item.get('subfolder', ''))
                            )

                if 'images' in node_output:
                    for item in node_output['images']:
                        filename = item.get('filename')
                        if filename:
                            downloaded_files.extend(
                                self._download_file(filename, destination_dir, file_type='output', subfolder=item.get('subfolder', ''))
                            )

            logger.info(f"Downloaded {len(downloaded_files)} files from ComfyUI")
            return downloaded_files

        except Exception as e:
            logger.error(f"Failed to download results from ComfyUI: {e}")
            return []

    def _download_file(self, filename: str, destination_dir: Path, file_type: str = 'output', subfolder: str = '') -> List[str]:
        try:
            params = {"filename": filename, "type": file_type}
            if subfolder:
                params["subfolder"] = subfolder

            query = urllib.parse.urlencode(params)
            url = f"{self.comfy_url}/view?{query}"

            destination_path = destination_dir / filename
            destination_path.parent.mkdir(parents=True, exist_ok=True)

            logger.info(f"Downloading from ComfyUI: {url}")
            urllib.request.urlretrieve(url, destination_path)

            return [str(destination_path)]

        except Exception as e:
            logger.error(f"Failed to download file {filename}: {e}")
            return []

    def _extract_thumbnail(self, video_path: Path, thumbnail_path: Path) -> bool:
        """Extract the first frame from a video as a JPEG thumbnail using ffmpeg."""
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-i", str(video_path),
                    "-vf", "select=eq(n\\,0)",
                    "-frames:v", "1",
                    "-q:v", "2",
                    "-y", str(thumbnail_path),
                ],
                capture_output=True,
                timeout=30,
            )
            if thumbnail_path.exists() and thumbnail_path.stat().st_size > 0:
                logger.info(f"Extracted thumbnail: {thumbnail_path}")
                return True
            logger.warning(f"ffmpeg ran but thumbnail not created (rc={result.returncode})")
            return False
        except FileNotFoundError:
            logger.warning("ffmpeg not found on system, cannot extract thumbnail")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg thumbnail extraction timed out")
            return False
        except Exception as e:
            logger.warning(f"Failed to extract thumbnail: {e}")
            return False


    def generate_video(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Re-check live connection if the cached flag says unavailable.
        # ComfyUI may have been started on-demand by the router since init.
        if not self.service_available:
            self.service_available = self._check_comfyui_connection()
        if not self.service_available:
            return VideoGenerationResult(
                success=False,
                error="ComfyUI service not available. Please start ComfyUI at http://127.0.0.1:8188",
                prompt_used=request.prompt,
            )

        # Refresh node registry — ComfyUI loads custom nodes only at startup.
        self._object_info_cache = None
        if not self.comfy_node_available("VHS_VideoCombine"):
            return VideoGenerationResult(
                success=False,
                error=(
                    "ComfyUI is missing the VHS_VideoCombine node (Video Helper Suite). "
                    "Most often the ComfyUI-VideoHelperSuite custom node is not installed at all "
                    "(fresh installs before 2026-08 never received custom nodes). "
                    "Fix: run plugins/comfyui/scripts/install_deps.sh — it installs every required "
                    "node from plugins/comfyui/custom_nodes.manifest — then restart ComfyUI. "
                    "If custom_nodes/ComfyUI-VideoHelperSuite/ IS present, its import failed "
                    "(commonly missing cv2: backend/venv/bin/pip install opencv-python); "
                    "check logs/comfyui.log for the import error, then restart ComfyUI."
                ),
                prompt_used=request.prompt,
            )

        # ── Prompt enhancement ───────────────────────────────────────
        # Settings → Verbatim Prompts (or VERBATIM_PROMPTS=1) means exact user text.
        _verbatim_video = False
        if request.enhance_prompt and request.prompt:
            try:
                from backend.services.media_director import verbatim_prompts_enabled
                _verbatim_video = bool(verbatim_prompts_enabled())
            except Exception:
                _verbatim_video = False
        if request.enhance_prompt and request.prompt and not _verbatim_video:
            try:
                from backend.utils.prompt_enhancer import enhance_video_prompt, get_default_negative_prompt
                # Pass model_family for motion-aware hints (wan vs cogvideox)
                mf = self._model_family(request.model)
                request.prompt = enhance_video_prompt(
                    request.prompt,
                    style=request.prompt_style,
                    width=request.width,
                    height=request.height,
                    model_family=mf,
                    fidelity_mode=getattr(request, "fidelity_mode", False),
                    motion_strength=request.motion_strength,
                    # Context a family compiler needs (ignored by the suffix path).
                    duration_s=(request.duration_frames or 0) / float(request.fps or 24),
                    first_frame=bool(request.first_frame_path or (request.metadata or {}).get("image_path")),
                    last_frame=bool(request.last_frame_path),
                    language=request.language,
                    h3_intent=request.h3_intent,
                )
                if not request.negative_prompt:
                    request.negative_prompt = get_default_negative_prompt(style=request.prompt_style)
                logger.info(f"Prompt enhanced (style={request.prompt_style}, family={mf}): {request.prompt[:120]}...")
            except Exception as e:
                logger.warning(f"Prompt enhancement failed, using original prompt: {e}")
        elif _verbatim_video:
            logger.info("verbatim prompts ON — skipping video prompt enhancement")

        if request.output_dir:
            batch_dir = Path(request.output_dir)
        else:
            # Standalone generation — Bates-stamped folder in Videos/
            try:
                from backend.services.output_registration import bates_name
                folder_name = bates_name("video_batch", "", self.default_output_dir)
            except Exception:
                # Bates failed — fall back to a date-stamped name rather than
                # raw uuid hex so the user-visible folder name stays readable.
                from datetime import datetime as _dt
                folder_name = f"VideoBatch_{_dt.now().strftime('%m-%d-%Y_%H-%M-%S')}"
            batch_dir = self.default_output_dir / folder_name
        batch_dir = Path(batch_dir)

        item_id = request.metadata.get("item_id") if request.metadata else None
        if not item_id:
            # Bates-stamped item ID instead of UUID soup
            try:
                from backend.services.output_registration import bates_name
                item_id = bates_name("video", "", batch_dir)
            except Exception:
                # Same fallback shape as folder_name above — readable names
                # over uuid hex when the Bates path fails for any reason.
                from datetime import datetime as _dt
                item_id = f"VideoGen_{_dt.now().strftime('%m-%d-%Y_%H-%M-%S')}"
            if request.metadata:
                request.metadata["item_id"] = item_id

        item_dir = batch_dir / item_id
        videos_dir = item_dir / "videos"
        frames_dir = item_dir / "frames"
        thumbs_dir = item_dir / "thumbnails"

        videos_dir.mkdir(parents=True, exist_ok=True)
        frames_dir.mkdir(parents=True, exist_ok=True)
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        result = VideoGenerationResult(
            success=False,
            prompt_used=request.prompt,
            metadata=request.metadata or {},
        )

        try:
            image_path = request.metadata.get("image_path") if request.metadata else None
            model = request.model or "cogvideox-5b"
            seed = request.seed if request.seed is not None else int(time.time() * 1000) % (2**31)

            # VRAM preflight: turn a known-under-spec card into an honest error
            # instead of queuing into a silent mid-render OOM. Fail-open on a
            # broken probe (see _vram_preflight); read-only, allocates nothing.
            preflight_error = self._vram_preflight(model)
            if preflight_error:
                result.error = preflight_error
                return result

            # Defense-in-depth: cap pixel area, then snap dims, before they enter
            # any workflow builder. 1920×1920 (3.7 MPx) requests ran until the
            # watchdog timeout on both Wan variants and read as a hang — and old
            # batch retry_data can replay those dims verbatim.
            # Off-by-one in the snap is the "tensor a (51) must match tensor b (50)" crash.
            request.width, request.height = self._clamp_aspect_ratio(
                request.width, request.height, model
            )
            request.width, request.height = self._clamp_pixel_area(
                request.width, request.height, model
            )
            request.width, request.height = self._align_dimensions(
                request.width, request.height, model
            )

            interpolation = request.interpolation_multiplier

            # ── Route by model type ──────────────────────────────────
            self._ensure_wan_models()
            self._ensure_ltx_models()
            self._ensure_hunyuan_models()
            if model in self.WAN22_MODELS or model in ("wan22", "wan2.2"):
                model_key = model if model in self.WAN22_MODELS else "wan22-14b"
                cfg = self.WAN22_MODELS[model_key]

                # Wan 14B MoE uses ComfyUI-GGUF (UnetLoaderGGUF). TI2V-5B uses UNETLoader.
                if not cfg.get("single") and not self.comfy_node_available("UnetLoaderGGUF"):
                    return VideoGenerationResult(
                        success=False,
                        error=(
                            "ComfyUI is missing UnetLoaderGGUF (ComfyUI-GGUF custom node). "
                            "Wan 14B GGUF models need gguf in the backend venv. "
                            "Fix: backend/venv/bin/pip install 'gguf>=0.13.0' sentencepiece protobuf, "
                            "then restart ComfyUI."
                        ),
                        prompt_used=request.prompt,
                    )

                is_i2v = cfg.get("type") == "i2v"

                wan_profile, wan_err = self._resolve_wan_profile(request, model_key)
                if wan_err:
                    result.error = wan_err
                    return result
                wan_steps = request.num_inference_steps
                wan_cfg = request.guidance_scale
                wan_shift = None
                wan_lora_high = wan_lora_low = None
                wan_lora_strength = 1.0
                if wan_profile:
                    explicit = str((request.metadata or {}).get("steps_explicit", "")).lower() in ("1", "true")
                    if not (explicit and request.num_inference_steps):
                        wan_steps = int(wan_profile["steps"])
                    if wan_profile.get("cfg") is not None:
                        wan_cfg = float(wan_profile["cfg"])
                    wan_shift = wan_profile.get("shift")
                    files = wan_profile.get("lora_files") or {}
                    wan_lora_high, wan_lora_low = files.get("unet_high"), files.get("unet_low")
                    wan_lora_strength = float(wan_profile.get("strength") or 1.0)

                if cfg.get("single"):
                    # Wan 2.2 TI2V-5B: ONE model does both — image-to-video if a start
                    # image is given, else text-to-video. Fits 16GB, no MoE two-pass.
                    img_name = None
                    if image_path and Path(image_path).exists():
                        img_name = self._upload_image_to_comfyui(image_path)
                        if not img_name:
                            result.error = "Failed to upload image to ComfyUI"
                            return result
                    workflow = self._create_wan22_5b_workflow(
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        model_key=model_key,
                        image_filename=img_name,
                        sampler_profile=request.wan_sampler_profile,
                        num_frames=request.duration_frames,
                        num_inference_steps=request.num_inference_steps,
                        guidance_scale=request.guidance_scale,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps,
                        interpolation_multiplier=interpolation,
                    )
                    logger.info(f"Using Wan 2.2 TI2V-5B ({'i2v' if img_name else 't2v'}, {model_key}) via ComfyUI")
                elif is_i2v:
                    if not image_path or not Path(image_path).exists():
                        result.error = "Wan 2.2 I2V requires an input image."
                        return result
                    uploaded_image = self._upload_image_to_comfyui(image_path)
                    if not uploaded_image:
                        result.error = "Failed to upload image to ComfyUI"
                        return result
                    workflow = self._create_wan22_i2v_workflow(
                        image_filename=uploaded_image,
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        model_key=model_key,
                        num_frames=request.duration_frames,
                        num_inference_steps=wan_steps,
                        guidance_scale=wan_cfg,
                        sampler_profile=request.wan_sampler_profile,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps,
                        interpolation_multiplier=interpolation,
                        lora_high=wan_lora_high,
                        lora_low=wan_lora_low,
                        lora_strength=wan_lora_strength,
                        shift_override=wan_shift,
                    )
                    logger.info(f"Using Wan 2.2 image-to-video ({model_key}) via ComfyUI GGUF")
                else:
                    if image_path:
                        result.error = f"{model_key} is text-to-video only. Use wan22-14b-i2v for image-to-video."
                        return result
                    workflow = self._create_wan22_t2v_workflow(
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        model_key=model_key,
                        num_frames=request.duration_frames,
                        num_inference_steps=wan_steps,
                        guidance_scale=wan_cfg,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps,
                        interpolation_multiplier=interpolation,
                        lora_high=wan_lora_high,
                        lora_low=wan_lora_low,
                        lora_strength=wan_lora_strength,
                        shift_override=wan_shift,
                    )
                    logger.info(f"Using Wan 2.2 text-to-video ({model_key}) via ComfyUI GGUF")

            elif model in self.HUNYUAN_MODELS or str(model).startswith("hunyuan"):
                model_key = model if model in self.HUNYUAN_MODELS else "hunyuan-t2v"
                if not self.comfy_node_available("UnetLoaderGGUF"):
                    return VideoGenerationResult(
                        success=False,
                        error=(
                            "ComfyUI is missing UnetLoaderGGUF (ComfyUI-GGUF custom node). "
                            "HunyuanVideo GGUF models need gguf in the backend venv. "
                            "Fix: backend/venv/bin/pip install 'gguf>=0.13.0' sentencepiece protobuf, "
                            "then restart ComfyUI."
                        ),
                        prompt_used=request.prompt,
                    )
                frames = self._hunyuan_frame_count(request.duration_frames)
                if (self.HUNYUAN_MODELS.get(model_key) or {}).get("type") == "i2v":
                    if not image_path or not Path(image_path).exists():
                        result.error = "HunyuanVideo I2V requires an input image."
                        return result
                    uploaded_image = self._upload_image_to_comfyui(image_path)
                    if not uploaded_image:
                        result.error = "Failed to upload image to ComfyUI"
                        return result
                    workflow = self._create_hunyuan_i2v_workflow(
                        image_filename=uploaded_image,
                        prompt=request.prompt,
                        model_key=model_key,
                        num_frames=frames,
                        num_inference_steps=request.num_inference_steps,
                        guidance_scale=request.guidance_scale,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps,
                        interpolation_multiplier=interpolation,
                    )
                    logger.info(f"Using HunyuanVideo image-to-video ({model_key}) via ComfyUI GGUF")
                else:
                    if image_path:
                        result.error = f"{model_key} is text-to-video only. Use hunyuan-i2v for image-to-video."
                        return result
                    workflow = self._create_hunyuan_t2v_workflow(
                        prompt=request.prompt,
                        model_key=model_key,
                        num_frames=frames,
                        num_inference_steps=request.num_inference_steps,
                        guidance_scale=request.guidance_scale,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps,
                        interpolation_multiplier=interpolation,
                    )
                    logger.info(f"Using HunyuanVideo text-to-video ({model_key}) via ComfyUI GGUF")

            elif model == "cogvideox-5b":
                if image_path:
                    result.error = f"{model} is text-to-video only. Use cogvideox-5b-i2v for image-to-video."
                    return result
                # Text-to-video via CogVideoX
                hf_model = self.COGVIDEOX_MODELS.get(model, "THUDM/CogVideoX-5b")
                workflow = self._create_cogvideox_text2video_workflow(
                    prompt=request.prompt,
                    model_name=hf_model,
                    num_frames=request.duration_frames,
                    num_inference_steps=request.num_inference_steps,
                    guidance_scale=request.guidance_scale,
                    width=request.width,
                    height=request.height,
                    seed=seed,
                    fps=request.fps,
                    interpolation_multiplier=interpolation,
                )
                # Add optional TeaCache / FETA nodes for CogVideoX
                meta = request.metadata or {}
                self._add_cogvideox_optional_nodes(
                    workflow, sampler_node_id="6",
                    teacache_threshold=meta.get("teacache_threshold"),
                    feta_weight=meta.get("feta_weight"),
                )
                logger.info(f"Using CogVideoX text-to-video ({model}) via ComfyUI")

            elif model == "cogvideox-5b-i2v":
                # Image-to-video via CogVideoX
                if not image_path or not Path(image_path).exists():
                    result.error = "CogVideoX image-to-video requires an input image."
                    return result
                uploaded_image = self._upload_image_to_comfyui(image_path)
                if not uploaded_image:
                    result.error = "Failed to upload image to ComfyUI"
                    return result
                missing = self._cogvideox_i2v_missing_files(model)
                if missing:
                    result.error = (
                        f"CogVideoX I2V files missing on this machine: {', '.join(missing)}. "
                        f"Open Manage Video Models and Install CogVideoX 1.5 5B I2V "
                        f"(companions auto-pull). Generation never downloads on its own."
                    )
                    return result
                files = self._cogvideox_i2v_files(model)
                workflow = self._create_cogvideox_i2v_workflow(
                    image_filename=uploaded_image,
                    prompt=request.prompt,
                    model_file=files["unet"],
                    vae_file=files["vae"],
                    num_frames=request.duration_frames,
                    num_inference_steps=request.num_inference_steps,
                    guidance_scale=request.guidance_scale,
                    width=request.width,
                    height=request.height,
                    seed=seed,
                    fps=request.fps,
                    interpolation_multiplier=interpolation,
                )
                # Add optional TeaCache / FETA nodes for CogVideoX I2V
                meta = request.metadata or {}
                self._add_cogvideox_optional_nodes(
                    workflow, sampler_node_id="6",
                    teacache_threshold=meta.get("teacache_threshold"),
                    feta_weight=meta.get("feta_weight"),
                )
                logger.info(f"Using CogVideoX image-to-video via ComfyUI")

            elif model in self.LTX_MODELS or str(model).startswith("ltx"):
                # Decode the soundtrack the model sampled only when the entry
                # says so (registry audio_out); the graph is the same otherwise.
                from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY as _reg
                ltx_audio = bool((_reg.get(model) or {}).get("audio_out"))
                if ltx_audio:
                    result.has_audio = True
                    result.metadata["has_audio"] = "1"
                model_key = model if model in self.LTX_MODELS else (
                    "ltx25-distilled-int8" if str(model).startswith("ltx25")
                    else "ltx23-distilled-fp8"
                )
                use_ltx25 = str(model_key).startswith("ltx25") or bool(
                    (self.LTX_MODELS.get(model_key) or {}).get("upscale_model")
                )
                if use_ltx25:
                    missing = self._ltx25_missing_files(model_key)
                    if missing:
                        result.error = (
                            f"LTX-2.5 model files missing on this machine: "
                            f"{', '.join(missing)}. Transfer or download them into "
                            f"the ComfyUI models tree, then retry. (The audio VAE "
                            f"must be reachable from models/checkpoints/ — a symlink "
                            f"to ../vae/ works and is created automatically on "
                            f"plugin start when the vae/ copy exists.)"
                        )
                        return result
                # Distilled defaults: 8 steps, CFG=1. Don't silently inherit Cog/Wan defaults.
                ltx_steps = request.num_inference_steps or 8
                ltx_cfg = request.guidance_scale if request.guidance_scale is not None else 1.0
                if ltx_cfg > 1.5:
                    logger.info(
                        "LTX distilled prefers CFG=1 (got %.2f); keeping caller value but "
                        "quality may degrade.",
                        ltx_cfg,
                    )
                i2v = bool(image_path and Path(image_path).exists())
                if i2v:
                    uploaded_image = self._upload_image_to_comfyui(image_path)
                    if not uploaded_image:
                        result.error = "Failed to upload image to ComfyUI"
                        return result
                    if use_ltx25:
                        workflow = self._create_ltx25_i2v_workflow(
                            image_filename=uploaded_image,
                            prompt=request.prompt,
                            negative_prompt=request.negative_prompt,
                            model_key=model_key,
                            num_frames=request.duration_frames,
                            num_inference_steps=ltx_steps,
                            guidance_scale=ltx_cfg,
                            width=request.width,
                            height=request.height,
                            seed=seed,
                            fps=request.fps or 16,
                            interpolation_multiplier=interpolation,
                            audio_out=ltx_audio,
                        )
                        logger.info("Using LTX-2.5 distilled I2V (%s) via ComfyUI", model_key)
                    else:
                        workflow = self._create_ltx23_i2v_workflow(
                            image_filename=uploaded_image,
                            prompt=request.prompt,
                            negative_prompt=request.negative_prompt,
                            model_key=model_key,
                            num_frames=request.duration_frames,
                            num_inference_steps=ltx_steps,
                            guidance_scale=ltx_cfg,
                            width=request.width,
                            height=request.height,
                            seed=seed,
                            fps=request.fps or 16,
                            interpolation_multiplier=interpolation,
                            audio_out=ltx_audio,
                        )
                        logger.info("Using LTX-2.3 distilled I2V (%s) via ComfyUI", model_key)
                elif use_ltx25:
                    workflow = self._create_ltx25_t2v_workflow(
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        model_key=model_key,
                        num_frames=request.duration_frames,
                        num_inference_steps=ltx_steps,
                        guidance_scale=ltx_cfg,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps or 16,
                        interpolation_multiplier=interpolation,
                            audio_out=ltx_audio,
                    )
                    logger.info("Using LTX-2.5 distilled T2V (%s) via ComfyUI", model_key)
                else:
                    workflow = self._create_ltx23_t2v_workflow(
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        model_key=model_key,
                        num_frames=request.duration_frames,
                        num_inference_steps=ltx_steps,
                        guidance_scale=ltx_cfg,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps or 16,
                        interpolation_multiplier=interpolation,
                            audio_out=ltx_audio,
                    )
                    logger.info("Using LTX-2.3 distilled T2V (%s) via ComfyUI", model_key)

            elif model in self.MINIMAX_MODELS or str(model).startswith("minimax"):
                model_key = model if model in self.MINIMAX_MODELS else "minimax-h3-int8"
                workflow, mm_error = self._build_minimax_request(
                    request, model_key, image_path, seed, interpolation
                )
                if mm_error:
                    result.error = mm_error
                    return result
                result.has_audio = True
                result.metadata["has_audio"] = "1"

            else:
                # SVD retired 2026-05-29. Supported: wan22-*, cogvideox-*, ltx23-*, ltx25-*, minimax-*.
                result.error = (
                    f"Unsupported video model '{model}'. Use wan22-5b, wan22-14b, "
                    f"wan22-14b-i2v, cogvideox-5b, cogvideox-5b-i2v, "
                    f"ltx23-distilled-fp8, ltx25-distilled-int8, or minimax-h3-int8."
                )
                return result

            # Apply Real-ESRGAN 2x upscale if requested
            upscale = request.metadata.get("upscale", False) if request.metadata else False
            if upscale:
                # Find VHS_VideoCombine and its current frame source
                vhs_node_id = next(
                    (nid for nid, node in workflow.items() if node.get("class_type") == "VHS_VideoCombine"),
                    None
                )
                if vhs_node_id:
                    # The node currently feeding images to VHS_VideoCombine
                    source_ref = workflow[vhs_node_id]["inputs"].get("images", [None])[0]
                    if source_ref:
                        self._add_upscale_node(workflow, source_ref, vhs_node_id)

            # Apply FaceRestore if requested (requires facerestore_cf custom node)
            if request.face_restore:
                try:
                    from backend.services.video_model_registry import is_model_installed
                    model_ready = is_model_installed("codeformer")
                except Exception:
                    model_ready = False
                if (
                    self.comfy_node_available("FaceRestoreModelLoader")
                    and self.comfy_node_available("FaceRestoreCFWithModel")
                    and model_ready
                ):
                    vhs_node_id = next(
                        (nid for nid, node in workflow.items() if node.get("class_type") == "VHS_VideoCombine"),
                        None
                    )
                    if vhs_node_id:
                        source_ref = workflow[vhs_node_id]["inputs"].get("images", [None])[0]
                        if source_ref:
                            self._add_face_detailer_node(workflow, source_ref, vhs_node_id)
                else:
                    if not self.comfy_node_available("FaceRestoreModelLoader"):
                        logger.warning(
                            "Face restore requested but ComfyUI node FaceRestoreModelLoader is not "
                            "installed. Restart ComfyUI to load facerestore_cf. Continuing without "
                            "face restore."
                        )
                    else:
                        logger.warning(
                            "Face restore requested but codeformer.pth is not installed. "
                            "Install CodeFormer from Manage Video Models. Continuing without face restore."
                        )

            # Apply FreeU if requested
            if request.freeu:
                model_node_id = None
                for nid, node in workflow.items():
                    if node.get("class_type") in self._COG_MODEL_LOADER_NODES:
                        model_node_id = nid
                        break
                if model_node_id:
                    # CogVideoX uses a custom typed model (COGVIDEOMODEL) from the wrapper.
                    # Generic FreeU_V2 outputs plain MODEL, which causes ComfyUI prompt
                    # validation to fail with "Return type mismatch ... MODEL vs COGVIDEOMODEL"
                    # on the CogVideoSampler input. Skip for Cog (Wan never reaches here).
                    family = self._model_family(model)
                    if family == "cogvideox":
                        logger.warning(
                            "FreeU Enhance requested for CogVideoX model but skipped: "
                            "incompatible with custom COGVIDEOMODEL typing (would produce "
                            "invalid prompt for CogVideoSampler). General options like "
                            "interpolation, upscale, and prompt enhancement still apply. "
                            "FreeU works on supported Wan paths."
                        )
                    else:
                        freeu_id = self._add_freeu_node(workflow, model_node_id, is_cogvideo=True)
                        for nid, node in workflow.items():
                            if node.get("class_type") == "CogVideoSampler":
                                if node["inputs"].get("model", [None])[0] == model_node_id:
                                    node["inputs"]["model"] = [freeu_id, 0]

            # Apply Lora if requested. Only the CogVideoX backbone has a LoRA hook
            # here (DownloadAndLoadCogVideoModel + CLIPLoader → LoraLoader chain).
            # Wan 2.2's GGUF backbone (UnetLoaderGGUF) has NO matching hook and no
            # base-matched Wan LoRAs exist, so wiring would be a no-op at best —
            # be HONEST about the skip instead of silently dropping it. Identity on
            # the Wan i2v path comes from the init (keyframe) image, not a LoRA.
            if request.lora_name:
                model_node_id = None
                clip_node_id = None
                for nid, node in workflow.items():
                    if node.get("class_type") in self._COG_MODEL_LOADER_NODES:
                        model_node_id = nid
                    elif node.get("class_type") == "CLIPLoader":
                        clip_node_id = nid
                family = self._model_family(model)
                if family == "cogvideox":
                    # Same type incompatibility as FreeU: LoraLoader produces generic
                    # MODEL; CogVideoSampler expects COGVIDEOMODEL from the custom loader.
                    # This produces the exact "Return type mismatch MODEL vs COGVIDEOMODEL"
                    # validation error seen in logs. Skip with explanation.
                    logger.warning(
                        "LoRA '%s' requested for CogVideoX but not applied: "
                        "incompatible with custom COGVIDEOMODEL typing used by "
                        "DownloadAndLoadCogVideoModel + CogVideoSampler (causes prompt "
                        "validation failure). The option is ignored for Cog models. "
                        "Use Wan models if LoRA character consistency is needed, or "
                        "rely on the I2V starting image for identity.",
                        request.lora_name,
                    )
                elif model_node_id and clip_node_id:
                    new_model, new_clip = self._add_lora_loader(workflow, model_node_id, clip_node_id, request.lora_name, request.lora_strength)
                    for nid, node in workflow.items():
                        if node.get("class_type") == "CogVideoSampler":
                            if node["inputs"].get("model", [None])[0] == model_node_id:
                                node["inputs"]["model"] = [new_model, 0]
                        elif "TextEncode" in node.get("class_type", ""):
                            if node["inputs"].get("clip", [None])[0] == clip_node_id:
                                node["inputs"]["clip"] = [new_clip, 0]
                else:
                    logger.warning(
                        "LoRA '%s' not applied: backbone=%s has no LoRA hook; "
                        "identity comes from the init frame.",
                        request.lora_name, family,
                    )

            # Frame-sequence export (Q3): when frames are requested, tee the FINAL
            # frames (post upscale/face-restore/interpolation — the exact frames the
            # MP4 receives) to a lossless PNG sequence ALONGSIDE the MP4, so they can be
            # stitched in an external editor without the h264/yuv420p loss. Additive —
            # the MP4 is still produced.
            if getattr(request, "generate_frames_only", False):
                self._add_frame_export(workflow, item_id)

            logger.info("Sending workflow to ComfyUI...")
            # ── Layer 1: live progress bridge ────────────────────────────────
            # Listen to ComfyUI's /ws so the UI sees per-step progress instead of
            # a silent /history poll. Additive + flag-gated + self-terminating —
            # if it fails, generation proceeds exactly as before.
            import uuid as _uuid

            class _NoOpProgressBridge:
                def start(self, *args, **kwargs):
                    return None

                def stop(self):
                    return None

            client_id = _uuid.uuid4().hex
            progress_bridge = _NoOpProgressBridge()
            try:
                from backend.services.comfyui_progress_bridge import ComfyUIProgressBridge

                progress_bridge = ComfyUIProgressBridge()
                progress_bridge.start(
                    client_id=client_id,
                    process_id=item_id,
                    comfy_url=self.comfy_url,
                    workflow=workflow,
                    extra={"batch_id": (request.metadata or {}).get("batch_id", "")},
                )
            except Exception as _be:
                logger.warning(f"Progress bridge unavailable (non-fatal): {_be}")

            prompt_id = self._queue_prompt(workflow, client_id=client_id)

            if not prompt_id:
                progress_bridge.stop()
                queue_detail = getattr(self, "_last_queue_error", None)
                result.error = (
                    f"Failed to queue workflow in ComfyUI: {queue_detail}"
                    if queue_detail else "Failed to queue workflow in ComfyUI"
                )
                return result

            # Soft budget scaled to what the GPU actually needs. Activity-aware
            # _wait_for_completion extends past this while Comfy/GPU stay busy.
            # Hard ceiling (3h) stops truly wedged jobs.
            is_wan = model in self.WAN22_MODELS or model in ("wan22", "wan2.2")
            steps = request.num_inference_steps or 30
            has_upscale = request.metadata.get("upscale", False) if request.metadata else False
            is_high_res = max(request.width or 0, request.height or 0) >= 1280
            fpb = getattr(request, "frames_per_batch", 1) or 1
            interp = getattr(request, "interpolation_multiplier", 1) or 1
            base = 1200 if is_wan else 600
            scale = (max(1, request.duration_frames or 49) / 81.0) * (max(10, steps) / 25.0)
            scale *= (1.7 if has_upscale else 1.0)
            scale *= (1.2 if fpb > 1 else 1.0)
            scale *= (1.15 if interp > 1 else 1.0)
            if is_high_res:
                gen_timeout = max(5400, int(base * scale * 2.5))  # HD / max-res floor 90m
            elif is_wan and (steps >= 40 or has_upscale):
                gen_timeout = max(3600, int(base * scale * 2.0))
            elif is_wan:
                gen_timeout = max(1800, int(base * scale * 1.2))
            else:
                gen_timeout = max(600, int(base * scale * 0.8))
            hard_ceiling = max(gen_timeout * 2, 10800)  # ≥3h for maxed Wan
            logger.info(
                f"Waiting for ComfyUI to complete generation (prompt_id: {prompt_id}, "
                f"soft_timeout: {gen_timeout}s, hard_ceiling: {hard_ceiling}s, "
                f"steps: {steps}, upscale: {has_upscale}, high_res: {is_high_res}, fpb={fpb})..."
            )
            outputs = self._wait_for_completion(
                prompt_id, timeout=gen_timeout, hard_ceiling_s=hard_ceiling
            )
            self._forget_prompt(prompt_id)
            progress_bridge.stop()  # /history poll owns completion; bridge is done

            if not outputs:
                result.error = "ComfyUI generation timed out or failed"
                return result

            logger.info("Downloading results from ComfyUI...")
            downloaded_files = self._download_result(outputs, videos_dir)

            if not downloaded_files:
                result.error = "No files were generated by ComfyUI"
                return result

            # Separate the rendered video(s) from any exported PNG frame sequence:
            # Q3 frame export adds a SaveImage node alongside VHS_VideoCombine, so the
            # download set can now contain both an MP4 and a PNG sequence. The video is
            # the primary artifact (and what the blank-render guard must inspect).
            _vid_exts = (".mp4", ".webm", ".avi", ".mov", ".gif")
            video_files = [f for f in downloaded_files if Path(f).suffix.lower() in _vid_exts]
            frame_files = sorted(f for f in downloaded_files if Path(f).suffix.lower() == ".png")
            primary = video_files[0] if video_files else downloaded_files[0]

            # Zero-placebo guard (issue #36 Phase 3 / NO-MOCKS charter): never report
            # success for a blank/empty/all-black render. ComfyUI can emit a black clip
            # when a model/loader fails silently. No opt-out — we do not ship fake output.
            blank_reason = _looks_like_blank_video(Path(primary))
            if blank_reason:
                result.error = (
                    f"ComfyUI produced an invalid video: {blank_reason}. This usually "
                    "means a model/loader failed silently — verify the model is fully "
                    "installed."
                )
                logger.error(f"Zero-placebo guard rejected ComfyUI output: {blank_reason}")
                return result  # success stays False — no fake 'done'

            result.video_path = str(Path(primary).relative_to(batch_dir))
            # frame_paths exposes the lossless PNG sequence when it was exported (for
            # self-stitching); otherwise it stays the produced video file(s), preserving
            # prior behavior.
            _fp_source = frame_files if frame_files else (video_files or downloaded_files)
            result.frame_paths = [str(Path(f).relative_to(batch_dir)) for f in _fp_source]
            result.success = True

            # Extract thumbnail from the primary video file
            video_file = Path(primary)
            if video_file.exists() and video_file.suffix.lower() in (".mp4", ".webm", ".avi", ".mov"):
                thumb_filename = video_file.stem + "_thumb.jpg"
                thumb_path = thumbs_dir / thumb_filename
                if self._extract_thumbnail(video_file, thumb_path):
                    result.thumbnail_path = str(thumb_path.relative_to(batch_dir))

            logger.info(f"Video generation successful: {result.video_path}")

            # Register into Documents/Files system if not batch-controlled
            # (batch-controlled videos get registered by the batch_video_generator)
            is_batch_controlled = (request.metadata or {}).get("batch_controlled", False)
            if not is_batch_controlled and result.success:
                try:
                    from backend.services.output_registration import register_file, ensure_subfolder
                    batch_folder_name = batch_dir.name
                    ensure_subfolder("Videos", batch_folder_name)
                    for vid_file in videos_dir.glob("*.mp4"):
                        register_file(
                            physical_path=str(vid_file),
                            folder_name="Videos",
                            subfolder_name=batch_folder_name,
                            file_metadata={"source": "comfyui", "prompt": request.prompt[:200]},
                        )
                except Exception as reg_err:
                    logger.warning(f"Video registration into Documents failed (non-critical): {reg_err}")

            # Post-frame (incl. post-upscale) VRAM hygiene to prevent leaks across batches/frames.
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

            return result

        except Exception as e:
            logger.error(f"Error during video generation: {e}")
            import traceback
            logger.error(traceback.format_exc())
            err_str = str(e)
            is_oom = (
                "out of memory" in err_str.lower()
                or "OutOfMemory" in err_str
                or "CUDA out of memory" in err_str
                or "torch.cuda.OutOfMemoryError" in err_str
                or "MPS backend out of memory" in err_str  # Apple Silicon (#43)
                or ("RuntimeError" in str(type(e)) and "memory" in err_str.lower())
            )
            if is_oom:
                result.error = "OOM during ComfyUI video generation (VRAM exhausted; reduce res/steps, disable upscale/LoRA, or free other models first)"
                try:
                    self.service_available = False
                except Exception:
                    pass
            else:
                result.error = err_str
            result.success = False
            return result


_video_generator_instance: Optional[ComfyUIVideoGenerator] = None


def get_video_generator() -> ComfyUIVideoGenerator:
    global _video_generator_instance
    if _video_generator_instance is None:
        _video_generator_instance = ComfyUIVideoGenerator()
    return _video_generator_instance


def resolve_generated_video_path(result, output_dir) -> Path:
    """Absolute path to the file produced by ``ComfyUIVideoGenerator.generate_video``.

    generate_video returns ``result.video_path`` RELATIVE to ``request.output_dir``
    (it does ``relative_to(batch_dir)`` at the very end). Any caller that set
    ``output_dir`` MUST rejoin it here before touching the file — otherwise the bare
    relative path is read against cwd and ``shutil.copyfile`` dies with ENOENT. This
    is the single source of truth for that resolution (shared by both i2v adapters
    and the music-video clip path). Absolute paths pass through unchanged.
    """
    vp = Path(result.video_path)
    return vp if vp.is_absolute() else (Path(output_dir) / vp)


class SvdI2VGenerator:
    """Adapts the SVD image-to-video path to the Editor's I2VGenerator protocol.

    Character identity rides in via the seed image — the storyboard frame is
    already LoRA-consistent — so SVD (which animates a single image and takes no
    text prompt or LoRA) is the right tool. `prompt`/`loras` are accepted to
    satisfy the protocol but intentionally ignored: the frame carries identity.
    """

    def __init__(self, fps: int = 7):
        self.fps = fps

    def i2v_from_image(
        self, *, image_path: str, prompt: str, loras: list[str],
        duration_seconds: float, output_path: str,
    ) -> str:
        # SVD retired — use CogVideoX-5b I2V to animate the single identity frame.
        # Clamp to a short clip (≤25 frames) to keep VRAM in budget on 16 GB.
        frames = max(14, min(25, int(round(duration_seconds * self.fps)) or 25))
        out_dir = Path(output_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        gen = get_video_generator()
        req = VideoGenerationRequest(
            model="cogvideox-5b-i2v",
            duration_frames=frames,
            fps=self.fps,
            enhance_prompt=False,
            output_dir=out_dir,                      # known base → result path resolves
            metadata={"image_path": image_path},
        )
        result = gen.generate_video(req)
        if not result.success or not result.video_path:
            raise RuntimeError(f"I2V failed: {result.error or 'no video produced'}")
        shutil.copyfile(resolve_generated_video_path(result, out_dir), output_path)
        return output_path


class MiniMaxH3SceneGenerator:
    """MiniMax H3 adapter for the Editor's SceneGenerator protocol: a run of
    shots becomes one clip with its own soundtrack. The shot list is compiled
    into the model's prompt (numbered shots, cut times, the cast's lines as
    tagged dialogue); the storyboard still of the window's first shot is the
    first frame and the next window's still the last frame, so windows join
    on frames the keyframe stage drew. With cast reference images and the
    reference build installed, identity comes from the references instead;
    otherwise the LoRA-locked still carries it, as on the Wan path.
    """

    def __init__(self, model: str = "minimax-h3-int8", fps: int = 24, style: str = "cinematic",
                 language: str = "English", prefer_references: bool = True):
        self.model = model
        self.fps = fps
        self.style = style
        self.language = language
        self.prefer_references = prefer_references

    def _reference_model(self) -> Optional[str]:
        """The installed reference build sharing this model's family, or None."""
        from backend.services.video_model_registry import (
            VIDEO_MODEL_REGISTRY, is_model_installed, model_capabilities,
        )
        for mid, entry in VIDEO_MODEL_REGISTRY.items():
            if entry.get("type") != "minimax":
                continue
            caps = model_capabilities(mid)
            if "ref2v" in caps.get("modes", []) and is_model_installed(mid):
                return mid
        return None

    def render_scene(self, *, shots, first_frame: str, last_frame: Optional[str], output_path: str,
                     duration_seconds: float, scene_mood: Optional[str] = None) -> str:
        from backend.services import h3_prompt_compiler as h3
        from backend.services.video_model_registry import tier_defaults_for

        refs: list[str] = []
        subjects: list[tuple] = []
        seen = set()
        for shot in shots:
            name = getattr(shot, "character_name", None)
            paths = [p for p in (getattr(shot, "ref_image_paths", None) or []) if p and Path(p).exists()]
            if name and paths and name not in seen:
                seen.add(name)
                start = len(refs) + 1
                refs.extend(paths[:3])
                subjects.append((name, getattr(shot, "character_description", None) or "", list(range(start, len(refs) + 1))))
        refs = refs[:9]
        ref_model = self._reference_model() if (self.prefer_references and refs) else None

        shot_rows = [
            {
                "description": getattr(shot, "image_prompt", "") or "",
                "duration_seconds": float(getattr(shot, "duration_seconds", 0) or 0),
                "character_name": getattr(shot, "character_name", None),
                "dialogue_text": getattr(shot, "dialogue_text", None),
            }
            for shot in shots
        ]
        if ref_model:
            intent = h3.intent_from_shots(shot_rows, duration_seconds, subjects=subjects, style=self.style,
                                          language=self.language)
            model = ref_model
        else:
            mode = "fl2va" if last_frame else "i2va"
            intent = h3.intent_from_shots(shot_rows, duration_seconds, mode=mode, style=self.style,
                                          language=self.language)
            model = self.model
        prompt, diag = h3.compile(intent)
        if diag.get("warnings"):
            logger.info("H3 scene prompt warnings: %s", "; ".join(diag["warnings"]))

        tier = tier_defaults_for(model)
        width, height = int(tier.get("width") or 864), int(tier.get("height") or 480)
        out_dir = Path(output_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        req = VideoGenerationRequest(
            model=model,
            prompt=prompt,
            duration_frames=diag["frames"],
            fps=self.fps,
            width=width,
            height=height,
            num_inference_steps=0,
            enhance_prompt=False,
            output_dir=out_dir,
            speed_profile=tier.get("speed_profile") or None,
            first_frame_path=None if ref_model else first_frame,
            last_frame_path=None if ref_model else last_frame,
            ref_images=refs if ref_model else [],
            h3_intent=h3.intent_to_dict(intent),
            language=self.language,
            metadata={"source": "film_crew", "scene_mood": scene_mood or ""},
        )
        gen = get_video_generator()
        result = gen.generate_video(req)
        if not result.success or not result.video_path:
            raise RuntimeError(f"MiniMax H3 scene failed: {result.error or 'no video produced'}")
        shutil.copyfile(resolve_generated_video_path(result, out_dir), output_path)
        return output_path


class Wan22I2VGenerator:
    """Wan 2.2 image-to-video adapter for the Editor's I2VGenerator protocol.

    This is the film pipeline's preferred animator (Layer 2 of the film-orchestrator
    plan). Identity rides ENTIRELY in the LoRA-locked storyboard/keyframe image that
    seeds the animation — Wan 2.2 takes a text prompt (motion guidance) plus that
    init frame. It does NOT apply a LoRA: the Wan GGUF backbone (UnetLoaderGGUF) has
    no LoRA hook (see _build_workflow's "no LoRA hook" skip), so any lora_name passed
    here is currently inert — it's threaded through only for forward-compat if a
    Wan-format LoRA loader is ever added. Lock the character in the keyframe upstream,
    not here. Per-step progress is surfaced automatically by the Layer-1 ws bridge
    inside generate_video. Short clips keep identity stable and VRAM in budget on 16 GB.
    """

    def __init__(self, fps: int = 24):
        self.fps = fps

    def i2v_from_image(
        self, *, image_path: str, prompt: str, loras: list[str],
        duration_seconds: float, output_path: str,
    ) -> str:
        # Clamp to a short clip — long Wan I2V drifts the face and blows 16 GB.
        # generate_video handles Wan's "frames % 8 == 1" alignment internally.
        frames = max(17, min(49, int(round(duration_seconds * self.fps)) or 25))
        out_dir = Path(output_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        gen = get_video_generator()
        req = VideoGenerationRequest(
            model="wan22-14b-i2v",
            prompt=prompt or "",
            duration_frames=frames,
            fps=self.fps,
            enhance_prompt=False,
            output_dir=out_dir,                      # known base → result path resolves
            # NOTE: the Wan GGUF backbone applies NO LoRA (no loader hook — see
            # _build_workflow). Identity comes from the LoRA-locked init frame, not
            # from this. lora_name is passed through inert for forward-compat only.
            lora_name=(loras[0] if loras else None),
            metadata={"image_path": image_path},
        )
        result = gen.generate_video(req)
        if not result.success or not result.video_path:
            raise RuntimeError(f"Wan 2.2 I2V failed: {result.error or 'no video produced'}")
        shutil.copyfile(resolve_generated_video_path(result, out_dir), output_path)
        return output_path
