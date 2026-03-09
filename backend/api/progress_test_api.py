# backend/api/progress_test_api.py
# Test API for debugging progress tracking

import logging
import time
import threading
from flask import Blueprint, jsonify, request

# Import directly to avoid dependency issues
try:
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType
except ImportError as e:
    logging.error(f"Failed to import unified progress system: {e}")
    # Fallback for testing
    def get_unified_progress():
        return None
    class ProcessType:
        CSV_PROCESSING = "csv_processing"
        IMAGE_GENERATION = "image_generation"
        FILE_GENERATION = "file_generation"
        INDEXING = "indexing"
        ANALYSIS = "analysis"
        UPLOAD = "upload"

progress_test_bp = Blueprint("progress_test", __name__, url_prefix="/api/progress-test")
logger = logging.getLogger(__name__)

@progress_test_bp.route("/test-image-gen", methods=["POST"])
def test_image_generation_progress():
    """Test image generation progress tracking"""
    try:
        progress_system = get_unified_progress()
        process_id = progress_system.create_process(
            ProcessType.IMAGE_GENERATION,
            "Test Image Generation",
            {"test": True}
        )
        
        def simulate_progress():
            for i in range(0, 101, 10):
                progress_system.update_process(
                    process_id,
                    i,
                    f"Generating image... {i}% complete"
                )
                time.sleep(0.5)
            progress_system.complete_process(process_id, "Image generation complete!")
        
        # Run in background thread
        thread = threading.Thread(target=simulate_progress)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "process_id": process_id,
            "message": "Image generation progress test started"
        })
        
    except Exception as e:
        logger.error(f"Error in test_image_generation_progress: {e}")
        return jsonify({"error": str(e)}), 500

@progress_test_bp.route("/test-file-gen", methods=["POST"])
def test_file_generation_progress():
    """Test file generation progress tracking"""
    try:
        progress_system = get_unified_progress()
        process_id = progress_system.create_process(
            ProcessType.FILE_GENERATION,
            "Test File Generation",
            {"test": True}
        )
        
        def simulate_progress():
            progress_system.update_process(process_id, 10, "Initializing file generation...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 30, "Processing data...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 60, "Generating content...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 90, "Finalizing output...")
            time.sleep(0.5)
            progress_system.complete_process(process_id, "File generation complete!")
        
        # Run in background thread
        thread = threading.Thread(target=simulate_progress)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "process_id": process_id,
            "message": "File generation progress test started"
        })
        
    except Exception as e:
        logger.error(f"Error in test_file_generation_progress: {e}")
        return jsonify({"error": str(e)}), 500

@progress_test_bp.route("/test-csv-gen", methods=["POST"])
def test_csv_generation_progress():
    """Test CSV generation progress tracking with realistic item-based progress"""
    try:
        progress_system = get_unified_progress()
        process_id = progress_system.create_process(
            ProcessType.CSV_PROCESSING,
            "Test CSV Generation",
            {"test": True, "total_items": 7}
        )
        
        def simulate_progress():
            total_items = 7
            for i in range(total_items + 1):
                progress = int((i / total_items) * 100)
                if i == 0:
                    message = f"Starting CSV generation... ({i}/{total_items} items)"
                elif i == total_items:
                    message = f"CSV generation complete! ({i}/{total_items} items)"
                else:
                    message = f"Generating item {i}... ({i}/{total_items} items)"
                
                progress_system.update_process(process_id, progress, message)
                time.sleep(1)  # 1 second between updates
            
            progress_system.complete_process(process_id, "CSV file saved successfully!")
        
        # Run in background thread
        thread = threading.Thread(target=simulate_progress)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "process_id": process_id,
            "message": "CSV generation progress test started (7 items)"
        })
        
    except Exception as e:
        logger.error(f"Error in test_csv_generation_progress: {e}")
        return jsonify({"error": str(e)}), 500

