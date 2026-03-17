#!/usr/bin/env python3
"""
API module for chat query endpoint.
Handles text-based queries, RAG functionality, and enhanced LLM interactions.
"""

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Blueprint, current_app, jsonify, request, url_for
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename

from backend.utils.query_engine_wrapper import get_query_engine
from backend.utils.response_utils import error_response, success_response

# --- LlamaIndex Imports - FAIL FAST if imports fail ---
from llama_index.core import PromptTemplate, VectorStoreIndex
from llama_index.core.chat_engine.types import BaseChatEngine
from llama_index.core.llms import LLM, ChatResponse, MessageRole
from llama_index.core.query_engine import (BaseQueryEngine,
                                           RetrieverQueryEngine)

# --- Local Imports ---
try:
    from backend import rule_utils
    from backend.utils.chat_utils import get_or_create_session
    from backend.config import \
        LLM_REQUEST_TIMEOUT as DEFAULT_LLM_REQUEST_TIMEOUT
    from backend.models import (Client, Document, LLMMessage, Model, Project,
                                Rule, Setting, Task, Website, db)
    from backend.utils import llm_service, prompt_utils

    local_imports_ok = True
except ImportError as e:
    logging.critical(
        f"CRITICAL Failed to import local dependencies for query_api: {e}",
        exc_info=True,
    )
    rule_utils = prompt_utils = db = Rule = LLMMessage = Model = Project = (  # type: ignore
        Client
    ) = Website = Document = Task = get_or_create_session = None  # type: ignore
    local_imports_ok = False

query_bp = Blueprint("query_api", __name__, url_prefix="/api/query")
logger = logging.getLogger(__name__)

# Precompiled regex for slash commands (e.g. '/createfile args').
COMMAND_RE = re.compile(r"^\s*(/[\w-]+)(?:\s+(.*))?$", re.DOTALL)


def _safe_format(template: str, **kwargs) -> str:
    """Format string with missing keys replaced by empty strings."""
    return template.format_map(defaultdict(str, **kwargs))


def _save_chat_message(session_id: str, role: str, content: str):
    if not db or not LLMMessage:
        return
    role = role.lower()
    if role not in {"user", "assistant", "system"} or not content:
        return
    db.session.add(LLMMessage(session_id=session_id, role=role, content=content, timestamp=datetime.now()))  # type: ignore


def create_rule_from_chat(session_id: Optional[str], rule_text: str) -> bool:
    if not local_imports_ok or not db or not Rule:
        logger.error("DB/Rule model unavailable for create_rule_from_chat.")
        return False
    if not rule_text or len(rule_text) < 5:
        logger.warning("Empty or too short rule text provided for /saverule.")
        return False
    try:
        setting = db.session.get(Setting, "behavior_learning_enabled")
        learning_enabled = setting.value == "true" if setting else False
    except Exception as e:
        logger.error(f"Failed to read behavior learning setting: {e}")
        learning_enabled = False
    if not learning_enabled:
        logger.info("Behavior learning disabled; skipping rule creation.")
        return False
    try:
        new_rule = Rule(level="LEARNED", reference_id=session_id, rule_text=rule_text.strip())  # type: ignore
        new_rule.target_models = ["__ALL__"]
        db.session.add(new_rule)  # type: ignore
        logger.info(f"Rule staged for commit via /saverule (Ref: {session_id}).")
        return True
    except Exception as e:
        logger.error(f"Error staging rule for commit via /saverule: {e}", exc_info=True)
        if db:
            db.session.rollback()  # type: ignore
        return False


def parse_keyword_arguments(args_string: str) -> Tuple[Dict[str, Any], str]:
    kwargs: Dict[str, Any] = {}
    remaining_parts: List[str] = []
    pattern = re.compile(r"\b([\w-]+)=((?:\"[^\"]*\")|(?:\'[^\']*\')|(?:\S+))")
    last_kwarg_end = 0
    temp_args_string = args_string
    processed_indices = [False] * len(args_string)

    while True:
        match = pattern.search(temp_args_string)
        if not match:
            break

        key = match.group(1).lower()
        value_with_quotes = match.group(2)

        if (value_with_quotes.startswith('"') and value_with_quotes.endswith('"')) or (
            value_with_quotes.startswith("'") and value_with_quotes.endswith("'")
        ):
            value = value_with_quotes[1:-1]
        else:
            value = value_with_quotes
        value = value.strip()

        if key.endswith("_id") or key == "rule_id":
            try:
                kwargs[key] = int(value)
            except ValueError:
                kwargs[key] = value
        else:
            kwargs[key] = value

        original_match = re.search(
            re.escape(match.group(0)), args_string[last_kwarg_end:]
        )
        if original_match:
            match_start_in_original = last_kwarg_end + original_match.start()
            match_end_in_original = last_kwarg_end + original_match.end()
            for i in range(match_start_in_original, match_end_in_original):
                processed_indices[i] = True
            last_kwarg_end = match_end_in_original

        temp_args_string = (
            temp_args_string[: match.start()]
            + " " * (match.end() - match.start())
            + temp_args_string[match.end() :]
        )

    current_part = ""
    for i, char_original in enumerate(args_string):
        if not processed_indices[i]:
            current_part += char_original
        else:
            if current_part.strip():
                remaining_parts.append(current_part.strip())
            current_part = ""
    if current_part.strip():
        remaining_parts.append(current_part.strip())

    raw_user_specifications = " ".join(filter(None, remaining_parts)).strip()
    return kwargs, raw_user_specifications


