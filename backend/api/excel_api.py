# backend/api/excel_api.py
# Phase 2A.2: Excel Content Extraction API
# Provides endpoints for Excel processing status and manual extraction

import logging
from flask import Blueprint, jsonify, request
from pathlib import Path

logger = logging.getLogger(__name__)

excel_bp = Blueprint("excel_api", __name__, url_prefix="/api/excel")


@excel_bp.route("/status", methods=["GET"])
def excel_service_status():
    """Get status of Excel content extraction service."""
    try:
        from backend.services.excel_content_service import get_excel_service_status

        status = get_excel_service_status()

        return jsonify({"status": "success", "service_status": status}), 200

    except ImportError:
        return (
            jsonify(
                {
                    "status": "error",
                    "error": "Excel content service not available",
                    "service_status": {
                        "service_available": False,
                        "pandas_available": False,
                        "openpyxl_available": False,
                        "error": "Service not installed",
                    },
                }
            ),
            503,
        )
    except Exception as e:
        logger.error(f"Error getting Excel service status: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@excel_bp.route("/extract", methods=["POST"])
def extract_content_from_uploaded_excel():
    """Extract structured content from an uploaded Excel file."""
    try:
        # Check if Excel file is provided
        if "excel" not in request.files:
            return jsonify({"status": "error", "error": "No Excel file provided"}), 400

        excel_file = request.files["excel"]
        if excel_file.filename == "":
            return jsonify({"status": "error", "error": "No Excel file selected"}), 400

        # Check if it's a supported Excel format
        from backend.services.excel_content_service import is_excel_file

        if not is_excel_file(excel_file.filename):
            return (
                jsonify(
                    {
                        "status": "error",
                        "error": f"Unsupported Excel format: {Path(excel_file.filename).suffix}",
                    }
                ),
                400,
            )

        # Save temporarily and extract content
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(
            delete=False, suffix=Path(excel_file.filename).suffix
        ) as temp_file:
            excel_file.save(temp_file.name)
            temp_path = temp_file.name

        try:
            from backend.services.excel_content_service import extract_excel_content

            extraction_result = extract_excel_content(temp_path)

            # Clean up temp file
            os.unlink(temp_path)

            # Prepare response with summary info
            response_data = {
                "status": "success",
                "filename": excel_file.filename,
                "extraction_summary": {
                    "success": extraction_result.get("success", False),
                    "error": extraction_result.get("error"),
                    "text_length": len(extraction_result.get("text_content", "")),
                    "worksheets_count": len(extraction_result.get("worksheets", [])),
                    "processing_info": extraction_result.get("processing_info", {}),
                },
            }

            # Add metadata summary if available
            metadata = extraction_result.get("metadata")
            if metadata:
                response_data["extraction_summary"]["metadata"] = {
                    "total_sheets": getattr(metadata, "total_sheets", 0),
                    "total_rows": getattr(metadata, "total_rows", 0),
                    "total_columns": getattr(metadata, "total_columns", 0),
                    "file_format": getattr(metadata, "file_format", "unknown"),
                    "has_formulas": getattr(metadata, "has_formulas", False),
                }

            # Include first 1000 characters of text content as preview
            text_content = extraction_result.get("text_content", "")
            if text_content:
                response_data["text_preview"] = text_content[:1000]
                if len(text_content) > 1000:
                    response_data["text_preview"] += "... (truncated)"

            return jsonify(response_data), 200

        except Exception as e:
            # Clean up temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise e

    except ImportError:
        return (
            jsonify(
                {"status": "error", "error": "Excel content service not available"}
            ),
            503,
        )
    except Exception as e:
        logger.error(f"Error extracting content from Excel: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@excel_bp.route("/supported-formats", methods=["GET"])
def get_supported_excel_formats():
    """Get list of supported Excel formats."""
    try:
        from backend.services.excel_content_service import excel_extractor

        return (
            jsonify(
                {
                    "status": "success",
                    "supported_formats": list(excel_extractor.supported_formats),
                    "max_size_mb": excel_extractor.max_file_size_mb,
                    "max_sheets_to_process": excel_extractor.max_sheets_to_process,
                }
            ),
            200,
        )

    except ImportError:
        return (
            jsonify(
                {
                    "status": "error",
                    "error": "Excel content service not available",
                    "supported_formats": [],
                    "max_size_mb": 0,
                }
            ),
            503,
        )
    except Exception as e:
        logger.error(f"Error getting supported formats: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@excel_bp.route("/process-summary/<int:document_id>", methods=["GET"])
def get_excel_processing_summary(document_id):
    """Get processing summary for a specific Excel document."""
    try:
        # This would typically query the database for processing results
        # For now, return a placeholder response
        return (
            jsonify(
                {
                    "status": "success",
                    "document_id": document_id,
                    "message": "Excel processing summary endpoint - to be implemented with database integration",
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error getting Excel processing summary: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500
