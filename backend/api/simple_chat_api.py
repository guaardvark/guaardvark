#!/usr/bin/env python3
"""
Simple Chat API - Direct LLM communication without RAG/LlamaIndex overhead
For basic conversations when enhanced features aren't needed
"""

import logging
import time
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from backend.utils.response_utils import success_response, error_response
from backend.utils.llm_service import run_llm_chat_prompt

logger = logging.getLogger(__name__)

simple_chat_bp = Blueprint("simple_chat", __name__, url_prefix="/api/simple-chat")

@simple_chat_bp.route("", methods=["POST"])
def simple_chat():
    """Simple chat endpoint using direct LLM communication"""
    try:
        # Validate request
        if not request.is_json:
            return error_response("Request must be JSON", 400)
        
        data = request.get_json()
        session_id = data.get('session_id')
        message = data.get('message')
        
        if not session_id or not message:
            return error_response("Missing session_id or message", 400)
        
        logger.info(f"Simple chat: Processing message for session {session_id}")
        
        # Get LLM instance
        llm = current_app.config.get("LLAMA_INDEX_LLM")
        if not llm:
            return error_response("LLM not configured", 503)
        
        # Generate response using direct LLM service
        start_time = time.monotonic()
        
        try:
            response_text = run_llm_chat_prompt(
                message, 
                llm_instance=llm,
                debug_id=f"simple_chat_{session_id}_{int(time.time())}"
            )
            
            duration = time.monotonic() - start_time
            
            if not response_text or response_text.strip() == "":
                return error_response("LLM returned empty response", 500)
            
            # Save conversation to session storage (optional)
            try:
                from backend.models import db
                from backend.api.enhanced_chat_api import get_chat_manager
                
                chat_manager = get_chat_manager()
                if chat_manager:
                    chat_manager._save_message(session_id, 'user', message)
                    chat_manager._save_message(session_id, 'assistant', response_text)
                    
            except Exception as save_error:
                logger.warning(f"Failed to save simple chat messages: {save_error}")
                # Continue anyway - the response is what matters
            
            logger.info(f"Simple chat: Generated response in {duration:.2f}s")
            
            # Return response in same format as enhanced chat for compatibility
            response_data = {
                "response": response_text,
                "model_used": getattr(llm, "model", "unknown"),
                "response_time": duration,
                "simple_mode_used": True,
                "rag_context": None,
                "context_stats": None,
                "token_usage": None
            }
            
            return success_response(response_data)
            
        except Exception as llm_error:
            logger.error(f"Simple chat LLM error: {llm_error}")
            return error_response(f"LLM error: {str(llm_error)}", 500)
        
    except Exception as e:
        logger.error(f"Simple chat error: {e}")
        return error_response(f"Chat processing failed: {str(e)}", 500)

@simple_chat_bp.route("/health", methods=["GET"])
def simple_chat_health():
    """Health check for simple chat service"""
    try:
        llm = current_app.config.get("LLAMA_INDEX_LLM")
        if not llm:
            return jsonify({"status": "error", "message": "LLM not configured"}), 503
            
        # Quick test
        start_time = time.monotonic()
        test_response = run_llm_chat_prompt("ping", llm_instance=llm, debug_id="health_check")
        duration = time.monotonic() - start_time
        
        if test_response and test_response.strip():
            return jsonify({
                "status": "healthy", 
                "model": getattr(llm, "model", "unknown"),
                "response_time": round(duration, 3),
                "test_response_length": len(test_response)
            }), 200
        else:
            return jsonify({"status": "error", "message": "LLM returned empty response"}), 503
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503