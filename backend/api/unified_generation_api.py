#!/usr/bin/env python3
"""
Unified Generation API
Consolidated API for both single and bulk CSV generation
Version 1.0: Unified generation system
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Union

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from backend.models import Task, db
from backend.utils.bulk_csv_generator import (
    BulkCSVGenerator, 
    GenerationTask, 
    create_tasks_from_topics,
    create_data_center_topics,
    create_demonstration_csv
)
from backend.utils.unified_progress_system import get_unified_progress, ProcessType
from backend.utils.context_variables import context_manager
from backend.utils.secure_file_operations import secure_write_file, sanitize_generation_params
from backend.utils.system_coordinator import get_system_coordinator
from backend.utils.response_formatter import (
    format_generation_success,
    format_bulk_job_success,
    format_generation_error,
    format_validation_error
)

unified_gen_bp = Blueprint("unified_generation_api", __name__, url_prefix="/api/generate")
logger = logging.getLogger(__name__)

@unified_gen_bp.route("/csv", methods=["POST"])
def generate_csv():
    """
    Unified CSV generation endpoint that handles both single and bulk generation
    Automatically routes to appropriate generation method based on request
    
    Expected payload (Single Generation):
    {
        "type": "single",
        "output_filename": "single_content.csv",
        "prompt": "Generate one CSV row about data center legal services",
        "client": "Professional Services",
        "project": "Legal Services"
    }
    
    Expected payload (Bulk Generation):
    {
        "type": "bulk",
        "output_filename": "bulk_content.csv",
        "client": "Professional Services",
        "project": "Legal Services Marketing",
        "website": "datacenterknowledge.com/business",
        "topics": ["topic1", "topic2", ...] or "auto",
        "num_items": 100,
        "concurrent_workers": 10,
        "target_word_count": 500,
        "batch_size": 50
    }
    
    Expected payload (Auto-detect):
    {
        "output_filename": "content.csv",
        "prompt": "Generate 50 CSV rows about legal services",
        "client": "Professional Services",
        "project": "Legal Services"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return format_validation_error("Request body must be JSON")

        output_filename = data.get("output_filename")
        if not output_filename:
            return format_validation_error("output_filename is required")

        # Auto-detect generation type if not specified
        generation_type = data.get("type", "auto")
        
        if generation_type == "auto":
            # Analyze request to determine if it's single or bulk
            generation_type = _detect_generation_type(data)
            logger.info(f"Auto-detected generation type: {generation_type}")

        # Route to appropriate generation method
        if generation_type == "single":
            return _handle_single_csv_generation(data)
        elif generation_type == "bulk":
            return _handle_bulk_csv_generation(data)
        else:
            return format_validation_error(f"Invalid generation type: {generation_type}")

    except Exception as e:
        logger.error(f"Error in unified CSV generation: {e}", exc_info=True)
        return format_generation_error(str(e))

def _detect_generation_type(data: Dict) -> str:
    """Auto-detect whether this should be single or bulk generation"""
    
    # Check for explicit bulk indicators
    if data.get("num_items", 0) > 1:
        return "bulk"
    
    if data.get("topics") and isinstance(data["topics"], list) and len(data["topics"]) > 1:
        return "bulk"
    
    if data.get("concurrent_workers", 0) > 1:
        return "bulk"
    
    # Check prompt for bulk indicators
    prompt = data.get("prompt", "").lower()
    bulk_indicators = [
        "multiple", "several", "many", "bulk", "batch", "generate", "create",
        "rows", "items", "pages", "articles", "content pieces"
    ]
    
    for indicator in bulk_indicators:
        if indicator in prompt:
            return "bulk"
    
    # Default to single generation
    return "single"

def _handle_single_csv_generation(data: Dict):
    """Handle single CSV row generation"""
    try:
        output_filename = data.get("output_filename")
        prompt = data.get("prompt", "")
        client = data.get("client", "Professional Services")
        project = data.get("project", "Content Generation")
        website = data.get("website", "professional-website.com")
        
        # Validate required fields
        if not prompt:
            return format_validation_error("prompt is required for single generation")
        
        # Secure filename
        secure_filename_result = secure_filename(output_filename)
        if not secure_filename_result.lower().endswith(".csv"):
            secure_filename_result += ".csv"
        
        # Get output directory
        output_dir = current_app.config.get("OUTPUT_DIR")
        if not output_dir:
            return format_generation_error("Server configuration error: Output directory not set", status_code=500)
        
        # Create single task with website-scoped ID
        from backend.models import Page, db
        import re

        # Generate website-scoped ID
        website_key = website.replace('http://', '').replace('https://', '').split('/')[0]
        website_prefix = website_key.replace('.', '-').replace('_', '-')[:20]

        # Get highest ID for this website
        try:
            max_page = db.session.query(Page).filter(
                Page.meta_json.like(f'%{website_key}%')
            ).order_by(Page.created_at.desc()).first()

            if max_page and max_page.id:
                match = re.search(r'-(\d+)$', max_page.id)
                counter = int(match.group(1)) + 1 if match else 1
            else:
                counter = 1
        except Exception:
            counter = 1

        item_id = f"{website_prefix}-{counter:03d}"

        task = GenerationTask(
            item_id=item_id,
            topic=prompt,
            client=client,
            project=project,
            website=website
        )
        
        # Create generator with single worker
        generator = BulkCSVGenerator(
            output_dir=output_dir,
            concurrent_workers=1,
            batch_size=1,
            target_word_count=data.get("target_word_count", 500)
        )
        
        # Generate single CSV row
        output_path, stats = generator.generate_bulk_csv(
            tasks=[task],
            output_filename=secure_filename_result
        )
        
        # Get file size
        file_size = os.path.getsize(output_path) if os.path.exists(output_path) else None
        
        return format_generation_success(
            message="Single CSV generation completed successfully.",
            generation_type="single",
            output_file=os.path.basename(output_path),
            output_path=output_path,
            file_size=file_size,
            statistics=stats
        )
        
    except Exception as e:
        logger.error(f"Error in single CSV generation: {e}", exc_info=True)
        return format_generation_error(str(e))

def _handle_bulk_csv_generation(data: Dict):
    """Handle bulk CSV generation using existing bulk generation logic"""
    try:
        # Use existing bulk generation logic
        from backend.api.bulk_generation_api import generate_bulk_csv
        
        # Create a mock request object with the data
        class MockRequest:
            def __init__(self, data):
                self._json = data
            
            def get_json(self):
                return self._json
        
        # Temporarily replace request with mock
        original_request = request
        try:
            # This is a simplified approach - in production, you'd want to refactor
            # the bulk generation logic to be more modular
            return generate_bulk_csv()
        finally:
            pass  # Restore original request if needed
        
    except Exception as e:
        logger.error(f"Error in bulk CSV generation: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@unified_gen_bp.route("/file", methods=["POST"])
def generate_file():
    """
    Unified file generation endpoint for non-CSV files
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be JSON."}), 400
        
        output_filename = data.get("output_filename")
        prompt = data.get("prompt")
        
        if not output_filename:
            return jsonify({"error": "output_filename is required."}), 400
        
        if not prompt:
            return jsonify({"error": "prompt is required."}), 400
        
        # Use existing single file generation logic
        from backend.api.generation_api import direct_generate_and_save_file_route
        
        # Create mock request
        class MockRequest:
            def __init__(self, data):
                self._json = data
            
            def get_json(self):
                return self._json
        
        # Call existing generation logic
        return direct_generate_and_save_file_route()
        
    except Exception as e:
        logger.error(f"Error in unified file generation: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@unified_gen_bp.route("/csv/large-scale", methods=["POST"])
def generate_large_scale_csv():
    """
    Large-scale CSV generation with web research integration
    Designed for 1000+ page generation with chunking and overnight processing
    
    Expected payload:
    {
        "output_filename": "large_dataset.csv",
        "client": "Professional Services",
        "project": "Legal Services Marketing",
        "website": "datacenterknowledge.com/business",
        "topics": ["topic1", "topic2", ...] or "auto",
        "num_items": 1000,
        "concurrent_workers": 10,
        "target_word_count": 500,
        "chunk_size": 100,
        "enable_web_research": true,
        "max_sources_per_task": 2
    }
    """
    try:
        data = request.get_json()
        if not data:
            return format_validation_error("Request body must be JSON")

        output_filename = data.get("output_filename")
        if not output_filename:
            return format_validation_error("output_filename is required")

        # Validate and set defaults for large-scale processing
        num_items = data.get("num_items", 1000)
        chunk_size = data.get("chunk_size", 100)
        concurrent_workers = data.get("concurrent_workers", 10)
        enable_web_research = data.get("enable_web_research", True)
        max_sources_per_task = data.get("max_sources_per_task", 2)
        
        # Enforce limits to prevent system overload
        num_items = min(num_items, 5000)  # Max 5000 items
        chunk_size = min(chunk_size, 200)  # Max 200 per chunk
        concurrent_workers = min(concurrent_workers, 20)  # Max 20 workers
        max_sources_per_task = min(max_sources_per_task, 5)  # Max 5 sources per task

        client = data.get("client", "")
        project = data.get("project", "")
        website = data.get("website", "")

        # Secure filename
        secure_filename_result = secure_filename(output_filename)
        if not secure_filename_result:
            return format_validation_error("Invalid filename provided")

        # Get output directory
        output_dir = current_app.config.get("OUTPUT_DIR")
        if not output_dir:
            return format_generation_error("Server configuration error: Output directory not set", status_code=500)

        # Generate or use provided topics
        topics_input = data.get("topics")
        if topics_input == "auto" or not topics_input:
            topics = create_data_center_topics(num_items)
        else:
            topics = topics_input[:num_items] if isinstance(topics_input, list) else [topics_input]

        # Create tasks
        tasks = create_tasks_from_topics(
            topics=topics,
            client=client,
            project=project,
            website=website
        )

        logger.info(f"Starting large-scale CSV generation: {len(tasks)} tasks with web research: {enable_web_research}")

        # Create enhanced generator for large-scale processing
        generator = BulkCSVGenerator(
            output_dir=output_dir,
            concurrent_workers=concurrent_workers,
            batch_size=min(chunk_size, 50),  # Batch size for concurrent processing
            target_word_count=data.get("target_word_count", 500)
        )

        # Use the enhanced generation method with web research
        output_path, stats = generator.generate_bulk_csv_with_web_research(
            tasks=tasks,
            output_filename=secure_filename_result,
            enable_web_research=enable_web_research,
            chunk_size=chunk_size,
            max_sources_per_task=max_sources_per_task
        )

        # Get file size
        file_size = os.path.getsize(output_path) if os.path.exists(output_path) else None

        # Enhanced response with chunking information
        response_data = {
            "message": f"Large-scale CSV generation completed successfully. Generated {stats.get('total_rows', 0)} rows with {'web research' if enable_web_research else 'LLM-only content'}.",
            "generation_type": "large_scale_bulk",
            "output_file": os.path.basename(output_path),
            "output_path": output_path,
            "file_size": file_size,
            "statistics": {
                **stats,
                "processing_method": "chunked" if num_items > 500 else "standard",
                "web_research_enabled": enable_web_research,
                "total_chunks": len(tasks) // chunk_size + (1 if len(tasks) % chunk_size else 0) if num_items > 500 else 1,
                "chunk_size": chunk_size,
                "concurrent_workers": concurrent_workers
            }
        }

        return jsonify({"success": True, **response_data}), 200
        
    except Exception as e:
        logger.error(f"Error in large-scale CSV generation: {e}", exc_info=True)
        return format_generation_error(str(e))

@unified_gen_bp.route("/status", methods=["GET"])
def get_generation_status():
    """Get status of generation jobs"""
    try:
        job_id = request.args.get("job_id")
        if not job_id:
            return jsonify({"error": "job_id is required"}), 400
        
        progress_system = get_unified_progress()
        status = progress_system.get_job_status(job_id)
        
        return jsonify({
            "job_id": job_id,
            "status": status
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting generation status: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