def handle_createfile_command(args_string: str, session_id: str):
    parsed_kwargs, raw_user_specifications = parse_keyword_arguments(args_string)
    filename_from_command = parsed_kwargs.get("output_file") or parsed_kwargs.get(
        "outputfile"
    )
    if not filename_from_command:
        return (
            jsonify(
                {
                    "error": 'Usage: /createfile output_file="filename.ext" <prompt_for_content>'
                }
            ),
            400,
        )

    secure_output_filename = secure_filename(filename_from_command)
    if not secure_output_filename:
        return (
            jsonify(
                {
                    "error": f"Invalid output_file name provided: '{filename_from_command}'"
                }
            ),
            400,
        )

    llm_instance = current_app.config.get("LLAMA_INDEX_LLM")
    active_model_name = getattr(llm_instance, "model", None)
    final_prompt_for_llm = raw_user_specifications

    if rule_utils and db:
        createfile_rule = rule_utils.get_active_command_rule(
            "/createfile", db.session, model_name=active_model_name
        )
        if createfile_rule and createfile_rule.rule_text:
            if "{{USER_INPUT}}" in createfile_rule.rule_text:
                final_prompt_for_llm = createfile_rule.rule_text.replace(
                    "{{USER_INPUT}}", raw_user_specifications
                )
            else:
                final_prompt_for_llm = f"{createfile_rule.rule_text.strip()}\n\n{raw_user_specifications}".strip()

    if not final_prompt_for_llm.strip():
        return (
            jsonify(
                {"error": "Cannot generate file: No content instructions provided."}
            ),
            400,
        )

    try:
        direct_gen_endpoint_url = url_for(
            "generation_api.generate_from_command", _external=True
        )
        direct_gen_payload = {
            "command_label": "/createfile",
            "output_filename": secure_output_filename,
            "generation_parameters": {
                "filename": secure_output_filename,
                "content": raw_user_specifications
            }
        }
        response = requests.post(
            direct_gen_endpoint_url,
            json=direct_gen_payload,
            timeout=current_app.config.get(
                "LLM_REQUEST_TIMEOUT", DEFAULT_LLM_REQUEST_TIMEOUT
            ),
        )
        response.raise_for_status()
        response_data = response.json()
        assistant_response_text = response_data.get(
            "message", f"File gen for '{secure_output_filename}' completed."
        )
        return (
            jsonify(
                {
                    "response": assistant_response_text,
                    "source_nodes": [],
                    "data": response_data,
                }
            ),
            response.status_code,
        )
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        if e.response:
            error_detail = e.response.text
        return (
            jsonify(
                {
                    "error": "Failed to call file generation service.",
                    "details": error_detail,
                }
            ),
            500,
        )


