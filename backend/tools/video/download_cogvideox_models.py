#!/usr/bin/env python3
"""
CogVideoX Model Download Script
Downloads CogVideoX models to the data/models/video_diffusion folder.

Available models:
- cogvideox-2b: 6s videos, fast (~12GB VRAM)
- cogvideox-5b: 6s videos, best quality (~16GB VRAM)
- cogvideox-5b-i2v: Image-to-video, 6s (~16GB VRAM)
"""

import logging
import sys
import gc
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Try to import required dependencies
try:
    import torch

    torch_available = True
except ImportError:
    torch_available = False
    logger.error("PyTorch not available. Please install PyTorch first.")
    sys.exit(1)

try:
    from diffusers import CogVideoXPipeline, CogVideoXImageToVideoPipeline

    cogvideox_available = True
except ImportError:
    cogvideox_available = False
    logger.error(
        "CogVideoX pipelines not available. Please install diffusers with CogVideoX support."
    )
    logger.error("Try: pip install diffusers --upgrade")
    sys.exit(1)


def get_project_root():
    """Get the project root directory."""
    # This script is in backend/tools/video/
    script_dir = Path(__file__).parent.absolute()
    return script_dir.parent.parent.parent


def get_models_dir():
    """Get the video diffusion models directory."""
    project_root = get_project_root()
    models_dir = project_root / "data" / "models" / "video_diffusion"
    models_dir.mkdir(parents=True, exist_ok=True)
    return models_dir


def check_model_exists(model_repo: str, models_dir: Path) -> bool:
    """Check if a CogVideoX model already exists in the cache."""
    # HuggingFace cache format: models--ORG--MODEL_NAME
    cache_name = model_repo.replace("/", "--")
    cache_path = models_dir / f"models--{cache_name}"

    # Check if the cache directory exists and has content
    if cache_path.exists():
        # Check if it has blobs (model files)
        blobs_dir = cache_path / "blobs"
        if blobs_dir.exists() and any(blobs_dir.iterdir()):
            return True

    return False


def download_model(
    model_key: str, model_repo: str, model_type: str, models_dir: Path
) -> bool:
    """Download a specific CogVideoX model."""
    try:
        logger.info(f"Downloading CogVideoX model: {model_key} ({model_repo})")

        # Determine device and dtype
        device = "cpu"
        dtype = torch.float32
        if torch.cuda.is_available():
            device = "cuda"
            dtype = torch.float16
            logger.info(f"Using CUDA with float16")
        else:
            logger.info(f"Using CPU with float32 (this will be slower)")

        # Select appropriate pipeline class based on model type
        if model_type == "image2video":
            PipelineClass = CogVideoXImageToVideoPipeline
        else:
            PipelineClass = CogVideoXPipeline

        logger.info(f"Downloading model files from HuggingFace...")
        logger.info(
            f"This may take several minutes depending on your internet connection."
        )

        # Download using from_pretrained with cache_dir
        # This will download all model files to the specified cache directory
        pipeline = PipelineClass.from_pretrained(
            model_repo,
            torch_dtype=dtype,
            cache_dir=str(models_dir),
        )

        # Clean up to free memory
        del pipeline
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info(f"Successfully downloaded {model_key}")
        return True

    except Exception as e:
        logger.error(f"Failed to download {model_key}: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        import traceback

        logger.debug(traceback.format_exc())
        return False


def main():
    """Main function to download CogVideoX models."""
    models_dir = get_models_dir()

    logger.info("CogVideoX Model Download Script")
    logger.info("=" * 60)
    logger.info(f"Models will be downloaded to: {models_dir}")
    logger.info("")

    # Model configurations matching offline_video_generator.py
    models_to_download = [
        {
            "key": "cogvideox-2b",
            "repo": "THUDM/CogVideoX-2b",
            "type": "text2video",
            "description": "6s videos, fast (~12GB VRAM)",
        },
        {
            "key": "cogvideox-5b",
            "repo": "THUDM/CogVideoX-5b",
            "type": "text2video",
            "description": "6s videos, best quality (~16GB VRAM)",
        },
        {
            "key": "cogvideox-5b-i2v",
            "repo": "THUDM/CogVideoX-5b-I2V",
            "type": "image2video",
            "description": "Image-to-video, 6s (~16GB VRAM)",
        },
    ]

    downloaded_count = 0
    skipped_count = 0
    failed_count = 0

    for model_config in models_to_download:
        model_key = model_config["key"]
        model_repo = model_config["repo"]
        model_type = model_config["type"]
        description = model_config["description"]

        logger.info(f"\nProcessing: {model_key} - {description}")

        if check_model_exists(model_repo, models_dir):
            logger.info(f"Model {model_key} already exists, skipping download")
            skipped_count += 1
            continue

        if download_model(model_key, model_repo, model_type, models_dir):
            logger.info(f"Successfully downloaded {model_key}")
            downloaded_count += 1
        else:
            logger.error(f"Failed to download {model_key}")
            failed_count += 1

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("DOWNLOAD SUMMARY:")
    logger.info(f"Downloaded: {downloaded_count}")
    logger.info(f"Skipped (already exists): {skipped_count}")
    logger.info(f"Failed: {failed_count}")

    if downloaded_count > 0:
        logger.info(f"\nSuccessfully downloaded {downloaded_count} CogVideoX model(s)!")
        logger.info("Models are now available for video generation.")

    if failed_count > 0:
        logger.warning(f"\n{failed_count} model(s) failed to download.")
        logger.info("You can run this script again to retry failed downloads.")
        logger.info("Make sure you have:")
        logger.info("  - Stable internet connection")
        logger.info("  - Sufficient disk space (each model is several GB)")
        logger.info("  - Updated diffusers library: pip install diffusers --upgrade")

    logger.info("\nMODEL INFORMATION:")
    logger.info("cogvideox-2b: Recommended for 12GB+ VRAM GPUs")
    logger.info("cogvideox-5b: Best quality, requires 16GB+ VRAM")
    logger.info("cogvideox-5b-i2v: Image-to-video model, requires 16GB+ VRAM")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
