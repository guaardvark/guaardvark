"""Model loading and management via spandrel.

Handles model registry, download, loading into VRAM, torch.compile,
and precision control. One model in VRAM at a time (LRU-1).
"""
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.request import urlretrieve

import torch

logger = logging.getLogger("upscaling.model_manager")

MODEL_REGISTRY: List[Dict[str, Any]] = [
    {
        "name": "HAT-L_SRx4",
        "scale": 4,
        "size_mb": 159,
        "url": "https://huggingface.co/jaideepsingh/upscale_models/resolve/main/HAT/HAT-L_SRx4_ImageNet-pretrain.pth",
    },
    {
        "name": "RealESRGAN_x4plus",
        "scale": 4,
        "size_mb": 64,
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
    },
    {
        "name": "RealESRGAN_x2plus",
        "scale": 2,
        "size_mb": 64,
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
    },
    {
        "name": "RealESRGAN_x4plus_anime_6B",
        "scale": 4,
        "size_mb": 17,
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
    },
    {
        "name": "realesr-animevideov3",
        "scale": 4,
        "size_mb": 6,
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth",
    },
    {
        "name": "realesr-general-x4v3",
        "scale": 4,
        "size_mb": 6,
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
    },
    {
        "name": "4x-UltraSharp",
        "scale": 4,
        "size_mb": 67,
        "url": "https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth",
    },
    {
        "name": "4x_NMKD-Superscale-SP_178000_G",
        "scale": 4,
        "size_mb": 67,
        "url": "https://huggingface.co/gemasai/4x_NMKD-Superscale-SP_178000_G/resolve/main/4x_NMKD-Superscale-SP_178000_G.pth",
    },
    {
        "name": "4x_foolhardy_Remacri",
        "scale": 4,
        "size_mb": 67,
        "url": "https://huggingface.co/FacehugmanIII/4x_foolhardy_Remacri/resolve/main/4x_foolhardy_Remacri.pth",
    },
]


def _registry_entry(name: str) -> Optional[Dict[str, Any]]:
    """Find a model in the registry by name."""
    for entry in MODEL_REGISTRY:
        if entry["name"] == name:
            return entry
    return None


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
        os.makedirs(models_dir, exist_ok=True)

    def _model_path(self, name: str) -> str:
        return os.path.join(self.models_dir, f"{name}.pth")

    def is_downloaded(self, name: str) -> bool:
        return os.path.isfile(self._model_path(name))

    def list_models(self) -> Dict[str, List[Dict[str, Any]]]:
        """List downloaded and available (not yet downloaded) models."""
        downloaded = []
        available = []
        for entry in MODEL_REGISTRY:
            info = {
                "name": entry["name"],
                "scale": entry["scale"],
                "size_mb": entry["size_mb"],
            }
            if self.is_downloaded(entry["name"]):
                info["compiled"] = (
                    self.compile_enabled and self.current_model_name == entry["name"]
                )
                downloaded.append(info)
            else:
                info["url"] = entry["url"]
                available.append(info)
        # Check for extra .pth files not in registry (user-dropped models)
        for f in os.listdir(self.models_dir):
            if f.endswith(".pth"):
                name = f[:-4]
                if not _registry_entry(name):
                    size_mb = round(os.path.getsize(os.path.join(self.models_dir, f)) / (1024 * 1024))
                    downloaded.append({
                        "name": name,
                        "scale": None,
                        "size_mb": size_mb,
                        "compiled": self.compile_enabled and self.current_model_name == name,
                    })
        return {"downloaded": downloaded, "available": available}

    def download_model(self, name: str) -> str:
        """Download a model from the registry. Returns local path."""
        entry = _registry_entry(name)
        if not entry:
            raise ValueError(f"Unknown model: {name}. Drop custom .pth files into {self.models_dir}")
        dest = self._model_path(name)
        if os.path.isfile(dest):
            logger.info(f"Model {name} already downloaded at {dest}")
            return dest
        logger.info(f"Downloading {name} from {entry['url']}...")
        urlretrieve(entry["url"], dest)
        logger.info(f"Downloaded {name} to {dest}")
        return dest

    def load_model(self, name: str) -> None:
        """Load a model into VRAM via spandrel. Evicts previous model."""
        import spandrel

        model_path = self._model_path(name)
        if not os.path.isfile(model_path):
            entry = _registry_entry(name)
            if entry:
                self.download_model(name)
            else:
                raise FileNotFoundError(f"Model file not found: {model_path}")

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
        if self.precision == "bf16" and torch.cuda.is_available() and torch.cuda.is_bf16_supported():
            model = model.bfloat16()
        elif self.precision == "fp16":
            model = model.half()

        model = model.to(device).eval()

        # torch.compile
        if self.compile_enabled and hasattr(torch, "compile"):
            logger.info(f"Compiling model {name} with torch.compile(mode='default')...")
            model = torch.compile(model, mode="default")

        self._model = model
        self.current_model_name = name
        self.current_scale = getattr(model_descriptor, "scale", None)
        logger.info(f"Model {name} loaded (scale={self.current_scale}, precision={self.precision})")

    def get_model(self):
        """Return the currently loaded model. Raises if none loaded."""
        if self._model is None:
            raise RuntimeError("No model loaded. Call load_model() first.")
        return self._model

    @property
    def scale(self) -> Optional[int]:
        return self.current_scale

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