@progress_test_bp.route("/test-indexing", methods=["POST"])
def test_indexing_progress():
    """Test indexing progress tracking"""
    try:
        progress_system = get_unified_progress()
        process_id = progress_system.create_process(
            ProcessType.INDEXING,
            "Test Document Indexing",
            {"test": True}
        )
        
        def simulate_progress():
            progress_system.update_process(process_id, 10, "Parsing documents...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 30, "Extracting content...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 60, "Building index...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 90, "Finalizing index...")
            time.sleep(0.5)
            progress_system.complete_process(process_id, "Indexing complete!")
        
        # Run in background thread
        thread = threading.Thread(target=simulate_progress)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "process_id": process_id,
            "message": "Indexing progress test started"
        })
        
    except Exception as e:
        logger.error(f"Error in test_indexing_progress: {e}")
        return jsonify({"error": str(e)}), 500

@progress_test_bp.route("/test-analysis", methods=["POST"])
def test_analysis_progress():
    """Test analysis progress tracking"""
    try:
        progress_system = get_unified_progress()
        process_id = progress_system.create_process(
            ProcessType.ANALYSIS,
            "Test Code Analysis",
            {"test": True}
        )
        
        def simulate_progress():
            progress_system.update_process(process_id, 15, "Analyzing code structure...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 40, "Processing dependencies...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 70, "Generating insights...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 95, "Finalizing analysis...")
            time.sleep(0.5)
            progress_system.complete_process(process_id, "Analysis complete!")
        
        # Run in background thread
        thread = threading.Thread(target=simulate_progress)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "process_id": process_id,
            "message": "Analysis progress test started"
        })
        
    except Exception as e:
        logger.error(f"Error in test_analysis_progress: {e}")
        return jsonify({"error": str(e)}), 500

@progress_test_bp.route("/test-upload", methods=["POST"])
def test_upload_progress():
    """Test upload progress tracking"""
    try:
        progress_system = get_unified_progress()
        process_id = progress_system.create_process(
            ProcessType.UPLOAD,
            "Test File Upload",
            {"test": True}
        )
        
        def simulate_progress():
            progress_system.update_process(process_id, 20, "Uploading file...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 50, "Processing upload...")
            time.sleep(0.5)
            progress_system.update_process(process_id, 80, "Validating file...")
            time.sleep(0.5)
            progress_system.complete_process(process_id, "Upload complete!")
        
        # Run in background thread
        thread = threading.Thread(target=simulate_progress)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "process_id": process_id,
            "message": "Upload progress test started"
        })
        
    except Exception as e:
        logger.error(f"Error in test_upload_progress: {e}")
        return jsonify({"error": str(e)}), 500

@progress_test_bp.route("/status", methods=["GET"])
def get_progress_status():
    """Get current progress system status"""
    try:
        progress_system = get_unified_progress()
        
        # Get active processes
        active_processes = {}
        for process_id, event in progress_system._active_processes.items():
            active_processes[process_id] = {
                "process_type": event.process_type.value,
                "progress": event.progress,
                "message": event.message,
                "status": event.status.value,
                "timestamp": event.timestamp.isoformat()
            }
        
        return jsonify({
            "success": True,
            "active_processes": active_processes,
            "total_active": len(active_processes)
        })
        
    except Exception as e:
        logger.error(f"Error in get_progress_status: {e}")
        return jsonify({"error": str(e)}), 500

@progress_test_bp.route("/test-socketio", methods=["POST"])
def test_socketio_connection():
    """Test SocketIO connection and emission"""
    try:
        progress_system = get_unified_progress()
        process_id = progress_system.create_process(
            ProcessType.CSV_PROCESSING,
            "SocketIO Test",
            {"test": True}
        )
        
        # Test immediate emission
        progress_system.update_process(process_id, 50, "SocketIO test message")
        time.sleep(0.5)
        progress_system.complete_process(process_id, "SocketIO test complete")
        
        return jsonify({
            "success": True,
            "process_id": process_id,
            "message": "SocketIO test completed"
        })
        
    except Exception as e:
        logger.error(f"Error in test_socketio_connection: {e}")
        return jsonify({"error": str(e)}), 500