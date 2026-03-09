#!/usr/bin/env python3
"""
Standardized Response Formatter
Ensures consistent API response formats across all generation endpoints
Version 1.0: Unified response format system
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

@dataclass
class GenerationResponse:
    """Standardized generation response format"""
    # Core response fields
    success: bool
    message: str
    status_code: int
    
    # Generation-specific fields
    generation_type: str  # "single", "bulk", "file"
    output_file: Optional[str] = None
    output_path: Optional[str] = None
    file_size: Optional[int] = None
    
    # Job tracking fields (for bulk operations)
    job_id: Optional[str] = None
    task_id: Optional[str] = None
    progress_url: Optional[str] = None
    
    # Statistics and metrics
    statistics: Optional[Dict[str, Any]] = None
    estimated_duration_minutes: Optional[float] = None
    
    # Context information
    context_used: Optional[Dict[str, Any]] = None
    
    # Error information
    error: Optional[str] = None
    error_details: Optional[str] = None
    error_code: Optional[str] = None
    
    # Metadata
    timestamp: str = None
    api_version: str = "2.0"
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)
    
    def to_json_response(self):
        """Convert to Flask JSON response"""
        from flask import jsonify
        return jsonify(self.to_dict()), self.status_code

class ResponseFormatter:
    """Utility class for formatting standardized API responses"""
    
    @staticmethod
    def success_response(
        message: str,
        generation_type: str,
        output_file: Optional[str] = None,
        output_path: Optional[str] = None,
        file_size: Optional[int] = None,
        statistics: Optional[Dict[str, Any]] = None,
        context_used: Optional[Dict[str, Any]] = None,
        status_code: int = 200
    ) -> GenerationResponse:
        """Create a standardized success response"""
        return GenerationResponse(
            success=True,
            message=message,
            status_code=status_code,
            generation_type=generation_type,
            output_file=output_file,
            output_path=output_path,
            file_size=file_size,
            statistics=statistics,
            context_used=context_used
        )
    
    @staticmethod
    def bulk_job_response(
        message: str,
        job_id: str,
        task_id: Optional[str] = None,
        estimated_duration_minutes: Optional[float] = None,
        context_used: Optional[Dict[str, Any]] = None,
        status_code: int = 202
    ) -> GenerationResponse:
        """Create a standardized bulk job response"""
        return GenerationResponse(
            success=True,
            message=message,
            status_code=status_code,
            generation_type="bulk",
            job_id=job_id,
            task_id=task_id,
            estimated_duration_minutes=estimated_duration_minutes,
            context_used=context_used,
            progress_url=f"/api/generate/status?job_id={job_id}"
        )
    
    @staticmethod
    def error_response(
        error: str,
        error_details: Optional[str] = None,
        error_code: Optional[str] = None,
        status_code: int = 500
    ) -> GenerationResponse:
        """Create a standardized error response"""
        return GenerationResponse(
            success=False,
            message="Generation failed",
            status_code=status_code,
            generation_type="unknown",
            error=error,
            error_details=error_details,
            error_code=error_code
        )
    
    @staticmethod
    def validation_error_response(
        error: str,
        error_details: Optional[str] = None,
        status_code: int = 400
    ) -> GenerationResponse:
        """Create a standardized validation error response"""
        return ResponseFormatter.error_response(
            error=error,
            error_details=error_details,
            error_code="VALIDATION_ERROR",
            status_code=status_code
        )
    
    @staticmethod
    def not_found_error_response(
        error: str,
        error_details: Optional[str] = None,
        status_code: int = 404
    ) -> GenerationResponse:
        """Create a standardized not found error response"""
        return ResponseFormatter.error_response(
            error=error,
            error_details=error_details,
            error_code="NOT_FOUND",
            status_code=status_code
        )
    
    @staticmethod
    def server_error_response(
        error: str,
        error_details: Optional[str] = None,
        status_code: int = 500
    ) -> GenerationResponse:
        """Create a standardized server error response"""
        return ResponseFormatter.error_response(
            error=error,
            error_details=error_details,
            error_code="SERVER_ERROR",
            status_code=status_code
        )

def format_generation_success(
    message: str,
    generation_type: str,
    output_file: Optional[str] = None,
    output_path: Optional[str] = None,
    file_size: Optional[int] = None,
    statistics: Optional[Dict[str, Any]] = None,
    context_used: Optional[Dict[str, Any]] = None,
    status_code: int = 200
):
    """Convenience function for formatting success responses"""
    response = ResponseFormatter.success_response(
        message=message,
        generation_type=generation_type,
        output_file=output_file,
        output_path=output_path,
        file_size=file_size,
        statistics=statistics,
        context_used=context_used,
        status_code=status_code
    )
    return response.to_json_response()

def format_bulk_job_success(
    message: str,
    job_id: str,
    task_id: Optional[str] = None,
    estimated_duration_minutes: Optional[float] = None,
    context_used: Optional[Dict[str, Any]] = None,
    status_code: int = 202
):
    """Convenience function for formatting bulk job responses"""
    response = ResponseFormatter.bulk_job_response(
        message=message,
        job_id=job_id,
        task_id=task_id,
        estimated_duration_minutes=estimated_duration_minutes,
        context_used=context_used,
        status_code=status_code
    )
    return response.to_json_response()

def format_generation_error(
    error: str,
    error_details: Optional[str] = None,
    error_code: Optional[str] = None,
    status_code: int = 500
):
    """Convenience function for formatting error responses"""
    response = ResponseFormatter.error_response(
        error=error,
        error_details=error_details,
        error_code=error_code,
        status_code=status_code
    )
    return response.to_json_response()

def format_validation_error(
    error: str,
    error_details: Optional[str] = None,
    status_code: int = 400
):
    """Convenience function for formatting validation errors"""
    response = ResponseFormatter.validation_error_response(
        error=error,
        error_details=error_details,
        status_code=status_code
    )
    return response.to_json_response()
