#!/usr/bin/env python3
"""
Bulk CSV Generator for High-Volume Content Creation
Optimized for generating 1,000+ pages per day based on specific prompts like the Imperial Data Center example
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse
from enum import Enum

import requests
from backend.utils.llm_service import get_default_llm
from backend.models import db, Generation, Page
from backend.utils.slugify import slugify, is_valid_slug
from flask import current_app

# Import progress system components
from backend.utils.unified_progress_system import ProcessStatus, ProcessType

# Import new refactored components
# NOTE: These modules are not implemented yet - imports disabled
# TODO: Implement these modules when persistence layer is needed
# from backend.repositories import (
#     GenerationRepository, PageRepository,
#     PageDTO, GenerationDTO,
#     create_generation_with_pages
# )
# from backend.validation import (
#     validate_site_meta, validate_csv_config,
#     validate_row, ValidationResult
# )
# from backend.renderers.structured_html import (
#     render_structured_html, HTMLRenderer
# )

# Import professional file processor for high-quality CSV writing
from backend.tools.file_processor import write_csv

logger = logging.getLogger(__name__)

# Import web search capabilities for large-scale data gathering
try:
    from backend.api.web_search_api import extract_website_content
    WEB_SEARCH_AVAILABLE = True
except ImportError:
    WEB_SEARCH_AVAILABLE = False
    logger.warning("Web search functionality not available - bulk generation will be limited to LLM-only content")

# ============================================================================
# ROW TRACKING SYSTEM - Integrated from bulk_csv_generator_with_tracking.py
# ============================================================================

class RowStatus(Enum):
    """Status of a tracked row"""
    ORIGINAL = "original"
    REPLACED = "replaced"
    FLAGGED = "flagged_manual_review"
    ACTIVE = "active"

@dataclass
class ValidationFailure:
    """Record of a single validation failure"""
    timestamp: str
    reason: str
    word_count: Optional[int] = None
    content_preview: Optional[str] = None
    validation_details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class RowReplacement:
    """Record of a row replacement attempt"""
    attempt_number: int
    timestamp: str
    success: bool
    validation_failure: Optional[ValidationFailure] = None
    generation_time_ms: Optional[float] = None

@dataclass
class RowRecord:
    """Comprehensive tracking record for a single row"""
    unique_id: str
    sequence_number: int
    status: RowStatus
    is_active: bool
    original_topic: str
    original_task_id: str
    request_timestamp: str
    replacement_count: int = 0
    replacement_history: List[RowReplacement] = field(default_factory=list)
    max_attempts_reached: bool = False
    current_row_data: Optional[Dict[str, Any]] = None
    original_row_data: Optional[Dict[str, Any]] = None
    final_word_count: Optional[int] = None
    final_validation_passed: bool = False
    generation_duration_ms: Optional[float] = None
    needs_manual_review: bool = False
    manual_review_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['status'] = self.status.value
        return data

class RowTracker:
    """Manages comprehensive row tracking for CSV generation"""

    def __init__(self, target_row_count: int, max_replacement_attempts: int = 5, job_id: Optional[str] = None):
        self.target_row_count = target_row_count
        self.max_replacement_attempts = max_replacement_attempts
        self.job_id = job_id or f"job_{int(time.time())}"
        self.records: Dict[str, RowRecord] = {}
        self.sequence_to_id: Dict[int, str] = {}
        self.total_generation_attempts = 0
        self.total_validation_failures = 0
        self.total_replacements = 0
        self.rows_flagged_for_review = 0
        self.start_time = datetime.now()
        self.end_time: Optional[datetime] = None
        self._id_counter = 0
        self._id_timestamp_base = datetime.now().strftime("%Y%m%d_%H%M%S")

    def generate_unique_id(self, sequence_number: int) -> str:
        self._id_counter += 1
        return f"{self._id_timestamp_base}_{sequence_number:03d}"

    def create_row_record(self, sequence_number: int, topic: str, task_id: str) -> RowRecord:
        unique_id = self.generate_unique_id(sequence_number)
        record = RowRecord(
            unique_id=unique_id,
            sequence_number=sequence_number,
            status=RowStatus.ORIGINAL,
            is_active=False,
            original_topic=topic,
            original_task_id=task_id,
            request_timestamp=datetime.now().isoformat()
        )
        self.records[unique_id] = record
        self.sequence_to_id[sequence_number] = unique_id
        return record

    def record_generation_attempt(self, unique_id: str, success: bool, row_data: Optional[Dict[str, Any]] = None,
                                  validation_failure: Optional[ValidationFailure] = None, generation_time_ms: Optional[float] = None):
        if unique_id not in self.records:
            return
        record = self.records[unique_id]
        self.total_generation_attempts += 1
        attempt_number = record.replacement_count + 1
        replacement = RowReplacement(
            attempt_number=attempt_number,
            timestamp=datetime.now().isoformat(),
            success=success,
            validation_failure=validation_failure,
            generation_time_ms=generation_time_ms
        )
        record.replacement_history.append(replacement)
        if success:
            record.current_row_data = row_data
            if record.original_row_data is None:
                record.original_row_data = row_data.copy()
            record.final_validation_passed = True
            record.is_active = True
            record.generation_duration_ms = generation_time_ms
            if row_data and 'Content' in row_data:
                content = row_data['Content'] or ""
                record.final_word_count = len(content.split())
            record.status = RowStatus.REPLACED if record.replacement_count > 0 else RowStatus.ORIGINAL
        else:
            record.replacement_count += 1
            self.total_validation_failures += 1
            if record.replacement_count >= self.max_replacement_attempts:
                record.status = RowStatus.FLAGGED
                record.max_attempts_reached = True
                record.needs_manual_review = True
                record.manual_review_reason = f"Failed validation after {record.replacement_count} attempts"
                self.rows_flagged_for_review += 1

    def get_rows_needing_generation(self) -> List[Tuple[int, str]]:
        needs_generation = []
        for seq_num in range(1, self.target_row_count + 1):
            if seq_num in self.sequence_to_id:
                unique_id = self.sequence_to_id[seq_num]
                record = self.records[unique_id]
                if not record.is_active and record.status != RowStatus.FLAGGED:
                    needs_generation.append((seq_num, unique_id))
        return needs_generation

    def get_active_rows_ordered(self) -> List[RowRecord]:
        active_records = [
            self.records[self.sequence_to_id[seq]]
            for seq in sorted(self.sequence_to_id.keys())
            if self.records[self.sequence_to_id[seq]].is_active
        ]
        return active_records

    def finalize(self):
        self.end_time = datetime.now()
        active_count = sum(1 for r in self.records.values() if r.is_active)
        logger.info(f"RowTracker finalized: {active_count} active rows, "
                   f"{self.total_validation_failures} failures, "
                   f"{self.rows_flagged_for_review} flagged for review")

    def export_tracking_log_json(self, output_path: str, job_params: Optional[Dict] = None, status: str = "completed"):
        # Check if file exists for updates
        existing_data = {}
        if os.path.exists(output_path):
            try:
                with open(output_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            except Exception as e:
                logger.warning(f"Could not read existing tracking file for update: {e}")
        
        # Prepare tracking data
        tracking_data = {
            'metadata': {
                'job_id': self.job_id, 
                'export_timestamp': datetime.now().isoformat(),
                'target_row_count': self.target_row_count, 
                'max_replacement_attempts': self.max_replacement_attempts,
                'status': status,  # NEW: Track job status
                # NEW: Store original job parameters
                'job_parameters': job_params or {}
            },
            'row_records': [record.to_dict() for record in self.records.values()]
        }
        
        # If updating existing file, preserve some metadata
        if existing_data and 'metadata' in existing_data:
            # Preserve original export timestamp for in_progress updates
            if status == "in_progress" and 'export_timestamp' in existing_data['metadata']:
                tracking_data['metadata']['export_timestamp'] = existing_data['metadata']['export_timestamp']
            # Preserve job_parameters if they exist and we're not updating them
            if 'job_parameters' in existing_data['metadata'] and not job_params:
                tracking_data['metadata']['job_parameters'] = existing_data['metadata']['job_parameters']
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(tracking_data, f, indent=2, ensure_ascii=False)

# End of Row Tracking System
# ============================================================================

@dataclass
class ContentRow:
    """Represents a single row of WordPress-style CSV content with enhanced metadata"""
    id: str
    title: str
    content: str
    excerpt: str
    category: Optional[str] = None
    tags: Optional[str] = None
    slug: Optional[str] = None
    meta_description: Optional[str] = None  # Added for 8-field CSV format
    image: Optional[str] = None

    # Enhanced metadata fields
    phone: Optional[str] = None
    contact_url: Optional[str] = None
    client_location: Optional[str] = None
    company_name: Optional[str] = None
    primary_service: Optional[str] = None
    secondary_service: Optional[str] = None
    brand_tone: Optional[str] = None
    hours: Optional[str] = None
    social_links_json: Optional[str] = None

@dataclass
class GenerationTask:
    """Represents a single content generation task with enhanced metadata"""
    item_id: str
    topic: str
    client: str
    project: str
    website: str
    client_notes: Optional[str] = None
    target_keywords: Optional[List[str]] = None

    # Enhanced site metadata
    phone: Optional[str] = None
    contact_url: Optional[str] = None
    client_location: Optional[str] = None
    company_name: Optional[str] = None
    primary_service: Optional[str] = None
    secondary_service: Optional[str] = None
    brand_tone: Optional[str] = 'neutral'
    hours: Optional[str] = None
    social_links: Optional[List[Dict[str, str]]] = None
    structured_html: bool = False
    include_h1: bool = True
    csv_delimiter: str = ','
    insert_content: Optional[str] = None
    insert_position: Optional[str] = None
    prompt_rule_id: Optional[int] = None  # CRITICAL: Rule ID for prompt template

    # BUG FIX #3: Add enhanced context mode fields
    context_mode: str = 'basic'  # 'basic', 'enhanced', or 'auto'
    use_entity_context: bool = False
    use_competitor_analysis: bool = False
    use_document_intelligence: bool = False
    entity_context: Optional[str] = None  # Populated by enhanced context system
    competitor_context: Optional[str] = None  # Populated from web scraping
    document_context: Optional[str] = None  # Populated from uploaded documents

@dataclass
class GenerationError:
    """Represents a generation error with detailed information"""
    task_id: str
    error_type: str
    error_message: str
    error_details: str
    timestamp: float
    retry_count: int = 0
    can_retry: bool = True

class BulkCSVGenerator:
    """High-performance CSV content generator with enhanced features"""

    def __init__(self,
                 output_dir: str,
                 concurrent_workers: int = 10,
                 batch_size: int = 50,
                 target_word_count: int = 500,
                 unified_progress_system=None,
                 job_id=None,
                 csv_delimiter: str = ',',
                 structured_html: bool = False,
                 enable_persistence: bool = True,
                 brand_tone: str = 'neutral',
                 include_h1: bool = True,
                 site_meta: Optional[Dict[str, Any]] = None,
                 # NEW: Job parameters for tracking metadata
                 job_params: Optional[Dict[str, Any]] = None,
                 # BUG FIX: Add model_name parameter to use specified model instead of default
                 model_name: Optional[str] = None):
        logger.info(f"Initializing BulkCSVGenerator with output_dir={output_dir}, workers={concurrent_workers}, model={model_name}")
        self.output_dir = output_dir
        self.concurrent_workers = concurrent_workers
        self.batch_size = batch_size
        self.target_word_count = target_word_count
        self.generated_count = 0
        self.error_count = 0
        self.start_time = time.time()
        self.errors: List[GenerationError] = []
        self.failed_tasks: List[str] = []
        self.retryable_errors: List[GenerationError] = []
        self.unified_progress = unified_progress_system
        self.job_id = job_id

        # Enhanced features
        self.csv_delimiter = csv_delimiter
        self.structured_html = structured_html
        self.enable_persistence = enable_persistence
        self.generation_record = None
        self.brand_tone = brand_tone
        self.include_h1 = include_h1
        self.site_meta = site_meta or {}

        # BUG FIX #9: Initialize output_format for rule template selection
        self.output_format = 'csv'  # Default to CSV format

        # NEW: Store job parameters for tracking metadata
        self.job_params = job_params or {}

        # Initialize HTML renderer if structured HTML is enabled
        # NOTE: HTMLRenderer module not yet implemented - disabled for now
        self.html_renderer = None  # Will be: HTMLRenderer(...) if structured_html else None
        
        # FIXED: Circuit breaker to prevent resource waste
        self.consecutive_failures = 0
        self.max_consecutive_failures = 8  # Increased threshold - Stop after 8 consecutive failures
        self.circuit_breaker_active = False
        self.successful_operations = 0

        # Cancellation flag for stopping generation
        self.cancelled = False
        
        # Initialize logging context
        self.log_context = {
            "job_id": job_id,
            "generator_id": f"generator_{int(time.time())}",
            "start_time": datetime.now().isoformat()
        }
        
        # Initialize LLM instance for background tasks
        # BUG FIX: Use specified model_name if provided, otherwise fall back to default
        self._model_name = model_name
        self._log_info("Attempting to initialize LLM instance", {"requested_model": model_name})
        try:
            if model_name:
                # Use the specified model instead of default
                from llama_index.llms.ollama import Ollama
                from backend.config import OLLAMA_BASE_URL, LLM_REQUEST_TIMEOUT
                timeout_value = min(LLM_REQUEST_TIMEOUT, 180.0)
                self.llm = Ollama(
                    model=model_name,
                    base_url=OLLAMA_BASE_URL,
                    request_timeout=timeout_value,
                    temperature=0.4,
                    additional_kwargs={"num_ctx": 8192, "top_p": 0.8, "top_k": 30}
                )
                self._log_info(f"LLM initialized with specified model: {model_name}")
            else:
                self.llm = get_default_llm()
            self._log_info("LLM instance initialized successfully", {
                "llm_type": str(type(self.llm)),
                "model": getattr(self.llm, 'model', 'unknown'),
                "base_url": getattr(self.llm, 'base_url', 'unknown')
            })
        except Exception as e:
            self._log_error("Failed to initialize LLM instance", {"error": str(e)}, exc_info=True)
            self.llm = None

    def _generate_slug_from_title(self, title: str) -> str:
        """Generate URL-friendly slug from title using new slug rules (no length limits)"""
        return slugify(title)  # Uses the enhanced slugify utility

    def _format_id_to_5_digits(self, id_str: str) -> str:
        """Convert ID to 5-digit format (e.g., '001' -> '00001', 'task_005' -> '10005')"""
        import re
        # Extract numeric part from ID
        match = re.search(r'(\d+)', str(id_str))
        if match:
            num = int(match.group(1))
            # Format as 5-digit number, starting from 10000 for single/double digits
            if num < 1000:
                return f"{10000 + num:05d}"
            else:
                return f"{num:05d}"
        return "10001"  # Default fallback

    def _populate_missing_fields(self, content_row: ContentRow, task: GenerationTask) -> ContentRow:
        """Populate missing WordPress fields with intelligent defaults and apply structured HTML"""

        # FIX BUG #19: Check for None before calling strip()
        # Generate category based on content/topic
        if not content_row.category or (isinstance(content_row.category, str) and content_row.category.strip() == ""):
            content_row.category = self._generate_category(content_row.title, task.topic)

        # Generate tags if missing
        if not content_row.tags or (isinstance(content_row.tags, str) and content_row.tags.strip() == ""):
            content_row.tags = self._generate_default_tags(content_row.category, task.topic)

        # Generate slug if missing using enhanced slugify
        if not content_row.slug or (isinstance(content_row.slug, str) and content_row.slug.strip() == ""):
            content_row.slug = slugify(content_row.title)  # Uses enhanced slugify without length limits

        # Set image same as category
        if not content_row.image or (isinstance(content_row.image, str) and content_row.image.strip() == ""):
            content_row.image = content_row.category

        # Apply structured HTML if enabled
        if self.structured_html and self.html_renderer:
            try:
                # Prepare site metadata for HTML renderer
                site_meta = {
                    'company_name': task.company_name or self.site_meta.get('company_name'),
                    'contact_url': task.contact_url or self.site_meta.get('contact_url'),
                    'primary_service': task.primary_service or self.site_meta.get('primary_service'),
                    'client_location': task.client_location or self.site_meta.get('client_location'),
                    'phone': task.phone or self.site_meta.get('phone'),
                    'brand_tone': task.brand_tone or self.brand_tone,
                    'hours': task.hours or self.site_meta.get('hours'),
                    'social_links': task.social_links or self.site_meta.get('social_links', [])
                }

                # Convert content to structured HTML
                structured_content = self.html_renderer.render(content_row.content, site_meta)
                content_row.content = structured_content

                self._log_info(f"Applied structured HTML to content for task {task.item_id}")

            except Exception as e:
                self._log_error(f"Failed to apply structured HTML for task {task.item_id}: {e}")

        return content_row

    def _generate_category(self, title: str, topic: str) -> str:
        """Generate appropriate category based on title and topic - generic fallback only"""
        # Extract keywords from topic to generate category
        topic_words = topic.lower().split()
        # Return capitalized first meaningful word as category
        for word in topic_words:
            if len(word) > 4 and word not in ['about', 'services', 'content']:
                return word.capitalize()
        return "General"

    def _generate_default_tags(self, category: str, topic: str) -> str:
        """Generate relevant tags based on topic keywords"""
        # Extract meaningful words from topic
        topic_words = topic.lower().split()
        tags = [word.strip('.,!?') for word in topic_words if len(word) > 3][:5]

        if not tags:
            tags = ["professional", "services"]

        return ", ".join(tags)

    def _record_error(self, task_id: str, error_type: str, error_message: str, error_details: str = "", can_retry: bool = True) -> GenerationError:
        """Record a generation error with detailed information"""
        error = GenerationError(
            task_id=task_id,
            error_type=error_type,
            error_message=error_message,
            error_details=error_details,
            timestamp=time.time(),
            can_retry=can_retry
        )
        self.errors.append(error)
        self.failed_tasks.append(task_id)
        
        if can_retry:
            self.retryable_errors.append(error)
        
        self._log_error("Generation error recorded", {
            "task_id": task_id,
            "error_type": error_type,
            "error_message": error_message,
            "can_retry": can_retry
        })
        return error

    def _detect_error_patterns(self, response_text: str) -> Optional[tuple]:
        """Detect common error patterns in LLM responses

        Returns:
            Optional[tuple]: (error_type, description) if error detected, None otherwise
        """
        error_patterns = [
            # FIXED: Reduced false positive error patterns - only match clear failures
            ("error_prefix", "ERROR:", "Response starts with ERROR:"),
            ("failed_prefix", "Failed to generate", "Response contains 'Failed to generate'"),
            ("rate_limit", "rate limit exceeded", "Response mentions rate limiting"),
            ("quota_exceeded", "quota exceeded", "Response mentions quota exceeded"),
            ("model_error", "model error:", "Response mentions model error"),
            ("service_unavailable", "service unavailable", "Response mentions service unavailable"),
            ("authentication_error", "authentication failed", "Response mentions authentication error"),
            ("python_code", "filename=", "Python code detected"),
            ("python_code", "columns=[", "Python pandas code detected"),
            ("python_code", "data=[", "Python list format detected"),
            ("python_code", "pd.DataFrame", "Python pandas code detected"),
            ("python_code", "import pandas", "Python import detected"),
            ("python_code", "df =", "Python dataframe code detected")
        ]

        response_lower = response_text.lower()
        for error_type, pattern, description in error_patterns:
            if pattern.lower() in response_lower:
                return (error_type, description)

        return None

    def _should_retry_error(self, error: GenerationError) -> bool:
        """Determine if an error should be retried"""
        if not error.can_retry:
            return False
        
        if error.retry_count >= 3:
            return False
        
        # Don't retry certain error types
        non_retryable_types = [
            "empty_response",
            "invalid_format",
            "authentication_error",
            "python_code"
        ]
        
        if error.error_type in non_retryable_types:
            return False
        
        return True

    def _get_error_summary(self) -> Dict:
        """Get a summary of all errors encountered"""
        error_summary = {
            "total_errors": len(self.errors),
            "failed_tasks": len(self.failed_tasks),
            "retryable_errors": len(self.retryable_errors),
            "error_types": {},
            "most_common_errors": []
        }
        
        # Count error types
        for error in self.errors:
            error_summary["error_types"][error.error_type] = error_summary["error_types"].get(error.error_type, 0) + 1
        
        # Get most common errors
        sorted_errors = sorted(error_summary["error_types"].items(), key=lambda x: x[1], reverse=True)
        error_summary["most_common_errors"] = sorted_errors[:5]
        
        return error_summary



    def _log_info(self, message: str, context: Dict = None):
        """Log info message with context"""
        log_data = {
            "message": message,
            "timestamp": datetime.now().isoformat(),
            **self.log_context,
            **(context or {})
        }
        logger.info(f"[BulkCSVGenerator] {message} | Context: {json.dumps(log_data)}")

    def _log_warning(self, message: str, context: Dict = None):
        """Log warning message with context"""
        log_data = {
            "message": message,
            "timestamp": datetime.now().isoformat(),
            **self.log_context,
            **(context or {})
        }
        logger.warning(f"[BulkCSVGenerator] {message} | Context: {json.dumps(log_data)}")

    def _log_error(self, message: str, context: Dict = None, exc_info: bool = False):
        """Log error message with context"""
        log_data = {
            "message": message,
            "timestamp": datetime.now().isoformat(),
            **self.log_context,
            **(context or {})
        }
        logger.error(f"[BulkCSVGenerator] {message} | Context: {json.dumps(log_data)}", exc_info=exc_info)

    def _log_debug(self, message: str, context: Dict = None):
        """Log debug message with context"""
        log_data = {
            "message": message,
            "timestamp": datetime.now().isoformat(),
            **self.log_context,
            **(context or {})
        }
        logger.debug(f"[BulkCSVGenerator] {message} | Context: {json.dumps(log_data)}")

    def _log_performance(self, operation: str, duration: float, context: Dict = None):
        """Log performance metrics"""
        log_data = {
            "operation": operation,
            "duration_seconds": duration,
            "timestamp": datetime.now().isoformat(),
            **self.log_context,
            **(context or {})
        }
        logger.info(f"[BulkCSVGenerator] Performance: {operation} took {duration:.2f}s | Context: {json.dumps(log_data)}")

    def _check_cancelled(self) -> bool:
        """Check if the job has been cancelled via unified progress system"""
        if self.cancelled:
            return True

        # Check if the process was cancelled in the unified progress system
        if self.unified_progress and self.job_id:
            try:
                process = self.unified_progress.get_process(self.job_id)
                if process and process.status.value == 'cancelled':
                    logger.info(f"Job {self.job_id} detected as cancelled in progress system")
                    self.cancelled = True
                    return True
            except Exception as e:
                logger.warning(f"Error checking cancellation status: {e}")

        return False

    def _update_progress(self, message: str, percentage: float = None, is_complete: bool = False, additional_data: dict = None):
        """Update progress using unified progress system and update associated task status"""
        logger.info(f"PROGRESS_DEBUG_V2: _update_progress entry - message='{message[:50]}...', percentage={percentage}, is_complete={is_complete}")
        if self.unified_progress and self.job_id:
            try:
                if is_complete:
                    self.unified_progress.complete_process(
                        process_id=self.job_id,
                        message=message,
                        additional_data=additional_data
                    )
                    # Update associated task status to completed
                    self._update_task_status("completed", message)
                elif percentage is not None:
                    # Round percentage UP and ensure at least 1% when tasks are actively completing
                    # Use ceiling for small percentages to show immediate progress
                    import math
                    if percentage > 0 and percentage < 1:
                        progress_value = 1  # Show 1% for any progress > 0%
                    else:
                        progress_value = math.ceil(percentage)  # Round UP to show progress
                    progress_value = min(100, progress_value)  # Cap at 100%
                    logger.info(f"PROGRESS_DEBUG: _update_progress called with percentage={percentage:.2f}, sending progress_value={progress_value}")
                    self.unified_progress.update_process(
                        process_id=self.job_id,
                        progress=progress_value,
                        message=message,
                        additional_data=additional_data
                    )
                    # Update associated task status to in-progress if not already
                    if percentage > 0:
                        self._update_task_status("in-progress", message)
                else:
                    # Calculate progress based on generated count and total items
                    # FIX BUG #20: Validate numeric types before division
                    total_items = 1  # Safe default

                    # Try multiple sources for total items, validate they're positive integers
                    if hasattr(self, '_current_tasks') and self._current_tasks:
                        try:
                            total_items = int(len(self._current_tasks))
                            if total_items <= 0:
                                total_items = 1
                        except (TypeError, ValueError):
                            total_items = 1
                    elif hasattr(self, 'total_items') and self.total_items:
                        try:
                            total_items = int(self.total_items)
                            if total_items <= 0:
                                total_items = 1
                        except (TypeError, ValueError):
                            total_items = 1
                    elif hasattr(self, '_current_tasks_count') and self._current_tasks_count:
                        try:
                            total_items = int(self._current_tasks_count)
                            if total_items <= 0:
                                total_items = 1
                        except (TypeError, ValueError):
                            total_items = 1

                    # Ensure generated_count is numeric
                    try:
                        generated = int(self.generated_count)
                    except (TypeError, ValueError):
                        generated = 0

                    calculated_progress = min(100, max(0, int(generated * 100 / total_items))) if total_items > 0 else 0
                    
                    # Enhanced progress message with item count
                    enhanced_message = f"{message} ({generated}/{total_items} items)"
                    
                    # Preserve or create additional_data with counts
                    progress_additional_data = additional_data or {}
                    if 'generated_count' not in progress_additional_data:
                        progress_additional_data['generated_count'] = generated
                    if 'target_count' not in progress_additional_data:
                        progress_additional_data['target_count'] = total_items
                    
                    self.unified_progress.update_process(
                        process_id=self.job_id,
                        progress=calculated_progress,
                        message=enhanced_message,
                        additional_data=progress_additional_data
                    )
                    
                    logger.info(f"CSV Progress: {generated}/{total_items} items ({calculated_progress}%) - {message}")
                logger.info(f"Progress updated: {message} ({percentage if percentage else 'calculated'}%)")
            except Exception as e:
                logger.warning(f"Failed to update progress: {e}")

    def _update_task_status(self, status: str, message: str = None):
        """Update associated task status in database"""
        if not self.job_id:
            return

        try:
            from backend.models import Task, db
            from flask import current_app
            # FIX BUG #12: Import datetime properly to avoid nested reference
            from datetime import datetime as dt, timezone

            # Ensure we're within application context
            if not current_app:
                try:
                    from backend.app import create_app
                    app = create_app()
                    with app.app_context():
                        self._update_task_status_with_context(status, message)
                        return
                except Exception as ctx_error:
                    logger.warning(f"Failed to create application context: {ctx_error}")
                    return

            # Find task by job_id
            task = Task.query.filter_by(job_id=self.job_id).first()
            if task:
                task.status = status
                task.updated_at = dt.now(timezone.utc)
                if message and status in ['completed', 'failed', 'error']:
                    # Store completion message in description for reference
                    if task.description:
                        task.description = f"{task.description}\n\nResult: {message}"
                    else:
                        task.description = f"Result: {message}"

                db.session.commit()
                logger.info(f"Updated task {task.id} status to '{status}' for job {self.job_id}")
            else:
                logger.warning(f"No task found with job_id {self.job_id}")

        except Exception as e:
            logger.warning(f"Failed to update task status for job {self.job_id}: {e}")
            try:
                db.session.rollback()
            except Exception as rollback_error:
                logger.warning(f"Failed to rollback database session in _update_task_status: {rollback_error}")
                # Remove the session to prevent further issues
                try:
                    db.session.remove()
                except Exception as remove_error:
                    logger.warning(f"Failed to remove database session in _update_task_status: {remove_error}")

    def _update_task_status_with_context(self, status: str, message: str = None):
        """Update task status within established application context"""
        from backend.models import Task, db
        import datetime

        try:
            # Find task by job_id
            task = Task.query.filter_by(job_id=self.job_id).first()
            if task:
                task.status = status
                task.updated_at = datetime.datetime.now(datetime.timezone.utc)
                if message and status in ['completed', 'failed', 'error']:
                    # Store completion message in description for reference
                    if task.description:
                        task.description = f"{task.description}\n\nResult: {message}"
                    else:
                        task.description = f"Result: {message}"

                db.session.commit()
                logger.info(f"Updated task {task.id} status to '{status}' for job {self.job_id}")
            else:
                logger.warning(f"No task found with job_id {self.job_id}")

        except Exception as e:
            logger.warning(f"Failed to update task status in context for job {self.job_id}: {e}")
            try:
                db.session.rollback()
            except Exception as rollback_error:
                logger.warning(f"Failed to rollback database session: {rollback_error}")
                try:
                    db.session.remove()
                except Exception as remove_error:
                    logger.warning(f"Failed to remove database session: {remove_error}")

    def _update_progress_with_error(self, error_message: str):
        """Update progress system and task status for error cases"""
        if self.unified_progress and self.job_id:
            try:
                self.unified_progress.error_process(
                    process_id=self.job_id,
                    message=error_message
                )
                # Update associated task status to failed
                self._update_task_status("failed", error_message)
                logger.info(f"Progress marked as error: {error_message}")
            except Exception as e:
                logger.warning(f"Failed to update progress with error: {e}")

    def _create_unified_prompt(self, task) -> str:
        """Create intelligent prompt using Rules system or fallback to default"""
        # Try to use prompt_rule_id if provided
        if hasattr(task, 'prompt_rule_id') and task.prompt_rule_id:
            try:
                from backend.models import Rule, db
                rule = db.session.query(Rule).filter(
                    Rule.id == task.prompt_rule_id,
                    Rule.is_active == True
                ).first()

                if rule and rule.rule_text:
                    logger.info(f"Using Rule #{rule.id}: {rule.name} for task {task.item_id}")
                    return self._inject_placeholders(rule.rule_text, task)
                else:
                    logger.warning(f"Rule #{task.prompt_rule_id} not found or inactive, trying default rule")
            except Exception as e:
                logger.error(f"Error loading rule #{task.prompt_rule_id}: {e}", exc_info=True)

        # Try format-specific default rule (NEW!)
        try:
            from backend import rule_utils
            from backend.models import db

            # Determine output format
            output_format = getattr(self, 'output_format', 'csv')
            default_rule_name = f"{output_format}_gen_default"  # e.g., "csv_gen_default"

            # Check if enhanced context should be used
            context_mode = getattr(task, 'context_mode', 'basic')
            if context_mode == 'enhanced':
                default_rule_name = f"{output_format}_gen_enhanced"  # e.g., "csv_gen_enhanced"

            logger.info(f"Attempting to load default rule: {default_rule_name}")

            # BUG FIX #8: Get active model name for rule matching with fallback
            model_name = getattr(self.llm, 'model', None) if hasattr(self, 'llm') and self.llm else None

            # Fetch rule using rule_utils
            rule_text, rule_id = rule_utils.get_active_system_prompt(
                default_rule_name, db.session, model_name=model_name
            )

            if rule_text and rule_id:
                logger.info(f"Using default rule '{default_rule_name}' (ID: {rule_id}) for task {task.item_id}")
                return self._inject_placeholders(rule_text, task)
            else:
                logger.info(f"No default rule '{default_rule_name}' found, using hardcoded fallback")

        except Exception as e:
            logger.warning(f"Error loading default rule: {e}", exc_info=True)

        # Hardcoded fallback (existing logic)
        phone = getattr(task, 'phone', None) or getattr(task, 'client_phone', None) or ""
        email = getattr(task, 'client_email', None) or ""
        location = getattr(task, 'client_location', None) or ""
        primary_service = getattr(task, 'primary_service', None) or ""
        secondary_service = getattr(task, 'secondary_service', None) or ""
        brand_tone = getattr(task, 'brand_tone', None) or "professional"
        hours = getattr(task, 'hours', None) or getattr(task, 'business_hours', None) or ""
        client_notes = getattr(task, 'client_notes', None) or ""

        # RAG Enhancement fields
        industry = getattr(task, 'industry', None) or ""
        target_audience = getattr(task, 'target_audience', None) or ""
        unique_selling_points = getattr(task, 'unique_selling_points', None) or ""
        content_goals = getattr(task, 'content_goals', None) or ""
        brand_voice_examples = getattr(task, 'brand_voice_examples', None) or ""
        regulatory_constraints = getattr(task, 'regulatory_constraints', None) or ""
        geographic_coverage = getattr(task, 'geographic_coverage', None) or ""
        keywords = getattr(task, 'keywords', [])
        competitor_urls_list = getattr(task, 'competitor_urls_list', [])

        # Build comprehensive business context from actual client data
        business_context_parts = [
            f"Company Name: {task.client}",
            f"Website: {task.website}"
        ]

        # Add CRITICAL industry information first
        if industry and industry.strip():
            business_context_parts.append(f"Industry: {industry}")

        # Add client description if available
        if client_notes and client_notes.strip():
            business_context_parts.append(f"Business Description: {client_notes}")

        # Add services - CRITICAL for context
        if primary_service and primary_service.strip():
            business_context_parts.append(f"Primary Service: {primary_service}")
        if secondary_service and secondary_service.strip():
            business_context_parts.append(f"Secondary Service: {secondary_service}")

        # Add unique selling points
        if unique_selling_points and unique_selling_points.strip():
            business_context_parts.append(f"Unique Selling Points: {unique_selling_points}")

        # Add target audience
        if target_audience and target_audience.strip():
            business_context_parts.append(f"Target Audience: {target_audience}")

        # Add location & geographic coverage
        if location and location.strip():
            business_context_parts.append(f"Location: {location}")
        if geographic_coverage and geographic_coverage.strip():
            business_context_parts.append(f"Geographic Coverage: {geographic_coverage}")

        # Add contact info
        if phone and phone.strip():
            business_context_parts.append(f"Phone: {phone}")
        if email and email.strip():
            business_context_parts.append(f"Email: {email}")

        # Add brand tone & voice examples
        business_context_parts.append(f"Brand Tone: {brand_tone}")
        if brand_voice_examples and brand_voice_examples.strip():
            business_context_parts.append(f"Brand Voice Example: {brand_voice_examples[:200]}")  # Limit length

        # Add content goals
        if content_goals and content_goals.strip():
            business_context_parts.append(f"Content Goals: {content_goals}")

        # Add compliance/regulatory info
        if regulatory_constraints and regulatory_constraints.strip():
            business_context_parts.append(f"Compliance Requirements: {regulatory_constraints}")

        # Add target keywords
        if keywords and len(keywords) > 0:
            business_context_parts.append(f"Target Keywords: {', '.join(keywords[:10])}")  # Limit to first 10

        business_context = "\n".join([f"- {part}" for part in business_context_parts])

        # Create content guidelines based on actual services and industry
        content_focus = ""
        if industry and industry.strip():
            content_focus = f"\n\n**INDUSTRY CONTEXT:** This business operates in: {industry}"

        if primary_service and primary_service.strip():
            content_focus += f"\n\n**CONTENT MUST FOCUS ON:** {primary_service}"
            if secondary_service and secondary_service.strip():
                content_focus += f" and {secondary_service}"
            content_focus += f"\n\n**CRITICAL - FORBIDDEN TOPICS:**"
            content_focus += f"\n- Do NOT write about HVAC, Medical Waste, Healthcare Operations, Environmental Remediation, or Industrial Maintenance"
            content_focus += f"\n- Do NOT invent services not listed in the business profile above"
            content_focus += f"\n- ONLY write about: {primary_service}"
            if secondary_service and secondary_service.strip():
                content_focus += f" and {secondary_service}"
            content_focus += f"\n- If the industry is '{industry}', ensure ALL content aligns with that industry"

        row_id = getattr(task, 'item_id', 'unknown')

        return f"""Generate exactly ONE CSV row with 7 comma-separated fields.

