# backend/utils/serialization_utils.py
# Centralized serialization utilities to eliminate duplicate patterns
# Consolidates serialization logic from clients_api.py, tasks_api.py, and other modules

import logging
from typing import Any, Dict, List, Optional, Union
from datetime import datetime

logger = logging.getLogger(__name__)


def format_logo_path(logo_path: Optional[str]) -> Optional[str]:
    if not logo_path:
        return None
    if "logos/" in logo_path or logo_path.startswith("logos/"):
        return logo_path
    return f"logos/{logo_path}"


def serialize_model_object(
    obj: Any,
    fields: Optional[Dict[str, Any]] = None,
    exclude_fields: Optional[List[str]] = None,
    include_relationships: bool = False,
    date_format: str = "iso",
) -> Optional[Dict[str, Any]]:
    """
    Generic serialization function for SQLAlchemy model objects.

    Args:
        obj: The model object to serialize
        fields: Optional dict of custom field mappings {field_name: value_or_callable}
        exclude_fields: List of field names to exclude from serialization
        include_relationships: Whether to include relationship counts/info
        date_format: Format for datetime fields ('iso', 'timestamp', 'string')

    Returns:
        Serialized dictionary or None if obj is None
    """
    if not obj:
        return None

    exclude_fields = exclude_fields or []

    try:
        # Check if object has a to_dict method and use it
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            data = obj.to_dict()
        else:
            # Build data dictionary from object attributes
            data = {}

            # Get basic attributes (avoiding SQLAlchemy internals)
            for attr_name in dir(obj):
                if (
                    attr_name.startswith("_")
                    or attr_name in exclude_fields
                    or callable(getattr(obj, attr_name, None))
                ):
                    continue

                try:
                    value = getattr(obj, attr_name)

                    # Handle different types
                    if isinstance(value, datetime):
                        if date_format == "iso":
                            data[attr_name] = value.isoformat()
                        elif date_format == "timestamp":
                            data[attr_name] = value.timestamp()
                        else:
                            data[attr_name] = str(value)
                    elif hasattr(value, "__dict__") and not isinstance(
                        value, (str, int, float, bool, list, dict)
                    ):
                        # Skip complex objects to avoid circular references
                        continue
                    else:
                        data[attr_name] = value

                except Exception as e:
                    logger.debug(f"Skipping attribute {attr_name}: {e}")
                    continue

        # Add custom fields
        if fields:
            for field_name, field_value in fields.items():
                if callable(field_value):
                    try:
                        data[field_name] = field_value(obj)
                    except Exception as e:
                        logger.warning(
                            f"Failed to compute custom field {field_name}: {e}"
                        )
                        data[field_name] = None
                else:
                    data[field_name] = field_value

        # Add relationship counts if requested
        if include_relationships:
            for attr_name in dir(obj):
                if attr_name.startswith("_"):
                    continue

                try:
                    attr_value = getattr(obj, attr_name)
                    if hasattr(attr_value, "count") and callable(attr_value.count):
                        data[f"{attr_name}_count"] = attr_value.count()
                except Exception:
                    continue

        # Remove excluded fields
        for field in exclude_fields:
            data.pop(field, None)

        return data

    except Exception as e:
        logger.error(f"Error serializing object {type(obj).__name__}: {e}")
        return None


def serialize_client(client_obj, overrides=None) -> Optional[Dict[str, Any]]:
    """
    Specialized client serialization using the generic function.
    Maintains compatibility with existing clients_api.py patterns.

    Args:
        overrides: Optional dict of pre-computed field values (e.g. {"project_count": 5})
                   to avoid N+1 queries when serializing lists.
    """

    def get_logo_url(obj):
        """Convert logo_path to relative path matching system logo pattern"""
        if not obj.logo_path:
            return None
        # If logo_path already contains 'logos/', return as-is
        if "logos/" in obj.logo_path:
            return obj.logo_path
        # Otherwise, prepend 'logos/' to match system logo pattern (e.g., 'system/logo.png')
        return f"logos/{obj.logo_path}"

    if overrides and "project_count" in overrides:
        # Use pre-computed count to avoid per-row query
        pc = overrides["project_count"]
        fields = {"project_count": lambda obj: pc}
    else:
        fields = {
            "project_count": lambda obj: (
                obj.projects.count() if hasattr(obj, "projects") else 0
            )
        }

    data = serialize_model_object(
        client_obj,
        fields=fields,
        exclude_fields=["password", "password_hash"],
        include_relationships=False,
        date_format="iso",
    )

    # Override logo_path with full URL
    if data and client_obj:
        data["logo_path"] = get_logo_url(client_obj)

    return data


def serialize_project(project_obj) -> Optional[Dict[str, Any]]:
    """
    Specialized project serialization using the generic function.
    Maintains compatibility with existing clients_api.py patterns.
    """

    def get_logo_url(obj):
        """Convert logo_path to relative path matching system logo pattern"""
        logo_path = getattr(obj, "logo_path", None)
        if not logo_path:
            return None
        # If logo_path already contains 'logos/', return as-is
        if "logos/" in logo_path:
            return logo_path
        # Otherwise, prepend 'logos/' to match system logo pattern (e.g., 'system/logo.png')
        return f"logos/{logo_path}"

    data = serialize_model_object(
        project_obj,
        fields={
            "client_info": lambda obj: (
                {
                    "id": obj.client.id,
                    "name": obj.client.name,
                    "logo_path": (
                        get_logo_url(obj.client)
                        if hasattr(obj, "client") and obj.client
                        else None
                    ),
                }
                if hasattr(obj, "client") and obj.client
                else None
            )
        },
        exclude_fields=[],
        include_relationships=False,
        date_format="iso",
    )

    # Override logo_path with full URL if project has logo_path
    if data and hasattr(project_obj, "logo_path"):
        data["logo_path"] = get_logo_url(project_obj)

    return data


