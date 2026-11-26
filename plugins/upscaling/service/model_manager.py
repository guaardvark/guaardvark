"""Model loading and management via spandrel.

Handles model registry, download, loading into VRAM, torch.compile,
and precision control. One model in VRAM at a time (LRU-1).

Weights arrive only through ``download_model`` (the Install button, POST
/models/download). Loading a model that is not on disk is refused.
"""
import importlib.util
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.request import urlretrieve

import torch

from service.model_file_integrity import MISSING, expect_size, file_problem

logger = logging.getLogger("upscaling.model_manager")


def _contained(root, *parts) -> Path:
    """Join ``parts`` under ``root``; raise ValueError if the result leaves root.

    Same contract as backend/utils/path_guard.py (sidecars do not import the
    backend): lexical normalisation, then a prefix check that must end on a
    path boundary.
    """
    root_abs = os.path.abspath(os.fspath(root))
    candidate = os.path.abspath(os.path.join(root_abs, *(os.fspath(p) for p in parts)))
    if candidate.startswith(root_abs) and (
        candidate == root_abs
        or root_abs.endswith(os.sep)
        or candidate[len(root_abs)] == os.sep
    ):
        return Path(candidate)
    raise ValueError(f"{candidate!r} is outside {root_abs!r}")

# size_bytes: the file's published size (Content-Length of its url, read
# 2026-11-23), compared against the copy on disk.
# vram_mb: the most the upscaling service holds on the GPU while it runs a job
# with the model (CUDA context included), which the backend books before it
# submits one; vram_measured says what the figure came from. A model with no
# figure is booked at UNMEASURED_VRAM_MB.
# precisions: the only precisions the model may be loaded at, for one that is
# wrong at some of them. A model without the field runs at whatever spandrel
# says its architecture supports (allowed_precisions).
MODEL_REGISTRY: List[Dict[str, Any]] = [
    {
        "name": "HAT-L_SRx4",
        "scale": 4,
        "size_mb": 159,
        "size_bytes": 165774123,
        # fp16 returns NaN for every pixel, which saves as a black picture.
        # One 1280x720 frame at tile 400 on a 16 GB consumer GPU, 2026-11-25:
        # fp16 all NaN; bf16 clean in 10.4 s; fp32 clean in 19.5 s.
        "precisions": ("bf16", "fp32"),
        "url": "https://huggingface.co/jaideepsingh/upscale_models/resolve/main/HAT/HAT-L_SRx4_ImageNet-pretrain.pth",
    },
    {
        "name": "RealESRGAN_x4plus",
        "scale": 4,
        "size_mb": 64,
        "size_bytes": 67040989,
        "vram_mb": 2200,
        "vram_measured": "not measured (not installed where measured); RRDBNet with 23 blocks like 4x-UltraSharp, so its figure",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
    },
    {
        "name": "RealESRGAN_x2plus",
        "scale": 2,
        "size_mb": 64,
        "size_bytes": 67061725,
        "vram_mb": 2200,
        "vram_measured": "not measured (not installed where measured); the x4plus RRDBNet on a pixel-unshuffled input, so at most 4x-UltraSharp's figure",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
    },
    {
        "name": "RealESRGAN_x4plus_anime_6B",
        "scale": 4,
        "size_mb": 17,
        "size_bytes": 17938799,
        "vram_mb": 2200,
        "vram_measured": "not measured (not installed where measured); RRDBNet with 6 blocks, an upper bound from 4x-UltraSharp",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
    },
    {
        "name": "realesr-animevideov3",
        "scale": 4,
        "size_mb": 6,
        "size_bytes": 2504012,
        "vram_mb": 800,
        "vram_measured": "not measured (not installed where measured); RealESRGAN Compact like realesr-general-x4v3 with fewer layers, so at most its figure",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth",
    },
    {
        "name": "realesr-general-x4v3",
        "scale": 4,
        "size_mb": 6,
        "size_bytes": 4885111,
        "vram_mb": 800,
        "vram_measured": "752 MiB held after one 1024x1024 still (service total, CUDA context included; the job ended between samples); fp16, tile 400, a 16 GB consumer GPU, 2026-10-12",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
    },
    {
        "name": "4x-UltraSharp",
        "scale": 4,
        "size_mb": 67,
        "size_bytes": 66961958,
        "vram_mb": 2200,
        "vram_measured": "2142 MiB peak (service total, CUDA context included) over ten 1280x720 frames and 2140 on one 1024x1024 still; fp16, tile 400, a 16 GB consumer GPU, 2026-10-12",
        "url": "https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth",
    },
    {
        "name": "4x_NMKD-Superscale-SP_178000_G",
        "scale": 4,
        "size_mb": 67,
        "size_bytes": 66958607,
        "vram_mb": 2200,
        "vram_measured": "not measured; ESRGAN with the same 16,697,987 parameters as 4x-UltraSharp (checked with spandrel), so the same figure",
        "url": "https://huggingface.co/gemasai/4x_NMKD-Superscale-SP_178000_G/resolve/main/4x_NMKD-Superscale-SP_178000_G.pth",
    },
    {
        "name": "4x_foolhardy_Remacri",
        "scale": 4,
        "size_mb": 67,
        "size_bytes": 67025055,
        "vram_mb": 2200,
        "vram_measured": "not measured; ESRGAN with the same 16,697,987 parameters as 4x-UltraSharp (checked with spandrel), so the same figure",
        "url": "https://huggingface.co/FacehugmanIII/4x_foolhardy_Remacri/resolve/main/4x_foolhardy_Remacri.pth",
    },
]


