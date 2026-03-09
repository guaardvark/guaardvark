# backend/api/image_api.py
# Phase 2A.1: Image Content Extraction API
# Provides endpoints for image processing status and manual extraction

import logging
from flask import Blueprint, jsonify, request
from pathlib import Path

logger = logging.getLogger(__name__)

image_bp = Blueprint("image_api", __name__, url_prefix="/api/image")

@image_bp.route("/status", methods=["GET"])
def image_service_status():
    """Get status of image content extraction service."""
    try:
        from backend.services.image_content_service import get_image_service_status
        status = get_image_service_status()
        
        return jsonify({
            "status": "success",
            "service_status": status
        }), 200
        
    except ImportError:
        return jsonify({
            "status": "error",
            "error": "Image content service not available",
            "service_status": {
                "service_available": False,
                "vision_model_available": False,
                "error": "Service not installed"
            }
        }), 503
    except Exception as e:
        logger.error(f"Error getting image service status: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@image_bp.route("/extract", methods=["POST"])
def extract_text_from_uploaded_image():
    """Extract text content from an uploaded image file."""
    try:
        # Check if image file is provided
        if 'image' not in request.files:
            return jsonify({
                "status": "error",
                "error": "No image file provided"
            }), 400
            
        image_file = request.files['image']
        if image_file.filename == '':
            return jsonify({
                "status": "error", 
                "error": "No image file selected"
            }), 400
        
        # Check if it's a supported image format
        from backend.services.image_content_service import is_image_file
        if not is_image_file(image_file.filename):
            return jsonify({
                "status": "error",
                "error": f"Unsupported image format: {Path(image_file.filename).suffix}"
            }), 400
        
        # Save temporarily and extract text
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(image_file.filename).suffix) as temp_file:
            image_file.save(temp_file.name)
            temp_path = temp_file.name
            
        try:
            from backend.services.image_content_service import extract_text_from_image
            extraction_result = extract_text_from_image(temp_path)
            
            # Clean up temp file
            os.unlink(temp_path)
            
            return jsonify({
                "status": "success",
                "filename": image_file.filename,
                "extraction_result": extraction_result
            }), 200
            
        except Exception as e:
            # Clean up temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise e
            
    except ImportError:
        return jsonify({
            "status": "error",
            "error": "Image content service not available"
        }), 503
    except Exception as e:
        logger.error(f"Error extracting text from image: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@image_bp.route("/supported-formats", methods=["GET"])
def get_supported_image_formats():
    """Get list of supported image formats."""
    try:
        from backend.services.image_content_service import image_extractor
        
        return jsonify({
            "status": "success",
            "supported_formats": list(image_extractor.supported_formats),
            "max_size_mb": image_extractor.max_image_size_mb
        }), 200
        
    except ImportError:
        return jsonify({
            "status": "error",
            "error": "Image content service not available",
            "supported_formats": [],
            "max_size_mb": 0
        }), 503
    except Exception as e:
        logger.error(f"Error getting supported formats: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500 