def serialize_task(task_obj) -> Optional[Dict[str, Any]]:
    """
    Specialized task serialization using the generic function.
    Maintains compatibility with existing tasks_api.py patterns.
    Enhanced to parse workflow_config and expose individual fields.
    """
    import json

    def parse_workflow_config(obj):
        """Parse workflow_config JSON string and return individual fields"""
        workflow_config = {}
        if hasattr(obj, "workflow_config") and obj.workflow_config:
            try:
                workflow_config = json.loads(obj.workflow_config)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    f"Failed to parse workflow_config for task {getattr(obj, 'id', 'unknown')}: {e}"
                )
        return workflow_config

    return serialize_model_object(
        task_obj,
        fields={
            "project_info": lambda obj: (
                {"id": obj.project.id, "name": obj.project.name}
                if hasattr(obj, "project") and obj.project
                else (
                    {"id": obj.project_id}
                    if hasattr(obj, "project_id") and obj.project_id
                    else None
                )
            ),
            "client_info": lambda obj: (
                {"id": obj.client_ref.id, "name": obj.client_ref.name}
                if hasattr(obj, "client_ref") and obj.client_ref
                else (
                    {"id": obj.client_id}
                    if hasattr(obj, "client_id") and obj.client_id
                    else None
                )
            ),
            "website_info": lambda obj: (
                {"id": obj.website_ref.id, "url": obj.website_ref.url}
                if hasattr(obj, "website_ref") and obj.website_ref
                else (
                    {"id": obj.website_id}
                    if hasattr(obj, "website_id") and obj.website_id
                    else None
                )
            ),
            # Include client_name, target_website and competitor_url if they exist
            "client_name": lambda obj: getattr(obj, "client_name", None),
            "target_website": lambda obj: getattr(obj, "target_website", None),
            "competitor_url": lambda obj: getattr(obj, "competitor_url", None),
            "client_id": lambda obj: getattr(obj, "client_id", None),
            "website_id": lambda obj: getattr(obj, "website_id", None),
            # Parse workflow_config and expose individual fields
            "workflow_config_parsed": lambda obj: parse_workflow_config(obj),
            "page_count": lambda obj: parse_workflow_config(obj).get("page_count"),
            "prompt_rule_id": lambda obj: parse_workflow_config(obj).get(
                "prompt_rule_id"
            ),
            "items": lambda obj: parse_workflow_config(obj).get("items"),
            "client_website": lambda obj: parse_workflow_config(obj).get(
                "client_website"
            ),
            "target_website_config": lambda obj: parse_workflow_config(obj).get(
                "target_website"
            ),
            "website_id_config": lambda obj: parse_workflow_config(obj).get(
                "website_id"
            ),
            "insert_content": lambda obj: parse_workflow_config(obj).get(
                "insert_content"
            ),
            "insert_position": lambda obj: parse_workflow_config(obj).get(
                "insert_position"
            ),
        },
        exclude_fields=[],
        include_relationships=False,
        date_format="iso",
    )


def serialize_list(
    objects: List[Any],
    serializer_func: callable = serialize_model_object,
    **serializer_kwargs,
) -> List[Dict[str, Any]]:
    """
    Serialize a list of objects using the specified serializer function.

    Args:
        objects: List of objects to serialize
        serializer_func: Function to use for serialization
        **serializer_kwargs: Additional arguments for the serializer

    Returns:
        List of serialized dictionaries
    """
    if not objects:
        return []

    result = []
    for obj in objects:
        serialized = serializer_func(obj, **serializer_kwargs)
        if serialized:
            result.append(serialized)

    return result


def serialize_with_pagination(
    objects: List[Any],
    total_count: int,
    page: int = 1,
    per_page: int = 10,
    serializer_func: callable = serialize_model_object,
    **serializer_kwargs,
) -> Dict[str, Any]:
    """
    Serialize objects with pagination metadata.

    Args:
        objects: List of objects to serialize
        total_count: Total number of items across all pages
        page: Current page number
        per_page: Items per page
        serializer_func: Function to use for serialization
        **serializer_kwargs: Additional arguments for the serializer

    Returns:
        Dictionary with serialized data and pagination info
    """
    serialized_data = serialize_list(objects, serializer_func, **serializer_kwargs)

    return {
        "data": serialized_data,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total_count,
            "pages": (total_count + per_page - 1) // per_page,
            "has_next": page * per_page < total_count,
            "has_prev": page > 1,
        },
    }


# Legacy compatibility - maintain existing function names for easy migration
def serialize_client_legacy(client_obj):
    """Legacy wrapper for backward compatibility"""
    return serialize_client(client_obj)


def serialize_project_legacy(project_obj):
    """Legacy wrapper for backward compatibility"""
    return serialize_project(project_obj)


def serialize_task_legacy(task_obj):
    """Legacy wrapper for backward compatibility"""
    return serialize_task(task_obj)
