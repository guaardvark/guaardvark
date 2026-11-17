
import logging
import os
import threading
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import numpy as np

logger = logging.getLogger(__name__)

# GFPGANer finds and masks faces with facexlib's RetinaFace detector and ParseNet
# parser. facexlib downloads either file when it is missing from the folder it is
# told to read, so both are checked here first and the helper is pointed at a
# folder that holds them. The `facexlib-face` entry of the video model registry
# (Manage Video Models) installs them.
FACE_HELPER_FILES = ("detection_Resnet50_Final.pth", "parsing_parsenet.pth")
FACE_HELPER_ENTRY = "facexlib-face"
ESRGAN_FILE = "RealESRGAN_x2plus.pth"

_helper_swap_lock = threading.Lock()

restoration_available = False
gfpgan_import_error = None

try:
    from PIL import Image
    import cv2
    import torch
    # basicsr 1.4.2 still imports torchvision.transforms.functional_tensor, which
    # torchvision 0.17+ removed. Alias it back to functional so the old API resolves.
    import sys as _sys
    from torchvision.transforms import functional as _tv_functional
    _sys.modules.setdefault("torchvision.transforms.functional_tensor", _tv_functional)
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


def _present(path: Path) -> bool:
    from backend.utils.model_file_integrity import file_complete
    return file_complete(path)


def _gfpganer_reading_helper_from(helper_dir: Optional[Path], **kwargs):
    """GFPGANer whose face detector and parser load from ``helper_dir``.

    GFPGANer hands FaceRestoreHelper a fixed model_rootpath ('gfpgan/weights',
    relative to the working directory) and takes no argument to change it, so
    the helper class it builds is swapped for the length of the constructor.
    The swapped helper refuses to start unless both files are in helper_dir,
    so facexlib never reaches for its download.
    """
    import gfpgan.utils as gfpgan_utils

    with _helper_swap_lock:
        helper_cls = gfpgan_utils.FaceRestoreHelper

        def helper(*args, **helper_kwargs):
            if helper_dir is None or not all(_present(helper_dir / f) for f in FACE_HELPER_FILES):
                raise FileNotFoundError(
                    f"face detector and parser ({', '.join(FACE_HELPER_FILES)}) not in {helper_dir}"
                )
            helper_kwargs["model_rootpath"] = str(helper_dir)
            return helper_cls(*args, **helper_kwargs)

        gfpgan_utils.FaceRestoreHelper = helper
        try:
            return gfpgan_utils.GFPGANer(**kwargs)
        finally:
            gfpgan_utils.FaceRestoreHelper = helper_cls


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

        # Why the last load attempt found weights missing (None when they were
        # all present), and the reason last logged, so a batch logs it once.
        self._weights_problem_text = None
        self._logged_weights_problem = None

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

    def _face_helper_dirs(self) -> list:
        """Folders the face detector and parser may load from, in order: where
        the registry's facexlib entry installs them, then GFPGANer's own default
        under the working directory, so copies already there are still used."""
        dirs = []
        try:
            from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, resolve_entry_dir
            dirs.append(resolve_entry_dir(VIDEO_MODEL_REGISTRY[FACE_HELPER_ENTRY]))
        except Exception as e:  # noqa: BLE001 - the working-directory folder is still checked
            logger.debug(f"facexlib registry folder unavailable: {e}")
        dirs.append(Path("gfpgan/weights").resolve())
        return dirs

    def _find_face_helper_dir(self) -> Optional[Path]:
        for folder in self._face_helper_dirs():
            if all(_present(folder / f) for f in FACE_HELPER_FILES):
                return folder
        return None

    def _weights_problem(self) -> Optional[str]:
        """What is missing for a load from this machine's files, or None."""
        problems = []
        local = [f"GFPGANv{self.gfpgan_model_version}.pth", ESRGAN_FILE]
        missing_local = [f for f in local if not _present(self.models_dir / f)]
        if missing_local:
            problems.append(
                f"{' and '.join(missing_local)} not in {self.models_dir}; no Install provides "
                "them, so place the files there"
            )
        if self._find_face_helper_dir() is None:
            install_name = FACE_HELPER_ENTRY
            try:
                from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
                install_name = VIDEO_MODEL_REGISTRY[FACE_HELPER_ENTRY]["name"]
            except Exception:  # noqa: BLE001 - the entry id still names it
                pass
            problems.append(
                f"the face detector and parser ({', '.join(FACE_HELPER_FILES)}) are not "
                f"installed; install '{install_name}' in Manage Video Models"
            )
        if not problems:
            return None
        return (
            "Face restoration skipped: " + "; ".join(problems)
            + ". Face restoration never downloads weights on its own."
        )

    def _load_gfpgan(self) -> bool:
        if not self.service_available:
            return False

        if self._gfpgan is not None:
            return True

        # Checked on every attempt (a few stat calls) so an Install takes effect
        # without a restart; logged once per distinct reason.
        problem = self._weights_problem()
        self._weights_problem_text = problem
        if problem:
            if problem != self._logged_weights_problem:
                logger.warning(problem)
                self._logged_weights_problem = problem
            return False

        try:
            logger.info("Loading GFPGAN model...")

            model_path_esrgan = self.models_dir / ESRGAN_FILE
            model_path_gfpgan = self.models_dir / f"GFPGANv{self.gfpgan_model_version}.pth"

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

            self._gfpgan = _gfpganer_reading_helper_from(
                self._find_face_helper_dir(),
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
                return False, None, {"error": self._weights_problem_text or "Failed to load GFPGAN model"}

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
                return False, None, {"error": self._weights_problem_text or "Failed to load GFPGAN model"}

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
            "models_dir": str(self.models_dir),
            # None when every weight file is on this machine, else what is missing.
            "weights_problem": self._weights_problem(),
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