**BUSINESS PROFILE:**
{business_context}

**Content Topic:** {task.topic}
{content_focus}

**CSV FORMAT - CRITICAL:**
Output: "ID","Title","Content","Excerpt","Category","Tags","slug"

**CSV ESCAPING RULES - MUST FOLLOW:**
1. Each field wrapped in double quotes
2. Inside Content field, use SINGLE quotes for HTML attributes
3. NO double quotes inside any field
4. Keep content on ONE line
5. Commas are allowed inside quoted fields (for tags)

**CORRECT Example Format:**
"{row_id}","Professional Services and Solutions","<p><strong>Expert professional services</strong> for your needs.</p>","Expert guidance for complex situations","Professional Services","professional, consultation, expert, quality, trusted","professional-services-and-solutions"

**Field Requirements:**
- ID: MUST BE EXACTLY "{row_id}" - use this exact value
- Title: 50-70 characters, SEO-optimized, topic-specific about {task.topic} for {task.client}
- Content: MINIMUM {self.target_word_count} words of professional HTML content
  * Write SPECIFICALLY about {task.topic} as it relates to {task.client}'s business
  * Use the business description and services above to inform your content
  * Use HTML tags: <h2>, <h3>, <p>, <strong>, <em>, <ul>, <li>
  * IMPORTANT: Use SINGLE quotes for ALL HTML attributes: class='foo' NOT class="foo"
  * Keep content on ONE line (no line breaks)
  * Focus EXCLUSIVELY on the topic and services listed above
  * LOCATION USAGE: Use locations for SEO targeting (Title, Slug, Tags) but DO NOT say "We're located in [city]" or "Based in the heart of [location]". Instead, use phrases like "Serving [location]" or "[Service] in [location]"