# The largest measured figure above; HAT-L (a transformer, not installed where
# the figures were measured) and user-dropped models may need more.
UNMEASURED_VRAM_MB = 2200


def vram_estimate_mb(name: str) -> int:
    """What a job with ``name`` is booked at: its measured figure, else UNMEASURED_VRAM_MB."""
    entry = _registry_entry(name)
    return int((entry or {}).get("vram_mb") or UNMEASURED_VRAM_MB)


def _registry_entry(name: str) -> Optional[Dict[str, Any]]:
    """Find a model in the registry by name."""
    for entry in MODEL_REGISTRY:
        if entry["name"] == name:
            return entry
    return None


def allowed_precisions(name: str, descriptor: Any = None) -> Tuple[str, ...]:
    """The precisions ``name`` may be loaded at: its registry ``precisions``
    when declared, else what spandrel says the architecture supports. fp32 is
    always among them."""
    declared = (_registry_entry(name) or {}).get("precisions")
    if declared:
        return tuple(declared)
    allowed = ["fp32"]
    if getattr(descriptor, "supports_bfloat16", True):
        allowed.insert(0, "bf16")
    if getattr(descriptor, "supports_half", True):
        allowed.insert(0, "fp16")
    return tuple(allowed)


def resolve_precision(configured: str, allowed: Sequence[str], bf16_available: bool) -> str:
    """The precision a model is loaded at: the configured one when the model
    and the GPU can run it, else bf16 when they can, else fp32."""
    usable = [p for p in allowed if p != "bf16" or bf16_available]
    if configured in usable:
        return configured
    if configured in ("fp16", "bf16") and "bf16" in usable:
        return "bf16"
    return "fp32"


# Face Enhancement runs GFPGAN v1.4 over the upscaled picture. GFPGAN finds and
# masks faces with facexlib's RetinaFace detector and ParseNet parser, and both
# libraries download a file they are pointed at and do not find. The Install
# (download_model(FACE_ENHANCE_MODEL)) is the only thing that fetches these
# files; Face Enhancement loads them from disk or is refused.
# size_bytes: the file's published size (Content-Length of its url, read
# 2026-11-23). GFPGANv1.4.pth carries none: its size_mb is approximate and was
# not checked against a download.
FACE_ENHANCE_MODEL = "GFPGANv1.4"
GFPGAN_FILE = "GFPGANv1.4.pth"
FACE_ENHANCE_FILES: List[Dict[str, Any]] = [
    {
        "file": GFPGAN_FILE,
        "size_mb": 332,
        "url": "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
    },
    {
        "file": "detection_Resnet50_Final.pth",
        "size_mb": 104,
        "size_bytes": 109497761,
        "url": "https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth",
    },
    {
        "file": "parsing_parsenet.pth",
        "size_mb": 81,
        "size_bytes": 85331193,
        "url": "https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth",
    },
]
FACE_HELPER_FILES = tuple(s["file"] for s in FACE_ENHANCE_FILES if s["file"] != GFPGAN_FILE)

# Where the Install puts the three files.
FACE_ENHANCE_DIR = Path(__file__).resolve().parent.parent / "models" / "face_enhance"


def _package_dir(package: str) -> Optional[Path]:
    """Folder of an installed package, found without importing it."""
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def _whole(folder: Path, spec: Dict[str, Any]) -> bool:
    path = folder / spec["file"]
    if spec.get("size_bytes"):
        expect_size(str(path), spec["size_bytes"])
    return file_problem(str(path)) is None


def face_helper_dir() -> Optional[Path]:
    """The folder the face detector and parser load from: the Install's, else
    facexlib's own weights folder (where Manage Video Models puts the same two
    files). None when neither holds both whole."""
    helpers = [s for s in FACE_ENHANCE_FILES if s["file"] in FACE_HELPER_FILES]
    folders = [FACE_ENHANCE_DIR]
    facexlib_dir = _package_dir("facexlib")
    if facexlib_dir is not None:
        folders.append(facexlib_dir / "weights")
    for folder in folders:
        if all(_whole(folder, s) for s in helpers):
            return folder
    return None


