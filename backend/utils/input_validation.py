"""
Input validation utilities for API endpoints.

This module provides comprehensive input validation functions to prevent
injection attacks, XSS, and other security vulnerabilities.
"""

import re
import logging
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

# Security patterns
DANGEROUS_PATTERNS = [
    r'<script.*?>',
    r'javascript:',
    r'vbscript:',
    r'onload\s*=',
    r'onerror\s*=',
    r'onclick\s*=',
    r'eval\s*\(',
    r'exec\s*\(',
    r'system\s*\(',
    r'__import__',
    r'subprocess',
    r'file://',
    r'data:.*base64',
]

# Compile patterns for better performance
COMPILED_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in DANGEROUS_PATTERNS]

# Valid email regex
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# Valid URL regex
URL_REGEX = re.compile(
    r'^https?://'  # http:// or https://
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
    r'localhost|'  # localhost...
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
    r'(?::\d+)?'  # optional port
    r'(?:/?|[/?]\S+)$', re.IGNORECASE)

# Valid filename characters
FILENAME_REGEX = re.compile(r'^[a-zA-Z0-9._-]+$')

# Maximum lengths for different fields
MAX_LENGTHS = {
    'name': 255,
    'description': 1000,
    'email': 254,
    'phone': 20,
    'url': 2000,
    'filename': 255,
    'tags': 500,
    'prompt': 10000,
    'session_id': 100,
    'command_label': 50,
}


class ValidationError(Exception):
    """Custom exception for validation errors."""
    pass


def sanitize_string(value: str, max_length: Optional[int] = None) -> str:
    """
    Sanitize a string by removing dangerous patterns and limiting length.
    
    Args:
        value: The string to sanitize
        max_length: Maximum allowed length
        
    Returns:
        Sanitized string
        
    Raises:
        ValidationError: If the string contains dangerous patterns
    """
    if not isinstance(value, str):
        raise ValidationError("Value must be a string")
    
    # Check for dangerous patterns
    for pattern in COMPILED_PATTERNS:
        if pattern.search(value):
            logger.warning(f"Dangerous pattern detected in input: {pattern.pattern}")
            raise ValidationError("Input contains potentially dangerous content")
    
    # Strip whitespace
    value = value.strip()
    
    # Check length
    if max_length and len(value) > max_length:
        raise ValidationError(f"Input too long. Maximum length is {max_length}")
    
    return value


def validate_email(email: str) -> str:
    """
    Validate an email address.
    
    Args:
        email: The email to validate
        
    Returns:
        Validated email address
        
    Raises:
        ValidationError: If email is invalid
    """
    if not isinstance(email, str):
        raise ValidationError("Email must be a string")
    
    email = email.strip().lower()
    
    if not EMAIL_REGEX.match(email):
        raise ValidationError("Invalid email format")
    
    if len(email) > MAX_LENGTHS['email']:
        raise ValidationError(f"Email too long. Maximum length is {MAX_LENGTHS['email']}")
    
    return email


def validate_url(url: str) -> str:
    """
    Validate a URL.
    
    Args:
        url: The URL to validate
        
    Returns:
        Validated URL
        
    Raises:
        ValidationError: If URL is invalid
    """
    if not isinstance(url, str):
        raise ValidationError("URL must be a string")
    
    url = url.strip()
    
    if not URL_REGEX.match(url):
        raise ValidationError("Invalid URL format")
    
    if len(url) > MAX_LENGTHS['url']:
        raise ValidationError(f"URL too long. Maximum length is {MAX_LENGTHS['url']}")
    
    # Additional security checks
    parsed = urlparse(url)
    if parsed.scheme not in ['http', 'https']:
        raise ValidationError("URL must use HTTP or HTTPS")
    
    return url


def validate_filename(filename: str) -> str:
    """
    Validate a filename for security.
    
    Args:
        filename: The filename to validate
        
    Returns:
        Validated filename
        
    Raises:
        ValidationError: If filename is invalid
    """
    if not isinstance(filename, str):
        raise ValidationError("Filename must be a string")
    
    # Use werkzeug's secure_filename first
    filename = secure_filename(filename)
    
    if not filename:
        raise ValidationError("Invalid filename")
    
    # Additional checks
    if filename.startswith('.'):
        raise ValidationError("Filename cannot start with a dot")
    
    if len(filename) > MAX_LENGTHS['filename']:
        raise ValidationError(f"Filename too long. Maximum length is {MAX_LENGTHS['filename']}")
    
    # Check for dangerous extensions
    dangerous_extensions = {'.exe', '.bat', '.cmd', '.com', '.pif', '.scr', '.vbs', '.js', '.jar', '.sh'}
    _, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
    if ext and f'.{ext.lower()}' in dangerous_extensions:
        raise ValidationError(f"File extension '{ext}' is not allowed")
    
    return filename