- Excerpt: 150-165 characters compelling summary (plain text, no HTML)
- Category: ONE simple category phrase based on {task.topic} and relevant to {primary_service or 'the business'}
- Tags: 3-5 specific keywords separated by commas, relevant to {task.topic} and {primary_service or 'the business'}
  * Example: "search optimization, website ranking, SEO strategy"
- slug: lowercase-with-hyphens URL slug

**CRITICAL RULES:**
1. ID field MUST be exactly: {row_id}
2. Base ALL content on: {task.topic}, {task.client}, and the business profile above
3. Content MUST align with the services: {primary_service or 'professional services'}{f' and {secondary_service}' if secondary_service else ''}
4. HTML attributes MUST use SINGLE quotes only
5. Output ONLY the CSV row, nothing else

Generate the CSV row now:"""

    def _inject_placeholders(self, rule_text: str, task) -> str:
        """Inject task data into rule template placeholders"""
        # BUG FIX #4: Extract metadata from task with proper fallbacks
        phone = getattr(task, 'phone', None) or getattr(task, 'client_phone', None) or ""
        location = getattr(task, 'client_location', None) or ""
        primary_service = getattr(task, 'primary_service', None) or ""
        secondary_service = getattr(task, 'secondary_service', None) or ""
        client_notes = getattr(task, 'client_notes', None) or ""
        brand_tone = getattr(task, 'brand_tone', None) or "professional"

        # BUG FIX #5: Handle both task.website and task properties
        website = getattr(task, 'website', '') or ""
        client = getattr(task, 'client', '') or "Client"
        topic = getattr(task, 'topic', '') or ""
        item_id = getattr(task, 'item_id', '') or ""

        # Enhanced context placeholders (if available)
        entity_context = getattr(task, 'entity_context', '') or ""
        competitor_context = getattr(task, 'competitor_context', '') or ""
        document_context = getattr(task, 'document_context', '') or ""

        # RAG Enhancement fields
        industry = getattr(task, 'industry', None) or ""
        target_audience = getattr(task, 'target_audience', None) or ""
        unique_selling_points = getattr(task, 'unique_selling_points', None) or ""
        content_goals = getattr(task, 'content_goals', None) or ""
        brand_voice_examples = getattr(task, 'brand_voice_examples', None) or ""
        regulatory_constraints = getattr(task, 'regulatory_constraints', None) or ""
        geographic_coverage = getattr(task, 'geographic_coverage', None) or ""
        keywords = getattr(task, 'keywords', [])
        competitor_urls_list = getattr(task, 'competitor_urls_list', [])

        # Convert arrays to strings
        keywords_str = ", ".join(keywords) if keywords else ""
        competitor_urls_str = ", ".join(competitor_urls_list) if competitor_urls_list else ""

        # Standard replacements
        replacements = {
            '{client}': client,
            '{client_notes}': client_notes,
            '{website}': website,
            '{topic}': topic,
            '{row_id}': item_id,
            '{word_count}': str(self.target_word_count),
            '{primary_service}': primary_service,
            '{secondary_service}': secondary_service,
            '{phone}': phone,
            '{location}': location,
            '{brand_tone}': brand_tone,
            # Enhanced context
            '{entity_context}': entity_context,
            '{competitor_context}': competitor_context,
            '{document_context}': document_context,
            # RAG Enhancement fields
            '{industry}': industry,
            '{target_audience}': target_audience,
            '{unique_selling_points}': unique_selling_points,
            '{content_goals}': content_goals,
            '{brand_voice_examples}': brand_voice_examples,
            '{regulatory_constraints}': regulatory_constraints,
            '{geographic_coverage}': geographic_coverage,
            '{keywords}': keywords_str,
            '{competitor_urls}': competitor_urls_str,
        }

        prompt = rule_text
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, str(value))

        return prompt

    def _create_slug(self, title: str) -> str:
        """Create a URL-friendly slug from title using enhanced slugify"""
        return slugify(title)  # Uses enhanced slugify without length limits

    def _sanitize_html_content(self, content: str) -> str:
        """Sanitize and validate HTML content to fix common formatting issues"""
        if not content:
            return content

        # Fix escaped quotes that break HTML
        content = content.replace('\\"', '"')
        content = content.replace('\\"\\"\\"', '"')

        # Fix broken div/class attributes
        content = content.replace('class=\\', 'class="')
        content = content.replace(r'\content', 'content')

        # Remove double escaping
        content = content.replace('\\n', '\n')
        content = content.replace('\\t', '\t')

        # Ensure proper HTML tag formatting
        import re
        # Fix malformed tags like <div class=\content-section\>
        content = re.sub(r'<(\w+)\s+class=\\([^\\>]+)\\', r'<\1 class="\2"', content)

        return content.strip()

    def _parse_csv_response(self, response: str, task: GenerationTask) -> Optional[ContentRow]:
        """Parse CSV response with flexible field handling"""
        import csv
        from io import StringIO
        
        try:
            # Clean response and handle common CSV wrapping issues
            response_clean = response.strip()
            if response_clean.startswith('```csv'):
                response_clean = response_clean[6:]
            if response_clean.endswith('```'):
                response_clean = response_clean[:-3]
            response_clean = response_clean.strip()
            
            # Parse CSV with flexible field handling
            reader = csv.reader(StringIO(response_clean))
            fields = next(reader)

            # Log field count for debugging
            logger.info(f"Received {len(fields)} fields for task {task.topic[:50]}...")

            # Handle variable field counts by mapping to expected positions
            # Expected order: id, title, content, excerpt, category, tags, slug, meta_description
            def safe_get_field(index: int, default: str = "") -> str:
                return fields[index].strip() if index < len(fields) else default

            # Generate missing fields if needed
            # FIX BUG #13: Ensure time module is available
            import time as time_module
            # ALWAYS use the unique item_id from the task (website-scoped ID) - ignore LLM-generated ID
            task_id = task.item_id if hasattr(task, 'item_id') and task.item_id else f"task_{int(time_module.time() * 1000) % 10000:04d}"

            # Log if LLM generated wrong ID
            llm_generated_id = safe_get_field(0, "")
            if llm_generated_id and llm_generated_id != task_id:
                if llm_generated_id.startswith("task_"):
                    logger.warning(f"FAILSAFE: LLM generated incorrect ID '{llm_generated_id}', forcing to '{task_id}'")
                else:
                    logger.info(f"Using task_id: {task_id} (LLM provided: {llm_generated_id})")
            else:
                logger.info(f"Using task_id: {task_id} from task.item_id: {getattr(task, 'item_id', 'MISSING')}")
            title = safe_get_field(1, task.topic[:100] if task.topic else "Generated Content")
            content = safe_get_field(2)
            excerpt = safe_get_field(3, content[:155] + "..." if content else "")
            category = safe_get_field(4, getattr(task, 'category', 'General'))
            # Generate meaningful fallback tags from topic instead of generic 'content|generation'
            topic_words = task.topic.lower().split()[:5] if task.topic else ['general']
            fallback_tags = ', '.join([word.strip('.,!?') for word in topic_words if len(word) > 3])
            tags = safe_get_field(5, fallback_tags if fallback_tags else 'professional, services')
            # CRITICAL: Always regenerate slug to ensure proper formatting
            slug_from_llm = safe_get_field(6, "")
            # Enforce proper slug formatting using our slugify utility
            slug = self._create_slug(title) if title else slug_from_llm
            logger.info(f"Slug enforcement: LLM='{slug_from_llm}' -> Enforced='{slug}'")

            meta_description = safe_get_field(7, excerpt[:160] if excerpt else "")
            
            # Handle cases where content might be in a different position for longer responses
            # FIX BUG #14: Validate range before calling max to avoid ValueError
            if len(fields) > 7 and len(fields) >= 3:
                # If we have extra fields, content might be the longest field
                search_end = min(len(fields), 5)
                if search_end > 2:  # Ensure we have a valid range
                    longest_field_idx = max(range(2, search_end),
                                          key=lambda i: len(fields[i]) if i < len(fields) else 0)
                    if len(fields[longest_field_idx]) > len(content):
                        content = fields[longest_field_idx]
                    logger.info(f"Using field {longest_field_idx} as content (length: {len(content)})")
            
            # BUG FIX CSV-1: Stricter minimum content validation to prevent truncated content
            # Require at least 200 characters AND 30 words to pass parsing
            content_stripped = content.strip() if content else ""
            word_count_early = len(content_stripped.split())

            if not content_stripped or len(content_stripped) < 200 or word_count_early < 30:
                logger.error(f"Content too short for task {task_id}: {len(content_stripped)} chars, {word_count_early} words (minimum: 200 chars, 30 words)")
                return None
            
            # Sanitize HTML content to fix formatting issues
            content = self._sanitize_html_content(content)

            # AUTO-CORRECTION: Fix common field mapping issues
            # If Category has pipes or commas, it's probably Tags
            if ('|' in category or ',' in category) and not ('|' in tags or ',' in tags):
                logger.warning(f"AUTO-CORRECT: Swapping Category '{category[:50]}...' with Tags '{tags[:50]}...'")
                category, tags = tags, category

            # If Excerpt is too short (< 50 chars) and Category is long (> 100 chars), swap them
            if len(excerpt) < 50 and len(category) > 100:
                logger.warning(f"AUTO-CORRECT: Swapping short Excerpt with long Category")
                excerpt, category = category, excerpt

            # If Content is too short (< 100 chars) but Excerpt is long, swap them
            if len(content) < 100 and len(excerpt) > 200:
                logger.warning(f"AUTO-CORRECT: Swapping short Content ({len(content)} chars) with long Excerpt ({len(excerpt)} chars)")
                content, excerpt = excerpt, content

            # Convert pipe separators to commas in tags if needed
            if '|' in tags:
                tags = tags.replace('|', ', ')
                logger.info(f"AUTO-CORRECT: Converted pipe separators to commas in tags")

            # BUG FIX CSV-6: Validate and fix Excerpt field
            if not excerpt or len(excerpt.strip()) < 50:
                # Generate excerpt from content - strip HTML and take first 150 chars
                import re
                text_only = re.sub('<[^<]+?>', '', content)  # Remove HTML tags
                text_only = ' '.join(text_only.split())  # Normalize whitespace
                excerpt = text_only[:150] + "..." if len(text_only) > 150 else text_only
                logger.info(f"AUTO-CORRECT: Generated excerpt from content ({len(excerpt)} chars)")
            elif len(excerpt) > 200:
                excerpt = excerpt[:197] + "..."
                logger.info(f"AUTO-CORRECT: Truncated excerpt to 200 chars")

            # Remove HTML from excerpt if present
            if '<' in excerpt:
                import re
                excerpt = re.sub('<[^<]+?>', '', excerpt)
                excerpt = ' '.join(excerpt.split())[:197] + "..."
                logger.info(f"AUTO-CORRECT: Removed HTML from excerpt")

            # BUG FIX CSV-7: Validate and fix Category field
            category_invalid = False

            if category:
                # Remove JSON array formatting if present: [""Law Firm""] or ["Law Firm"]
                if category.startswith('[') and category.endswith(']'):
                    logger.warning(f"AUTO-CORRECT: Removing JSON array formatting from category: {category}")
                    category = category.strip('[]').strip('"').strip("'").strip()
                    category_invalid = len(category) < 3

                # Detect if slug accidentally ended up in category (contains hyphens and looks like URL)
                if '-' in category or category.endswith(')'):
                    hyphen_count = category.count('-')
                    if hyphen_count >= 3 or category.endswith(')'):
                        logger.warning(f"AUTO-CORRECT: Category '{category}' looks like a slug, regenerating...")
                        category_invalid = True

                # Detect if it's just a city/location name (single lowercase word or title case city)
                if ' ' not in category and (category.islower() or category.istitle()):
                    # Check if it matches a known city pattern
                    if len(category) < 15:  # Cities are typically short names
                        logger.warning(f"AUTO-CORRECT: Category '{category}' looks like a city name, regenerating...")
                        category_invalid = True

                # Detect concatenated words without spaces (e.g., "AssetForfeitureDefenseStockton")
                if category and ' ' not in category and len(category) > 20:
                    # Likely concatenated, check for capital letters indicating word boundaries
                    capital_count = sum(1 for c in category if c.isupper())
                    if capital_count >= 3:
                        logger.warning(f"AUTO-CORRECT: Category '{category}' appears concatenated ({capital_count} capitals), regenerating...")
                        category_invalid = True

            if category_invalid or not category or len(category.strip()) < 3:
                # Generate category from primary service, industry, or topic
                primary_service = getattr(task, 'primary_service', None)
                industry = getattr(task, 'industry', None)

                if primary_service:
                    category = primary_service
                elif industry:
                    category = industry
                else:
                    # Extract main concept from title (first 2-3 significant words)
                    title_words = [w for w in title.split() if len(w) > 3][:3]
                    category = ' '.join(title_words) if title_words else "General"
                logger.info(f"AUTO-CORRECT: Generated category: {category}")

            # Category should be 2-3 words max, no punctuation
            if ',' in category or len(category) > 50:
                category = category.split(',')[0].strip()[:50]
                logger.info(f"AUTO-CORRECT: Simplified category to: {category}")

            # BUG FIX CSV-8: Validate and fix Tags field
            # Tags should be comma-separated, 3-7 tags, each 1-3 words
            if tags:
                # Split by comma and clean
                tag_list = [t.strip() for t in tags.split(',') if t.strip()]

                # Remove tags that are too long (likely full sentences)
                tag_list = [t for t in tag_list if len(t.split()) <= 4 and len(t) <= 40]

                # Ensure we have 3-7 tags
                if len(tag_list) < 3:
                    # Add fallback tags from topic
                    topic_words = [w.strip('.,!?:') for w in task.topic.split() if len(w) > 4][:3]
                    tag_list.extend(topic_words)
                    tag_list = list(dict.fromkeys(tag_list))[:7]  # Remove duplicates, max 7
                elif len(tag_list) > 7:
                    tag_list = tag_list[:7]

                tags = ', '.join(tag_list)
                logger.info(f"AUTO-CORRECT: Fixed tags to: {tags}")

            # BUG FIX: Apply insert_content if specified
            final_content = content
            if hasattr(task, 'insert_content') and task.insert_content and hasattr(task, 'insert_position') and task.insert_position:
                if task.insert_position == 'top':
                    final_content = f"{task.insert_content}\n\n{content}"
                elif task.insert_position == 'bottom':
                    final_content = f"{content}\n\n{task.insert_content}"
                # 'none' or any other value means no insertion
                logger.info(f"Applied insert_content to task {task_id} at position: {task.insert_position}")
                
            return ContentRow(
                id=task_id,
                title=title,
                content=final_content,
                excerpt=excerpt,
                category=category,
                tags=tags,
                slug=slug,
                meta_description=meta_description
            )
            
        except Exception as e:
            logger.error(f"Error parsing CSV response: {e}")
            logger.debug(f"Response was: {response[:200]}...")
            return None
    
    def _cleanup_unused_method_1(self, content_row: ContentRow) -> bool:
        """Validate that a CSV row meets quality standards"""
        try:
            # Check required fields are not empty
            required_fields = [
                (content_row.id, "ID"),
                (content_row.title, "Title"),
                (content_row.content, "Content"),
                (content_row.excerpt, "Excerpt")
            ]
            
            for field_value, field_name in required_fields:
                if not field_value or field_value.strip() == "":
                    logger.warning(f"Missing required field {field_name} in row {content_row.id}")
                    return False
            
            # Validate content length
            if len(content_row.content.strip()) < 50:
                logger.warning(f"Content too short ({len(content_row.content)} chars) in row {content_row.id}")
                return False
            
            if len(content_row.content.strip()) > 10000:
                logger.warning(f"Content too long ({len(content_row.content)} chars) in row {content_row.id}")
                return False
            
            # Validate excerpt length (optional field) - relaxed for book descriptions
            if content_row.excerpt and len(content_row.excerpt.strip()) > 500:
                logger.warning(f"Excerpt too long ({len(content_row.excerpt)} chars) in row {content_row.id}")
                return False
            
            # Validate title length
            if len(content_row.title.strip()) < 10:
                logger.warning(f"Title too short ({len(content_row.title)} chars) in row {content_row.id}")
                return False
            
            if len(content_row.title.strip()) > 200:
                logger.warning(f"Title too long ({len(content_row.title)} chars) in row {content_row.id}")
                return False
            
            # Check for common error patterns
            error_patterns = [
                "ERROR:",
                "Failed to generate",
                "Unable to create",
                "[ERROR]",
                "[FAILED]",
                "Sorry, I cannot",
                "I apologize",
                "I'm unable to"
            ]
            
            content_lower = content_row.content.lower()
            for pattern in error_patterns:
                if pattern.lower() in content_lower:
                    logger.warning(f"Error pattern detected in content: {pattern} in row {content_row.id}")
                    return False
            
            # Validate ID format (should be alphanumeric)
            if not content_row.id.replace("-", "").replace("_", "").isalnum():
                logger.warning(f"Invalid ID format: {content_row.id}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating CSV row {content_row.id}: {e}")
            return False
    
    def _validate_csv_file(self, file_path: str) -> bool:
        """Validate the entire CSV file for proper format and content"""
        try:
            if not os.path.exists(file_path):
                logger.error(f"CSV file does not exist: {file_path}")
                return False
            
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.error(f"CSV file is empty: {file_path}")
                return False
            
            # Read and validate CSV structure
            with open(file_path, 'r', encoding='utf-8') as csvfile:
                reader = csv.reader(csvfile)
                
                # Check headers
                try:
                    headers = next(reader)
                    expected_headers = ["ID", "Title", "Content", "Excerpt", "Category", "Tags", "slug"]
                    
                    if len(headers) != len(expected_headers):
                        logger.error(f"CSV headers count mismatch: expected {len(expected_headers)}, got {len(headers)}")
                        return False
                    
                    for i, (expected, actual) in enumerate(zip(expected_headers, headers)):
                        if expected != actual:
                            logger.error(f"CSV header mismatch at position {i}: expected '{expected}', got '{actual}'")
                            return False
                    
                except StopIteration:
                    logger.error(f"CSV file has no headers: {file_path}")
                    return False
                
                # Check data rows
                row_count = 0
                for row_num, row in enumerate(reader, start=2):  # Start at 2 because we already read headers
                    row_count += 1
                    
                    if len(row) != len(expected_headers):
                        logger.error(f"Row {row_num} has wrong field count: expected {len(expected_headers)}, got {len(row)}")
                        return False
                    
                    # Check for empty required fields
                    if not row[0].strip() or not row[1].strip() or not row[2].strip():
                        logger.error(f"Row {row_num} has empty required fields: ID='{row[0]}', Title='{row[1]}', Content='{row[2]}'")
                        return False
                
                if row_count == 0:
                    logger.error(f"CSV file has no data rows: {file_path}")
                    return False
                
                logger.info(f"CSV validation passed: {file_path} ({row_count} data rows)")
                return True
                
        except Exception as e:
            logger.error(f"Error validating CSV file {file_path}: {e}")
            return False

    def _validate_job_completion_requirements(self, file_path: str, expected_tasks: int) -> bool:
        """Validate that the job meets completion requirements before marking as complete"""
        try:
            if not os.path.exists(file_path):
                logger.error(f"Job completion validation failed: File does not exist: {file_path}")
                return False
            
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.error(f"Job completion validation failed: File is empty: {file_path}")
                return False
            
            # Count actual data rows in the file
            with open(file_path, 'r', encoding='utf-8') as csvfile:
                reader = csv.reader(csvfile)
                
                # Skip header
                try:
                    next(reader)
                except StopIteration:
                    logger.error(f"Job completion validation failed: No headers found in file: {file_path}")
                    return False
                
                # Count data rows
                data_row_count = 0
                for row in reader:
                    if len(row) >= 3 and row[0].strip() and row[1].strip() and row[2].strip():
                        data_row_count += 1
                
                logger.info(f"Job completion validation: Found {data_row_count} valid data rows, expected {expected_tasks} tasks")
                
                # Check if we have a reasonable number of successful generations
                success_rate = data_row_count / expected_tasks if expected_tasks > 0 else 0
                
                # More lenient success rate requirement - allow 25% success rate for small batches, 30% for larger batches
                min_success_rate = 0.25 if expected_tasks <= 10 else 0.30
                
                if success_rate < min_success_rate:
                    logger.error(f"Job completion validation failed: Success rate too low ({success_rate:.1%}) - only {data_row_count}/{expected_tasks} items generated (minimum: {min_success_rate:.1%})")
                    return False
                
                if data_row_count == 0:
                    logger.error(f"Job completion validation failed: No valid data rows generated")
                    return False

                # FIX BUG #15: File already open in outer scope, don't reopen
                # Check for minimum content quality using existing reader
                csvfile.seek(0)  # Reset file pointer to beginning
                reader = csv.reader(csvfile)
                next(reader)  # Skip header

                quality_issues = 0
                for row_num, row in enumerate(reader, start=2):
                    if len(row) >= 3:
                        content = row[2].strip()
                        if len(content) < 30:  # More lenient minimum content length
                            quality_issues += 1
                        elif len(content) > 15000:  # More lenient maximum content length
                            quality_issues += 1

                quality_rate = (data_row_count - quality_issues) / data_row_count if data_row_count > 0 else 0
                if quality_rate < 0.6:  # More lenient quality requirement - require at least 60% quality rate
                    logger.error(f"Job completion validation failed: Quality rate too low ({quality_rate:.1%}) - {quality_issues} quality issues found (minimum: 60%)")
                    return False
                
                logger.info(f"Job completion validation passed: {data_row_count} valid rows, {success_rate:.1%} success rate, quality rate acceptable")
                return True
                
        except Exception as e:
            logger.error(f"Error in job completion validation for {file_path}: {e}")
            return False
    
    
    
    def _write_csv_with_conversion(self, rows: List[Dict], output_path: str) -> None:
        """Write CSV with pipe-to-comma conversion for WordPress compatibility"""
        try:
            # Import the write_csv function
            from backend.tools.file_processor import write_csv
            
            # First, try to write as standard comma-separated CSV
            write_csv(rows, output_path)
            logger.info(f"Successfully wrote standard CSV: {output_path}")
            
        except Exception as e:
            logger.warning(f"Standard CSV writing failed: {e}")
            logger.info("Falling back to pipe-separated CSV format")
            
            # Fallback: Write as pipe-separated CSV
            try:
                import os
                # FIX BUG #16: Handle case where output_path has no directory
                output_dir = os.path.dirname(output_path)
                if output_dir:  # Only create directory if path includes one
                    os.makedirs(output_dir, exist_ok=True)
                
                with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
                    if rows:
                        # Write header
                        fieldnames = list(rows[0].keys())
                        csvfile.write('|'.join(fieldnames) + '\n')
                        
                        # Write data rows
                        for row in rows:
                            row_values = [str(row.get(field, '')) for field in fieldnames]
                            csvfile.write('|'.join(row_values) + '\n')
                    else:
                        # Write empty file with headers
                        fieldnames = ['ID', 'Title', 'Content', 'Excerpt', 'Category', 'Tags', 'slug']
                        csvfile.write('|'.join(fieldnames) + '\n')
                
                logger.info(f"Successfully wrote pipe-separated CSV: {output_path}")
                
            except Exception as fallback_error:
                logger.error(f"Both CSV writing methods failed: {fallback_error}")
                raise Exception(f"Failed to write CSV file with both methods: {fallback_error}")
    
    def _validate_content_row(self, content_row: ContentRow) -> bool:
        """Validate content row using basic validation checks"""
        try:
            # Basic validation checks
            if not content_row.id or not content_row.id.strip():
                self._log_warning(f"Content row missing ID")
                return False

            if not content_row.title or not content_row.title.strip():
                self._log_warning(f"Content row {content_row.id} missing title")
                return False

            if not content_row.content or len(content_row.content.strip()) < 50:
                self._log_warning(f"Content row {content_row.id} has insufficient content")
                return False

            # BUG FIX CSV-2: Detect truncated/malformed HTML content
            content = content_row.content.strip()

            # Check for incomplete HTML tags at the end
            incomplete_endings = ('<', '</', '</h', '</p', '</div', '<h', '<p', '<div', '</ul', '</li')
            if any(content.endswith(ending) for ending in incomplete_endings):
                self._log_warning(f"Truncated HTML detected for row {content_row.id}: ends with incomplete tag")
                return False

            # Check for severely unbalanced HTML tags
            open_tags = content.count('<p>') + content.count('<h1>') + content.count('<h2>') + content.count('<div>')
            close_tags = content.count('</p>') + content.count('</h1>') + content.count('</h2>') + content.count('</div>')

            if open_tags > 0 and close_tags == 0:
                self._log_warning(f"Malformed HTML for row {content_row.id}: {open_tags} open tags, {close_tags} close tags")
                return False

            # BUG FIX CSV-3: Check for content that looks like metadata or error messages
            lower_content = content.lower()
            error_patterns = [
                'here is a sample bulk csv',
                'this csv file contains',
                'expected format:',
                'error:',
                'case number","officer name',  # Generic CSV header pattern detection
                'agency name","date of seizure'  # Generic CSV header pattern detection
            ]

            for pattern in error_patterns:
                if pattern in lower_content[:200]:  # Check first 200 chars
                    self._log_warning(f"Content appears to be metadata/error for row {content_row.id}: contains '{pattern}'")
                    return False

            # Note: Excerpt, Category, and Tags validation removed
            # Auto-correction in parsing (CSV-6, CSV-7, CSV-8) ensures these are properly formatted
            # Validation only checks critical fields (ID, Title, Content)

            # Validate slug format
            if content_row.slug and not is_valid_slug(content_row.slug):
                self._log_warning(f"Content row {content_row.id} has invalid slug format: {content_row.slug}")
                # Don't fail - slug will be regenerated if needed

            return True

        except Exception as e:
            self._log_error(f"Error in content row validation: {e}")
            return False

    def _generate_single_content(self, task: GenerationTask) -> Optional[ContentRow]:
        """Generate content for a single task"""
        try:
            if not self.llm:
                logger.error(f"LLM instance not available for task {task.item_id}")
                return None
            
            # Create unified context-aware prompt
            prompt = self._create_unified_prompt(task)
            
            # Generate content using direct LLM instance
            logger.info(f"Generating content for task {task.item_id} with prompt length: {len(prompt)}")
            # DEBUG: Log first 500 chars of prompt to verify {row_id} replacement
            logger.info(f"DEBUG PROMPT PREVIEW (first 500 chars): {prompt[:500]}")
            
            try:
                # Use the same pattern as llm_service.py for consistent results
                from backend.utils.llm_service import ChatMessage, MessageRole

                # Build system prompt with retry feedback if this is a retry attempt
                system_prompt = "You are a professional content generator. Generate high-quality CSV content as requested. Follow the exact format specified."

                # Add feedback from previous attempt if available
                if hasattr(task, 'previous_attempt_failed_reason') and task.previous_attempt_failed_reason:
                    if task.previous_attempt_failed_reason == "content_too_short":
                        word_count = getattr(task, 'previous_attempt_word_count', 0)
                        system_prompt += f"\n\nIMPORTANT: Previous attempt generated only {word_count} words which was too short. You MUST generate at least {self.target_word_count} words of detailed, comprehensive content. Expand on the topic with more detail, examples, and information."
                    elif task.previous_attempt_failed_reason == "validation_failed":
                        system_prompt += "\n\nIMPORTANT: Previous attempt failed validation. Ensure all required fields are properly filled and formatted according to the CSV specification."
                    logger.info(f"Enhanced system prompt with retry feedback: {task.previous_attempt_failed_reason}")

                messages = [
                    ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
                    ChatMessage(role=MessageRole.USER, content=prompt),
                ]
                llm_response = self.llm.chat(messages)
                logger.info(f"LLM chat response type: {type(llm_response)}")
                
                # Extract content from chat response
                if llm_response and hasattr(llm_response, 'message') and llm_response.message:
                    try:
                        response_text = llm_response.message.content
                    except (ValueError, AttributeError):
                        blocks = getattr(llm_response.message, 'blocks', [])
                        response_text = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "")
                    logger.info(f"Using llm_response.message.content: {len(response_text) if response_text else 0} chars")
                else:
                    response_text = str(llm_response) if llm_response else ""
                    logger.info(f"Using str(llm_response): {len(response_text) if response_text else 0} chars")
                
                # DEBUG: Log response content for empty CSV diagnosis
                if not response_text or len(response_text.strip()) == 0:
                    logger.error(f"EMPTY RESPONSE DEBUG for task {task.item_id}:")
                    logger.error(f"  - llm_response: {llm_response}")
                    logger.error(f"  - response_text: '{response_text}'")
                    logger.error(f"  - hasattr message: {hasattr(llm_response, 'message') if llm_response else 'N/A'}")
                    if llm_response and hasattr(llm_response, 'message'):
                        logger.error(f"  - message: {llm_response.message}")
                        if llm_response.message:
                            try:
                                logger.error(f"  - message.content: '{llm_response.message.content}'")
                            except (ValueError, AttributeError):
                                blocks = getattr(llm_response.message, 'blocks', [])
                                _debug_content = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "")
                                logger.error(f"  - message.content (from blocks): '{_debug_content}'")
                    return None
                    
            except Exception as e:
                logger.error(f"LLM chat() failed for task {task.item_id}: {e}", exc_info=True)
                return None
            
            # Validate response text
            if not response_text or not response_text.strip():
                self._record_error(task.item_id, "empty_response", "Empty or null response", f"Response text is empty. LLM response: {llm_response}", can_retry=True)
                return None
            
            logger.info(f"Response text preview: {response_text[:100] if response_text else 'EMPTY'}")
            
            # Check for error patterns in response
            error_detection = self._detect_error_patterns(response_text)
            if error_detection:
                error_type, error_description = error_detection
                self._record_error(task.item_id, error_type, error_description, f"Error pattern detected in response: {response_text[:200]}...", can_retry=True)
                return None
            
            # Parse and validate the CSV response
            content_row = self._parse_csv_response(response_text, task)
            
            if not content_row:
                self._record_error(task.item_id, "csv_parsing_failed", "Failed to parse CSV response", f"Could not parse CSV from response: {response_text[:200]}...", can_retry=True)
                return None
            
            # Validate using enhanced validation system
            if not self._validate_content_row(content_row):
                self._record_error(task.item_id, "validation_failed", "Content row validation failed", f"Row failed validation: {content_row.id}", can_retry=True)
                return None
            
            # STRICT: Validate content length - MINIMUM required, no tolerance below minimum
            word_count = len(content_row.content.split())
            minimum_words = self.target_word_count  # Enforce true minimum
            if word_count < minimum_words:
                error_msg = f"Content too short: {word_count} words (minimum {minimum_words} required)"
                logger.warning(f"{error_msg} - Will retry with enhanced prompt")
                self._record_error(task.item_id, "content_too_short", error_msg,
                                 f"Generated {word_count} words, required minimum {minimum_words}", can_retry=True)
                # Store the issue details for the retry attempt
                task.previous_attempt_word_count = word_count
                task.previous_attempt_failed_reason = "content_too_short"
                return None
            elif word_count < self.target_word_count * 1.1:  # Warn if just barely meeting minimum
                logger.warning(f"Content word count ({word_count}) meets minimum but is below ideal for task {task.item_id}")
            
            logger.info(f"Generated content for task {task.item_id}: {word_count} words")
            return content_row

        except Exception as e:
            logger.error(f"Error generating content for task {task.item_id}: {e}", exc_info=True)
            return None

    def _create_generation_record(self, tasks: List[GenerationTask], site_key: str = None) -> str:
        """Create a generation record using the repository layer"""
        if not self.enable_persistence:
            return None

        try:
            # NOTE: GenerationRepository not yet implemented - persistence disabled
            self._log_warning("Persistence layer not yet implemented - skipping generation record")
            return None

            # TODO: Uncomment when repository layer is implemented
            # Extract metadata from first task
            # first_task = tasks[0] if tasks else None
            # meta_data = {}
            # if first_task:
            #     meta_data = {
            #         'phone': first_task.phone,
            #         'contact_url': first_task.contact_url,
            #         'client_location': first_task.client_location,
            #         'company_name': first_task.company_name,
            #         'primary_service': first_task.primary_service,
            #         'secondary_service': first_task.secondary_service,
            #         'brand_tone': first_task.brand_tone or self.brand_tone,
            #         'hours': first_task.hours,
            #         'social_links': first_task.social_links or []
            #     }

            # Create generation using repository
            # generation = GenerationRepository.create(
            #     site_key=site_key or (first_task.website if first_task else 'bulk_generation'),
            #     delimiter=self.csv_delimiter,
            #     structured_html=self.structured_html,
            #     brand_tone=first_task.brand_tone if first_task else self.brand_tone,
            #     meta_json=meta_data
            # )

            # self.generation_record = generation
            # self._log_info(f"Created generation record: {generation.id}")
            # return generation.id

        except Exception as e:
            self._log_error(f"Failed to create generation record: {e}")
            return None

    def _persist_page(self, content_row: ContentRow, generation_id: str) -> bool:
        """Persist a generated page using the repository layer"""
        if not self.enable_persistence or not generation_id:
            return False

        try:
            # NOTE: PageRepository not yet implemented - persistence disabled
            self._log_warning(f"Persistence layer not implemented - skipping page {content_row.id}")
            return False

            # TODO: Uncomment when repository layer is implemented
            # # Prepare metadata
            # meta_data = {
            #     'phone': content_row.phone,
            #     'contact_url': content_row.contact_url,
            #     'client_location': content_row.client_location,
            #     'company_name': content_row.company_name,
            #     'primary_service': content_row.primary_service,
            #     'secondary_service': content_row.secondary_service,
            #     'brand_tone': content_row.brand_tone,
            #     'hours': content_row.hours,
            #     'social_links_json': content_row.social_links_json
            # }

            # # Convert tags to list if it's a string
            # tags_list = None
            # if content_row.tags:
            #     if isinstance(content_row.tags, str):
            #         tags_list = [tag.strip() for tag in content_row.tags.split(',')]
            #     else:
            #         tags_list = content_row.tags

            # # Create PageDTO
            # page_dto = PageDTO(
            #     title=content_row.title,
            #     slug=content_row.slug,
            #     content=content_row.content,
            #     excerpt=content_row.excerpt,
            #     category=content_row.category,
            #     tags=tags_list,
            #     meta=meta_data
            # )

            # # Persist using repository
            # result = PageRepository.upsert_many(generation_id, [page_dto])

            # if result['inserted'] > 0 or result['updated'] > 0:
            #     self._log_info(f"Persisted page {content_row.id}: {result}")
            #     return True
            # else:
            #     self._log_error(f"Failed to persist page {content_row.id}: {result}")
            #     return False

        except Exception as e:
            self._log_error(f"Failed to persist page {content_row.id}: {e}")
            return False

    def _generate_single_content_with_retry(self, task: GenerationTask) -> Optional[ContentRow]:
        """Generate content for a single task with automatic retry logic"""
        # FIXED: Circuit breaker check
        if self.circuit_breaker_active:
            logger.warning(f"Circuit breaker active - skipping task {task.item_id}")
            self._record_error(task.item_id, "circuit_breaker", "Circuit breaker active due to consecutive failures", can_retry=False)
            return None
            
        max_retries = getattr(self, 'max_retries', 3)
        
        for attempt in range(max_retries + 1):
            try:
                logger.info(f"Attempt {attempt + 1}/{max_retries + 1} for task {task.item_id}")
                
                result = self._generate_single_content(task)
                
                if result:
                    # FIXED: Reset consecutive failure counter on success
                    self.consecutive_failures = 0
                    self.successful_operations += 1
                    
                    # Reset circuit breaker after successful operations
                    if self.circuit_breaker_active and self.successful_operations >= 3:
                        self.circuit_breaker_active = False
                        logger.info(f"Circuit breaker reset after {self.successful_operations} successful operations")
                        
                    if attempt > 0:
                        logger.info(f"Task {task.item_id} succeeded on attempt {attempt + 1}")
                    return result
                else:
                    if attempt < max_retries:
                        # Check if this error is retryable
                        retryable_errors = [error for error in self.errors if error.task_id == task.item_id and error.can_retry]
                        if retryable_errors:
                            logger.warning(f"Task {task.item_id} failed on attempt {attempt + 1}, retrying... (retryable error)")
                            time.sleep(1 * (attempt + 1))  # Exponential backoff
                            continue
                        else:
                            logger.error(f"Task {task.item_id} failed with non-retryable error, not retrying")
                            break
                    else:
                        logger.error(f"Task {task.item_id} failed after {max_retries + 1} attempts")
                        break
                        
            except Exception as e:
                logger.error(f"Exception on attempt {attempt + 1} for task {task.item_id}: {e}")
                if attempt < max_retries:
                    time.sleep(1 * (attempt + 1))  # Exponential backoff
                    continue
                else:
                    self._record_error(task.item_id, "max_retries_exceeded", f"Failed after {max_retries + 1} attempts", str(e), can_retry=False)
                    break
        
        # FIXED: Track consecutive failures and activate circuit breaker
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.circuit_breaker_active = True
            logger.error(f"Circuit breaker activated after {self.consecutive_failures} consecutive failures")
            # Update progress system about circuit breaker activation
            if self.unified_progress and self.job_id:
                try:
                    self.unified_progress.error_process(
                        process_id=self.job_id,
                        message=f"Circuit breaker activated after {self.consecutive_failures} consecutive failures"
                    )
                except Exception as e:
                    logger.warning(f"Failed to update progress with circuit breaker error: {e}")
            
        return None

    def _generate_batch_concurrent(self, tasks: List[GenerationTask]) -> List[ContentRow]:
        """Generate content for a batch of tasks concurrently"""
        results = []
        total_tasks = len(getattr(self, '_current_tasks', tasks))
        
        with ThreadPoolExecutor(max_workers=self.concurrent_workers) as executor:
            # Submit all tasks with retry logic
            future_to_task = {
                executor.submit(self._generate_single_content_with_retry, task): task 
                for task in tasks
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result = future.result(timeout=120)  # FIXED: 2 minute timeout per task
                    if result:
                        results.append(result)
                        self.generated_count += 1
                        
                        # Update progress after each successful item (page-based)
                        current_page = self.generated_count
                        total_pages = total_tasks
                        progress_percentage = (current_page / total_pages) * 100

                        logger.info(f"PROGRESS_DEBUG: current_page={current_page}, total_pages={total_pages}, percentage={progress_percentage:.2f}%")

                        page_message = f"Page {current_page} of {total_pages} completed"
                        self._update_progress(page_message, progress_percentage, additional_data={
                            'generated_count': current_page,
                            'target_count': total_pages
                        })
                    else:
                        self.error_count += 1
                        logger.error(f"Failed to generate content for task {task.item_id}")
                except Exception as e:
                    self.error_count += 1
                    logger.error(f"Task {task.item_id} generated an exception: {e}")
        
        return results

    def _gather_web_research(self, topic: str, max_sources: int = 3) -> str:
        """Gather web research for a topic to enhance content generation"""
        if not WEB_SEARCH_AVAILABLE:
            return ""
        
        try:
            # Generate search URLs related to the topic
            search_urls = self._generate_research_urls(topic)
            research_content = []
            
            for url in search_urls[:max_sources]:
                try:
                    content_data = extract_website_content(url)
                    if content_data.get("success") and content_data.get("content"):
                        research_content.append({
                            "source": url,
                            "title": content_data.get("title", ""),
                            "content": content_data.get("content", "")[:500]  # Limit to 500 chars per source
                        })
                        self._log_info(f"Successfully gathered research from {url}")
                except Exception as e:
                    self._log_error(f"Failed to gather research from {url}: {e}")
                    continue
            
            # Format research into context
            if research_content:
                context = "RESEARCH CONTEXT:\n"
                for i, source in enumerate(research_content, 1):
                    context += f"\nSource {i} ({source['source']}):\n"
                    context += f"Title: {source['title']}\n"
                    context += f"Content: {source['content']}\n"
                return context
            
        except Exception as e:
            self._log_error(f"Web research failed for topic '{topic}': {e}")
        
        return ""

    def _generate_research_urls(self, topic: str) -> List[str]:
        """Generate relevant URLs for research based on topic"""
        # For data center topics, use known authoritative sources
        base_urls = [
            f"https://datacenterknowledge.com/search?q={topic.replace(' ', '+')}", 
            f"https://www.datacenterjournal.com/?s={topic.replace(' ', '+')}", 
            f"https://www.datacenterdynamics.com/en/search/?q={topic.replace(' ', '+')}"
        ]
        return base_urls

    def _chunk_large_task_set(self, tasks: List[GenerationTask], chunk_size: int = 100) -> List[List[GenerationTask]]:
        """Split large task sets into manageable chunks for overnight processing"""
        chunks = []
        for i in range(0, len(tasks), chunk_size):
            chunk = tasks[i:i + chunk_size]
            chunks.append(chunk)
        
        self._log_info(f"Split {len(tasks)} tasks into {len(chunks)} chunks of max {chunk_size} tasks each")
        return chunks

    def generate_bulk_csv_with_web_research(self,
                                          tasks: List[GenerationTask],
                                          output_filename: str,
                                          enable_web_research: bool = True,
                                          chunk_size: int = 100,
                                          max_sources_per_task: int = 2) -> Tuple[str, Dict]:
        """
        Generate bulk CSV with web research integration for large-scale operations (1000+ pages)
        Supports chunking for overnight processing and web research enhancement
        """
        total_tasks = len(tasks)
        self._log_info(f"Starting large-scale CSV generation: {total_tasks} tasks with web research: {enable_web_research}")
        
        if total_tasks > 500:
            # For very large operations, use chunking
            task_chunks = self._chunk_large_task_set(tasks, chunk_size)
            all_results = []
            
            for chunk_index, chunk_tasks in enumerate(task_chunks, 1):
                self._log_info(f"Processing chunk {chunk_index}/{len(task_chunks)} ({len(chunk_tasks)} tasks)")
                
                # Process chunk with web research
                chunk_results = self._process_chunk_with_research(
                    chunk_tasks, 
                    enable_web_research, 
                    max_sources_per_task,
                    chunk_index
                )
                all_results.extend(chunk_results)
                
                # Progress update
                completed_tasks = len(all_results)  # More accurate count based on actual results
                progress = (completed_tasks / total_tasks) * 100
                self._update_progress(f"Completed chunk {chunk_index}/{len(task_chunks)} - {completed_tasks}/{total_tasks} tasks", progress)
                
                # Prevent overwhelming external services - add small delay between chunks
                if chunk_index < len(task_chunks):
                    time.sleep(2)
            
            # Write final CSV
            return self._write_enhanced_csv(all_results, output_filename)
        else:
            # For smaller operations, use standard processing with optional research
            return self.generate_bulk_csv(tasks, output_filename)

    def _process_chunk_with_research(self, 
                                   tasks: List[GenerationTask], 
                                   enable_web_research: bool,
                                   max_sources_per_task: int,
                                   chunk_index: int) -> List[ContentRow]:
        """Process a chunk of tasks with optional web research"""
        results = []
        
        for task in tasks:
            try:
                # Gather web research if enabled
                research_context = ""
                if enable_web_research and WEB_SEARCH_AVAILABLE:
                    research_context = self._gather_web_research(task.topic, max_sources_per_task)
                
                # Generate content with research context
                enhanced_task = GenerationTask(
                    item_id=task.item_id,
                    topic=f"{task.topic}\n\n{research_context}" if research_context else task.topic,
                    client=task.client,
                    project=task.project,
                    website=task.website,
                    client_notes=task.client_notes,
                    target_keywords=task.target_keywords
                )
                
                content_row = self._generate_single_content_with_retry(enhanced_task)
                if content_row:
                    results.append(content_row)
                    
            except Exception as e:
                self._log_error(f"Failed to process task {task.item_id} in chunk {chunk_index}: {e}")
                continue
        
        return results

    def _write_enhanced_csv(self, content_rows: List[ContentRow], output_filename: str) -> Tuple[str, Dict]:
        """Write enhanced CSV with comprehensive metadata"""
        output_path = os.path.join(self.output_dir, output_filename)
        
        # Convert ContentRow objects to dictionaries for Enfold format
        csv_data = []
        for row in content_rows:
            csv_data.append({
                "ID": row.id,
                "Title": row.title,
                "Content": row.content,
                "Excerpt": row.excerpt,
                "Category": row.category or "General",
                "Tags": row.tags or "professional, services, quality, expert, trusted",
                "slug": row.slug or self._generate_slug_from_title(row.title),
                "Image": row.image or row.category or "General"
            })
        
        # Write using professional CSV writer
        write_csv(csv_data, output_path)
        
        # Generate comprehensive statistics
        stats = {
            "total_rows": len(csv_data),
            "generation_time": time.time() - self.start_time,
            "success_rate": len(csv_data) / (len(csv_data) + self.error_count) * 100 if (len(csv_data) + self.error_count) > 0 else 0,
            "errors": self.error_count,
            "web_research_enabled": WEB_SEARCH_AVAILABLE,
            "file_size_bytes": os.path.getsize(output_path) if os.path.exists(output_path) else 0
        }
        
        self._log_info(f"Enhanced CSV generation complete: {output_path} with {len(csv_data)} rows")
        return output_path, stats

    def generate_bulk_csv(self,
                         tasks: List[GenerationTask],
                         output_filename: str,
                         resume_from_id: Optional[str] = None) -> Tuple[str, Dict]:
        """
        Generate bulk CSV content from a list of tasks with comprehensive row tracking

        This method guarantees:
        1. Exactly len(tasks) rows in the output
        2. Every row has a unique, traceable ID
        3. Complete audit trail of all operations
        4. Detailed tracking logs for future adjustments

        Args:
            tasks: List of generation tasks
            output_filename: Output CSV filename
            resume_from_id: Resume from specific task ID (for interrupted jobs)

        Returns:
            Tuple of (output_path, statistics)
        """
        import threading
        thread_name = threading.current_thread().name
        logger.info(f"[DEBUG] generate_bulk_csv START in thread '{thread_name}' with {len(tasks)} tasks")

        output_path = os.path.join(self.output_dir, output_filename)
        target_count = len(tasks)

        logger.info(f"[DEBUG] output_path={output_path}, target_count={target_count}")

        # Filter tasks if resuming
        if resume_from_id:
            start_index = 0
            for i, task in enumerate(tasks):
                if task.item_id == resume_from_id:
                    start_index = i
                    break
            tasks = tasks[start_index:]
            logger.info(f"Resuming from task {resume_from_id}, {len(tasks)} tasks remaining")

        # Initialize row tracker
        self.row_tracker = RowTracker(
            target_row_count=target_count,
            max_replacement_attempts=5,
            job_id=self.job_id or f"csv_gen_{int(time.time())}"
        )

        self._log_info("Initializing row tracking system", {
            "target_count": target_count,
            "max_replacement_attempts": 5,
            "job_id": self.row_tracker.job_id
        })

        # Create tracking JSON at start with status="in_progress"
        tracking_dir = os.path.join(self.output_dir, 'tracking')
        os.makedirs(tracking_dir, exist_ok=True)
        job_id = self.row_tracker.job_id
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = os.path.join(tracking_dir, f'{job_id}_tracking_{timestamp}.json')
        
        # Create initial tracking file with status="in_progress"
        initial_job_params = self.job_params if self.job_params else {}
        self.row_tracker.export_tracking_log_json(json_path, initial_job_params, status="in_progress")
        logger.info(f"Created initial tracking JSON: {json_path}")
        
        # Store json_path for later update
        self._tracking_json_path = json_path

        # Create row records for all tasks
        self._log_info("Creating row records", {"count": len(tasks)})
        task_to_record = {}

        for i, task in enumerate(tasks, start=1):
            record = self.row_tracker.create_row_record(
                sequence_number=i,
                topic=task.topic,
                task_id=task.item_id
            )
            task_to_record[task.item_id] = record

        # Store tasks for progress calculation
        self._current_tasks = tasks

        # Update initial progress
        self._update_progress(f"Starting tracked generation: {target_count} rows", 0.0)

        # Main generation loop with tracking
        generation_round = 0
        max_rounds = 20  # Safety limit

        logger.info(f"[DEBUG] Entering main generation loop (max_rounds={max_rounds})")

        while generation_round < max_rounds:
            generation_round += 1

            # Check if we're done
            active_rows = self.row_tracker.get_active_rows_ordered()
            if len(active_rows) >= target_count:
                self._log_info(f"Target count reached: {len(active_rows)}/{target_count}")
                break

            # Get rows that need (re)generation
            needs_work = self.row_tracker.get_rows_needing_generation()

            if not needs_work:
                self._log_info("No rows need work - checking for flagged rows")

                # If we're short, use flagged rows with their best attempt
                flagged_rows = [r for r in self.row_tracker.records.values() if r.status == RowStatus.FLAGGED]
                if len(active_rows) + len(flagged_rows) >= target_count:
                    self._log_info(f"Using {len(flagged_rows)} flagged rows to reach target")

                    for flagged in flagged_rows:
                        if flagged.current_row_data and not flagged.is_active:
                            flagged.is_active = True
                            flagged.needs_manual_review = True
                            self._log_warning(
                                f"Row {flagged.unique_id} flagged but included in output",
                                {"attempts": flagged.replacement_count}
                            )
                    break
                else:
                    self._log_error("Cannot reach target count - insufficient valid rows")
                    break

            # Process batch of rows needing work
            batch_size = min(self.batch_size, len(needs_work))
            batch = needs_work[:batch_size]

            self._log_info(
                f"Round {generation_round}: Processing {len(batch)} rows",
                {
                    "active": len(active_rows),
                    "target": target_count,
                    "needs_work": len(needs_work)
                }
            )
            
            # DEBUG: Log batch processing details for empty CSV diagnosis
            logger.info(f"DEBUG BATCH PROCESSING: Round {generation_round}")
            logger.info(f"  - batch_size: {batch_size}")
            logger.info(f"  - needs_work count: {len(needs_work)}")
            logger.info(f"  - active_rows count: {len(active_rows)}")
            logger.info(f"  - target_count: {target_count}")

            # Check for cancellation
            if self._check_cancelled():
                self._log_info("Generation cancelled by user")
                break

            # Generate content for this batch
            for seq_num, unique_id in batch:
                # Find corresponding task
                record = self.row_tracker.records[unique_id]
                task = next((t for t in tasks if t.item_id == record.original_task_id), None)

                if not task:
                    self._log_error(f"Cannot find task for record {unique_id}")
                    continue

                # Add retry context if this is a replacement attempt
                if record.replacement_count > 0:
                    # Get previous failure reason
                    if record.replacement_history:
                        last_failure = record.replacement_history[-1].validation_failure
                        if last_failure:
                            task.previous_attempt_failed_reason = last_failure.reason
                            if last_failure.word_count:
                                task.previous_attempt_word_count = last_failure.word_count

                # BUG FIX: Update progress BEFORE LLM call to prevent timeout
                # This refreshes the 5-minute timeout timer in UnifiedProgressSystem
                # Without this, long-running LLM calls could cause the job to time out
                completed_count = len(self.row_tracker.get_active_rows_ordered())
                pre_gen_progress = max(1, int((completed_count / target_count) * 80))
                logger.info(f"[DEBUG] About to generate content for seq_num={seq_num}, task={task.item_id}")
                self._update_progress(
                    f"Generating row {seq_num}/{target_count}: {task.topic[:30]}...",
                    pre_gen_progress
                )

                # Generate content
                start_time = time.time()
                logger.info(f"[DEBUG] Calling _generate_single_content for task {task.item_id}")

                try:
                    content_row = self._generate_single_content(task)
                    logger.info(f"[DEBUG] _generate_single_content returned for task {task.item_id} (result: {'OK' if content_row else 'None'})")
                    generation_time_ms = (time.time() - start_time) * 1000

                    if content_row is None:
                        # Generation failed
                        validation_failure = ValidationFailure(
                            timestamp=datetime.now().isoformat(),
                            reason="generation_returned_none",
                            validation_details={"error": "LLM returned None"}
                        )

                        self.row_tracker.record_generation_attempt(
                            unique_id=unique_id,
                            success=False,
                            validation_failure=validation_failure,
                            generation_time_ms=generation_time_ms
                        )
                        continue

                    # Validate content
                    validation_passed = self._validate_content_row(content_row)

                    if validation_passed:
                        # Success! Record and mark active
                        row_data = {
                            "ID": content_row.id,
                            "Title": content_row.title,
                            "Content": content_row.content,
                            "Excerpt": content_row.excerpt or "",
                            "Category": content_row.category or "General",
                            "Tags": content_row.tags or "professional, services, quality",
                            "slug": content_row.slug or self._generate_slug_from_title(content_row.title)
                        }

                        self.row_tracker.record_generation_attempt(
                            unique_id=unique_id,
                            success=True,
                            row_data=row_data,
                            generation_time_ms=generation_time_ms
                        )

                        self.generated_count += 1
                        self._update_progress(
                            f"Generated {self.generated_count}/{target_count} rows",
                            (self.generated_count / target_count) * 100,
                            additional_data={
                                'generated_count': self.generated_count,
                                'target_count': target_count
                            }
                        )

                    else:
                        # Validation failed - record and will retry
                        content_text = content_row.content or ""
                        word_count = len(content_text.split())

                        # Determine failure reason
                        if word_count < 100:
                            reason = "content_too_short"
                        elif not content_row.title or len(content_row.title) < 10:
                            reason = "title_too_short"
                        elif any(content_text.strip().endswith(tag) for tag in ['<', '</', '</h', '</p']):
                            reason = "truncated_html"
                        else:
                            reason = "validation_failed"

                        validation_failure = ValidationFailure(
                            timestamp=datetime.now().isoformat(),
                            reason=reason,
                            word_count=word_count,
                            content_preview=content_text[:100],
                            validation_details={
                                "title_length": len(content_row.title or ""),
                                "has_excerpt": bool(content_row.excerpt)
                            }
                        )

                        self.row_tracker.record_generation_attempt(
                            unique_id=unique_id,
                            success=False,
                            validation_failure=validation_failure,
                            generation_time_ms=generation_time_ms
                        )

                except Exception as e:
                    # Generation exception
                    generation_time_ms = (time.time() - start_time) * 1000

                    validation_failure = ValidationFailure(
                        timestamp=datetime.now().isoformat(),
                        reason="generation_exception",
                        validation_details={"error": str(e)}
                    )

                    self.row_tracker.record_generation_attempt(
                        unique_id=unique_id,
                        success=False,
                        validation_failure=validation_failure,
                        generation_time_ms=generation_time_ms
                    )

                    self._log_error(f"Exception generating row {unique_id}: {e}")

        # Finalize tracking
        self.row_tracker.finalize()

        # Get final active rows
        active_rows = self.row_tracker.get_active_rows_ordered()
        self._log_info(f"Generation complete: {len(active_rows)} active rows")

        # Prepare rows for CSV writing
        all_content_rows = []
        for record in active_rows:
            if record.current_row_data:
                # Add tracking ID to metadata
                row_copy = record.current_row_data.copy()
                row_copy['_tracking_id'] = record.unique_id
                row_copy['_tracking_sequence'] = record.sequence_number
                all_content_rows.append(row_copy)

        # Update tracking data with final results
        # Use stored job parameters or extract from tasks as fallback
        if self.job_params:
            job_params = self.job_params
        else:
            # Fallback: Extract job parameters from tasks and generator settings
            job_params = {
                'prompt_rule_id': tasks[0].prompt_rule_id if tasks else None,
                'model_name': getattr(self, 'model_name', None),
                'client': tasks[0].client if tasks else 'Professional Services',
                'project': tasks[0].project if tasks else 'Content Generation',
                'website': tasks[0].website if tasks else 'website.com',
                'client_notes': tasks[0].client_notes if tasks else '',
                'target_word_count': self.target_word_count,
                'concurrent_workers': self.concurrent_workers,
                'batch_size': self.batch_size,
                'insert_content': tasks[0].insert_content if tasks else None,
                'insert_position': tasks[0].insert_position if tasks else None
            }
        
        # Update existing tracking file with final results
        self.row_tracker.export_tracking_log_json(self._tracking_json_path, job_params, status="completed")
        logger.info(f"Updated tracking JSON with final results: {self._tracking_json_path}")

        # DEBUG: Log final content rows for empty CSV diagnosis
        logger.info(f"DEBUG FINAL CSV WRITING:")
        logger.info(f"  - all_content_rows count: {len(all_content_rows)}")
        logger.info(f"  - active_rows count: {len(active_rows)}")
        logger.info(f"  - target_count: {target_count}")
        if all_content_rows:
            logger.info(f"  - Sample content row keys: {list(all_content_rows[0].keys()) if all_content_rows[0] else 'None'}")
            logger.info(f"  - Sample content row values: {list(all_content_rows[0].values())[:3] if all_content_rows[0] else 'None'}")
        else:
            logger.error("  - NO CONTENT ROWS FOUND - This will cause empty CSV!")
            logger.error(f"  - Row tracker records: {len(self.row_tracker.records)}")
            logger.error(f"  - Active records: {sum(1 for r in self.row_tracker.records.values() if r.is_active)}")
            for record in self.row_tracker.records.values():
                if not record.is_active:
                    logger.error(f"    - Inactive record {record.unique_id}: status={record.status}, replacement_count={record.replacement_count}")

        # Write CSV file with pipe-to-comma conversion
        if all_content_rows:
            try:
                logger.info(f"Writing {len(all_content_rows)} rows with pipe-to-comma conversion")
                self._write_csv_with_conversion(all_content_rows, output_path)
                logger.info(f"Successfully wrote CSV file with conversion: {output_path}")
            except Exception as e:
                logger.error(f"Error writing CSV with conversion: {e}")
                raise Exception(f"Failed to write CSV file: {e}")
        else:
            logger.warning("No valid content rows to write")
            # Create empty file with headers
            try:
                self._write_csv_with_conversion([], output_path)
                logger.info("Created empty CSV file with headers")
            except Exception as e:
                logger.error(f"Error creating empty CSV file: {e}")
                raise Exception(f"Failed to create CSV file: {e}")

        # Validate output file was created and has content
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            if file_size == 0:
                logger.error(f"Generated CSV file is empty: {output_path}")
                # Remove empty file
                os.remove(output_path)
                raise Exception("CSV generation failed - output file is empty")
            
            # Perform comprehensive CSV validation before marking job complete
            validation_result = self._validate_csv_file(output_path)
            if not validation_result:
                logger.error(f"CSV validation failed for file: {output_path}")
                # Remove invalid file
                os.remove(output_path)
                
                # Update progress with validation failure
                validation_error_message = f"Generation failed: CSV validation failed - file structure is invalid"
                self._update_progress_with_error(validation_error_message)
                
                raise Exception("CSV generation failed - output file validation failed")
            
            # Additional validation checks for job completion
            if not self._validate_job_completion_requirements(output_path, len(tasks)):
                logger.error(f"Job completion validation failed for file: {output_path}")
                # Remove invalid file
                os.remove(output_path)
                
                # Update progress with completion failure
                completion_error_message = f"Generation failed: Job completion requirements not met"
                self._update_progress_with_error(completion_error_message)
                
                raise Exception("CSV generation failed - job completion requirements not met")
            
            logger.info(f"Generated and validated CSV file: {output_path} ({file_size} bytes)")
            
            # Count successful generations
            actual_generated_count = len(all_content_rows)
            
            # Update final success progress
            final_message = f"Generation complete! Generated {actual_generated_count}/{len(tasks)} items successfully. File: {os.path.basename(output_path)}"
            self._update_progress(final_message, 100.0, is_complete=True, additional_data={
                'generated_count': actual_generated_count,
                'target_count': len(tasks)
            })
        else:
            logger.error(f"CSV file was not created: {output_path}")
            
            # Update error progress
            error_message = f"Generation failed: CSV file was not created"
            self._update_progress_with_error(error_message)
            
            raise Exception("CSV generation failed - output file was not created")
        
        # Generate statistics with error details and tracking information
        total_time = time.time() - self.start_time
        error_summary = self._get_error_summary()

        # Ensure actual_generated_count is always defined
        if 'actual_generated_count' not in locals():
            actual_generated_count = len(all_content_rows)

        # Get tracking statistics
        tracking_stats = {
            'total_generation_attempts': self.row_tracker.total_generation_attempts,
            'total_validation_failures': self.row_tracker.total_validation_failures,
            'rows_flagged_for_review': self.row_tracker.rows_flagged_for_review,
            'target_row_count': self.row_tracker.target_row_count,
            'actual_row_count': len(active_rows),
            'rows_match_target': len(active_rows) == target_count
        }

        stats = {
            "total_tasks": len(tasks),
            "requested_items": len(tasks),
            "generated_count": actual_generated_count,
            "error_count": self.error_count,
            "success_rate": actual_generated_count / len(tasks) * 100 if len(tasks) > 0 else 0,
            "total_time_seconds": total_time,
            "average_time_per_item": total_time / actual_generated_count if actual_generated_count > 0 else 0,
            "items_per_hour": actual_generated_count / (total_time / 3600) if total_time > 0 else 0,
            "output_file": output_path,
            "file_size_bytes": file_size,
            "error_summary": error_summary,
            "failed_tasks": self.failed_tasks,
            "retryable_errors": len(self.retryable_errors),

            # Tracking statistics
            "tracking": tracking_stats,
            "tracking_files": {
                "json_log": json_path
            }
        }

        logger.info(f"Bulk generation complete: {stats}")
        return output_path, stats

    def generate_retry_csv(self, tasks: List[GenerationTask], output_filename: str) -> Tuple[str, Dict]:
        """
        Optimized CSV generation specifically for retry operations.
        
        This method is optimized for retry scenarios where:
        - We're processing a small number of failed rows (5-50 items)
        - We can skip web research and duplicate checking
        - We use smaller batch sizes for faster processing
        - We minimize logging overhead
        
        Args:
            tasks: List of generation tasks (typically 5-50 items for retry)
            output_filename: Output CSV filename
            
        Returns:
            Tuple of (output_path, statistics)
        """
        logger.info(f"Starting optimized retry CSV generation for {len(tasks)} tasks")
        
        # Store original settings
        original_batch_size = self.batch_size
        original_concurrent_workers = self.concurrent_workers
        
        try:
            # OPTIMIZATION: Use smaller batch size for retry operations
            # For retry, we typically have 5-50 items, so process them in smaller batches
            retry_batch_size = min(len(tasks), 10)  # Process all items in 1-2 batches max
            self.batch_size = retry_batch_size
            
            # OPTIMIZATION: Use fewer concurrent workers for retry to reduce overhead
            retry_workers = min(self.concurrent_workers, max(2, len(tasks) // 2))
            self.concurrent_workers = retry_workers
            
            logger.info(f"Retry optimization: batch_size={retry_batch_size}, workers={retry_workers}")
            
            # Use the standard generate_bulk_csv but with optimized settings
            output_path, stats = self.generate_bulk_csv(tasks, output_filename)
            
            # Add retry-specific statistics
            stats.update({
                "retry_optimized": True,
                "original_batch_size": original_batch_size,
                "retry_batch_size": retry_batch_size,
                "original_workers": original_concurrent_workers,
                "retry_workers": retry_workers,
                "processing_time_optimized": True
            })
            
            logger.info(f"Retry CSV generation completed: {output_path}")
            return output_path, stats
            
        finally:
            # Restore original settings
            self.batch_size = original_batch_size
            self.concurrent_workers = original_concurrent_workers

def create_tasks_from_topics(topics: List[str],
                           client: str,
                           project: str,
                           website: str,
                           client_notes: str = None) -> List[GenerationTask]:
    """Create generation tasks from a list of topics with website-scoped sequential IDs"""
    from backend.models import Page, db
    import hashlib

    tasks = []

    # Get the highest existing ID for this website from the database
    # Use website domain as scope for ID generation
    website_key = website.replace('http://', '').replace('https://', '').split('/')[0]

    # Query for max ID with this website in metadata
    try:
        max_page = db.session.query(Page).filter(
            Page.meta_json.like(f'%{website_key}%')
        ).order_by(Page.created_at.desc()).first()

        # Extract counter from last ID if exists
        if max_page and max_page.id:
            # Try to extract number from ID like "site-com-001" or just use timestamp
            import re
            match = re.search(r'-(\d+)$', max_page.id)
            start_counter = int(match.group(1)) + 1 if match else 1
        else:
            start_counter = 1
    except Exception as e:
        logger.warning(f"Could not query existing pages for {website_key}: {e}")
        start_counter = 1

    # Generate IDs: website-based prefix + sequential counter
    website_prefix = website_key.replace('.', '-').replace('_', '-')[:20]

    for i, topic in enumerate(topics):
        # Format: websitename-###
        page_id = f"{website_prefix}-{start_counter + i:03d}"

        task = GenerationTask(
            item_id=page_id,
            topic=topic,
            client=client,
            project=project,
            website=website,
            client_notes=client_notes
        )
        tasks.append(task)
    return tasks

def create_data_center_topics(count: int = 100) -> List[str]:
    """Generate data center related topics for Imperial Data Center"""
    base_topics = [
        "Data Center Site Selection Legal Requirements",
        "Construction Permits for Data Center Development",
        "Data Center Financing Legal Structures",
        "Regulatory Compliance for Data Center Operations",
        "Data Center Real Estate Due Diligence",
        "Environmental Impact Assessment for Data Centers",
        "Data Center Lease Agreement Negotiations",
        "Zoning Laws for Data Center Construction",
        "Data Center Security Compliance Requirements",
        "Power Purchase Agreements for Data Centers",
        "Data Center Tax Incentives and Legal Benefits",
        "Intellectual Property Protection in Data Centers",
        "Data Center Acquisition Legal Framework",
        "Cross-Border Data Center Legal Considerations",
        "Data Center Insurance and Liability Coverage",
        "Energy Efficiency Legal Requirements for Data Centers",
        "Data Center Employment Law Compliance",
        "Telecommunications Licensing for Data Centers",
        "Data Center Emergency Response Legal Planning",
        "GDPR Compliance for Data Center Operations"
    ]
    
    # Expand the list to reach the desired count
    topics = []
    while len(topics) < count:
        for base_topic in base_topics:
            if len(topics) >= count:
                break
            
            # Add variations
            variations = [
                base_topic,
                f"{base_topic} Best Practices",
                f"{base_topic} Checklist",
                f"{base_topic} Guide",
                f"{base_topic} Requirements"
            ]
            
            for variation in variations:
                if len(topics) < count:
                    topics.append(variation)
    
    return topics[:count]

def create_demonstration_csv(output_dir: str, 
                           num_items: int = 20,
                           concurrent_workers: int = 5) -> Tuple[str, Dict]:
    """
    Create a demonstration CSV file with Imperial Data Center content
    
    This function demonstrates the high-volume capability by generating
    multiple pieces of content concurrently.
    """
    logger.info(f"Creating demonstration CSV with {num_items} items")
    
    # Create generator instance
    generator = BulkCSVGenerator(
        output_dir=output_dir,
        concurrent_workers=concurrent_workers,
        batch_size=10,
        target_word_count=500
    )
    
    # Create topics and tasks
    topics = create_data_center_topics(num_items)
    tasks = create_tasks_from_topics(
        topics=topics,
        client="Imperial Data Center",
        project="Legal Services Marketing",
        website="datacenterknowledge.com/business"
    )
    
    # Generate the CSV
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"imperial_data_demo_{timestamp}.csv"
    
    return generator.generate_bulk_csv(tasks, output_filename)

def create_cli_parser() -> argparse.ArgumentParser:
    """Create command-line argument parser with enhanced options"""
    parser = argparse.ArgumentParser(
        description="Bulk CSV Generator for High-Volume Content Creation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic generation with 10 items
  python bulk_csv_generator.py --items 10 --output-dir ./output

  # Enhanced generation with structured HTML and custom delimiter
  python bulk_csv_generator.py --items 50 --structured-html --delimiter semicolon --brand-tone friendly

  # Full configuration with metadata
  python bulk_csv_generator.py --items 100 --company-name "Legal Experts LLC" --phone "+1-555-123-4567" --contact-url "https://example.com/contact" --persist

Supported Delimiters:
  comma     - Standard comma (,) delimiter
  semicolon - Semicolon (;) delimiter
  tab       - Tab (\\t) delimiter

Brand Tones:
  neutral, friendly, authoritative, playful, luxury
        """
    )

    # Basic options
    parser.add_argument('--items', type=int, default=10,
                        help='Number of content items to generate (default: 10)')
    parser.add_argument('--output-dir', type=str, default='./output',
                        help='Output directory for generated CSV files (default: ./output)')
    parser.add_argument('--workers', type=int, default=5,
                        help='Number of concurrent workers (default: 5)')
    parser.add_argument('--word-count', type=int, default=500,
                        help='Target word count per content item (default: 500)')

    # CSV configuration
    parser.add_argument('--delimiter', choices=['comma', 'semicolon', 'tab'], default='comma',
                        help='CSV delimiter type (default: comma)')
    parser.add_argument('--structured-html', action='store_true',
                        help='Enable structured HTML output with semantic tags')
    parser.add_argument('--include-h1', action='store_true', default=True,
                        help='Include H1 tags in structured HTML (default: True)')

    # Site metadata
    parser.add_argument('--company-name', type=str,
                        help='Company name for content generation')
    parser.add_argument('--phone', type=str,
                        help='Phone number for content generation')
    parser.add_argument('--contact-url', type=str,
                        help='Contact URL for content generation')
    parser.add_argument('--client-location', type=str,
                        help='Client location for content generation')
    parser.add_argument('--primary-service', type=str,
                        help='Primary service for content generation')
    parser.add_argument('--secondary-service', type=str,
                        help='Secondary service for content generation')
    parser.add_argument('--brand-tone', choices=['neutral', 'friendly', 'authoritative', 'playful', 'luxury'],
                        default='neutral', help='Brand tone for content generation (default: neutral)')
    parser.add_argument('--hours', type=str,
                        help='Business hours for content generation')

    # Generation options
    parser.add_argument('--persist', action='store_true',
                        help='Enable database persistence for generated content')
    parser.add_argument('--topics', type=str, nargs='+',
                        help='Custom topics for content generation (space-separated)')
    parser.add_argument('--client', type=str, default='Demo Client',
                        help='Client name for generation (default: Demo Client)')
    parser.add_argument('--project', type=str, default='Demo Project',
                        help='Project name for generation (default: Demo Project)')
    parser.add_argument('--website', type=str, default='example.com',
                        help='Website for generation (default: example.com)')

    # Output options
    parser.add_argument('--output-filename', type=str,
                        help='Custom output filename (will be timestamped if not provided)')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging')

    return parser