def handle_createcsv_command(args_string: str, session_id: str):
    """
    Handle /createcsv command with intelligent routing to bulk generation.
    
    Supports two formats:
    1. Legacy: /createcsv rule_id=<ID> filename=<FILENAME.csv> items=<ITEM1,ITEM2,...>
    2. Natural Language: /createcsv filename="myfile.csv"
       [followed by detailed natural language instructions]
    """
    logger.info(f"Handling /createcsv command with args: {args_string}")
    parsed_kwargs, raw_user_specifications = parse_keyword_arguments(args_string)
    
    filename = parsed_kwargs.get("filename")
    if not filename:
        return (
            jsonify({
                "error": "Usage: /createcsv filename=\"myfile.csv\" [followed by detailed instructions]"
            }),
            400,
        )

    secure_filename_val = secure_filename(filename)
    if not secure_filename_val.endswith(".csv"):
        secure_filename_val += ".csv"
    
    # Check if this is legacy format (has rule_id) or natural language format
    rule_id_val = parsed_kwargs.get("rule_id")
    items_str = parsed_kwargs.get("items")
    
    if rule_id_val and items_str:
        # LEGACY FORMAT: /createcsv rule_id=X filename=Y items=Z
        logger.info("Using LEGACY format with rule_id and items")
        
        items = [item.strip() for item in items_str.split(",") if item.strip()]
        
        if not isinstance(rule_id_val, int):
            return (
                jsonify({
                    "error": "Legacy format: rule_id must be an integer"
                }),
                400,
            )
        
        # Route to legacy batch CSV generation
        return _handle_legacy_createcsv(rule_id_val, secure_filename_val, items, session_id)
    
    else:
        # NATURAL LANGUAGE FORMAT: /createcsv filename="myfile.csv" [detailed instructions]
        logger.info("Using NATURAL LANGUAGE format with bulk generation API")
        
        # Extract the natural language instructions from the raw user specifications
        # Remove the filename part and use the rest as natural language instructions
        natural_language_instructions = raw_user_specifications
        if not natural_language_instructions or len(natural_language_instructions.strip()) < 10:
            return (
                jsonify({
                    "error": "Please provide detailed instructions after the filename. Example: /createcsv filename=\"myfile.csv\" Generate 10 landing pages for Acme Corp..."
                }),
                400,
            )
        
        logger.info(f"Natural language instructions: {natural_language_instructions[:200]}...")
        
        try:
            # Call the bulk generation API with natural language processing
            bulk_endpoint_url = url_for("bulk_generation_api.generate_bulk_csv", _external=True)
            
            # Prepare bulk generation payload with natural language
            bulk_payload = {
                "output_filename": secure_filename_val,
                "natural_language": natural_language_instructions
            }
            
            logger.info(f"Bulk generation payload for natural language /createcsv: {bulk_payload}")
            
            response = requests.post(
                bulk_endpoint_url,
                json=bulk_payload,
                timeout=30,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code in [200, 202]:  # 200 OK or 202 Accepted
                bulk_result = response.json()
                logger.info(f"Bulk generation API response: {bulk_result}")
                
                return jsonify({
                    "success": True,
                    "message": f"Started bulk CSV generation for '{secure_filename_val}' using natural language processing",
                    "filename": secure_filename_val,
                    "job_id": bulk_result.get("job_id"),
                    "task_id": bulk_result.get("task_id"),
                    "estimated_items": bulk_result.get("num_items", "auto-detected"),
                    "routing": "bulk_generation_api",
                    "mode": "natural_language"
                }), 200
            else:
                error_msg = f"Bulk generation API error: {response.status_code} - {response.text}"
                logger.error(error_msg)
                return jsonify({
                    "error": f"Failed to start bulk generation: {error_msg}"
                }), 500
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to call bulk generation API: {e}")
            return jsonify({
                "error": f"Failed to communicate with bulk generation service: {str(e)}"
            }), 500
        except Exception as e:
            logger.error(f"Unexpected error in natural language /createcsv: {e}", exc_info=True)
            return jsonify({
                "error": f"Unexpected error: {str(e)}"
            }), 500


def _handle_legacy_createcsv(rule_id_val: int, secure_filename_val: str, items: List[str], session_id: str):
    """Handle legacy /createcsv format with rule_id and items list."""
    logger.info(f"Using LEGACY format for /createcsv with rule_id={rule_id_val} and {len(items)} items")
    
    # For now, convert legacy format to bulk generation with auto-detected context
    logger.info("Converting legacy /createcsv to bulk generation format")
    
    try:
        # Call the bulk generation API with legacy data converted to bulk format
        bulk_endpoint_url = url_for("bulk_generation_api.generate_bulk_csv", _external=True)
        
        # Convert legacy items to bulk generation format
        bulk_payload = {
            "output_filename": secure_filename_val,
            "topics": items,  # Use items as topics
            "num_items": len(items),
            "concurrent_workers": min(10, max(2, len(items) // 2)),  # Smart worker allocation
            "target_word_count": 500,  # Default word count
            "batch_size": min(20, len(items) // 2),  # Smart batch sizing
            "resume_from_id": None
        }
        
        logger.info(f"Legacy to bulk conversion payload: {bulk_payload}")
        
        response = requests.post(
            bulk_endpoint_url,
            json=bulk_payload,
            timeout=30,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code in [200, 202]:  # 200 OK or 202 Accepted
            bulk_result = response.json()
            logger.info(f"Legacy conversion successful: {bulk_result}")
            
            return jsonify({
                "success": True,
                "message": f" Converted legacy /createcsv to modern bulk generation for '{secure_filename_val}'",
                "filename": secure_filename_val,
                "job_id": bulk_result.get("job_id"),
                "task_id": bulk_result.get("task_id"),
                "items_count": len(items),
                "routing": "bulk_generation_api",
                "mode": "legacy_converted",
                "note": "Legacy format automatically converted to modern bulk generation"
            }), 200
        else:
            error_msg = f"Bulk generation API error: {response.status_code} - {response.text}"
            logger.error(error_msg)
            return jsonify({
                "error": f"Failed to convert legacy format: {error_msg}"
            }), 500
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to call bulk generation API for legacy conversion: {e}")
        return jsonify({
            "error": f"Failed to communicate with bulk generation service: {str(e)}"
        }), 500
    except Exception as e:
        logger.error(f"Unexpected error in legacy /createcsv conversion: {e}", exc_info=True)
        return jsonify({
            "error": f"Unexpected error in legacy conversion: {str(e)}"
        }), 500


def _should_use_bulk_generation(args_string: str, raw_user_specifications: str, items_count: int = 0) -> tuple[bool, dict]:
    """
    Analyze CSV generation request to determine if it should use bulk generation API.
    
    Returns:
        (should_use_bulk, bulk_params) - bool and dict with bulk generation parameters
    """
    
    # Keywords that strongly suggest bulk generation
    bulk_keywords = [
        "100", "50", "pages", "articles", "posts", "items", "entries", "rows",
        "professional services", "data center", "legal services", "seo", "wordpress",
        "landing pages", "website content", "marketing content", "bulk",
        "many", "multiple", "several", "dozens", "scores", "hundreds"
    ]
    
    # Specific numerical patterns that suggest bulk generation
    import re
    content_combined = f"{args_string} {raw_user_specifications}".lower()
    
    # Look for numbers indicating quantity
    quantity_patterns = [
        r"(\d+)\s*(?:pages?|articles?|posts?|items?|entries?|rows?)",
        r"generate\s+(\d+)",
        r"create\s+(\d+)",
        r"(\d+)\s*(?:landing\s+pages?|web\s*pages?)",
        r"(?:about|around|approximately)\s+(\d+)",
    ]
    
    detected_quantity = 0
    for pattern in quantity_patterns:
        matches = re.findall(pattern, content_combined)
        if matches:
            quantities = [int(m) for m in matches if m.isdigit()]
            if quantities:
                detected_quantity = max(quantities)
                break
    
    # Use items_count if provided (from /createcsv command)
    if items_count > 0:
        detected_quantity = max(detected_quantity, items_count)
    
    # Criteria for bulk generation
    should_use_bulk = False
    reasons = []
    
    # Criterion 1: Explicit quantity >= 10
    if detected_quantity >= 10:
        should_use_bulk = True
        reasons.append(f"Detected quantity: {detected_quantity} items")
    
    # Criterion 2: Contains bulk-related keywords
    keyword_matches = [kw for kw in bulk_keywords if kw in content_combined]
    if len(keyword_matches) >= 2:
        should_use_bulk = True
        reasons.append(f"Bulk keywords detected: {', '.join(keyword_matches[:3])}")
    
    # Criterion 3: Professional Services specific content (always use bulk)
    if "professional services" in content_combined or "data center legal" in content_combined:
        should_use_bulk = True
        reasons.append("Professional Services content detected")
        # Default to 100 items for Professional Services if no quantity specified
        if detected_quantity == 0:
            detected_quantity = 100
    
    # Criterion 4: WordPress/SEO content with substantial requirements
    if any(word in content_combined for word in ["wordpress", "seo", "landing pages", "website content"]):
        if detected_quantity >= 5 or detected_quantity == 0:
            should_use_bulk = True
            reasons.append("WordPress/SEO content detected")
            if detected_quantity == 0:
                detected_quantity = 20  # Default for SEO content
    
    # Criterion 5: Content length indicators
    word_count_patterns = [
        r"(\d+)\s*(?:words?|characters?)",
        r"(\d+)\+\s*words?",
        r"(?:at least|minimum|min)\s+(\d+)\s*words?"
    ]
    
    detected_word_count = 500  # Default
    for pattern in word_count_patterns:
        matches = re.findall(pattern, content_combined)
        if matches:
            word_counts = [int(m) for m in matches if m.isdigit()]
            if word_counts:
                detected_word_count = max(word_counts)
                break
    
    # If word count is high (500+), lean towards bulk
    if detected_word_count >= 500 and detected_quantity >= 5:
        should_use_bulk = True
        reasons.append(f"High word count: {detected_word_count} words")
    
    # Build bulk parameters
    bulk_params = {
        "num_items": max(detected_quantity, 10) if should_use_bulk else detected_quantity,
        "target_word_count": detected_word_count,
        "concurrent_workers": min(15, max(5, detected_quantity // 10)) if detected_quantity > 0 else 10,
        "client": "Professional Services" if "professional services" in content_combined else "Professional Services",
        "website": "datacenterknowledge.com/business" if "data center" in content_combined else "professional-website.com",
        "project": "Content Generation via Chat",
        "reasons": reasons
    }
    
    return should_use_bulk, bulk_params


def handle_generatecsv_command(args_string: str, session_id: str):
    """Handle /generatecsv command with intelligent routing to bulk generation when appropriate."""
    logger.info(f"Handling /generatecsv command with args: {args_string}")
    parsed_kwargs, raw_user_specifications = parse_keyword_arguments(args_string)
    
    logger.debug(f"Parsed kwargs: {parsed_kwargs}")
    logger.debug(f"Raw user specifications: {raw_user_specifications}")
    
    # Extract filename from command or use default
    filename = parsed_kwargs.get("filename") or parsed_kwargs.get("output_file") or "generated_content.csv"
    
    # Ensure CSV extension
    if not filename.lower().endswith(".csv"):
        filename += ".csv"
    
    secure_filename_val = secure_filename(filename)
    if not secure_filename_val:
        return (
            jsonify(
                {
                    "error": f"Invalid filename provided: '{filename}'"
                }
            ),
            400,
        )

    # SMART ROUTING: Check if this should use bulk generation
    should_use_bulk, bulk_params = _should_use_bulk_generation(args_string, raw_user_specifications)
    
    logger.info(f"Smart routing decision: should_use_bulk={should_use_bulk}")
    logger.info(f"Bulk params: {bulk_params}")
    
    if should_use_bulk:
        logger.info(f"Routing to BULK generation API. Reasons: {bulk_params['reasons']}")
        
        try:
            # Call the bulk generation API directly (not via url_for to avoid issues)
            bulk_endpoint_url = "http://localhost:5000/api/bulk-generate/csv"
            
            # Prepare bulk generation payload
            bulk_payload = {
                "output_filename": secure_filename_val,
                "client": bulk_params["client"],
                "project": bulk_params["project"],
                "website": bulk_params["website"],
                "topics": "auto",  # Auto-generate topics based on client/domain
                "num_items": bulk_params["num_items"],
                "concurrent_workers": bulk_params["concurrent_workers"],
                "target_word_count": bulk_params["target_word_count"],
                "batch_size": min(25, bulk_params["num_items"] // 4),  # Smaller batches for better resumability
                "resume_from_id": None
            }
            
            logger.info(f"Bulk generation payload: {bulk_payload}")
            
            response = requests.post(
                bulk_endpoint_url,
                json=bulk_payload,
                timeout=60,  # Bulk API responds quickly (dispatches to background)
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            
            response_data = response.json()
            
            # Enhanced response message for bulk generation
            bulk_response = f"""**High-Performance Bulk CSV Generation Initiated!**

**Generation Details:**
**Items**: {bulk_params['num_items']} pages/articles  
**Target Length**: {bulk_params['target_word_count']} words each
**Concurrent Workers**: {bulk_params['concurrent_workers']}
**Client Focus**: {bulk_params['client']}
**Batch Size**: {bulk_payload['batch_size']} (for resumability)

 **Why Bulk Generation?**
{chr(10).join(f'{reason}' for reason in bulk_params['reasons'])}

**Job Details:**
**Job ID**: `{response_data.get('job_id', 'N/A')}`
**Output File**: `{secure_filename_val}`
**Estimated Time**: ~{response_data.get('estimated_duration_minutes', 0):.1f} minutes

**Resume Protection**: If interrupted, this job will automatically resume from the last completed batch.

**Output Location**: Check the `data/outputs/` directory for your completed CSV file.

 **Progress Tracking**: Monitor progress via the Tasks page or check logs for real-time updates."""
            
            return (
                jsonify(
                    {
                        "response": bulk_response,
                        "source_nodes": [],
                        "data": response_data,
                        "generation_type": "bulk",
                        "job_id": response_data.get('job_id'),
                        "bulk_params": bulk_params
                    }
                ),
                response.status_code,
            )
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling bulk CSV generation service: {e}", exc_info=True)
            error_detail = str(e)
            if hasattr(e, 'response') and e.response:
                error_detail = e.response.text
            
            return (
                jsonify(
                    {
                        "error": f"Bulk CSV generation failed: {error_detail}",
                        "fallback_suggestion": "Try reducing the number of items or simplifying the request."
                    }
                ),
                500,
            )
    
    # SINGLE GENERATION PATH - Now implemented with enhanced CSV formatting
    logger.info(f"Using SINGLE generation API for smaller request")
    
    try:
        # Import generation API and CSV formatting utilities
        from flask import url_for
        import requests
        
        # Call the enhanced generation API with CSV formatting
        generation_endpoint_url = "http://localhost:5000/api/generate/from_command"
        
        # Prepare payload for single generation with CSV formatting
        generation_payload = {
            "command_label": "generatecsv",
            "output_filename": secure_filename_val,
            "args_string": raw_user_specifications or f"Generate CSV content: {filename}"
        }
        
        logger.info(f"Single CSV generation payload: {generation_payload}")
        
        response = requests.post(
            generation_endpoint_url,
            json=generation_payload,
            timeout=120,  # Allow time for LLM generation
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            response_data = response.json()
            logger.info(f"Single CSV generation successful: {response_data.get('output_file', 'unknown')}")
            
            return (
                jsonify({
                    "success": True,
                    "message": f"**CSV File Generated**: `{response_data.get('output_file', secure_filename_val)}`\n\n**Location**: `data/outputs/`\n**Type**: Professional CSV with proper headers\n**Format**: Ready for business use (WordPress, databases, spreadsheets)\n\nYour CSV file has been generated with proper formatting and structure.",
                    "output_file": response_data.get("output_file"),
                    "full_path": response_data.get("full_path"),
                    "file_size": response_data.get("file_size"),
                    "generation_type": "single",
                    "csv_enhanced": True
                }),
                200,
            )
        else:
            logger.error(f"Single CSV generation failed: {response.status_code} - {response.text}")
            
            # Fallback: suggest bulk generation for complex requests
            return (
                jsonify({
                    "error": f"CSV generation failed with status {response.status_code}",
                    "details": "Single CSV generation encountered an error",
                    "fallback_suggestion": "For complex CSV requests, try specifying 10+ items to use our high-performance bulk generation system",
                    "example": f'/generatecsv filename="{secure_filename_val}" Generate 10 business pages about your industry'
                }),
                response.status_code,
            )
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling single CSV generation service: {e}", exc_info=True)
        
        return (
            jsonify({
                "error": "CSV generation service temporarily unavailable",
                "details": str(e),
                "fallback_suggestion": "Try using bulk generation instead",
                "example": f'/generatecsv filename="{secure_filename_val}" Generate 10+ items for bulk processing'
            }),
            503,
        )
    except Exception as e:
        logger.error(f"Unexpected error in single CSV generation: {e}", exc_info=True)
        
        return (
            jsonify({
                "error": "Unexpected error during CSV generation",
                "details": "An internal error occurred",
                "suggestion": "Please try again or contact support if the issue persists"
            }),
            500,
        )


def handle_generatefile_command(args_string: str, session_id: str):
    """Handle /generatefile command for natural language file generation."""
    logger.info(f"Handling /generatefile command with args: {args_string}")
    parsed_kwargs, raw_user_specifications = parse_keyword_arguments(args_string)
    
    # Extract filename from command or use default
    output_file = parsed_kwargs.get("output_file") or parsed_kwargs.get("filename") or "generated_content.txt"
    
    secure_output_filename = secure_filename(output_file)
    if not secure_output_filename:
        return (
            jsonify(
                {
                    "error": f"Invalid output_file name provided: '{output_file}'"
                }
            ),
            400,
        )

    llm_instance = current_app.config.get("LLAMA_INDEX_LLM")
    active_model_name = getattr(llm_instance, "model", None)
    final_prompt_for_llm = raw_user_specifications

    if rule_utils and db:
        generatefile_rule = rule_utils.get_active_command_rule(
            "/generatefile", db.session, model_name=active_model_name
        )
        if generatefile_rule and generatefile_rule.rule_text:
            if "{{USER_INPUT}}" in generatefile_rule.rule_text:
                final_prompt_for_llm = generatefile_rule.rule_text.replace(
                    "{{USER_INPUT}}", raw_user_specifications
                )
            else:
                final_prompt_for_llm = f"{generatefile_rule.rule_text.strip()}\n\n{raw_user_specifications}".strip()

    if not final_prompt_for_llm.strip():
        return (
            jsonify(
                {"error": "Cannot generate file: No content instructions provided."}
            ),
            400,
        )

    try:
        direct_gen_endpoint_url = url_for(
            "generation_api.generate_from_command", _external=True
        )
        direct_gen_payload = {
            "command_label": "/generatefile",
            "output_filename": secure_output_filename,
            "generation_parameters": {
                "filename": secure_output_filename,
                "content": raw_user_specifications
            }
        }
        response = requests.post(
            direct_gen_endpoint_url,
            json=direct_gen_payload,
            timeout=current_app.config.get(
                "LLM_REQUEST_TIMEOUT", DEFAULT_LLM_REQUEST_TIMEOUT
            ),
        )
        response.raise_for_status()
        response_data = response.json()
        assistant_response_text = response_data.get(
            "message", f"File generation for '{secure_output_filename}' completed."
        )
        return (
            jsonify(
                {
                    "response": assistant_response_text,
                    "source_nodes": [],
                    "data": response_data,
                }
            ),
            response.status_code,
        )
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        if e.response:
            error_detail = e.response.text
        return (
            jsonify(
                {
                    "error": "Failed to call file generation service.",
                    "details": error_detail,
                }
            ),
            500,
        )


def handle_saverule_command(args_string: str, session_id: str):
    """Handle the /saverule command."""
    if create_rule_from_chat(session_id, args_string):
        return (
            jsonify({"response": "Rule saved successfully!", "source_nodes": []}),
            200,
        )
    return jsonify({"error": "Failed to save rule."}), 500


def handle_batchcsv_command(args_string: str, session_id: str):
    """
    Handle /batchcsv command - optimized for bulk CSV generation of hundreds of pages.
    Always routes to bulk generation API with intelligent parameter extraction.
    """
    logger.info(f"Handling /batchcsv command with args: {args_string}")
    parsed_kwargs, raw_user_specifications = parse_keyword_arguments(args_string)
    
    # Extract filename from command or use default with timestamp
    filename = parsed_kwargs.get("filename") or parsed_kwargs.get("output_file")
    if not filename:
        # Generate filename based on content if not provided
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"batch_content_{timestamp}.csv"
    
    # Ensure CSV extension
    if not filename.lower().endswith(".csv"):
        filename += ".csv"
    
    secure_filename_val = secure_filename(filename)
    if not secure_filename_val:
        return (
            jsonify(
                {
                    "error": f"Invalid filename provided: '{filename}'"
                }
            ),
            400,
        )

    # Enhanced parameter extraction for batch generation
    content_combined = f"{args_string} {raw_user_specifications}".lower()
    
    # Extract quantity with defaults favoring bulk generation
    quantity_patterns = [
        r"(\d+)\s*(?:pages?|articles?|posts?|items?|entries?|rows?)",
        r"generate\s+(\d+)",
        r"create\s+(\d+)",
        r"(\d+)\s*(?:landing\s+pages?|web\s*pages?)",
        r"(?:about|around|approximately)\s+(\d+)",
        r"(?:hundred|hundreds?)\s*(?:of)?",  # "hundreds" = 200
        r"(?:dozen|dozens?)\s*(?:of)?",      # "dozens" = 24
    ]
    
    detected_quantity = 100  # Default to 100 for batch operations
    for pattern in quantity_patterns:
        matches = re.findall(pattern, content_combined)
        if matches:
            quantities = [int(m) for m in matches if m.isdigit()]
            if quantities:
                detected_quantity = max(quantities)
                break
        # Handle text quantities
        if "hundred" in pattern and re.search(pattern, content_combined):
            detected_quantity = 200
            break
        elif "dozen" in pattern and re.search(pattern, content_combined):
            detected_quantity = 24
            break
    
    # Extract word count with intelligent defaults
    word_count_patterns = [
        r"(\d+)\s*(?:words?|characters?)",
        r"(\d+)\+\s*words?",
        r"(?:at least|minimum|min)\s+(\d+)\s*words?"
    ]
    
    detected_word_count = 500  # Default for batch generation
    for pattern in word_count_patterns:
        matches = re.findall(pattern, content_combined)
        if matches:
            word_counts = [int(m) for m in matches if m.isdigit()]
            if word_counts:
                detected_word_count = max(word_counts)
                break
    
    # Extract client/company name
    client_patterns = [
        r"for\s+([\w\s]+?)(?:\s+about|\s+with|\s+regarding|$)",
        r"(?:company|client|business):\s*([\w\s]+?)(?:\s+about|\s+with|$)",
        r"([\w\s]+?)(?:\s+articles?|\s+pages?|\s+content)"
    ]
    
    detected_client = "Professional Services"  # Default
    for pattern in client_patterns:
        matches = re.findall(pattern, raw_user_specifications, re.IGNORECASE)
        if matches:
            client_candidate = matches[0].strip().title()
            if len(client_candidate) > 3 and len(client_candidate) < 50:
                detected_client = client_candidate
                break
    
    # Extract topic/subject
    topic_patterns = [
        r"about\s+([\w\s]+?)(?:\s+with|\s+for|$)",
        r"regarding\s+([\w\s]+?)(?:\s+with|\s+for|$)",
        r"(?:articles?|pages?|content)\s+(?:about|on|for)\s+([\w\s]+?)(?:\s+with|$)"
    ]
    
    detected_topic = "professional services"  # Default
    for pattern in topic_patterns:
        matches = re.findall(pattern, raw_user_specifications, re.IGNORECASE)
        if matches:
            topic_candidate = matches[0].strip().lower()
            if len(topic_candidate) > 3:
                detected_topic = topic_candidate
                break
    
    # Determine website based on topic/client
    if any(keyword in content_combined for keyword in ["data center", "datacenter", "imperial"]):
        detected_website = "datacenterknowledge.com/business"
        if "imperial" not in detected_client.lower():
            detected_client = "Professional Services"
    elif any(keyword in content_combined for keyword in ["tech", "software", "cloud", "computing"]):
        detected_website = f"{detected_client.lower().replace(' ', '')}.com"
    else:
        detected_website = "professional-website.com"
    
    # Calculate optimal concurrent workers based on quantity
    concurrent_workers = min(20, max(5, detected_quantity // 25))
    batch_size = min(50, max(10, detected_quantity // 10))
    
    # Always use bulk generation for /batchcsv
    try:
        bulk_endpoint_url = "http://localhost:5000/api/bulk-generate/csv"
        
        # Prepare optimized bulk generation payload
        bulk_payload = {
            "output_filename": secure_filename_val,
            "client": detected_client,
            "project": f"Batch Content Generation - {detected_topic.title()}",
            "website": detected_website,
            "topics": "auto",  # Auto-generate topics based on client/domain
            "num_items": detected_quantity,
            "concurrent_workers": concurrent_workers,
            "target_word_count": detected_word_count,
            "batch_size": batch_size,
            "resume_from_id": None
        }
        
        logger.info(f"Batch CSV generation payload: {bulk_payload}")
        
        response = requests.post(
            bulk_endpoint_url,
            json=bulk_payload,
            timeout=60,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        
        response_data = response.json()
        
        # Enhanced response message for batch generation
        batch_response = f"""**High-Performance Batch CSV Generation Started!**

**Batch Generation Details:**
**Total Items**: {detected_quantity} pages/articles  
**Content Length**: {detected_word_count} words each
**Estimated Total**: ~{detected_quantity * detected_word_count:,} words
**Client/Focus**: {detected_client}
**Topic Area**: {detected_topic.title()}
**Concurrent Workers**: {concurrent_workers} (optimized for scale)
**Batch Size**: {batch_size} (for efficient processing)

 **Performance Optimization:**
**Target Rate**: ~{concurrent_workers * 2} articles/minute
**Estimated Duration**: ~{detected_quantity / (concurrent_workers * 2):.1f} minutes
**Daily Capacity**: Up to 28,800+ pages/day

**Job Tracking:**
**Job ID**: `{response_data.get('job_id', 'N/A')}`
**Task ID**: `{response_data.get('task_id', 'N/A')}`  
**Output File**: `{secure_filename_val}`

**Enterprise Features:**
**Auto-Resume**: Job will resume if interrupted
**Progress Tracking**: Real-time progress monitoring
**Quality Control**: SEO-optimized content generation
**Concurrent Processing**: Maximum throughput efficiency

**Output Location**: `data/outputs/{secure_filename_val}`

 **Monitor Progress**: Check the Tasks page or DevTools for real-time updates."""
        
        return (
            jsonify(
                {
                    "response": batch_response,
                    "source_nodes": [],
                    "data": response_data,
                    "generation_type": "batch",
                    "job_id": response_data.get('job_id'),
                    "batch_params": {
                        "num_items": detected_quantity,
                        "target_word_count": detected_word_count,
                        "concurrent_workers": concurrent_workers,
                        "client": detected_client,
                        "topic": detected_topic,
                        "website": detected_website
                    }
                }
            ),
            response.status_code,
        )
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling batch CSV generation service: {e}", exc_info=True)
        error_detail = str(e)
        if hasattr(e, 'response') and e.response:
            error_detail = e.response.text
        
        return (
            jsonify(
                {
                    "error": f"Batch CSV generation failed: {error_detail}",
                    "suggestion": "Check that the bulk generation service is running and try again."
                }
            ),
            500,
        )


COMMAND_HANDLERS = {
    "/saverule": handle_saverule_command,
    "/createfile": handle_createfile_command,
    "/createcsv": handle_createcsv_command,
    "/generatecsv": handle_generatecsv_command,
    "/generatefile": handle_generatefile_command,
    "/batchcsv": handle_batchcsv_command,
}


def command_dispatcher(command_label: str, args_string: str, session_id: str):
    """Dispatch recognized commands to their handler functions."""
    handler = COMMAND_HANDLERS.get(command_label)
    if handler:
        return handler(args_string, session_id)
    return handle_dynamic_command(command_label, args_string, session_id)


def get_query_engine(index: VectorStoreIndex, llm: LLM, template: PromptTemplate):
    """Return a query engine or None on failure."""
    try:
        if hasattr(index, "as_retriever") and hasattr(index, "storage_context"):
            base_ret = index.as_retriever(similarity_top_k=10)
            merging_ret = AutoMergingRetriever(base_ret, index.storage_context)
            return _build_query_engine_from_retriever(
                merging_ret,
                llm=llm,
                text_qa_template=template,
                streaming=False,
            )
    except Exception as e:
        logger.error(f"Failed to build AutoMergingRetriever engine: {e}")

    try:
        return index.as_query_engine(
            llm=llm,
            streaming=False,
            text_qa_template=template,
        )
    except Exception as e:
        logger.error(f"Failed to build query engine: {e}", exc_info=True)
        return None


def _handle_rag_query(user_prompt: str, session_id: str):
    """Run a standard RAG query using model-aware rules and templates. Patched to always include summary nodes for relevant client/file."""
    index_instance = current_app.config.get("LLAMA_INDEX_INDEX")
    llm_instance = current_app.config.get("LLAMA_INDEX_LLM")
    if not index_instance or not llm_instance:
        return error_response("Core query components unavailable.", status_code=503)

    active_model_name = getattr(llm_instance, "model", None)

    # --- Fetch rules with model-specific fallback ---
    rules_context = ""
    if rule_utils:
        try:
            rules_context = rule_utils.get_formatted_rules(
                levels=["system", "learned"], model_name=active_model_name
            )
            if not rules_context:
                rules_context = rule_utils.get_formatted_rules(
                    levels=["system", "learned"], model_name="__ALL__"
                )
            logger.info(
                "Applying rules for model '%s' (chars=%d)",
                active_model_name,
                len(rules_context),
            )
        except Exception as e:
            logger.error(f"Failed to fetch rules: {e}")

    # --- Fetch QA template with fallback ---
    qa_template_string = "{context_str}\n\n{query_str}"
    qa_rule_id = None
    if rule_utils:
        try:
            qa_template_string, qa_rule_id = rule_utils.get_active_qa_default_template(
                db_session=db.session, model_name=active_model_name
            )
            if not qa_rule_id:
                qa_template_string, _ = rule_utils.get_active_qa_default_template(
                    db_session=db.session, model_name="__ALL__"
                )
            logger.info("Using QA template rule id %s", qa_rule_id)
        except Exception as e:
            logger.error(f"Failed to get qa_default template: {e}")

    if "{rules_str}" not in qa_template_string:
        qa_template_string = "{rules_str}\n\n" + qa_template_string

    # --- ENHANCED: Retrieve entity and document context ---
    entity_context = ""
    document_context = ""
    
    try:
        retriever = index_instance.as_retriever(similarity_top_k=15)
        
        # Extract entity mentions from user prompt
        import re
        entity_patterns = {
            'client': r"client\s+([\w\s\.-]+?)(?:\s|$|,|\.|!|\?)",
            'project': r"project\s+([\w\s\.-]+?)(?:\s|$|,|\.|!|\?)",
            'website': r"website\s+([\w\s\.-]+?)(?:\s|$|,|\.|!|\?)",
            'task': r"task\s+([\w\s\.-]+?)(?:\s|$|,|\.|!|\?)",
            'file': r"file\s+([\w\.-]+\.(?:csv|pdf|xml|txt))(?:\s|$|,|\.|!|\?)"
        }
        
        detected_entities = {}
        for entity_type, pattern in entity_patterns.items():
            matches = re.findall(pattern, user_prompt, re.IGNORECASE)
            if matches:
                detected_entities[entity_type] = matches
        
        # Retrieve relevant nodes
        nodes = retriever.retrieve(user_prompt)
        
        # Separate entity summaries from document content
        entity_nodes = []
        document_nodes = []
        csv_summary_nodes = []
        
        for node in nodes:
            if hasattr(node, "metadata"):
                content_type = node.metadata.get("content_type", "")
                entity_type = node.metadata.get("entity_type", "")
                
                if content_type == "entity_summary":
                    entity_nodes.append(node)
                elif content_type == "csv_summary":
                    csv_summary_nodes.append(node)
                elif content_type == "document":
                    document_nodes.append(node)
                else:
                    # Default to document nodes for unknown types
                    document_nodes.append(node)
        
        # Build entity context
        if entity_nodes:
            entity_context_parts = []
            for node in entity_nodes[:5]:  # Limit to top 5 entity results
                entity_context_parts.append(f"[{node.metadata.get('entity_type', 'Entity').upper()}] {node.text}")
            entity_context = "\n\n".join(entity_context_parts)
        
        # Build document context (including CSV summaries)
        document_context_parts = []
        for node in csv_summary_nodes[:3]:  # Prioritize CSV summaries
            document_context_parts.append(f"[CSV SUMMARY] {node.text}")
        for node in document_nodes[:3]:  # Add regular document content
            document_context_parts.append(f"[DOCUMENT] {node.text}")
        document_context = "\n\n".join(document_context_parts)
        
        logger.info(f"Retrieved context - Entities: {len(entity_nodes)}, Documents: {len(document_nodes)}, CSV Summaries: {len(csv_summary_nodes)}")
        
    except Exception as e:
        logger.error(f"Failed to retrieve entity/document context: {e}")
        entity_context = ""
        document_context = ""

    # --- End ENHANCED RETRIEVAL ---

    final_prompt_str = _safe_format(
        qa_template_string,
        rules_str=rules_context,
        context_str="{context_str}",
        query_str="{query_str}",
    )

    final_template = PromptTemplate(final_prompt_str)
    query_engine = get_query_engine(index_instance, llm_instance, final_template)
    if not query_engine:
        return error_response("Failed to initialize query engine", status_code=500)

    try:
        # ENHANCED: Combine entity context and document context
        enhanced_context_parts = []
        if entity_context:
            enhanced_context_parts.append(f"ENTITY CONTEXT:\n{entity_context}")
        if document_context:
            enhanced_context_parts.append(f"DOCUMENT CONTEXT:\n{document_context}")
        
        enhanced_context = "\n\n".join(enhanced_context_parts)
        full_prompt = f"{enhanced_context}\n\nUSER QUERY: {user_prompt}" if enhanced_context else user_prompt
        
        response_obj = query_engine.query(full_prompt)
        answer = str(getattr(response_obj, "response", "")).strip()
        source_nodes_info = []
        if hasattr(response_obj, "source_nodes"):
            for node_with_score in response_obj.source_nodes:
                if node_with_score.node:
                    source_nodes_info.append(
                        {
                            "score": (
                                round(node_with_score.score, 4)
                                if node_with_score.score
                                else "N/A"
                            ),
                            "metadata": node_with_score.node.metadata,
                            "node_id": node_with_score.node.node_id,
                        }
                    )
        if local_imports_ok and db:
            _save_chat_message(session_id, "assistant", answer)
            db.session.commit()
        return success_response(
            {
                "query": user_prompt,
                "response": answer,
                "source_nodes": source_nodes_info,
            }
        )
    except Exception as query_err:
        logger.error(f"RAG QueryEngine error: {query_err}", exc_info=True)
        return error_response(
            f"Error during query processing: {query_err}", status_code=500
        )


def handle_query(user_prompt: str, session_id: str):
    """Central dispatcher for RAG queries and slash commands."""
    match = COMMAND_RE.match(user_prompt.strip())
    if match:
        command_label = match.group(1).lower()
        args_string = (match.group(2) or "").strip()
        return command_dispatcher(command_label, args_string, session_id)
    return _handle_rag_query(user_prompt, session_id)


def handle_dynamic_command(command_label: str, args_string: str, session_id: str):
    llm_instance = current_app.config.get("LLAMA_INDEX_LLM")
    active_model_name = getattr(llm_instance, "model", "unknown_model")
    if not prompt_utils:
        return jsonify({"error": "Prompt utility unavailable."}), 500

    instruction_template = prompt_utils.get_active_command_prompt_for_model(
        command_label, model_name=active_model_name
    )
    if not instruction_template:
        return (
            jsonify(
                {
                    "response": f"Command rule '{command_label}' not found or not active.",
                    "source_nodes": [],
                }
            ),
            200,
        )

    parsed_kwargs, raw_user_specifications = parse_keyword_arguments(args_string)
    output_filename_arg = parsed_kwargs.pop("output_file", None) or parsed_kwargs.pop(
        "outputfile", None
    )

    secure_output_filename = None
    if output_filename_arg:
        secure_output_filename = secure_filename(output_filename_arg)
        if not secure_output_filename:
            return (
                jsonify(
                    {"error": f"Invalid output_file name: '{output_filename_arg}'"}
                ),
                400,
            )

    final_prompt = instruction_template
    placeholder_map = {
        "{{USER_SPECIFICATIONS}}": raw_user_specifications,
        "{{PROMPT_CONTENT}}": raw_user_specifications,
        "{{USER_INPUT}}": raw_user_specifications,
        "{{OUTPUT_FILENAME}}": secure_output_filename,
    }
    for placeholder, value in placeholder_map.items():
        if value is not None:
            final_prompt = final_prompt.replace(placeholder, str(value))
    for key, value in parsed_kwargs.items():
        if value is not None:
            final_prompt = final_prompt.replace(f"{{{{{key.upper()}}}}}", str(value))
    if raw_user_specifications and not any(
        p in instruction_template for p in placeholder_map
    ):
        final_prompt += f"\n\n{raw_user_specifications}"
    final_prompt = final_prompt.strip()

    try:
        if secure_output_filename:
            target_endpoint_func_name = (
                "generation_api.direct_generate_and_save_file_route"
            )
            endpoint_url = url_for(target_endpoint_func_name, _external=True)
            payload = {
                "outputfile": secure_output_filename,
                "prompt_text": final_prompt,
            }
            response = requests.post(
                endpoint_url,
                json=payload,
                timeout=current_app.config.get(
                    "LLM_REQUEST_TIMEOUT", DEFAULT_LLM_REQUEST_TIMEOUT
                ),
            )
            response.raise_for_status()
            response_data = response.json()
            assistant_response_text = response_data.get(
                "message", f"Command '{command_label}' processed."
            )
            return (
                jsonify(
                    {
                        "response": assistant_response_text,
                        "source_nodes": [],
                        "data": response_data,
                    }
                ),
                response.status_code,
            )
        else:
            llm_response_text = llm_service.generate_text_basic(
                prompt=final_prompt, is_json_response=False
            )
            if not llm_response_text:
                return (
                    jsonify({"error": "LLM returned no response."}),
                    500,
                )
            return jsonify({"response": llm_response_text, "source_nodes": []}), 200
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        if e.response:
            error_detail = e.response.text
        return (
            jsonify(
                {
                    "error": f"Error processing dynamic command '{command_label}'.",
                    "details": error_detail,
                }
            ),
            500,
        )


@query_bp.route("", methods=["POST"])
def query_endpoint():
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json()
    user_prompt = data.get("prompt")
    session_id = data.get("session_id", "main_chat_session")
    bypass_rules = data.get("bypassRules", False) or data.get("bypass_rules", False)

    if not user_prompt:
        return error_response("Missing 'prompt'")

    if local_imports_ok and db:
        try:
            get_or_create_session(session_id)
            _save_chat_message(session_id, "user", user_prompt)
            db.session.commit()
        except Exception as save_err:
            logger.error(f"Failed to save user message: {save_err}", exc_info=True)
            if db:
                db.session.rollback()

    command_match = re.match(r"^\s*(\/[\w-]+)\s*(.*)", user_prompt.strip(), re.DOTALL)
    if command_match:
        command_label = command_match.group(1).lower()
        # --- FIXED: Use group(2) for arguments, not group(3) ---
        args_string = command_match.group(2).strip()
        
        logger.info(f"Command detected: '{command_label}' with args: '{args_string}'")

        if command_label == "/saverule":
            if create_rule_from_chat(session_id, args_string):
                if local_imports_ok and db:
                    try:
                        db.session.commit()
                        logger.info(
                            f"Rule saved successfully via /saverule (Ref: {session_id})"
                        )
                    except Exception as commit_err:
                        logger.error(
                            f"Failed to commit rule via /saverule: {commit_err}",
                            exc_info=True,
                        )
                        db.session.rollback()
                        return (
                            jsonify({"error": "Failed to save rule: database error."}),
                            500,
                        )
                return (
                    jsonify(
                        {"response": "Rule saved successfully!", "source_nodes": []}
                    ),
                    200,
                )
            else:
                return jsonify({"error": "Failed to save rule."}), 500
        elif command_label == "/createfile":
            return handle_createfile_command(args_string, session_id)
        elif command_label == "/createcsv":
            return handle_createcsv_command(args_string, session_id)
        elif command_label == "/generatecsv":
            return handle_generatecsv_command(args_string, session_id)
        elif command_label == "/generatefile":
            return handle_generatefile_command(args_string, session_id)
        elif command_label == "/batchcsv":
            return handle_batchcsv_command(args_string, session_id)
        else:
            return handle_dynamic_command(command_label, args_string, session_id)

    index_instance = current_app.config.get("LLAMA_INDEX_INDEX")
    llm_instance = current_app.config.get("LLAMA_INDEX_LLM")
    if not index_instance or not llm_instance:
        return error_response("Core query components unavailable.", status_code=503)
    try:
        active_model_name = getattr(llm_instance, "model", None)
        rules_context = ""
        if not bypass_rules:
            rules_context = (
                rule_utils.get_formatted_rules(
                    levels=["system", "learned"], model_name=active_model_name
                )
                if rule_utils
                else ""
            )
        qa_template_string, qa_rule_id = (
            rule_utils.get_active_qa_default_template(
                db_session=db.session,
                model_name=active_model_name,
            )
            if rule_utils and not bypass_rules
            else ("{context_str}\n\n{query_str}", None)
        )
        if bypass_rules:
            logger.info("Rules bypass enabled - using minimal template for code intelligence")
            qa_template_string = "{context_str}\n\n{query_str}"
        if qa_rule_id is None and not bypass_rules:
            logger.warning("Using fallback qa_default template for query API.")
        if "{rules_str}" not in qa_template_string and not bypass_rules:
            qa_template_string = "{rules_str}\n\n" + qa_template_string
        final_prompt_str = _safe_format(
            qa_template_string,
            rules_str=rules_context if not bypass_rules else "",
            context_str="{context_str}",
            query_str="{query_str}",
        )
# Redundant code removed - qa_template_string already properly fetched above using rule_utils
        try:
            final_prompt_str = _safe_format(
                qa_template_string,
                rules_str=rules_context,
                show_reasoning_text_block="",
                context_str="{context_str}",
                query_str="{query_str}",
            )
        except Exception:
            final_prompt_str = _safe_format(
                qa_template_string,
                rules_str=rules_context,
                context_str="{context_str}",
                query_str="{query_str}",
            )
        final_template = PromptTemplate(final_prompt_str)

        query_engine = get_query_engine(
            index_instance,
            llm=llm_instance,
            template=final_template,
        )
        if query_engine is None:
            query_engine = index_instance.as_query_engine(
                llm=llm_instance,
                streaming=False,
                text_qa_template=final_template,
            )
        response_obj = query_engine.query(user_prompt)
        answer = str(getattr(response_obj, "response", "")).strip()
        source_nodes_info = []
        if hasattr(response_obj, "source_nodes"):
            for node_with_score in response_obj.source_nodes:
                if node_with_score.node:
                    source_nodes_info.append(
                        {
                            "score": (
                                round(node_with_score.score, 4)
                                if node_with_score.score
                                else "N/A"
                            ),
                            "metadata": node_with_score.node.metadata,
                            "node_id": node_with_score.node.node_id,
                        }
                    )
        if local_imports_ok and db:
            _save_chat_message(session_id, "assistant", answer)
            db.session.commit()
        return success_response(
            {
                "query": user_prompt,
                "response": answer,
                "source_nodes": source_nodes_info,
            }
        )
    except Exception as query_err:
        logger.error(f"RAG QueryEngine error: {query_err}", exc_info=True)
        return error_response(
            f"Error during query processing: {query_err}", status_code=500
        )
    return handle_query(user_prompt, session_id)