def validate_integer(value: Any, min_value: Optional[int] = None, max_value: Optional[int] = None) -> int:
    """
    Validate an integer value.
    
    Args:
        value: The value to validate
        min_value: Minimum allowed value
        max_value: Maximum allowed value
        
    Returns:
        Validated integer
        
    Raises:
        ValidationError: If value is invalid
    """
    try:
        if isinstance(value, str):
            value = int(value)
        elif not isinstance(value, int):
            raise ValidationError("Value must be an integer")
    except (ValueError, TypeError):
        raise ValidationError("Invalid integer value")
    
    if min_value is not None and value < min_value:
        raise ValidationError(f"Value must be at least {min_value}")
    
    if max_value is not None and value > max_value:
        raise ValidationError(f"Value must be at most {max_value}")
    
    return value


def validate_json_data(data: Dict[str, Any], required_fields: List[str], optional_fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Validate JSON request data.
    
    Args:
        data: The JSON data to validate
        required_fields: List of required field names
        optional_fields: List of optional field names
        
    Returns:
        Validated data dictionary
        
    Raises:
        ValidationError: If data is invalid
    """
    if not isinstance(data, dict):
        raise ValidationError("Request data must be a JSON object")
    
    # Check for required fields
    for field in required_fields:
        if field not in data:
            raise ValidationError(f"Missing required field: {field}")
    
    # Check for unknown fields
    allowed_fields = set(required_fields)
    if optional_fields:
        allowed_fields.update(optional_fields)
    
    for field in data.keys():
        if field not in allowed_fields:
            logger.warning(f"Unknown field in request: {field}")
    
    return data


def validate_request_size(data: Any, max_size_mb: int = 200) -> None:
    """
    Validate the size of request data.
    
    Args:
        data: The data to check
        max_size_mb: Maximum size in MB
        
    Raises:
        ValidationError: If data is too large
    """
    import sys
    
    size_bytes = sys.getsizeof(data)
    max_size_bytes = max_size_mb * 1024 * 1024
    
    if size_bytes > max_size_bytes:
        raise ValidationError(f"Request data too large. Maximum size is {max_size_mb}MB")


def validate_session_id(session_id: str) -> str:
    """
    Validate a session ID.
    
    Args:
        session_id: The session ID to validate
        
    Returns:
        Validated session ID
        
    Raises:
        ValidationError: If session ID is invalid
    """
    if not isinstance(session_id, str):
        raise ValidationError("Session ID must be a string")
    
    session_id = session_id.strip()
    
    if not session_id:
        raise ValidationError("Session ID cannot be empty")
    
    if len(session_id) > MAX_LENGTHS['session_id']:
        raise ValidationError(f"Session ID too long. Maximum length is {MAX_LENGTHS['session_id']}")
    
    # Only allow alphanumeric characters, hyphens, and underscores
    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        raise ValidationError("Session ID contains invalid characters")
    
    return session_id


def validate_prompt(prompt: str) -> str:
    """
    Validate a user prompt.
    
    Args:
        prompt: The prompt to validate
        
    Returns:
        Validated prompt
        
    Raises:
        ValidationError: If prompt is invalid
    """
    if not isinstance(prompt, str):
        raise ValidationError("Prompt must be a string")
    
    prompt = prompt.strip()
    
    if not prompt:
        raise ValidationError("Prompt cannot be empty")
    
    if len(prompt) > MAX_LENGTHS['prompt']:
        raise ValidationError(f"Prompt too long. Maximum length is {MAX_LENGTHS['prompt']}")
    
    # Allow some flexibility for prompts but still check for dangerous patterns
    dangerous_prompt_patterns = [
        r'<script.*?>',
        r'javascript:',
        r'vbscript:',
        r'file://',
        r'data:.*base64',
    ]
    
    for pattern in dangerous_prompt_patterns:
        if re.search(pattern, prompt, re.IGNORECASE):
            logger.warning(f"Potentially dangerous pattern in prompt: {pattern}")
            raise ValidationError("Prompt contains potentially dangerous content")
    
    return prompt


def create_validation_decorator(validation_func):
    """
    Create a decorator for input validation.
    
    Args:
        validation_func: Function to validate input
        
    Returns:
        Decorator function
    """
    def decorator(f):
        def wrapper(*args, **kwargs):
            try:
                return validation_func(*args, **kwargs)
            except ValidationError as e:
                return {"error": str(e)}, 400
            except Exception as e:
                logger.error(f"Validation error: {e}", exc_info=True)
                return {"error": "Invalid input"}, 400
        return wrapper
    return decorator 