def convert_delimiter_arg(delimiter_name: str) -> str:
    """Convert delimiter argument to actual delimiter character"""
    delimiter_map = {
        'comma': ',',
        'semicolon': ';',
        'tab': '\t'
    }
    return delimiter_map.get(delimiter_name, ',')


def validate_cli_args(args) -> Dict[str, str]:
    """Validate CLI arguments and return any validation errors"""
    errors = {}

    # Basic phone format validation (validation system not yet implemented)
    if args.phone:
        import re
        # Simple phone validation - must contain digits
        if not re.search(r'\d{3,}', args.phone):
            errors['phone'] = ['Phone number must contain at least 3 digits']

    # Validate contact URL if provided
    if args.contact_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(args.contact_url)
            if not parsed.scheme or not parsed.netloc:
                errors['contact_url'] = ['Invalid URL format']
        except Exception:
            errors['contact_url'] = ['Invalid URL format']

    # Validate items count
    if args.items <= 0:
        errors['items'] = ['Items count must be positive']

    return errors


def main():
    """Enhanced main function with full CLI support"""
    parser = create_cli_parser()
    args = parser.parse_args()

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Validate arguments
    validation_errors = validate_cli_args(args)
    if validation_errors:
        print("Validation errors:")
        for field, errors in validation_errors.items():
            print(f"  {field}: {', '.join(errors)}")
        sys.exit(1)

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Convert delimiter
    csv_delimiter = convert_delimiter_arg(args.delimiter)

    # Prepare site metadata
    site_meta = {
        'company_name': args.company_name,
        'phone': args.phone,
        'contact_url': args.contact_url,
        'client_location': args.client_location,
        'primary_service': args.primary_service,
        'secondary_service': args.secondary_service,
        'brand_tone': args.brand_tone,
        'hours': args.hours
    }

    # Remove None values
    site_meta = {k: v for k, v in site_meta.items() if v is not None}

    # Create generator instance with enhanced configuration
    generator = BulkCSVGenerator(
        output_dir=args.output_dir,
        concurrent_workers=args.workers,
        batch_size=min(args.items, 50),  # Reasonable batch size
        target_word_count=args.word_count,
        csv_delimiter=csv_delimiter,
        structured_html=args.structured_html,
        enable_persistence=args.persist,
        brand_tone=args.brand_tone,
        include_h1=args.include_h1,
        site_meta=site_meta
    )

    # Create topics and tasks
    if args.topics:
        topics = args.topics
    else:
        topics = create_data_center_topics(args.items)

    # Ensure we have enough topics
    while len(topics) < args.items:
        topics.extend(topics[:min(len(topics), args.items - len(topics))])

    topics = topics[:args.items]

    # Create tasks with enhanced metadata
    tasks = []
    for i, topic in enumerate(topics, 1):
        task = GenerationTask(
            item_id=f"{i:03d}",
            topic=topic,
            client=args.client,
            project=args.project,
            website=args.website,
            # Site metadata
            company_name=args.company_name,
            phone=args.phone,
            contact_url=args.contact_url,
            client_location=args.client_location,
            primary_service=args.primary_service,
            secondary_service=args.secondary_service,
            brand_tone=args.brand_tone,
            hours=args.hours,
            structured_html=args.structured_html,
            include_h1=args.include_h1,
            csv_delimiter=csv_delimiter
        )
        tasks.append(task)

    # Generate output filename
    if args.output_filename:
        output_filename = args.output_filename
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        delimiter_suffix = args.delimiter
        html_suffix = "_html" if args.structured_html else ""
        tone_suffix = f"_{args.brand_tone}" if args.brand_tone != 'neutral' else ""
        output_filename = f"bulk_generation_{args.items}items_{delimiter_suffix}{html_suffix}{tone_suffix}_{timestamp}.csv"

    print(f"Starting bulk CSV generation:")
    print(f"  Items: {args.items}")
    print(f"  Workers: {args.workers}")
    print(f"  Word count: {args.word_count}")
    print(f"  Delimiter: {args.delimiter} ({csv_delimiter})")
    print(f"  Structured HTML: {args.structured_html}")
    print(f"  Brand tone: {args.brand_tone}")
    print(f"  Persistence: {args.persist}")
    print(f"  Output: {args.output_dir}/{output_filename}")

    if site_meta:
        print(f"  Site metadata: {list(site_meta.keys())}")

    # Generate the CSV
    try:
        output_path, stats = generator.generate_bulk_csv(tasks, output_filename)

        print(f"\n🎉 Generation complete!")
        print(f"📁 Output file: {output_path}")
        print(f"📊 Statistics:")
        print(f"  - Generated: {stats['generated_count']}/{stats['total_tasks']} items")
        print(f"  - Success rate: {stats['success_rate']:.1f}%")
        print(f"  - Total time: {stats['total_time_seconds']:.1f} seconds")
        print(f"  - Rate: {stats['items_per_hour']:.1f} items/hour")
        print(f"  - File size: {stats['file_size_bytes']:,} bytes")

        if stats['error_count'] > 0:
            print(f"  - Errors: {stats['error_count']}")
            if 'error_summary' in stats and 'most_common_errors' in stats['error_summary']:
                print(f"  - Most common errors: {stats['error_summary']['most_common_errors'][:3]}")

    except Exception as e:
        print(f"❌ Generation failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main() 