def face_enhance_missing() -> List[Dict[str, Any]]:
    """The Face Enhancement files the Install still has to fetch."""
    have_helpers = face_helper_dir() is not None
    return [
        s for s in FACE_ENHANCE_FILES
        if not (have_helpers and s["file"] in FACE_HELPER_FILES)
        and not _whole(FACE_ENHANCE_DIR, s)
    ]


def face_enhance_status(supported: bool) -> Dict[str, Any]:
    """Face Enhancement's state for the Upscaling page. ``supported``: whether
    gfpgan loads in this service; where it does not, the weights would sit
    unused and no Install is offered."""
    missing = face_enhance_missing()
    return {
        "name": FACE_ENHANCE_MODEL,
        "supported": supported,
        "installed": supported and not missing,
        "missing": [s["file"] for s in missing],
        "size_mb": sum(s["size_mb"] for s in missing),
    }


def missing_face_enhance_message() -> Optional[str]:
    """Why Face Enhancement cannot load its weights, or None when all are on disk."""
    missing = face_enhance_missing()
    if not missing:
        return None
    return (
        f"Face Enhancement is not installed (missing {', '.join(s['file'] for s in missing)}). "
        "Install it from the Upscaling page (the Install button beside Face "
        "Enhancement), then try again. Upscaling never downloads a model on its own."
    )


class ModelNotInstalledError(FileNotFoundError):
    """The requested model's weights are not on disk. The message names the
    model and what to do about it."""


