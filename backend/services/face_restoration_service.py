
import logging
import os
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import numpy as np

logger = logging.getLogger(__name__)

restoration_available = False
gfpgan_import_error = None

try:
    from PIL import Image
    import cv2
    import torch
    try:
        from gfpgan import GFPGANer
        from realesrgan import RealESRGANer
        from basicsr.archs.rrdbnet_arch import RRDBNet
        restoration_available = True
        logger.info("Face restoration dependencies loaded successfully")
    except Exception as import_err:
        restoration_available = False
        gfpgan_import_error = str(import_err)
        logger.warning(f"GFPGAN import failed: {import_err}")
except ImportError as e:
    restoration_available = False
    gfpgan_import_error = str(e)
    logger.warning(f"Face restoration dependencies not available: {e}")

try:
    from backend.config import CACHE_DIR
    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"


class FaceRestorationService:

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent
        self.models_dir = project_root / "data" / "models" / "face_restoration"
        self.models_dir.mkdir(parents=True, exist_ok=True)

        try:
            import torch
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            self._device = "cpu"

        self._gfpgan = None
        self._upsampler = None
        self._import_error = gfpgan_import_error

        self.service_available = self._check_runtime_availability()

        self.gfpgan_model_version = "1.4"
        self.upscale_factor = 2

        logger.info(f"FaceRestorationService initialized - Device: {self._device}, Available: {self.service_available}")

    def _check_runtime_availability(self) -> bool:
        if not restoration_available:
            return False
        
        try:
            from gfpgan import GFPGANer
            return True
        except Exception as e:
            logger.warning(f"GFPGAN runtime check failed: {e}")
            return False

    def _load_gfpgan(self) -> bool:
        if not self.service_available:
            return False

        if self._gfpgan is not None:
            return True

        try:
            logger.info("Loading GFPGAN model...")

            model_path_esrgan = self.models_dir / "RealESRGAN_x2plus.pth"

            if not model_path_esrgan.exists():
                logger.info("RealESRGAN model not found, GFPGAN will download it automatically")

            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=2)
            self._upsampler = RealESRGANer(
                scale=2,
                model_path=str(model_path_esrgan),
                model=model,
                tile=400,
                tile_pad=10,
                pre_pad=0,
                half=True if self._device == "cuda" else False,
                device=self._device
            )

            model_path_gfpgan = self.models_dir / f"GFPGANv{self.gfpgan_model_version}.pth"

            self._gfpgan = GFPGANer(
                model_path=str(model_path_gfpgan),
                upscale=self.upscale_factor,
                arch='clean',
                channel_multiplier=2,
                bg_upsampler=self._upsampler,
                device=self._device
            )

            logger.info("GFPGAN model loaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to load GFPGAN model: {e}")
            self._gfpgan = None
            return False

    def restore_face(self,
                     image_path: str,
                     output_path: Optional[str] = None,
                     face_upsample: bool = True,
                     background_enhance: bool = True,
                     weight: float = 0.5) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        if not self.service_available:
            return False, None, {"error": "Face restoration service not available"}

        try:
            if not self._load_gfpgan():
                return False, None, {"error": "Failed to load GFPGAN model"}

            logger.info(f"Restoring faces in: {image_path}")
            input_img = cv2.imread(image_path, cv2.IMREAD_COLOR)

            if input_img is None:
                return False, None, {"error": "Failed to load input image"}

            cropped_faces, restored_faces, restored_img = self._gfpgan.enhance(
                input_img,
                has_aligned=False,
                only_center_face=False,
                paste_back=True,
                weight=weight
            )

            if output_path is None:
                input_path = Path(image_path)
                output_path = str(input_path.parent / f"{input_path.stem}_restored{input_path.suffix}")

            cv2.imwrite(output_path, restored_img)

            metadata = {
                "faces_detected": len(cropped_faces) if cropped_faces else 0,
                "face_upsample": face_upsample,
                "background_enhance": background_enhance,
                "weight": weight,
                "upscale_factor": self.upscale_factor,
                "model_version": self.gfpgan_model_version,
                "original_size": input_img.shape[:2],
                "restored_size": restored_img.shape[:2]
            }

            logger.info(f"Face restoration complete: {metadata['faces_detected']} faces restored")
            return True, output_path, metadata

        except Exception as e:
            logger.error(f"Face restoration failed: {e}")
            return False, None, {"error": str(e)}

    def restore_face_from_pil(self,
                             image: Image.Image,
                             weight: float = 0.5) -> Tuple[bool, Optional[Image.Image], Dict[str, Any]]:
        if not self.service_available:
            return False, None, {"error": "Face restoration service not available"}

        try:
            if not self._load_gfpgan():
                return False, None, {"error": "Failed to load GFPGAN model"}

            input_img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

            cropped_faces, restored_faces, restored_img = self._gfpgan.enhance(
                input_img,
                has_aligned=False,
                only_center_face=False,
                paste_back=True,
                weight=weight
            )

            restored_pil = Image.fromarray(cv2.cvtColor(restored_img, cv2.COLOR_BGR2RGB))

            metadata = {
                "faces_detected": len(cropped_faces) if cropped_faces else 0,
                "weight": weight,
                "upscale_factor": self.upscale_factor,
                "model_version": self.gfpgan_model_version
            }

            logger.info(f"Face restoration complete: {metadata['faces_detected']} faces restored")
            return True, restored_pil, metadata

        except Exception as e:
            logger.error(f"Face restoration failed: {e}")
            return False, None, {"error": str(e)}

    def batch_restore_faces(self,
                           image_paths: list,
                           output_dir: Optional[str] = None,
                           weight: float = 0.5) -> Dict[str, Any]:
        results = {
            "total": len(image_paths),
            "successful": 0,
            "failed": 0,
            "images": []
        }

        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)

        for image_path in image_paths:
            output_path = None
            if output_dir:
                filename = Path(image_path).name
                output_path = str(Path(output_dir) / f"restored_{filename}")

            success, restored_path, metadata = self.restore_face(
                image_path=image_path,
                output_path=output_path,
                weight=weight
            )

            if success:
                results["successful"] += 1
            else:
                results["failed"] += 1

            results["images"].append({
                "input_path": image_path,
                "output_path": restored_path,
                "success": success,
                "metadata": metadata
            })

        logger.info(f"Batch restoration complete: {results['successful']}/{results['total']} successful")
        return results

    def get_service_status(self) -> Dict[str, Any]:
        try:
            import torch
            cuda_available = torch.cuda.is_available()
        except ImportError:
            cuda_available = False

        status = {
            "service_available": self.service_available,
            "device": self._device,
            "cuda_available": cuda_available,
            "gfpgan_loaded": self._gfpgan is not None,
            "model_version": self.gfpgan_model_version,
            "upscale_factor": self.upscale_factor,
            "models_dir": str(self.models_dir)
        }
        
        if self._import_error:
            status["import_error"] = self._import_error
        
        return status


_restoration_service = None

def get_face_restoration_service() -> FaceRestorationService:
    global _restoration_service
    if _restoration_service is None:
        _restoration_service = FaceRestorationService()
    return _restoration_service
