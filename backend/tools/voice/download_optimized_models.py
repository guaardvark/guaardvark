#!/usr/bin/env python3
"""
Whisper Model Download Script for Performance Optimization
Downloads the optimized Whisper models for better voice chat performance.
"""

import os
import subprocess
import sys
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_script_dir():
    """Get the directory containing this script."""
    return Path(__file__).parent.absolute()


def download_model(model_name, models_dir):
    """Download a specific Whisper model."""
    try:
        download_script = (
            models_dir / "whisper.cpp" / "models" / "download-ggml-model.sh"
        )

        if not download_script.exists():
            logger.error(f"Download script not found: {download_script}")
            return False

        logger.info(f"Downloading Whisper model: {model_name}")

        cmd = ["bash", str(download_script), model_name]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes timeout
            cwd=str(models_dir / "whisper.cpp" / "models"),
        )

        if result.returncode == 0:
            logger.info(f"Successfully downloaded: {model_name}")
            return True
        else:
            logger.error(f"Failed to download {model_name}: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout downloading {model_name}")
        return False
    except Exception as e:
        logger.error(f"Error downloading {model_name}: {e}")
        return False


def check_model_exists(model_name, models_dir):
    """Check if a model file already exists."""
    model_path = models_dir / "whisper.cpp" / "models" / f"ggml-{model_name}.bin"
    return model_path.exists()


def main():
    """Main function to download optimized models."""
    script_dir = get_script_dir()
    models_dir = script_dir

    logger.info("Whisper Model Download Script - Performance Optimization")
    logger.info("=" * 60)

    # Models to download for optimal performance
    # Order by importance: tiny.en (fastest), tiny (fast), base (balanced)
    models_to_download = [
        ("tiny.en", "Tiny English (Fastest - Recommended for voice chat)"),
        ("tiny", "Tiny (Fast - Good for short audio)"),
        ("base", "Base (Balanced - Current default)"),
        ("small", "Small (Accurate - For longer audio)"),
    ]

    downloaded_count = 0
    skipped_count = 0
    failed_count = 0

    for model_name, description in models_to_download:
        logger.info(f"\nProcessing: {model_name} - {description}")

        if check_model_exists(model_name, models_dir):
            logger.info(f"✓ Model {model_name} already exists, skipping download")
            skipped_count += 1
            continue

        if download_model(model_name, models_dir):
            logger.info(f"✓ Successfully downloaded {model_name}")
            downloaded_count += 1
        else:
            logger.error(f"✗ Failed to download {model_name}")
            failed_count += 1

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("DOWNLOAD SUMMARY:")
    logger.info(f"Downloaded: {downloaded_count}")
    logger.info(f"Skipped (already exists): {skipped_count}")
    logger.info(f"Failed: {failed_count}")

    if downloaded_count > 0:
        logger.info(
            f"\n✓ Successfully downloaded {downloaded_count} models for performance optimization!"
        )
        logger.info("Voice chat performance should be significantly improved.")

    if failed_count > 0:
        logger.warning(
            f"\n{failed_count} models failed to download. Voice chat may use fallback models."
        )
        logger.info("You can run this script again to retry failed downloads.")

    # Performance recommendations
    logger.info("\nPERFORMANCE RECOMMENDATIONS:")
    logger.info("tiny.en: Best for English voice chat (8% processing time)")
    logger.info("tiny: Good for multilingual short audio (10% processing time)")
    logger.info("base: Balanced accuracy/speed (25% processing time)")
    logger.info("small: Most accurate for longer audio (40% processing time)")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
