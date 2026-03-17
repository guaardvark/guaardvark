from __future__ import annotations

from typing import Any, Optional, Dict, Union
from datetime import datetime
from flask import jsonify


def make_response(
    success: bool,
    message: str = "",
    data: Optional[Any] = None,
    status_code: int = 200,
    error_code: Optional[str] = None,
    details: Optional[Dict] = None,
):
    """Return a standardized JSON API response.

    Args:
        success: Whether the operation was successful
        message: Human-readable message
        data: Response data payload
        status_code: HTTP status code
        error_code: Machine-readable error code (for errors)
        details: Additional error details

    Returns:
        Tuple of (json_response, status_code)
    """
    payload = {
        "success": success,
        "timestamp": datetime.now().isoformat(),
        "status_code": status_code,
    }

    if message:
        payload["message"] = message

    if success:
        if data is not None:
            payload["data"] = data
    else:
        # Error response structure
        payload["error"] = {
            "code": error_code or "GENERIC_ERROR",
            "message": message,
        }
        if details:
            payload["error"]["details"] = details
        if data is not None:
            payload["error"]["context"] = data

    return jsonify(payload), status_code


def success_response(
    data: Optional[Any] = None,
    message: str = "Operation completed successfully",
    status_code: int = 200,
):
    """Return a standardized success response.

    Args:
        data: Response data payload
        message: Success message
        status_code: HTTP status code (default 200)

    Returns:
        Tuple of (json_response, status_code)
    """
    return make_response(True, message, data, status_code)


def error_response(
    message: str,
    status_code: int = 400,
    error_code: Optional[str] = None,
    data: Optional[Any] = None,
    details: Optional[Dict] = None,
):
    """Return a standardized error response.

    Args:
        message: Error message
        status_code: HTTP status code (default 400)
        error_code: Machine-readable error code
        data: Additional context data
        details: Detailed error information

    Returns:
        Tuple of (json_response, status_code)
    """
    if not error_code:
        # Auto-generate error codes based on status
        error_code_map = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
            500: "INTERNAL_ERROR",
            501: "NOT_IMPLEMENTED",
            503: "SERVICE_UNAVAILABLE",
        }
        error_code = error_code_map.get(status_code, "UNKNOWN_ERROR")

    return make_response(False, message, data, status_code, error_code, details)


def validation_error_response(
    message: str = "Validation failed",
    errors: Optional[Dict] = None,
    field_errors: Optional[Dict] = None,
):
    """Return a standardized validation error response.

    Args:
        message: Validation error message
        errors: General validation errors
        field_errors: Field-specific validation errors

    Returns:
        Tuple of (json_response, status_code)
    """
    details = {}
    if errors:
        details["errors"] = errors
    if field_errors:
        details["field_errors"] = field_errors

    return error_response(
        message,
        status_code=422,
        error_code="VALIDATION_ERROR",
        details=details if details else None,
    )


def not_found_response(resource: str = "Resource"):
    """Return a standardized 404 response.

    Args:
        resource: Name of the resource that wasn't found

    Returns:
        Tuple of (json_response, status_code)
    """
    return error_response(
        f"{resource} not found", status_code=404, error_code="NOT_FOUND"
    )


def unauthorized_response(message: str = "Authentication required"):
    """Return a standardized 401 response.

    Args:
        message: Authentication error message

    Returns:
        Tuple of (json_response, status_code)
    """
    return error_response(message, status_code=401, error_code="UNAUTHORIZED")


def forbidden_response(message: str = "Access denied"):
    """Return a standardized 403 response.

    Args:
        message: Authorization error message

    Returns:
        Tuple of (json_response, status_code)
    """
    return error_response(message, status_code=403, error_code="FORBIDDEN")


def conflict_response(message: str, details: Optional[Dict] = None):
    """Return a standardized 409 conflict response.

    Args:
        message: Conflict error message
        details: Additional conflict details

    Returns:
        Tuple of (json_response, status_code)
    """
    return error_response(
        message, status_code=409, error_code="CONFLICT", details=details
    )


def internal_error_response(
    message: str = "Internal server error", details: Optional[Dict] = None
):
    """Return a standardized 500 response.

    Args:
        message: Internal error message
        details: Additional error details (be careful not to leak sensitive info)

    Returns:
        Tuple of (json_response, status_code)
    """
    return error_response(
        message, status_code=500, error_code="INTERNAL_ERROR", details=details
    )