class ModelManager:
    """Manages upscaling model lifecycle: download, load, compile, evict."""

    def __init__(self, models_dir: str, precision: str = "bf16", compile_enabled: bool = True):
        self.models_dir = models_dir
        self.precision = precision
        self.compile_enabled = compile_enabled
        self._model = None
        self._model_descriptor = None
        self.current_model_name: Optional[str] = None
        self.current_scale: Optional[int] = None
        # What the loaded model runs at; differs from ``precision`` when the
        # model cannot run at the configured one. Frames are cast to this.
        self.current_precision: str = precision
        os.makedirs(models_dir, exist_ok=True)

    def _model_path(self, name: str) -> str:
        path = str(_contained(self.models_dir, f"{name}.pth"))
        # A legacy pickle .pth has no length of its own; the registry's
        # published size lets the shared check tell a cut-short copy.
        entry = next((m for m in MODEL_REGISTRY if m["name"] == name), None)
        if entry and entry.get("size_bytes"):
            expect_size(path, entry["size_bytes"])
        return path

    def is_downloaded(self, name: str) -> bool:
        """True when the model's file is on disk and whole."""
        return file_problem(self._model_path(name)) is None

    def incomplete_reason(self, name: str) -> Optional[str]:
        """What is wrong with a model file that is on disk but not whole; None
        when it is whole or not there at all."""
        problem = file_problem(self._model_path(name))
        return None if problem in (None, MISSING) else problem

    def missing_model_message(self, name: str) -> Optional[str]:
        """Why ``name`` cannot be used, or None when its weights are on disk.

        Raises ValueError for a name that would resolve outside the models dir.
        """
        if self.is_downloaded(name):
            return None
        reason = self.incomplete_reason(name)
        if reason and _registry_entry(name):
            return (
                f"Upscaling model '{name}' is not installed: the file on disk is "
                f"incomplete ({reason}). Install it again from the Upscaling page "
                "(Manage Upscaling Models), which replaces the file, then try again. "
                "Upscaling never downloads a model on its own."
            )
        if reason:
            return (
                f"Upscaling model '{name}' cannot be used: {name}.pth in "
                f"{self.models_dir} is incomplete ({reason}). Replace the file "
                "with a whole copy, or pick another installed model."
            )
        if _registry_entry(name):
            return (
                f"Upscaling model '{name}' is not installed. Install it from the "
                "Upscaling page (Manage Upscaling Models), then try again. "
                "Upscaling never downloads a model on its own."
            )
        return (
            f"Upscaling model '{name}' was not found: it is not one the Upscaling "
            f"page installs and there is no {name}.pth in {self.models_dir}. "
            "Pick an installed model."
        )

    def list_models(self) -> Dict[str, List[Dict[str, Any]]]:
        """List downloaded and available (not yet downloaded) models.

        A registry model whose file is on disk but not whole is listed as
        available with ``incomplete`` set to the reason. A user-dropped file
        that is not whole is left out.
        """
        downloaded = []
        available = []
        for entry in MODEL_REGISTRY:
            info = {
                "name": entry["name"],
                "scale": entry["scale"],
                "size_mb": entry["size_mb"],
                "vram_mb": vram_estimate_mb(entry["name"]),
            }
            if self.is_downloaded(entry["name"]):
                info["compiled"] = (
                    self.compile_enabled and self.current_model_name == entry["name"]
                )
                downloaded.append(info)
            else:
                info["url"] = entry["url"]
                reason = self.incomplete_reason(entry["name"])
                if reason:
                    info["incomplete"] = reason
                available.append(info)
        # Check for extra .pth files not in registry (user-dropped models)
        for f in os.listdir(self.models_dir):
            if f.endswith(".pth"):
                name = f[:-4]
                if not _registry_entry(name) and self.is_downloaded(name):
                    size_mb = round(os.path.getsize(os.path.join(self.models_dir, f)) / (1024 * 1024))
                    downloaded.append({
                        "name": name,
                        "scale": None,
                        "size_mb": size_mb,
                        "vram_mb": UNMEASURED_VRAM_MB,
                        "compiled": self.compile_enabled and self.current_model_name == name,
                    })
        return {"downloaded": downloaded, "available": available}

    def download_model(self, name: str) -> str:
        """Download a model from the registry (the Install route only). Returns local path."""
        if name == FACE_ENHANCE_MODEL:
            return self.download_face_enhance()
        entry = _registry_entry(name)
        if not entry:
            raise ValueError(f"Unknown model: {name}. Drop custom .pth files into {self.models_dir}")
        dest = self._model_path(name)
        if self.is_downloaded(name):
            logger.info(f"Model {name} already downloaded at {dest}")
            return dest
        logger.info(f"Downloading {name} from {entry['url']}...")
        # An interrupted fetch must not leave its front part under the final
        # name, where it would read as an installed model.
        part = dest + ".part"
        urlretrieve(entry["url"], part)
        os.replace(part, dest)
        logger.info(f"Downloaded {name} to {dest}")
        return dest

    def download_face_enhance(self) -> str:
        """Fetch the Face Enhancement files that are missing (the Install route
        only). Returns their folder."""
        for spec in face_enhance_missing():
            dest = FACE_ENHANCE_DIR / spec["file"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Downloading {spec['file']} from {spec['url']}...")
            # A partial file must never read as an installed one.
            part = str(dest) + ".part"
            urlretrieve(spec["url"], part)
            os.replace(part, dest)
            logger.info(f"Downloaded {spec['file']} to {dest}")
        return str(FACE_ENHANCE_DIR)

    def load_model(self, name: str) -> None:
        """Load a model into VRAM via spandrel. Evicts previous model.

        Raises ModelNotInstalledError when the weights are not on disk; it
        never downloads them.
        """
        model_path = self._model_path(name)
        missing = self.missing_model_message(name)
        if missing:
            raise ModelNotInstalledError(missing)

        import spandrel

        # Evict current model
        if self._model is not None:
            del self._model
            del self._model_descriptor
            self._model = None
            self._model_descriptor = None
            torch.cuda.empty_cache()
            logger.info(f"Evicted model {self.current_model_name}")

        logger.info(f"Loading model {name} from {model_path}...")
        model_descriptor = spandrel.ModelLoader().load_from_file(model_path)
        self._model_descriptor = model_descriptor
        model = model_descriptor.model

        # Precision
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        bf16_available = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
        precision = resolve_precision(
            self.precision, allowed_precisions(name, model_descriptor), bf16_available
        )
        if precision != self.precision:
            logger.info(f"Model {name} does not run at {self.precision}; loading it at {precision}")
        if precision == "bf16":
            model = model.bfloat16()
        elif precision == "fp16":
            model = model.half()

        model = model.to(device).eval()

        # torch.compile
        if self.compile_enabled and hasattr(torch, "compile"):
            logger.info(f"Compiling model {name} with torch.compile(mode='default')...")
            model = torch.compile(model, mode="default")

        self._model = model
        self.current_model_name = name
        self.current_scale = getattr(model_descriptor, "scale", None)
        self.current_precision = precision
        logger.info(f"Model {name} loaded (scale={self.current_scale}, precision={precision})")

    def get_model(self):
        """Return the currently loaded model. Raises if none loaded."""
        if self._model is None:
            raise RuntimeError("No model loaded. Call load_model() first.")
        return self._model

    @property
    def scale(self) -> Optional[int]:
        return self.current_scale

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def unload(self):
        """Unload current model and free VRAM."""
        if self._model is not None:
            del self._model
            del self._model_descriptor
            self._model = None
            self._model_descriptor = None
            self.current_model_name = None
            self.current_scale = None
            torch.cuda.empty_cache()
