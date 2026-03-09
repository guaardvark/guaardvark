# backend/api/enhanced_chat_api.py
# Enhanced Chat API with improved context management, RAG, and token management
# Integrates all the new components for better LLM performance

import logging
import json
import uuid
import os
import time
import threading
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from flask import Blueprint, current_app, request, jsonify, Response, stream_with_context, send_file
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

# Local imports
try:
    from backend.utils.response_utils import error_response, success_response
    from backend.utils.db_utils import ensure_db_session_cleanup
except ImportError as e:
    logger.error(f"Failed to import basic dependencies: {e}")
    error_response = lambda msg, code=400: {"error": msg, "status": code}
    success_response = lambda data: {"success": True, "data": data}
    ensure_db_session_cleanup = lambda func: func  # No-op decorator

# Enhanced imports with fallbacks
try:
    from backend.utils.context_manager import ContextManager
except ImportError:
    ContextManager = None
    logger.warning("ContextManager not available")

try:
    from backend.utils.unified_index_manager import get_global_index_manager
except ImportError:
    get_global_index_manager = None
    logger.warning("Index manager not available")

try:
    from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker
except ImportError:
    EnhancedRAGChunker = None
    logger.warning("Enhanced RAG chunker not available")

try:
    from backend.utils import llm_service
except ImportError:
    llm_service = None
    logger.warning("LLM service not available")

try:
    from backend.utils.conversation_logger import get_conversation_logger
except ImportError:
    get_conversation_logger = None
    logger.warning("Conversation logger not available")


# Force local LlamaIndex configuration before imports
try:
    from backend.utils.llama_index_local_config import force_local_llama_index_config
    force_local_llama_index_config()
except Exception as e:
    logger.error(f"Failed to force local LlamaIndex config in enhanced_chat_api: {e}")

# LlamaIndex imports
from llama_index.core.chat_engine import CondensePlusContextChatEngine
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core import Settings
from llama_index.core.schema import QueryBundle

enhanced_chat_bp = Blueprint("enhanced_chat", __name__, url_prefix="/api/enhanced-chat")

# Global request tracking for duplicate prevention
_active_requests = set()
_request_cache = {}
_cache_lock = threading.Lock()

class EnhancedChatManager:
    """Enhanced chat manager with advanced context and RAG capabilities"""

    def __init__(self):
        # Initialize managers lazily (only when needed)
        self._context_manager = None
        self._index_manager = None
        self._rag_chunker = None

        # Chat engine cache
        self.chat_engines = {}

        # Persistent session memory storage
        self.session_memories = {}
        self.session_messages = {}  # Store full conversation history

        # Statistics
        self.stats = {
            'total_conversations': 0,
            'total_messages': 0,
            'context_compressions': 0,
            'rag_queries': 0,
            'avg_response_time': 0.0
        }

        # Intent detection cache (simple replacement for hardcoded methods)
        self.intent_cache = {}

    def _save_message_to_db(self, session_id: str, role: str, content: str):
        """Save a chat message to the database"""
        try:
            from backend.models import db, LLMSession, LLMMessage

            # Ensure session exists
            session = db.session.query(LLMSession).filter_by(id=session_id).first()
            if not session:
                session = LLMSession(id=session_id, user="anonymous")  # TODO: Get real user
                db.session.add(session)
                db.session.flush()

            # Add message
            message = LLMMessage(session_id=session_id, role=role, content=content)
            db.session.add(message)
            db.session.commit()

            logger.debug(f"Saved {role} message to database for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to save message to database: {e}")
            db.session.rollback()

    def _load_chat_history_from_db(self, session_id: str) -> List[ChatMessage]:
        """Load chat history from database"""
        try:
            from backend.models import db, LLMMessage

            messages = db.session.query(LLMMessage).filter_by(session_id=session_id).order_by(LLMMessage.timestamp).all()
            chat_history = []

            for msg in messages:
                if msg.role == "user":
                    chat_history.append(ChatMessage(role=MessageRole.USER, content=msg.content))
                elif msg.role == "assistant":
                    chat_history.append(ChatMessage(role=MessageRole.ASSISTANT, content=msg.content))
                # Skip system messages for now

            logger.debug(f"Loaded {len(chat_history)} messages from database for session {session_id}")
            return chat_history

        except Exception as e:
            logger.error(f"Failed to load chat history from database: {e}")
            return []

    def _get_dynamic_model_config(self, model_name: str) -> Dict[str, Any]:
        """Generate dynamic model configuration based on model characteristics"""
        # Default configuration - maximized for local system
        config = {
            'max_context_tokens': 32768,  # 4x increase for local system
            'max_response_tokens': 8192,  # 4x increase for local system
            'temperature': 0.7,
            'top_p': 0.9
        }

        model_lower = model_name.lower()

        # Adjust based on model size indicators in name
        if any(size in model_lower for size in ['1b', '1.5b']):
            # Smaller models
            config.update({
                'max_context_tokens': 16384,  # 4x increase for local system
                'max_response_tokens': 4096,  # 4x increase for local system
                'temperature': 0.8,
                'top_p': 0.9
            })
        elif any(size in model_lower for size in ['3b', '4b']):
            # Medium-small models
            config.update({
                'max_context_tokens': 32768,  # 4x increase for local system
                'max_response_tokens': 8192,  # 4x increase for local system
                'temperature': 0.7,
                'top_p': 0.9
            })
        elif any(size in model_lower for size in ['6.7b', '7b', '8b']):
            # Medium-large models
            config.update({
                'max_context_tokens': 65536,  # 4x increase for local system
                'max_response_tokens': 16384,  # 4x increase for local system
                'temperature': 0.7,
                'top_p': 0.9
            })
        elif any(size in model_lower for size in ['13b', '15b', '70b']):
            # Large models
            config.update({
                'max_context_tokens': 131072,  # 4x increase for local system
                'max_response_tokens': 32768,  # 4x increase for local system
                'temperature': 0.6,
                'top_p': 0.85
            })

        # Adjust based on model type/family
        if 'coder' in model_lower or 'code' in model_lower:
            # Code-focused models
            config.update({
                'temperature': 0.3,  # Lower temperature for more precise code
                'top_p': 0.8
            })
        elif 'mistral' in model_lower or 'dolphin' in model_lower:
            # Mistral family adjustments
            config.update({
                'temperature': 0.7,
                'top_p': 0.9
            })
        elif 'gemma' in model_lower:
            # Gemma family adjustments
            config.update({
                'temperature': 0.7,
                'top_p': 0.9
            })
        elif 'llama' in model_lower:
            # Llama family adjustments
            config.update({
                'temperature': 0.7,
                'top_p': 0.9
            })
        elif any(vision in model_lower for vision in ['llava', 'moondream', 'qwen2.5vl']):
            # Vision models - may need different handling
            config.update({
                'max_context_tokens': 65536,  # 4x increase for local system
                'max_response_tokens': 12288,  # 4x increase for local system
                'temperature': 0.7,
                'top_p': 0.9
            })

        return config

    def _get_model_config(self, model_name: str) -> Dict[str, Any]:
        """Get configuration for any model using dynamic configuration"""
        try:
            return self._get_dynamic_model_config(model_name)
        except Exception as e:
            logger.warning(f"Error generating dynamic model config for {model_name}: {e}")
            # Emergency fallback
            return {
                'max_context_tokens': 32768,  # 4x increase for local system
                'max_response_tokens': 8192,  # 4x increase for local system
                'temperature': 0.7,
                'top_p': 0.9
            }

    def _detect_intent_with_rules(self, message: str) -> str:
        """
        Rule-based intent detection using existing Rules System infrastructure
        Much faster and more reliable than LLM-based detection
        """
        try:
            from backend.models import Rule, db

            # Try to get intent detection rules from database
            # Look for rules with type="COMMAND_RULE" and names containing "Intent Detection"
            intent_rules = db.session.query(Rule).filter_by(
                is_active=True,
                type="COMMAND_RULE",
                level="SYSTEM"
            ).filter(Rule.name.like('%Intent Detection%')).order_by(Rule.created_at).all()

            if intent_rules:
                logger.info(f"Intent detection: Found {len(intent_rules)} intent detection rules in database")
                # Use rule-based pattern matching
                message_lower = message.lower()

                for rule in intent_rules:
                    # Parse rule text as keyword patterns
                    # Expected format: "intent_name: keyword1,keyword2,keyword3"
                    try:
                        lines = rule.rule_text.strip().split('\n')
                        for line in lines:
                            if ':' in line:
                                intent_name, keywords_str = line.split(':', 1)
                                intent_name = intent_name.strip()
                                keywords = [k.strip() for k in keywords_str.split(',')]

                                # Check if any keywords match
                                if any(keyword in message_lower for keyword in keywords):
                                    logger.info(f"Intent detection: Matched rule '{rule.name}' -> {intent_name}")
                                    return intent_name
                    except Exception as rule_error:
                        logger.warning(f"Error parsing intent rule {rule.id}: {rule_error}")
                        continue
            else:
                logger.info("Intent detection: No intent detection rules found in database, using hardcoded fallback")

            # Fallback to simple pattern matching if no rules found
            return self._fallback_intent_detection(message)

        except Exception as e:
            logger.warning(f"Intent detection: Rule lookup failed: {e}, using fallback")
            return self._fallback_intent_detection(message)

    def _fallback_intent_detection(self, message: str) -> str:
        """Simple fallback when LLM is unavailable"""
        msg_lower = message.lower()

        # ENHANCED: More specific file analysis detection to prevent auto-output
        # Only trigger file_analysis if user explicitly asks for analysis
        if any(w in msg_lower for w in ['analyze', 'review', 'examine', 'inspect', 'check']):
            # Check if there's a file reference in the message
            has_file_reference = any(w in msg_lower for w in ['file', 'document', 'code', 'upload']) or \
                                any(ext in msg_lower for ext in ['.jsx', '.js', '.py', '.html', '.css', '.json', '.csv', '.txt', '.md'])
            if has_file_reference:
                return "file_analysis"
        elif any(w in msg_lower for w in ['what is', 'what does', 'explain', 'describe', 'tell me about']) and \
             any(w in msg_lower for w in ['file', 'document', 'code', 'upload']):
            # For general questions about files, use general_chat instead of auto-analysis
            return "general_chat"
        elif any(w in msg_lower for w in ['bulk', 'batch', 'many']) and \
             any(w in msg_lower for w in ['csv', 'generate']):
            return "bulk_csv_generation"
        elif any(w in msg_lower for w in ['website', 'url', 'http']):
            return "website_analysis"
        elif any(w in msg_lower for w in ['generate', 'create', 'make']) and \
             any(w in msg_lower for w in ['file', 'csv']):
            return "file_generation"
        elif any(w in msg_lower for w in ['improve', 'fix', 'optimize']) and \
             any(w in msg_lower for w in ['file', 'document', 'code']):
            return "file_improvement"
        elif any(w in msg_lower for w in ['save as', 'download as', 'export as']):
            return "explicit_file_generation"
        else:
            return "general_chat"

    @property
    def context_manager(self):
        """Initialize context manager with configuration flags"""
        if self._context_manager is None and ContextManager:
            try:
                from backend.config import ENHANCED_CONTEXT_ENABLED, CONTEXT_PERSISTENCE_DIR

                if ContextManager is None:
                    logger.warning("ContextManager class not available - skipping initialization")
                    self._context_manager = None
                elif ENHANCED_CONTEXT_ENABLED:
                    self._context_manager = ContextManager(
                        max_tokens=8192,
                        compression_threshold=0.8,
                        persistence_dir=CONTEXT_PERSISTENCE_DIR
                    )
                    logger.info("Enhanced Context Manager activated")
                else:
                    logger.info("Enhanced Context Manager disabled by configuration")
                    self._context_manager = None

            except Exception as e:
                logger.error(f"Failed to initialize Context Manager: {e}")
                self._context_manager = None
        return self._context_manager

    @property
    def index_manager(self):
        """Initialize index manager with configuration flags"""
        if self._index_manager is None and get_global_index_manager:
            try:
                from backend.config import ADVANCED_RAG_ENABLED

                if get_global_index_manager is None:
                    logger.warning("get_global_index_manager function not available - skipping initialization")
                    self._index_manager = None
                elif ADVANCED_RAG_ENABLED:
                    self._index_manager = get_global_index_manager()
                    logger.info("Advanced Index Manager activated")
                else:
                    logger.info("Advanced Index Manager disabled by configuration")
                    self._index_manager = None

            except Exception as e:
                logger.error(f"Failed to initialize Index Manager: {e}")
                self._index_manager = None
        return self._index_manager

    @property
    def rag_chunker(self):
        """Initialize RAG chunker with configuration flags"""
        if self._rag_chunker is None and EnhancedRAGChunker:
            try:
                from backend.config import ADVANCED_RAG_ENABLED

                if EnhancedRAGChunker is None:
                    logger.warning("EnhancedRAGChunker class not available - skipping initialization")
                    self._rag_chunker = None
                elif ADVANCED_RAG_ENABLED:
                    self._rag_chunker = EnhancedRAGChunker()
                    logger.info("Enhanced RAG Chunker activated")
                else:
                    logger.info("Enhanced RAG Chunker disabled by configuration")
                    self._rag_chunker = None

            except Exception as e:
                logger.error(f"Failed to initialize RAG Chunker: {e}")
                self._rag_chunker = None
        return self._rag_chunker

    def _get_active_model(self) -> str:
        """Get the currently active model"""
        try:
            llm_instance = current_app.config.get('LLAMA_INDEX_LLM')
            if llm_instance and hasattr(llm_instance, 'model'):
                return llm_instance.model
        except Exception as e:
            logger.warning(f"Error getting active model: {e}")
        return 'gemma3:4b'  # Default

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for text"""
        # Simple estimation: ~4 characters per token
        return max(1, len(text) // 4)
    
    def _save_message(self, session_id: str, role: str, content: str):
        """Save a message to the session history"""
        if session_id not in self.session_messages:
            self.session_messages[session_id] = []
        
        self.session_messages[session_id].append({
            'role': role,
            'content': content,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Keep only last 50 messages per session for memory efficiency
        if len(self.session_messages[session_id]) > 50:
            self.session_messages[session_id] = self.session_messages[session_id][-50:]

    def _build_system_prompt(self, model_name: str, context_info: Dict[str, Any], simple_mode: bool = False, web_search_context: str = "", chat_mode: str = None) -> str:
        """Build a bulletproof system prompt with anti-fabrication rules and web search integration"""

        # For simple mode, use a simple prompt without anti-fabrication complexity
        if simple_mode:
            return """You are a friendly and helpful AI assistant. Be natural, conversational, and concise in your responses.
For simple greetings and casual interactions, respond naturally without being overly formal or verbose."""

        try:
            # Import rule_utils for database-based rule fetching and settings
            from backend import rule_utils
            from backend.models import db
            from backend.utils.settings_utils import get_web_access

            # Detect if web search is available and enabled
            web_search_enabled = False
            try:
                web_search_enabled = get_web_access() and bool(web_search_context)
                logger.info(f"Web search enabled: {web_search_enabled}, has context: {bool(web_search_context)}")
            except Exception as e:
                logger.warning(f"Could not determine web search status: {e}")
                web_search_enabled = False

            # Get the system prompt template from database (RulesPage)
            # Try enhanced_chat first, then fall back to global_default_chat_system_prompt
            base_template, rule_id = rule_utils.get_active_system_prompt(
                "enhanced_chat",
                db.session,
                model_name=model_name
            )

            if not base_template:
                # Fallback to global_default_chat_system_prompt
                base_template, rule_id = rule_utils.get_active_system_prompt(
                    "global_default_chat_system_prompt",
                    db.session,
                    model_name=model_name
                )

            if rule_id:
                logger.info(f"Using system prompt rule ID {rule_id} from database for model '{model_name}'")
            else:
                logger.warning(f"No system prompt rule found, using fallback for model '{model_name}'")
                # Use a basic template with required placeholders
                base_template = """{rules_str}

You are a helpful AI assistant. Be accurate, concise, and honest.

{context_str}

{query_str}"""

            # Build context string for template
            context_parts = []

            # Add web search context if available
            if web_search_context:
                context_parts.append(web_search_context)

            # Add conversation context info
            if context_info and context_info.get('total_contexts', 0) > 0:
                context_parts.append(f"CONVERSATION CONTEXT: You have access to {context_info['total_contexts']} conversation contexts.")

            # Add model-specific information
            model_info = ""
            if 'gemma' in model_name.lower():
                model_info = "You are powered by Gemma, optimized for helpful and informative responses."
            elif 'llama' in model_name.lower():
                model_info = "You are powered by Llama, focused on detailed reasoning and analysis."
            elif 'mistral' in model_name.lower():
                model_info = "You are powered by Mistral, optimized for efficient and accurate responses."

            if model_info:
                context_parts.append(f"MODEL INFO: {model_info}")

            # Add mode-specific targeting guidance (prompt-based, not brain switching)
            if chat_mode == 'filegen':
                context_parts.append("FOCUS MODE: FileGen - Prioritize CSV generation, file creation, and structured data output. Suggest appropriate file formats and data organization techniques.")
            elif chat_mode == 'code':
                context_parts.append("FOCUS MODE: Code - Prioritize code analysis, programming assistance, best practices, debugging, and software development guidance. Provide detailed technical insights.")
            elif chat_mode is None:
                context_parts.append("UNIVERSAL MODE: No specific focus - Full capability access with balanced responses across all domains.")

            # Build final context string
            context_str = "\n\n".join(context_parts) if context_parts else "No additional context available."

            # Apply template with context (this will be used by the chat engine's system message)
            # The template includes the anti-fabrication rules and proper formatting
            final_prompt = base_template.format(
                rules_str="BULLETPROOF ANTI-FABRICATION SYSTEM ACTIVE",
                context_str=context_str,
                query_str="[USER QUERY WILL BE PROVIDED SEPARATELY]"
            )

            logger.info(f"Built bulletproof system prompt (web_search_enabled={web_search_enabled}, context_parts={len(context_parts)})")

            return final_prompt

        except Exception as e:
            logger.error(f"Error building bulletproof system prompt, falling back to safe default: {e}")

            # SAFE FALLBACK with basic anti-fabrication rules
            fallback_prompt = f"""CRITICAL ANTI-FABRICATION RULES
- NEVER FABRICATE INFORMATION: Only use provided context or verified training knowledge
- NEVER CLAIM CAPABILITIES YOU DON'T HAVE: Be transparent about limitations
- NEVER INVENT DATA: Never make up statistics, facts, or current information
- ALWAYS CITE SOURCES: Clearly distinguish between context, training knowledge, and speculation

You are a helpful AI assistant powered by {model_name}. Be honest, accurate, and transparent about your limitations.

Context: {context_info.get('total_contexts', 0)} conversation contexts available.
"""
            return fallback_prompt

    def _retrieve_entity_context(self, query: str, max_chunks: int = 3) -> List[Dict[str, Any]]:
        """Always retrieve entity context for universal RAG access to CLIENT/WEBSITE/FILE/PROJECT data"""
        entity_context = []
        try:
            from backend.services.indexing_service import search_with_llamaindex

            # Search specifically for entity summaries with high priority
            entity_results = search_with_llamaindex(f"entity_summary {query}", max_chunks=max_chunks * 3)

            if entity_results:
                for result in entity_results:
                    metadata = result.get('metadata', {})
                    if metadata.get('content_type') == 'entity_summary':
                        chunk_info = {
                            'content': result.get('text', ''),
                            'metadata': metadata,
                            'score': result.get('score', 0.0),
                            'source': f"{metadata.get('entity_type', 'Entity')} - {metadata.get('entity_id', 'Unknown')}",
                            'content_type': 'entity_summary',
                            'entity_type': metadata.get('entity_type', '')
                        }
                        entity_context.append(chunk_info)

            logger.info(f"Retrieved {len(entity_context)} entity context chunks for universal RAG")
            return entity_context[:max_chunks]

        except ImportError as ie:
            logger.warning(f"Failed to import indexing service for entity context: {ie}")
            return []
        except Exception as e:
            logger.warning(f"Failed to retrieve entity context: {e}", exc_info=True)
            return []

    def _retrieve_relevant_context(self, query: str, session_id: str, max_chunks: int = 5) -> List[Dict[str, Any]]:
        """Retrieve relevant context from the knowledge base, including entity context and uploaded files"""
        try:
            logger.info(f"DEBUG: _retrieve_relevant_context called with query: '{query}'")
            # UNIVERSAL RAG: Always retrieve entity context for CLIENT/WEBSITE/FILE/PROJECT access
            entity_context = self._retrieve_entity_context(query, max_chunks=2)
            logger.info(f"UNIVERSAL RAG: Retrieved {len(entity_context)} entity context chunks")

            # First, check for uploaded code files that match the query
            # CRITICAL FIX: Use original message for file matching, not the enhanced message with timestamps
            # The enhance_message_with_time() adds timestamps that confuse file matching logic
            original_query = query
            if "[CURRENT SYSTEM TIME]" in query:
                # Extract the original message after the timestamp block
                parts = query.split("\n\n", 1)  # Split at first double newline
                if len(parts) > 1:
                    original_query = parts[1].strip()
                    logger.info(f"DEBUG: Extracted original query from enhanced message: '{original_query}'")

            print(f"=== ENHANCED CHAT DEBUG: About to call _retrieve_uploaded_files_context with original_query='{original_query}', session_id='{session_id}' ===")
            logger.debug(f"DEBUG: About to call _retrieve_uploaded_files_context with original_query='{original_query}', session_id='{session_id}'")
            print(f"CALLING FILE RETRIEVAL: query='{original_query}', session='{session_id}'")
            uploaded_file_context = self._retrieve_uploaded_files_context(original_query, session_id)
            print(f"FILE RETRIEVAL RESULT: {len(uploaded_file_context)} files returned")
            logger.info(f"DEBUG: Got {len(uploaded_file_context)} uploaded file contexts")
            if uploaded_file_context:
                logger.info(f"DEBUG: File contexts: {[f['source'] for f in uploaded_file_context]}")
            else:
                logger.info(f"DEBUG: No file contexts returned")

            # Try to get retriever from index manager for traditional RAG
            rag_context = []
            try:
                # Use working search_with_llamaindex directly instead of index_manager
                from backend.services.indexing_service import search_with_llamaindex
                try:
                    search_results = search_with_llamaindex(query, max_chunks=max_chunks * 2)
                    logger.info(f"DEBUG: search_with_llamaindex returned {len(search_results) if search_results else 0} results")

                    # Convert search results directly to rag_context instead of using retriever
                    if search_results:
                        for result in search_results:
                            chunk_info = {
                                'content': result.get('text', ''),
                                'metadata': result.get('metadata', {}),
                                'score': result.get('score', 0.0),
                                'source': result.get('metadata', {}).get('source_filename', 'Unknown'),
                                'content_type': 'document',
                                'entity_type': ''
                            }
                            rag_context.append(chunk_info)

                    retriever = None  # Skip the retriever loop below
                except Exception as search_error:
                    logger.error(f"search_with_llamaindex failed: {search_error}")
                    retriever = None

                if retriever:
                    # Try to retrieve relevant nodes
                    # CRITICAL FIX: Wrap string query in QueryBundle for LlamaIndex retriever
                    query_bundle = QueryBundle(query_str=query)
                    nodes = retriever.retrieve(query_bundle)

                    if nodes:
                        # Process RAG nodes
                        for node in nodes:
                            node_metadata = node.metadata if hasattr(node, 'metadata') else {}
                            content_type = node_metadata.get('content_type', 'document')
                            entity_type = node_metadata.get('entity_type', '')

                            chunk_info = {
                                'content': node.get_content(),
                                'metadata': node_metadata,
                                'score': getattr(node, 'score', 0.0),
                                'source': node_metadata.get('source_document', 'Unknown'),
                                'content_type': content_type,
                                'entity_type': entity_type
                            }
                            rag_context.append(chunk_info)
                    else:
                        logger.info("No relevant nodes found from index - checking uploaded files only")
                else:
                    logger.info("No retriever available - checking uploaded files only")

            except Exception as index_error:
                logger.info(f"Index not available or empty - checking uploaded files only: {index_error}")

            # UNIVERSAL RAG: Combine all context types with entity context always included
            # Priority: uploaded files > universal entity context > search entity context > documents
            additional_entity_chunks = []
            document_chunks = []
            uploaded_file_chunks = uploaded_file_context  # These get highest priority

            for chunk in rag_context:
                content_type = chunk.get('content_type', 'document')
                if content_type == 'entity_summary':
                    additional_entity_chunks.append(chunk)
                else:
                    document_chunks.append(chunk)

            # Combine contexts: uploaded files > universal entities > search entities > documents
            all_contexts = uploaded_file_chunks + entity_context + additional_entity_chunks + document_chunks
            context_chunks = all_contexts[:max_chunks]

            # Add to context manager with enhanced metadata
            if context_chunks:
                # Create enhanced context with file and entity information
                enhanced_context_parts = []

                # Add uploaded file context first (highest priority)
                for chunk in uploaded_file_chunks:
                    enhanced_context_parts.append(f"[UPLOADED FILE: {chunk['source']}]\n{chunk['content']}")

                # Add universal entity context (always included)
                for chunk in entity_context:
                    entity_label = chunk['entity_type'].upper() if chunk['entity_type'] else 'ENTITY'
                    enhanced_context_parts.append(f"[{entity_label}] {chunk['content']}")

                # Add additional entity context from search
                for chunk in additional_entity_chunks[:1]:  # Limit additional entity chunks
                    entity_label = chunk['entity_type'].upper() if chunk['entity_type'] else 'ENTITY'
                    enhanced_context_parts.append(f"[{entity_label}] {chunk['content']}")

                # Add document context
                for chunk in document_chunks[:2]:  # Limit document chunks
                    enhanced_context_parts.append(f"[DOCUMENT] {chunk['content']}")

                combined_context = "\n\n".join(enhanced_context_parts)

                if self.context_manager:
                    try:
                        self.context_manager.add_context(
                            session_id=session_id,
                            content=combined_context,
                            chunk_type='enhanced_rag_with_files',
                            metadata={
                                'query': query,
                                'chunks_count': len(context_chunks),
                                'uploaded_file_chunks': len(uploaded_file_chunks),
                                'universal_entity_chunks': len(entity_context),
                                'additional_entity_chunks': len(additional_entity_chunks),
                                'document_chunks': len(document_chunks)
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to add context to context manager: {e}")

            self.stats['rag_queries'] += 1
            if uploaded_file_chunks or entity_context:
                logger.info(f"UNIVERSAL RAG: Retrieved context - {len(uploaded_file_chunks)} uploaded files, {len(entity_context)} universal entities, {len(additional_entity_chunks)} search entities, {len(document_chunks)} documents")
            else:
                logger.info(f"Retrieved context: {len(additional_entity_chunks)} search entities, {len(document_chunks)} documents")

            return context_chunks

        except Exception as e:
            logger.warning(f"Error retrieving context (falling back to basic chat): {e}")
            return []

    def _create_chat_engine(self, session_id: str, model_name: str, web_search_context: str = "", chat_mode: str = None):
        """Create or get cached chat engine for session with web search context"""
        if session_id in self.chat_engines:
            return self.chat_engines[session_id]

        try:
            # Get model configuration
            model_config = self._get_model_config(model_name)

            # Try to get index and create retriever
            retriever = None
            try:
                if self.index_manager:
                    index, _ = self.index_manager.get_index()
                    base_retriever = index.as_retriever(similarity_top_k=5)

                    # Use direct retriever - let errors surface properly
                    retriever = base_retriever
                    logger.info("Using enhanced chat with RAG capabilities")
                else:
                    logger.info("Index manager not available, using basic chat mode")
            except Exception as e:
                logger.info(f"Vector store not available, using basic chat mode: {e}")
                retriever = None

            # Create or retrieve persistent memory buffer with chat history
            if session_id in self.session_memories:
                memory = self.session_memories[session_id]
                # Handle both old and new ChatMemoryBuffer API
                try:
                    history_length = len(memory.chat_history) if hasattr(memory, 'chat_history') else len(memory.chat_store.store)
                except:
                    history_length = 0
                logger.info(f"Retrieved existing memory for session {session_id} with {history_length} messages")
            else:
                # FIXED: Get chat history from database first, fall back to in-memory
                chat_history = self._load_chat_history_from_db(session_id)
                if not chat_history and session_id in self.session_messages:
                    chat_history = self.session_messages[session_id]
                    logger.info(f"Using in-memory chat history with {len(chat_history)} messages")
                elif chat_history:
                    logger.info(f"Loaded {len(chat_history)} messages from database for session {session_id}")

                # Create memory with chat history (archived pattern)
                try:
                    memory = ChatMemoryBuffer.from_defaults(
                        chat_history=chat_history,
                        token_limit=model_config['max_context_tokens'] // 2
                    )
                    logger.info(f"Memory buffer created with {len(chat_history)} messages")
                except Exception as memory_error:
                    logger.warning(f"Failed to create memory with chat history: {memory_error}")
                    memory = ChatMemoryBuffer.from_defaults(token_limit=model_config['max_context_tokens'] // 2)

                self.session_memories[session_id] = memory
                # FIXED: Sync in-memory storage with loaded chat history
                if session_id not in self.session_messages:
                    self.session_messages[session_id] = chat_history
                logger.info(f"Created new memory for session {session_id} with {len(chat_history)} messages")

            # Get context info for system prompt (safe access)
            context_info = {}
            if self.context_manager:
                try:
                    context_info = self.context_manager.get_context_stats(session_id)
                except Exception as e:
                    logger.warning(f"Context manager not available, using empty context: {e}")
                    context_info = {}
            system_prompt = self._build_system_prompt(model_name, context_info, simple_mode=False, web_search_context=web_search_context, chat_mode=chat_mode)

            # Create chat engine - with or without retriever
            if retriever:
                try:
                    chat_engine = CondensePlusContextChatEngine.from_defaults(
                        retriever=retriever,
                        memory=memory,
                        system_prompt=system_prompt,
                        llm=current_app.config.get('LLAMA_INDEX_LLM'),
                        verbose=current_app.debug,
                        streaming=True
                    )
                    logger.info(f"Created enhanced chat engine with RAG for session {session_id}")
                except Exception as engine_error:
                    logger.warning(f"Failed to create enhanced chat engine, falling back to simple: {engine_error}")
                    # Fall back to basic chat engine without retriever
                    from llama_index.core.chat_engine import SimpleChatEngine
                    chat_engine = SimpleChatEngine.from_defaults(
                        memory=memory,
                        system_prompt=system_prompt,
                        llm=current_app.config.get('LLAMA_INDEX_LLM'),
                        verbose=current_app.debug
                    )
                    logger.info(f"Created basic chat engine (fallback) for session {session_id}")
            else:
                # Fall back to basic chat engine without retriever
                from llama_index.core.chat_engine import SimpleChatEngine
                chat_engine = SimpleChatEngine.from_defaults(
                    memory=memory,
                    system_prompt=system_prompt,
                    llm=current_app.config.get('LLAMA_INDEX_LLM'),
                    verbose=current_app.debug
                )
                logger.info(f"Created basic chat engine (no RAG) for session {session_id}")

            # Cache the engine
            self.chat_engines[session_id] = chat_engine

            return chat_engine

        except Exception as e:
            logger.error(f"Error creating chat engine: {e}")
            raise

    def _create_simple_chat_engine(self, session_id: str, model_name: str, web_search_context: str = ""):
        """Create a simple chat engine without RAG capabilities but with web search context"""
        if session_id in self.chat_engines:
            return self.chat_engines[session_id]

        try:
            # Get model configuration
            model_config = self._get_model_config(model_name)

            # Create or retrieve persistent memory buffer with chat history
            if session_id in self.session_memories:
                memory = self.session_memories[session_id]
                # Handle both old and new ChatMemoryBuffer API
                try:
                    history_length = len(memory.chat_history) if hasattr(memory, 'chat_history') else len(memory.chat_store.store)
                except:
                    history_length = 0
                logger.info(f"Retrieved existing memory for session {session_id} with {history_length} messages")
            else:
                # FIXED: Get chat history from database first, fall back to in-memory
                chat_history = self._load_chat_history_from_db(session_id)
                if not chat_history and session_id in self.session_messages:
                    chat_history = self.session_messages[session_id]
                    logger.info(f"Using in-memory chat history with {len(chat_history)} messages")
                elif chat_history:
                    logger.info(f"Loaded {len(chat_history)} messages from database for session {session_id}")

                # Create memory with chat history (archived pattern)
                try:
                    memory = ChatMemoryBuffer.from_defaults(
                        chat_history=chat_history,
                        token_limit=model_config['max_context_tokens'] // 2
                    )
                    logger.info(f"Memory buffer created with {len(chat_history)} messages")
                except Exception as memory_error:
                    logger.warning(f"Failed to create memory with chat history: {memory_error}")
                    memory = ChatMemoryBuffer.from_defaults(token_limit=model_config['max_context_tokens'] // 2)

                self.session_memories[session_id] = memory
                # FIXED: Sync in-memory storage with loaded chat history
                if session_id not in self.session_messages:
                    self.session_messages[session_id] = chat_history
                logger.info(f"Created new memory for session {session_id} with {len(chat_history)} messages")

            # Get context info for system prompt (safe access)
            context_info = {}
            if self.context_manager:
                try:
                    context_info = self.context_manager.get_context_stats(session_id)
                except Exception as e:
                    logger.warning(f"Context manager not available, using empty context: {e}")
                    context_info = {}
            system_prompt = self._build_system_prompt(model_name, context_info, simple_mode=True, web_search_context=web_search_context)

            # Create simple chat engine without retriever
            from llama_index.core.chat_engine import SimpleChatEngine

            # Debug logging
            llm_instance = current_app.config.get('LLAMA_INDEX_LLM')
            logger.info(f"Creating SimpleChatEngine with LLM: {llm_instance}")
            logger.info(f"System prompt length: {len(system_prompt) if system_prompt else 0}")
            logger.info(f"Memory: {memory}")

            chat_engine = SimpleChatEngine.from_defaults(
                memory=memory,
                system_prompt=system_prompt,
                llm=llm_instance,
                verbose=current_app.debug
            )
            logger.info(f"Created simple chat engine for session {session_id}")

            # Cache the engine
            self.chat_engines[session_id] = chat_engine

            return chat_engine

        except Exception as e:
            logger.error(f"Error creating simple chat engine: {e}")
            raise

    def _get_or_create_session(self, session_id: str) -> Any:
        """Get or create a chat session"""
        try:
            # Import db within Flask app context
            from backend.models import db, LLMSession
            session = db.session.get(LLMSession, session_id)
            if not session:
                try:
                    session = LLMSession(id=session_id, user="default")
                    db.session.add(session)
                    db.session.commit()  # Commit the session to prevent unique constraint violations
                    self.stats['total_conversations'] += 1
                except Exception as create_error:
                    # Handle race condition where session was created between check and insert
                    db.session.rollback()
                    session = db.session.get(LLMSession, session_id)
                    if not session:
                        # If still no session, re-raise the error
                        logger.error(f"Failed to create session {session_id}: {create_error}")
                        raise create_error
                    logger.warning(f"Session {session_id} already existed during creation attempt")
            return session
        except Exception as e:
            logger.error(f"Error in _get_or_create_session: {e}")
            # Create a minimal session object if database fails
            class MinimalSession:
                def __init__(self, session_id):
                    self.id = session_id
                    self.user = "default"
            return MinimalSession(session_id)

    def _save_message(self, session_id: str, role: str, content: str) -> Optional[int]:
        """Save a message to the database and context manager with robust transaction handling
        Returns: Message ID if successful, None if failed"""
        try:
            # Import db utilities and models within Flask app context
            from backend.models import db, LLMMessage
            from backend.utils.db_utils import safe_db_commit, safe_db_rollback

            # Validate inputs
            if not session_id or not role or not content:
                logger.warning(f"Invalid message data: session_id={session_id}, role={role}, content_length={len(content) if content else 0}")
                return None

            # Save to database with proper transaction handling
            try:
                message = LLMMessage(
                    session_id=session_id,
                    role=role,
                    content=content,
                    timestamp=datetime.now()
                )
                db.session.add(message)

                # Use safe commit with proper error handling
                if safe_db_commit(f"save_message_{session_id}"):
                    logger.debug(f"Successfully saved message for session {session_id}")
                    message_id = message.id  # Capture the ID after commit
                else:
                    logger.error(f"Failed to commit message for session {session_id}")
                    return None

            except Exception as db_error:
                logger.error(f"Database error saving message for session {session_id}: {db_error}")
                safe_db_rollback(f"save_message_{session_id}")
                return None

            # Add to context manager if available (separate transaction)
            if self.context_manager:
                try:
                    self.context_manager.add_context(
                        session_id=session_id,
                        content=f"{role}: {content}",
                        chunk_type='message',
                        metadata={'role': role, 'timestamp': datetime.now().isoformat()}
                    )
                except Exception as cm_error:
                    logger.warning(f"Context manager error for session {session_id}: {cm_error}")
                    # Don't fail the whole operation if context manager fails

            # Add to session messages for memory persistence
            if session_id not in self.session_messages:
                self.session_messages[session_id] = []

                            # Create ChatMessage for memory buffer (using LlamaIndex format)
                from llama_index.core.llms import ChatMessage, MessageRole
                try:
                    message_role = MessageRole.USER if role == 'user' else MessageRole.ASSISTANT
                    chat_message = ChatMessage(role=message_role, content=content)
                    self.session_messages[session_id].append(chat_message)

                    # Update existing memory buffer if available
                    if session_id in self.session_memories:
                        memory = self.session_memories[session_id]
                        memory.put(chat_message)
                        logger.debug(f"Added message to existing memory buffer for session {session_id}")

                    logger.debug(f"Added message to session_messages for session {session_id}")
                except Exception as msg_error:
                    logger.warning(f"Failed to add message to session storage: {msg_error}")

            # Update stats (only if database save succeeded)
            self.stats['total_messages'] += 1

            return message_id  # Return the message ID if everything succeeded

        except Exception as e:
            logger.error(f"Error saving message for session {session_id}: {e}", exc_info=True)
            # Don't raise - just log the error and continue
            return None

    def _is_simple_message(self, message: str) -> bool:
        """Detect if a message is simple and doesn't need RAG processing"""
        message_lower = message.lower().strip()

        # Complex keywords that indicate RAG/analysis is needed
        complex_keywords = [
            'analyze', 'analysis', 'examine', 'review', 'document', 'file', 'csv', 'data',
            'generate', 'create', 'build', 'make', 'produce', 'explain', 'describe',
            'improve', 'optimize', 'fix', 'debug', 'code', 'script', 'programming',
            'best practices', 'how to', 'tell me about', 'research',
            'compare', 'evaluate', 'assess', 'implementation', 'strategy'
        ]

        # Check for complex keywords first
        if any(keyword in message_lower for keyword in complex_keywords):
            return False

        # Simple greeting patterns - use word boundaries to prevent substring matches
        import re
        simple_patterns = [
            r'\bhello\b', r'\bhi\b(?!\w)', r'\bhey\b', r'\bgood morning\b', r'\bgood afternoon\b', r'\bgood evening\b',
            r'\bhow are you\b', r'\bhow do you do\b', r'\bwhats up\b', r'\bhow is it going\b',
            r'\bnice to meet you\b', r'\bpleased to meet you\b', r'\bgood to see you\b',
            r'\bthanks\b', r'\bthank you\b', r'\bthats great\b', r'\bawesome\b', r'\bcool\b', r'\bnice\b',
            r'\bok\b', r'\bokay\b', r'\byes\b', r'\bno\b', r'\bsure\b', r'\bfine\b', r'\bgood\b', r'\bgreat\b',
            r'\bbye\b', r'\bgoodbye\b', r'\bsee you\b', r'\bcatch you later\b', r'\btalk to you later\b'
        ]

        # Check for WHOLE WORD matches, not substrings
        for pattern in simple_patterns:
            if re.search(pattern, message_lower):
                return True

        # Check if message is just punctuation or very short
        if len(message.strip()) <= 10 and not any(char.isalpha() for char in message):
            return True

        return False

    def _should_use_web_search(self, message: str) -> bool:
        """SIMPLIFIED: Detect if a message likely needs current information"""
        message_lower = message.lower().strip()

        # Clear indicators that current/real-time information is needed
        current_indicators = [
            'current', 'today', 'todays', 'now', 'latest', 'recent',
            'what is', 'what are', 'check', 'find', 'search',
            'website', 'site', 'www.', 'http', '.com', '.org', '.net'
        ]

        # URL pattern detection
        import re
        has_url = bool(re.search(r'(?:https?://|www\.)[^\s]+', message))

        # Check for current information indicators
        needs_current_info = any(indicator in message_lower for indicator in current_indicators)

        # Question words that often need current information
        question_patterns = [
            r'what.*(?:is|are).*(?:the|current|today|latest)',
            r'how.*(?:is|are|to)',
            r'when.*(?:did|will|is)',
            r'where.*(?:is|can|to)',
            r'who.*(?:is|are)'
        ]

        has_question_pattern = any(re.search(pattern, message_lower) for pattern in question_patterns)

        # Be more permissive - if it's a question or mentions current info or URLs, try web search
        result = has_url or needs_current_info or has_question_pattern or len(message.split()) > 6

        if result:
            logger.info(f"Web search ENABLED for: '{message[:50]}...'")
        else:
            logger.debug(f"Web search SKIPPED for: '{message[:50]}...'")

        return result

    def _perform_web_search_safe(self, query: str) -> Dict[str, Any]:
        """BULLETPROOF: Safely perform web search with comprehensive error handling"""
        try:
            logger.info(f"DEBUG: _perform_web_search_safe received query: '{query}'")

            # Import web search functionality with error handling
            try:
                from backend.api.web_search_api import enhanced_web_search
                from backend.utils.settings_utils import get_web_access
            except ImportError as e:
                logger.error(f"Web search functionality not available: {e}")
                return {
                    "success": False,
                    "error": "Web search functionality not available",
                    "strategy_used": "none",
                    "user_message": "I cannot search the web as the web search functionality is not available."
                }

            # Check if web access is enabled in settings
            if not get_web_access():
                logger.info("Web search requested but disabled in settings")
                return {
                    "success": False,
                    "error": "Web search disabled in settings",
                    "strategy_used": "disabled",
                    "user_message": "I cannot search the web as web access is disabled in system settings. You can enable it in Settings > Allow LLM Web Search. I'll use my training knowledge to help you instead.",
                    "fallback_available": True
                }

            logger.info(f"Performing web search for: '{query[:100]}...'")

            # Perform the web search
            search_results = enhanced_web_search(query)

            if search_results.get("success"):
                data = search_results.get("data", {})
                strategy = search_results.get("strategy_used", "unknown")

                logger.info(f"Web search successful using {strategy}")

                # Format results for LLM context
                formatted_context = self._format_web_search_context(search_results, query)

                return {
                    "success": True,
                    "strategy_used": strategy,
                    "raw_results": search_results,
                    "formatted_context": formatted_context,
                    "user_message": f"Based on web search results ({strategy}): {data.get('snippet', 'Information retrieved')}"
                }
            else:
                # Web search failed - return failure info for transparency
                error_info = search_results.get("data", {})
                logger.warning(f"Web search failed: {error_info}")

                return {
                    "success": False,
                    "error": "Web search failed",
                    "strategy_used": search_results.get("strategy_used", "failed"),
                    "raw_results": search_results,
                    "user_message": "I attempted to search the web but couldn't retrieve current information. I'll provide what I can from my training knowledge."
                }

        except Exception as e:
            logger.error(f"Web search error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "strategy_used": "error",
                "user_message": f"I encountered an error while trying to search the web: {str(e)}"
            }

    def _format_web_search_context(self, search_results: Dict[str, Any], original_query: str) -> str:
        """Format web search results for LLM context with clear source attribution"""
        data = search_results.get("data", {})
        strategy = search_results.get("strategy_used", "unknown")

        context_parts = [f"=== WEB SEARCH RESULTS FOR: {original_query} ==="]
        context_parts.append(f"Search Strategy: {strategy}")
        context_parts.append(f"Search Timestamp: {datetime.now().isoformat()}")
        context_parts.append("")

        data_type = data.get("type", "unknown")

        if data_type == "website_content":
            context_parts.append(f"WEBSITE CONTENT:")
            context_parts.append(f"URL: {data.get('url', 'N/A')}")
            context_parts.append(f"Title: {data.get('title', 'N/A')}")
            if data.get('description'):
                context_parts.append(f"Description: {data.get('description')}")
            if data.get('content'):
                context_parts.append(f"Content: {data.get('content')}")

        elif data_type == "weather":
            context_parts.append(f"WEATHER INFORMATION:")
            context_parts.append(f"Location: {data.get('location', 'N/A')}")
            context_parts.append(f"Temperature: {data.get('temperature_fahrenheit', 'N/A')}°F ({data.get('temperature_celsius', 'N/A')}°C)")
            context_parts.append(f"Conditions: {data.get('description', 'N/A')}")
            context_parts.append(f"Humidity: {data.get('humidity', 'N/A')}%")

        elif data_type == "general_search":
            context_parts.append(f"SEARCH RESULTS:")
            context_parts.append(f"Source: {data.get('source', 'N/A')}")
            if data.get('url'):
                context_parts.append(f"URL: {data.get('url')}")
            context_parts.append(f"Information: {data.get('snippet', 'N/A')}")

        else:
            context_parts.append(f"SEARCH DATA:")
            context_parts.append(f"Information: {data.get('snippet', str(data))}")

        context_parts.append("")
        context_parts.append("=== END WEB SEARCH RESULTS ===")

        return "\n".join(context_parts)

    def process_chat_message(self, session_id: str, message: str,
                           use_rag: bool = True, debug_mode: bool = False, simple_mode: bool = False,
                           chat_mode: str = None) -> Dict[str, Any]:
        """Process a chat message with enhanced features"""
        start_time = datetime.now()

        try:
            print(f"DEBUG TRACE: process_chat_message called with message: '{message[:50]}...'")
            logger.debug(f"DEBUG TRACE: process_chat_message called with message: '{message[:50]}...'")
            logger.debug(f"FLOW DEBUG: About to enhance message and check intent...")

            import sys, os
            sys.path.append(os.path.dirname(os.path.dirname(__file__)))
            from backend.utils.prompt_utils import enhance_message_with_time
            enhanced_message = enhance_message_with_time(message)

            logger.debug(f"FLOW DEBUG: Message enhanced, checking chat_mode logic...")

            logger.info(f"Enhanced chat: process_chat_message called with session_id={session_id}, message='{message[:50]}...', use_rag={use_rag}, simple_mode={simple_mode}")

            # Respect user preferences for simple mode and RAG
            # Allow simple mode for basic conversations without RAG overhead

            # Auto-detect simple messages to prevent unnecessary RAG
            if not simple_mode and self._is_simple_message(message):
                simple_mode = True
                logger.info(f"Enhanced chat: Auto-detected simple message, enabling simple mode")

            if chat_mode:
                logger.debug(f"FLOW DEBUG: Chat mode provided: {chat_mode}")
                logger.info(f"Enhanced chat: Explicit chat_mode '{chat_mode}' provided - using enhanced mode with universal RAG")
            else:
                logger.debug(f"FLOW DEBUG: No chat mode, proceeding to intent detection...")
                logger.info(f"Enhanced chat: No specific mode - using enhanced mode with universal RAG")

            # Use rule-based intent detection (leverages existing Rules System)
            logger.debug(f"FLOW DEBUG: About to call rule-based intent detection...")
            logger.info(f"Enhanced chat: About to call rule-based intent detection...")
            try:
                detected_intent = self._detect_intent_with_rules(enhanced_message)
                logger.info(f"Enhanced chat: Rule-based detected intent: {detected_intent}")
            except Exception as intent_error:
                logger.error(f"Enhanced chat: Intent detection failed: {intent_error}")
                import traceback
                logger.error(f"Enhanced chat: Intent detection traceback: {traceback.format_exc()}")
                detected_intent = "general_chat"  # Safe fallback
                logger.info(f"Enhanced chat: Using fallback intent: {detected_intent}")

            # Route to appropriate handler based on detected intent
            if detected_intent == "explicit_file_generation":
                return self._handle_file_generation_request(session_id, enhanced_message)
            elif detected_intent == "file_analysis":
                return self._handle_file_analysis_request(session_id, enhanced_message)
            elif detected_intent == "file_improvement":
                return self._handle_file_improvement_request(session_id, enhanced_message)
            elif detected_intent == "bulk_csv_generation":
                # Route bulk CSV to file generation handler with context
                return self._handle_file_generation_request(session_id, enhanced_message)
            elif detected_intent == "website_analysis":
                return self._handle_website_analysis_request(session_id, enhanced_message)
            elif detected_intent == "file_generation":
                return self._handle_file_generation_request(session_id, enhanced_message)
            # For general_chat and other intents, proceed with regular chat

            # Regular chat processing (includes uploaded file discussion)
            logger.info(f"Enhanced chat: Proceeding with regular chat processing...")
            logger.info(f"DEBUG: About to call _process_regular_chat with use_rag={use_rag}, simple_mode={simple_mode}")
            return self._process_regular_chat(session_id, message, use_rag, debug_mode, simple_mode, start_time, chat_mode)

        except Exception as e:
            logger.error(f"Enhanced chat: process_chat_message exception: {str(e)}")
            logger.error(f"Enhanced chat: Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Enhanced chat: process_chat_message traceback:\n{traceback.format_exc()}")
            error_details = f"Error: {type(e).__name__}: {str(e)}"
            traceback_details = traceback.format_exc()
            return {
                "success": False,
                "error": str(e),
                "response": f"Sorry, I encountered an error: {error_details}",
                "error_type": type(e).__name__,
                "error_traceback": traceback_details[:500] if traceback_details else ""
            }

    # ============================================================================
    # DEPRECATED: The following _is_*_request methods have been replaced by
    # detect_intent_llm() which uses LLM-based structured classification.
    # These methods can be safely removed in a future cleanup.
    # ============================================================================

    def _is_file_analysis_request(self, message: str) -> bool:
        """DEPRECATED: Use detect_intent_llm() instead. Check if the message is requesting file analysis"""
        analysis_keywords = [
            'analyze', 'analysis', 'review', 'examine', 'inspect', 'check',
            'what is', 'what does', 'explain', 'describe', 'tell me about'
        ]
        file_keywords = [
            'file', 'code', 'script', 'document', 'uploaded', 'python', 'javascript',
            'json', 'csv', 'pdf', 'html', 'css', 'sql'
        ]

        message_lower = message.lower()
        has_analysis = any(keyword in message_lower for keyword in analysis_keywords)
        has_file = any(keyword in message_lower for keyword in file_keywords)

        logger.debug(f"[FILE_ANALYSIS_DEBUG] File analysis detection: message='{message}', has_analysis={has_analysis}, has_file={has_file}, result={has_analysis and has_file}")

        return has_analysis and has_file

    def _is_file_improvement_request(self, message: str) -> bool:
        """Check if the message is requesting file improvements"""
        improvement_keywords = [
            'improve', 'optimize', 'enhance', 'fix', 'update', 'modify',
            'refactor', 'rewrite', 'make better',
            'suggest improvements', 'recommend changes'
        ]
        file_keywords = [
            'file', 'code', 'script', 'document', 'uploaded', 'python', 'javascript',
            'json', 'csv', 'pdf', 'html', 'css', 'sql'
        ]

        message_lower = message.lower()
        has_improvement = any(keyword in message_lower for keyword in improvement_keywords)
        has_file = any(keyword in message_lower for keyword in file_keywords)

        return has_improvement and has_file

    def _is_file_generation_request(self, message: str) -> bool:
        """Check if the message is requesting file generation"""
        generation_keywords = [
            'generate', 'create', 'make', 'build', 'produce', 'export',
            'download', 'save', 'output', 'compile', 'list', 'analyze'
        ]
        file_keywords = [
            'file', 'csv', 'json', 'txt', 'report', 'document', 'spreadsheet',
            'table', 'data', 'output', 'export', 'script', 'python', 'javascript'
        ]

        message_lower = message.lower()
        has_generation = any(keyword in message_lower for keyword in generation_keywords)
        has_file = any(keyword in message_lower for keyword in file_keywords)

        logger.info(f"File generation detection: message='{message}', has_generation={has_generation}, has_file={has_file}")

        return has_generation and has_file

    def _is_bulk_csv_generation_request(self, message: str) -> bool:
        """Check if the message is requesting bulk CSV generation"""
        bulk_keywords = [
            'bulk', 'batch', 'multiple', 'many', 'lots of', 'hundreds', 'thousands',
            'mass', 'large scale', 'scale', 'volume', 'quantity', 'series'
        ]
        csv_keywords = [
            'csv', 'spreadsheet', 'table', 'data', 'excel', 'sheet'
        ]
        generation_keywords = [
            'generate', 'create', 'make', 'build', 'produce', 'export',
            'download', 'save', 'output', 'compile', 'list'
        ]

        message_lower = message.lower()
        has_bulk = any(keyword in message_lower for keyword in bulk_keywords)
        has_csv = any(keyword in message_lower for keyword in csv_keywords)
        has_generation = any(keyword in message_lower for keyword in generation_keywords)

        # Check for quantity indicators - but only for larger quantities
        import re
        quantity_patterns = [
            r'\b(?:10|1[1-9]|[2-9]\d|\d{3,})\s*(?:csv|files|pages|items|entries)\b',  # 10 or more
            r'\b(?:generate|create|make)\s+(?:10|1[1-9]|[2-9]\d|\d{3,})\b',  # 10 or more
            r'\b(?:10|1[1-9]|[2-9]\d|\d{3,})\s*(?:bulk|batch|mass)\b'  # 10 or more
        ]
        has_large_quantity = any(re.search(pattern, message_lower) for pattern in quantity_patterns)

        # Check for small quantities that should NOT trigger bulk generation
        small_quantity_patterns = [
            r'\b(?:1|2|3|4|5|6|7|8|9)\s*(?:csv|files|pages|items|entries)\b',  # 1-9
            r'\b(?:generate|create|make)\s+(?:1|2|3|4|5|6|7|8|9)\b',  # 1-9
            r'\b(?:1|2|3|4|5|6|7|8|9)\s*(?:rows?|items?|entries?)\b'  # 1-9 rows/items
        ]
        has_small_quantity = any(re.search(pattern, message_lower) for pattern in small_quantity_patterns)

        logger.info(f"Bulk CSV generation detection: message='{message}', has_bulk={has_bulk}, has_csv={has_csv}, has_generation={has_generation}, has_large_quantity={has_large_quantity}, has_small_quantity={has_small_quantity}")

        # Only trigger bulk generation if:
        # 1. Has bulk keywords AND csv AND generation, OR
        # 2. Has csv AND generation AND large quantity (10+), AND NOT small quantity
        return ((has_bulk and has_csv and has_generation) or
                (has_csv and has_generation and has_large_quantity and not has_small_quantity))

    def _is_website_analysis_request(self, message: str) -> bool:
        """Check if the message is requesting website analysis"""
        website_keywords = [
            'website', 'site', 'web page', 'webpage', 'url', 'www.', 'http', 'https'
        ]
        analysis_keywords = [
            'analyze', 'analysis', 'check', 'examine', 'review', 'what is', 'what does',
            'tell me about', 'describe', 'explain', 'look at', 'see', 'find'
        ]

        message_lower = message.lower()
        has_website = any(keyword in message_lower for keyword in website_keywords)
        has_analysis = any(keyword in message_lower for keyword in analysis_keywords)

        # Also check for URL patterns
        import re
        url_pattern = r'(?:https?://|www\.)[^\s]+'
        has_url = bool(re.search(url_pattern, message))

        logger.info(f"Website analysis detection: message='{message}', has_website={has_website}, has_analysis={has_analysis}, has_url={has_url}")

        return (has_website and has_analysis) or has_url

    def _handle_file_analysis_request(self, session_id: str, message: str) -> Dict[str, Any]:
        """Handle file analysis requests"""
        start_time = datetime.now()
        try:
            # First try session-specific documents
            documents = self._get_session_documents(session_id)

            # If no session documents found, try enhanced file retrieval for any uploaded code files
            if not documents:
                logger.info(f"No session documents found, trying enhanced file retrieval for query: '{message}'")
                file_contexts = self._retrieve_uploaded_files_context(message, session_id)

                # Convert file contexts to document format
                documents = []
                for ctx in file_contexts:
                    documents.append({
                        'id': ctx['metadata'].get('file_id'),
                        'filename': ctx['metadata'].get('source_document'),
                        'type': ctx['metadata'].get('source_document', '').split('.')[-1] if '.' in ctx['metadata'].get('source_document', '') else 'unknown',
                        'index_status': 'STORED',  # Files from enhanced retrieval are available
                        'uploaded_at': ctx['metadata'].get('uploaded_at'),
                        'tags': [],
                        'content': ctx['content']
                    })
                logger.info(f"Enhanced file retrieval found {len(documents)} documents")

            if not documents:
                return {
                    "success": True,
                    "response": "I don't see any uploaded files to analyze. Please upload some files first and then ask me to analyze them.",
                    "model_used": self._get_active_model(),
                    "response_time": (datetime.now() - start_time).total_seconds(),
                    "session_id": session_id,
                    "file_analysis": {
                        "documents_found": 0,
                        "analysis_type": "none"
                    }
                }

            # ENHANCED: More conversational response that asks user intent
            analysis_parts = []
            analysis_parts.append(f"I found {len(documents)} document(s) that might be relevant:")

            for doc in documents:
                status_emoji = "" if doc['index_status'] == 'INDEXED' else "⏳" if doc['index_status'] == 'INDEXING' else ""
                analysis_parts.append(f"{status_emoji} **{doc['filename']}** ({doc['type']})")

            analysis_parts.append("\nWhat would you like me to do with these files? I can:")
            analysis_parts.append("**Analyze** the code structure and functionality")
            analysis_parts.append("**Review** for issues or improvements")
            analysis_parts.append("**Explain** what the code does")
            analysis_parts.append("**Optimize** or suggest enhancements")
            analysis_parts.append("**Generate** modified versions")
            analysis_parts.append("\nJust let me know what you'd like me to focus on!")

            response_text = "\n".join(analysis_parts)

            return {
                "success": True,
                "response": response_text,
                "model_used": self._get_active_model(),
                "response_time": (datetime.now() - start_time).total_seconds(),
                "session_id": session_id,
                "file_analysis": {
                    "documents_found": len(documents),
                    "analysis_type": "detailed",
                    "documents": documents
                }
            }

        except Exception as e:
            logger.error(f"File analysis error: {e}")
            return {
                "success": False,
                "error": str(e),
                "response": f"Sorry, I encountered an error while analyzing files: {str(e)}",
                "response_time": (datetime.now() - start_time).total_seconds()
            }

    def _handle_website_analysis_request(self, session_id: str, message: str) -> Dict[str, Any]:
        """Handle website analysis requests using web search API"""
        start_time = datetime.now()
        try:
            # Import web search functionality
            try:
                from backend.api.web_search_api import enhanced_web_search
            except ImportError:
                return {
                    "success": False,
                    "error": "Web search functionality not available",
                    "response": "Sorry, web search functionality is not available at the moment.",
                    "response_time": (datetime.now() - start_time).total_seconds()
                }

            # Extract URL from message
            import re
            url_pattern = r'(?:https?://|www\.)[^\s]+'
            urls = re.findall(url_pattern, message)

            # If no URL found, try to extract domain names
            if not urls:
                # Look for domain patterns like "example.com" or "datacenterknowledge.com"
                domain_pattern = r'\b[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9]\.[a-zA-Z]{2,}\b'
                domains = re.findall(domain_pattern, message)
                if domains:
                    # Take the first domain found
                    urls = [domains[0]]

            if not urls:
                return {
                    "success": False,
                    "error": "No URL found in message",
                    "response": "I couldn't find a website URL in your message. Please include a URL like 'www.example.com' or 'https://example.com'",
                    "response_time": (datetime.now() - start_time).total_seconds()
                }

            url = urls[0]
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            logger.info(f"Analyzing website: {url}")

            # Use web search API to get website content
            search_result = enhanced_web_search(url)

            if not search_result.get("success"):
                return {
                    "success": False,
                    "error": "Website analysis failed",
                    "response": f"Sorry, I couldn't analyze the website {url}. The website might be unavailable or blocked.",
                    "response_time": (datetime.now() - start_time).total_seconds()
                }

            website_data = search_result.get("data", {})

            # Generate analysis response
            analysis_parts = []
            analysis_parts.append(f"## Website Analysis: {url}")
            analysis_parts.append("")

            if website_data.get("title"):
                analysis_parts.append(f"**Title:** {website_data['title']}")
                analysis_parts.append("")

            if website_data.get("description"):
                analysis_parts.append(f"**Description:** {website_data['description']}")
                analysis_parts.append("")

            if website_data.get("content"):
                content = website_data['content']
                # Truncate content if too long
                if len(content) > 2000:
                    content = content[:2000] + "..."
                analysis_parts.append(f"**Main Content:**")
                analysis_parts.append(content)
                analysis_parts.append("")

            analysis_parts.append(f"**Analysis Method:** {search_result.get('strategy_used', 'unknown')}")

            response_text = "\n".join(analysis_parts)

            return {
                "success": True,
                "response": response_text,
                "model_used": self._get_active_model(),
                "response_time": (datetime.now() - start_time).total_seconds(),
                "session_id": session_id,
                "website_analysis": {
                    "url": url,
                    "strategy_used": search_result.get("strategy_used"),
                    "title": website_data.get("title"),
                    "description": website_data.get("description"),
                    "content_length": len(website_data.get("content", ""))
                }
            }

        except Exception as e:
            logger.error(f"Website analysis error: {e}")
            return {
                "success": False,
                "error": str(e),
                "response": f"Sorry, I encountered an error while analyzing the website: {str(e)}",
                "response_time": (datetime.now() - start_time).total_seconds()
            }

    def _handle_file_generation_request(self, session_id: str, message: str) -> Dict[str, Any]:
        """Handle explicit file generation requests - trust the LLM more, less hard-coding"""
        start_time = datetime.now()
        try:
            message_lower = message.lower()

            # Enhanced file type detection - support many more formats
            file_type = "txt"
            if any(ext in message_lower for ext in ['.js', 'javascript']):
                file_type = "js"
            elif any(ext in message_lower for ext in ['.jsx', 'react']):
                file_type = "jsx"
            elif any(ext in message_lower for ext in ['.ts', 'typescript']):
                file_type = "ts"
            elif any(ext in message_lower for ext in ['.tsx']):
                file_type = "tsx"
            elif any(ext in message_lower for ext in ['.py', 'python']):
                file_type = "py"
            elif any(ext in message_lower for ext in ['.php']):
                file_type = "php"
            elif any(ext in message_lower for ext in ['.css']):
                file_type = "css"
            elif any(ext in message_lower for ext in ['.html', 'html5']):
                file_type = "html"
            elif any(ext in message_lower for ext in ['.json']):
                file_type = "json"
            elif any(ext in message_lower for ext in ['.csv']):
                file_type = "csv"
            elif any(ext in message_lower for ext in ['.sql', 'mysql', 'postgres']):
                file_type = "sql"
            elif any(ext in message_lower for ext in ['.xml']):
                file_type = "xml"
            elif any(ext in message_lower for ext in ['.yaml', '.yml']):
                file_type = "yaml"
            elif any(ext in message_lower for ext in ['.md', 'markdown']):
                file_type = "md"
            elif any(ext in message_lower for ext in ['.sh', 'bash', 'shell']):
                file_type = "sh"
            elif any(ext in message_lower for ext in ['.dockerfile', 'docker']):
                file_type = "dockerfile"
            elif any(ext in message_lower for ext in ['.java']):
                file_type = "java"
            elif any(ext in message_lower for ext in ['.c', '.cpp', '.h']):
                file_type = "c" if '.c' in message_lower else "cpp"
            elif any(ext in message_lower for ext in ['.go', 'golang']):
                file_type = "go"
            elif any(ext in message_lower for ext in ['.rs', 'rust']):
                file_type = "rs"
            elif any(ext in message_lower for ext in ['.rb', 'ruby']):
                file_type = "rb"
            elif any(ext in message_lower for ext in ['.swift']):
                file_type = "swift"
            elif any(ext in message_lower for ext in ['.kt', 'kotlin']):
                file_type = "kt"

            # Generate filename
            import re
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename_match = re.search(r'(?:called|named|as)\s+["\']?([a-zA-Z0-9_\-\.]+)["\']?', message_lower)
            if filename_match:
                base_name = filename_match.group(1).strip()
                if not base_name.endswith(f'.{file_type}'):
                    filename = f"{base_name}.{file_type}"
                else:
                    filename = base_name
            else:
                filename = f"generated_file_{timestamp}.{file_type}"

            # Simple, clean prompt - trust the LLM
            llm = current_app.config.get('LLAMA_INDEX_LLM')
            if not llm:
                return {
                    "success": False,
                    "error": "LLM not available",
                    "response": "Sorry, the language model is not available for file generation.",
                    "response_time": (datetime.now() - start_time).total_seconds()
                }

            # Clean generation prompt - no hard-coded instructions
            generated_content = llm_service.run_llm_chat_prompt(
                message,  # Use the user's message directly
                llm_instance=llm,
                messages=[
                    llm_service.ChatMessage(
                        role=llm_service.MessageRole.SYSTEM,
                        content=f"Generate clean {file_type.upper()} code. Output only the code, no explanations or markdown formatting."
                    ),
                    llm_service.ChatMessage(
                        role=llm_service.MessageRole.USER,
                        content=message
                    )
                ]
            )

            if not generated_content or not generated_content.strip():
                return {
                    "success": False,
                    "error": "Empty content generated",
                    "response": "Sorry, I couldn't generate content for the file. Please try a more specific request.",
                    "response_time": (datetime.now() - start_time).total_seconds()
                }

            # Clean up the content - remove any commentary or markdown
            clean_content = self._clean_generated_content(generated_content, file_type)

            # Save the file
            output_dir = current_app.config.get("OUTPUT_DIR", "data/outputs")
            file_path = os.path.join(output_dir, filename)
            os.makedirs(output_dir, exist_ok=True)

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(clean_content)

            file_size = os.path.getsize(file_path)

            # Simple response
            return {
                "success": True,
                "response": f"**File Generated**: `{filename}`\n\n**Location**: `data/outputs/`\n**Size**: {file_size} bytes\n**Type**: {file_type.upper()}\n\nYour file has been generated and saved. You can find it in the outputs folder.",
                "model_used": self._get_active_model(),
                "response_time": (datetime.now() - start_time).total_seconds(),
                "session_id": session_id,
                "file_info": {
                    "filename": filename,
                    "file_path": file_path,
                    "file_size": file_size,
                    "file_type": file_type
                }
            }

        except Exception as e:
            logger.error(f"Error in file generation: {e}")
            return {
                "success": False,
                "error": str(e),
                "response": f"Sorry, I encountered an error generating the file: {str(e)}",
                "response_time": (datetime.now() - start_time).total_seconds()
            }

    def _clean_generated_content(self, content: str, file_type: str) -> str:
        """Clean generated content to remove commentary and formatting"""
        lines = content.split('\n')
        clean_lines = []

        for line in lines:
            line = line.strip()
            # Skip obvious commentary lines
            if line.startswith('Sure,') or line.startswith('Here\'s') or line.startswith('Based on'):
                continue
            if line.startswith('```') or line.endswith('```'):
                continue
            if line.startswith('//') and any(word in line.lower() for word in ['example', 'based on', 'here\'s']):
                continue

            clean_lines.append(line)

        # Join back and clean up
        result = '\n'.join(clean_lines).strip()

        # Remove leading/trailing empty lines
        while result.startswith('\n'):
            result = result[1:]
        while result.endswith('\n\n'):
            result = result[:-1]

        return result

    def _handle_file_improvement_request(self, session_id: str, message: str) -> Dict[str, Any]:
        """Handle file improvement requests"""
        start_time = datetime.now()
        try:
            # Get uploaded documents for this session
            documents = self._get_session_documents(session_id)

            if not documents:
                return {
                    "success": True,
                    "response": "I don't see any uploaded files to improve. Please upload some files first and then ask me to improve them.",
                    "model_used": self._get_active_model(),
                    "response_time": (datetime.now() - start_time).total_seconds(),
                    "session_id": session_id,
                    "file_improvement": {
                        "documents_found": 0,
                        "improvement_type": "none"
                    }
                }

            # Generate improvements for each document
            improvement_results = []
            for doc in documents:
                improvement = self._generate_document_improvement(doc, message)
                improvement_results.append(improvement)

            # Generate improvement response
            improvement_text = self._generate_improvement_response(improvement_results, message)

            return {
                "success": True,
                "response": improvement_text,
                "model_used": self._get_active_model(),
                "response_time": (datetime.now() - start_time).total_seconds(),
                "session_id": session_id,
                "file_improvement": {
                    "documents_found": len(documents),
                    "improvement_type": "comprehensive",
                    "results": improvement_results
                }
            }

        except Exception as e:
            logger.error(f"File improvement error: {e}")
            return {
                "success": False,
                "error": str(e),
                "response": f"Sorry, I encountered an error while improving files: {str(e)}",
                "response_time": (datetime.now() - start_time).total_seconds()
            }

    def _get_session_documents(self, session_id: str) -> List[Dict]:
        """Get documents associated with the current session"""
        try:
            # Import db within Flask app context
            from backend.models import db, Document as DBDocument

            # Query documents that have tags containing the session ID
            session_documents = db.session.query(DBDocument).filter(
                DBDocument.tags.contains(f"session_{session_id}")
            ).all()

            # Convert to list of dictionaries
            documents = []
            for doc in session_documents:
                documents.append({
                    'id': doc.id,
                    'filename': doc.filename,
                    'type': doc.type,
                    'index_status': doc.index_status,
                    'uploaded_at': doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                    'tags': doc.tags,
                    'path': doc.path
                })

            logger.info(f"Found {len(documents)} documents for session {session_id}")
            return documents

        except Exception as e:
            logger.error(f"Error getting session documents for {session_id}: {e}")
            return []

    def _analyze_document(self, document: Dict) -> Dict:
        """Analyze a single document"""
        try:
            # Read document content
            content = self._read_document_content(document)

            # Basic analysis based on file type
            analysis = {
                "filename": document["filename"],
                "file_type": document["file_type"],
                "size": len(content) if content else 0,
                "lines": len(content.split('\n')) if content else 0,
                "analysis": {}
            }

            # Type-specific analysis
            if document["file_type"] in ["py", "python"]:
                analysis["analysis"] = self._analyze_python_file(content)
            elif document["file_type"] in ["js", "javascript", "jsx", "ts", "tsx"]:
                analysis["analysis"] = self._analyze_javascript_file(content)
            elif document["file_type"] == "json":
                analysis["analysis"] = self._analyze_json_file(content)
            elif document["file_type"] == "csv":
                analysis["analysis"] = self._analyze_csv_file(content)
            elif document["file_type"] == "html":
                analysis["analysis"] = self._analyze_html_file(content)
            elif document["file_type"] == "css":
                analysis["analysis"] = self._analyze_css_file(content)
            else:
                analysis["analysis"] = self._analyze_generic_file(content)

            return analysis

        except Exception as e:
            logger.error(f"Error analyzing document {document['filename']}: {e}")
            return {
                "filename": document["filename"],
                "error": str(e)
            }

    def _read_document_content(self, document: Dict) -> str:
        """Read document content from file"""
        try:
            file_path = document["file_path"]
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
        except Exception as e:
            logger.error(f"Error reading document {document['filename']}: {e}")

        return ""

    def _analyze_python_file(self, content: str) -> Dict:
        """Analyze Python file content"""
        lines = content.split('\n')
        return {
            "language": "Python",
            "functions": len([line for line in lines if line.strip().startswith('def ')]),
            "classes": len([line for line in lines if line.strip().startswith('class ')]),
            "imports": len([line for line in lines if line.strip().startswith(('import ', 'from '))]),
            "comments": len([line for line in lines if line.strip().startswith('#')]),
            "complexity": "High" if len(lines) > 100 else "Medium" if len(lines) > 50 else "Low"
        }

    def _analyze_javascript_file(self, content: str) -> Dict:
        """Analyze JavaScript file content"""
        lines = content.split('\n')
        return {
            "language": "JavaScript",
            "functions": len([line for line in lines if 'function ' in line or '=>' in line]),
            "classes": len([line for line in lines if 'class ' in line]),
            "imports": len([line for line in lines if line.strip().startswith(('import ', 'const ', 'let ', 'var '))]),
            "comments": len([line for line in lines if line.strip().startswith('//') or line.strip().startswith('/*')]),
            "complexity": "High" if len(lines) > 100 else "Medium" if len(lines) > 50 else "Low"
        }

    def _analyze_json_file(self, content: str) -> Dict:
        """Analyze JSON file content"""
        try:
            import json
            data = json.loads(content)
            return {
                "language": "JSON",
                "is_valid": True,
                "structure": self._analyze_json_structure(data),
                "size": len(content)
            }
        except:
            return {
                "language": "JSON",
                "is_valid": False,
                "error": "Invalid JSON format"
            }

    def _analyze_json_structure(self, data, max_depth=3) -> Dict:
        """Analyze JSON structure recursively"""
        if isinstance(data, dict):
            return {
                "type": "object",
                "keys": list(data.keys()),
                "key_count": len(data),
                "nested": {k: self._analyze_json_structure(v, max_depth-1) for k, v in list(data.items())[:5]}
            }
        elif isinstance(data, list):
            return {
                "type": "array",
                "length": len(data),
                "sample": [self._analyze_json_structure(item, max_depth-1) for item in data[:3]]
            }
        else:
            return {
                "type": type(data).__name__,
                "value": str(data)[:100]
            }

    def _analyze_csv_file(self, content: str) -> Dict:
        """Analyze CSV file content"""
        lines = content.split('\n')
        if lines:
            headers = lines[0].split(',')
            return {
                "language": "CSV",
                "rows": len(lines) - 1,
                "columns": len(headers),
                "headers": headers,
                "has_data": len(lines) > 1
            }
        return {"language": "CSV", "error": "Empty or invalid CSV"}

    def _analyze_html_file(self, content: str) -> Dict:
        """Analyze HTML file content"""
        return {
            "language": "HTML",
            "tags": len([line for line in content.split('\n') if '<' in line and '>' in line]),
            "has_doctype": '<!DOCTYPE' in content,
            "has_title": '<title>' in content,
            "has_meta": '<meta' in content
        }

    def _analyze_css_file(self, content: str) -> Dict:
        """Analyze CSS file content"""
        return {
            "language": "CSS",
            "rules": len([line for line in content.split('\n') if '{' in line and '}' in line]),
            "selectors": len([line for line in content.split('\n') if '{' in line]),
            "has_media_queries": '@media' in content,
            "has_keyframes": '@keyframes' in content
        }

    def _analyze_generic_file(self, content: str) -> Dict:
        """Analyze generic file content"""
        lines = content.split('\n')
        return {
            "language": "Text",
            "lines": len(lines),
            "characters": len(content),
            "words": len(content.split()),
            "has_content": len(content.strip()) > 0
        }

    def _generate_analysis_response(self, analysis_results: List[Dict], user_message: str) -> str:
        """Generate a comprehensive analysis response"""
        if not analysis_results:
            return "No files found to analyze."

        response = f"**File Analysis Results**\n\n"

        for result in analysis_results:
            if "error" in result:
                response += f"**{result['filename']}**: Error - {result['error']}\n\n"
                continue

            analysis = result.get("analysis", {})
            response += f"**{result['filename']}** ({result['file_type'].upper()})\n"
            response += f"   Size: {result['size']} characters, {result['lines']} lines\n"

            if "language" in analysis:
                response += f"   Language: {analysis['language']}\n"

            if "functions" in analysis:
                response += f"   Functions: {analysis['functions']}\n"

            if "classes" in analysis:
                response += f"   Classes: {analysis['classes']}\n"

            if "complexity" in analysis:
                response += f"   Complexity: {analysis['complexity']}\n"

            if "is_valid" in analysis:
                response += f"   Valid: {'Yes' if analysis['is_valid'] else 'No'}\n"

            response += "\n"

        response += "**Suggestions**:\n"
        response += "Ask me to 'improve' any of these files for specific enhancements\n"
        response += "Request 'optimization' for performance improvements\n"
        response += "Ask for 'security review' to identify potential issues\n"

        return response

    def _generate_document_improvement(self, document: Dict, user_message: str) -> Dict:
        """Generate improvements for a document"""
        try:
            content = self._read_document_content(document)
            if not content:
                return {"filename": document["filename"], "error": "Could not read file content"}

            # Create improvement prompt
            improvement_prompt = f"""
            Analyze and improve the following {document['file_type']} file:

            FILENAME: {document['filename']}

            CURRENT CONTENT:
            {content}

            USER REQUEST: {user_message}

            Please provide:
            1. Specific improvements and optimizations
            2. An improved version of the code/content
            3. Explanations of the changes made
            4. Best practices recommendations
            """

            # Use LLM to generate improvements
            llm = current_app.config.get('LLAMA_INDEX_LLM')
            if llm:
                improved_content = llm_service.run_llm_chat_prompt(
                    improvement_prompt,
                    llm_instance=llm,
                    messages=[
                        llm_service.ChatMessage(role=llm_service.MessageRole.SYSTEM,
                                               content="You are an expert code reviewer and optimizer. Provide detailed, actionable improvements."),
                        llm_service.ChatMessage(role=llm_service.MessageRole.USER,
                                               content=improvement_prompt)
                    ]
                )

                return {
                    "filename": document["filename"],
                    "file_type": document["file_type"],
                    "improvements": improved_content,
                    "has_improvements": True
                }

        except Exception as e:
            logger.error(f"Error generating improvements for {document['filename']}: {e}")

        return {"filename": document["filename"], "error": "Could not generate improvements"}

    def _generate_improvement_response(self, improvement_results: List[Dict], user_message: str) -> str:
        """Generate a comprehensive improvement response"""
        if not improvement_results:
            return "No files found to improve."

        response = f" **File Improvement Analysis**\n\n"

        for result in improvement_results:
            if "error" in result:
                response += f"**{result['filename']}**: Error - {result['error']}\n\n"
                continue

            response += f"**{result['filename']}** ({result['file_type'].upper()})\n\n"
            response += f"{result['improvements']}\n\n"
            response += "---\n\n"

        response += "**Next Steps**:\n"
        response += "**Say 'generate the improved files'** to create and save the enhanced versions\n"
        response += "**Ask 'implement those recommendations'** to apply the suggestions\n"
        response += "**Request 'create improved versions'** to get downloadable files\n"
        response += "Ask for specific optimizations or features\n"
        response += "Request 'security review' or 'performance analysis'\n\n"
        response += "**Pro Tip**: Just like in Cursor, you can upload → analyze → improve → generate in one workflow!\n"

        return response

    def _process_regular_chat(self, session_id: str, message: str, use_rag: bool, debug_mode: bool, simple_mode: bool, start_time: datetime = None, chat_mode: str = None) -> Dict[str, Any]:
        """Process a regular chat message with enhanced features including bulletproof web search integration"""

        if start_time is None:
            start_time = datetime.now()

        try:
            import sys, os
            sys.path.append(os.path.dirname(os.path.dirname(__file__)))
            from backend.utils.prompt_utils import enhance_message_with_time
            enhanced_message = enhance_message_with_time(message)

            logger.info(f"Enhanced chat: process_chat_message called with session_id={session_id}, message='{message[:50]}...', use_rag={use_rag}, simple_mode={simple_mode}")

            # Get or create session
            logger.info(f"Enhanced chat: Getting or creating session...")
            session = self._get_or_create_session(session_id)
            logger.info(f"Enhanced chat: Session obtained: {session.id}")

            # Save user message first for memory persistence
            user_message_id = self._save_message(session_id, 'user', message)

            # Get active model
            logger.info(f"Enhanced chat: Getting active model...")
            model_name = self._get_active_model()
            logger.info(f"Enhanced chat: Active model: {model_name}")

            logger.info(f"Enhanced chat: Getting model config...")
            model_config = self._get_model_config(model_name)
            logger.info(f"Enhanced chat: Model config obtained: {list(model_config.keys())}")

            # BULLETPROOF WEB SEARCH INTEGRATION
            web_search_result = None
            web_search_used = False
            web_search_context = ""  # Initialize web_search_context

            # Check if web search is needed (only for non-simple messages)
            if not simple_mode and self._should_use_web_search(enhanced_message):
                logger.info(f"Enhanced chat: Web search required for query")
                logger.info(f"DEBUG: Original message to search: '{message}'")
                logger.info(f"DEBUG: Enhanced message (not used for search): '{enhanced_message[:200]}...'")
                web_search_result = self._perform_web_search_safe(message)  # Use original message, not enhanced
                web_search_used = True

                if web_search_result.get("success"):
                    web_search_context = web_search_result.get("formatted_context", "")
                    logger.info(f"Enhanced chat: Web search successful, context length: {len(web_search_context)}")
                else:
                    # Web search failed - add transparent error message to context
                    error_message = web_search_result.get("user_message", "Web search failed")
                    web_search_context = f"=== WEB SEARCH STATUS ===\n{error_message}\n=== END WEB SEARCH STATUS ==="
                    logger.warning(f"Enhanced chat: Web search failed: {web_search_result.get('error', 'Unknown error')}")
            else:
                logger.info(f"Enhanced chat: Web search not needed for this query")

            # Retrieve relevant RAG context if enabled and not in simple mode
            rag_context = []
            logger.info(f"DEBUG: RAG check - use_rag={use_rag}, simple_mode={simple_mode}")
            if use_rag and not simple_mode:
                logger.info(f"Enhanced chat: Attempting RAG context retrieval...")
                try:
                    rag_context = self._retrieve_relevant_context(enhanced_message, session_id)
                    logger.info(f"Enhanced chat: RAG context retrieved: {len(rag_context)} chunks")
                except Exception as rag_error:
                    logger.warning(f"RAG context retrieval failed, falling back to simple mode: {rag_error}")
                    simple_mode = True
                    rag_context = []
            else:
                logger.info(f"DEBUG: Skipping RAG - use_rag={use_rag}, simple_mode={simple_mode}")

            # User message already saved earlier in function (line 2027)
            logger.info(f"Enhanced chat: User message already saved earlier")

            # Get conversation context
            conversation_context = []
            if self.context_manager:
                logger.info(f"Enhanced chat: Getting conversation context from context manager...")
                conversation_context = self.context_manager.get_context(
                    session_id,
                    max_tokens=model_config['max_context_tokens'] // 3  # Reserve space for response
                )
                logger.info(f"Enhanced chat: Conversation context obtained: {len(conversation_context) if isinstance(conversation_context, list) else 'not a list'}")
            else:
                logger.warning("Context manager not available, using empty context")

            # ENHANCED PROMPT BUILDING WITH WEB SEARCH + RAG INTEGRATION
            logger.info(f"Enhanced chat: Creating enhanced prompt...")
            enhanced_message_with_context = enhanced_message

            # Combine web search and RAG contexts
            all_context_parts = []

            # Add web search context first (highest priority for current information)
            if web_search_context:
                all_context_parts.append(web_search_context)
                logger.info(f"Enhanced chat: Added web search context to prompt")

            # Add RAG context second (for document/knowledge base information)
            if rag_context and not simple_mode:
                rag_context_text = "\n\n".join(
                    f"[RAG Source: {chunk['source']}] {chunk['content']}"
                    for chunk in rag_context[:3]  # Limit to top 3 chunks
                )
                if rag_context_text:
                    all_context_parts.append(f"=== KNOWLEDGE BASE CONTEXT ===\n{rag_context_text}\n=== END KNOWLEDGE BASE CONTEXT ===")
                    logger.info(f"Enhanced chat: Added RAG context to prompt")

            # Build final enhanced message with all contexts
            if all_context_parts:
                combined_context = "\n\n".join(all_context_parts)
                enhanced_message_with_context = f"{combined_context}\n\nUser Question: {enhanced_message}"
                logger.info(f"Enhanced chat: Enhanced message created with combined context (length: {len(combined_context)})")
            else:
                logger.info(f"Enhanced chat: Using original message (no additional context)")

            # Create or get chat engine with fallback for simple mode
            logger.info(f"Enhanced chat: Creating chat engine (simple_mode={simple_mode})...")
            try:
                if simple_mode:
                    # Use simple chat engine without RAG
                    logger.info(f"Enhanced chat: Creating simple chat engine...")
                    chat_engine = self._create_simple_chat_engine(session_id, model_name, web_search_context)
                    logger.info(f"Enhanced chat: Simple chat engine created successfully")
                else:
                    # Use enhanced chat engine with RAG
                    logger.info(f"Enhanced chat: Creating enhanced chat engine...")
                    chat_engine = self._create_chat_engine(session_id, model_name, web_search_context, chat_mode)
                    logger.info(f"Enhanced chat: Enhanced chat engine created successfully")
            except Exception as engine_error:
                logger.warning(f"Enhanced chat engine failed, falling back to simple mode: {engine_error}")
                simple_mode = True
                chat_engine = self._create_simple_chat_engine(session_id, model_name, web_search_context)

            # Generate response
            logger.info(f"Enhanced chat: Generating response with chat engine...")
            print(f"DEBUG TRACE: About to call chat_engine.stream_chat with message length: {len(enhanced_message_with_context)}")
            logger.debug(f"DEBUG TRACE: About to call chat_engine.stream_chat with message length: {len(enhanced_message_with_context)}")
            response_stream = chat_engine.stream_chat(enhanced_message_with_context)
            logger.info(f"Enhanced chat: Response stream obtained")
            print(f"DEBUG TRACE: Got response_stream from chat_engine")
            logger.debug(f"DEBUG TRACE: Got response_stream from chat_engine")

            # Collect response chunks
            logger.info(f"Enhanced chat: Collecting response chunks...")
            response_chunks = []
            for chunk in response_stream.response_gen:
                response_chunks.append(chunk)
            logger.info(f"Enhanced chat: Collected {len(response_chunks)} response chunks")

            # Combine response
            full_response = "".join(response_chunks)
            logger.info(f"Enhanced chat: Combined response length: {len(full_response)}")
            print(f"DEBUG TRACE: Full response content: '{full_response}'")
            logger.debug(f"DEBUG TRACE: Full response content: '{full_response}'")

            # CRITICAL FIX: Handle LlamaIndex "Empty Response" from context overflow
            if full_response.strip() == "Empty Response" or full_response.strip() == "":
                logger.warning("LlamaIndex returned 'Empty Response' - likely context overflow. Using direct LLM fallback.")
                try:
                    # Direct LLM call with minimal context for simple messages
                    llm = current_app.config.get('LLAMA_INDEX_LLM')
                    if llm:
                        # For simple greetings, use ultra-minimal prompt
                        msg_lower = message.lower().strip()
                        if len(message) < 50 and any(word in msg_lower for word in ['hello', 'hi', 'hey', 'how are you', 'good morning', 'good afternoon']):
                            simple_prompt = f"Respond naturally and briefly to this greeting: {message}"
                        else:
                            # For other messages, use the original message without complex context
                            simple_prompt = message

                        direct_response = llm.complete(simple_prompt)
                        full_response = direct_response.text.strip() if hasattr(direct_response, 'text') else str(direct_response).strip()

                        if full_response and full_response != "Empty Response":
                            logger.info(f"Direct LLM fallback successful, length: {len(full_response)}")
                            simple_mode = True
                            rag_context = []
                        else:
                            # If direct LLM also fails, use hardcoded responses for greetings
                            friendly_responses = {
                                'hello': "Hello! How can I help you today?",
                                'hi': "Hi there! What would you like to know?",
                                'hey': "Hey! How can I assist you?",
                                'how are you': "I'm doing great, thank you for asking! How can I help you?"
                            }

                            for greeting, response_text in friendly_responses.items():
                                if greeting in msg_lower:
                                    full_response = response_text
                                    break
                            else:
                                full_response = "I'm ready to help! Ask me questions, request file generation, or upload documents for analysis."

                            logger.info(f"Using hardcoded response for: {message}")
                    else:
                        full_response = "Hello! I'm ready to help you with questions, file generation, and document analysis."

                    simple_mode = True
                    rag_context = []

                except Exception as fallback_error:
                    logger.error(f"Direct LLM fallback failed: {fallback_error}")
                    # Final emergency responses
                    if any(word in message.lower() for word in ['hello', 'hi', 'hey']):
                        full_response = "Hello! How can I help you today?"
                    else:
                        full_response = "I'm ready to assist you. What would you like to do?"

            # Save assistant response
            logger.info(f"Enhanced chat: Saving assistant response...")
            self._save_message(session_id, 'assistant', full_response)
            logger.info(f"Enhanced chat: Assistant response saved")

            # Commit database changes
            logger.info(f"Enhanced chat: Committing database changes...")
            from backend.models import db
            db.session.commit()

            # Calculate response time
            response_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"Enhanced chat: Response time: {response_time:.2f} seconds")

            # Update statistics
            self.stats['avg_response_time'] = (
                (self.stats['avg_response_time'] * (self.stats['total_messages'] - 1) + response_time) /
                self.stats['total_messages']
            )

            # Prepare response with web search information
            logger.info(f"Enhanced chat: Preparing final response data...")
            response_data = {
                'response': full_response,
                'session_id': session_id,
                'user_message_id': user_message_id,  # Include user message ID for frontend tracking
                'model_used': model_name,
                'response_time': response_time,
                'context_stats': self.context_manager.get_context_stats(session_id) if self.context_manager else {},
                'rag_context': rag_context if debug_mode else None,
                'simple_mode_used': simple_mode,
                'web_search_used': web_search_used,
                'web_search_successful': web_search_result.get("success", False) if web_search_result else False,
                'web_search_strategy': web_search_result.get("strategy_used", "none") if web_search_result else "none",
                'token_usage': {
                    'estimated_input_tokens': self._estimate_tokens(enhanced_message_with_context),
                    'estimated_output_tokens': self._estimate_tokens(full_response)
                }
            }

            # Log conversation to offline conversation logger for detailed summaries
            try:
                if get_conversation_logger:
                    conv_logger = get_conversation_logger()

                    # Calculate token count from response data
                    total_tokens = response_data['token_usage']['estimated_input_tokens'] + response_data['token_usage']['estimated_output_tokens']

                    # Build context list for logging
                    context_used = []
                    if web_search_used:
                        context_used.append(f"web_search_{response_data['web_search_strategy']}")
                    if rag_context and len(rag_context) > 0:
                        context_used.append(f"rag_docs_{len(rag_context)}")
                    if simple_mode:
                        context_used.append("simple_mode")

                    # Build metadata for logging
                    metadata = {
                        'model_used': model_name,
                        'web_search_used': web_search_used,
                        'rag_enabled': use_rag,
                        'simple_mode': simple_mode,
                        'chat_mode': chat_mode
                    }

                    # Log the conversation exchange
                    conv_logger.log_conversation(
                        session_id=session_id,
                        user_message=message,  # Original user message
                        assistant_response=full_response,
                        context_used=context_used,
                        processing_time=response_time,
                        token_count=total_tokens,
                        conversation_type="enhanced",
                        metadata=metadata
                    )

                    logger.info(f"Enhanced chat: Conversation logged to offline system")
            except Exception as e:
                logger.warning(f"Enhanced chat: Failed to log conversation: {e}")

            return response_data

        except Exception as e:
            import traceback
            logger.error(f"Error processing chat message: {e}\n{traceback.format_exc()}")
            # db.session.rollback() # Removed as per edit hint
            raise

    def _is_improved_file_generation_request(self, message: str) -> bool:
        """Check if the message is requesting generation of improved files"""
        generation_keywords = [
            'generate', 'create', 'implement', 'apply', 'save', 'export', 'output',
            'produce', 'build', 'write', 'make', 'download'
        ]
        improvement_keywords = [
            'improved', 'better', 'optimized', 'enhanced', 'fixed', 'updated',
            'modified', 'refactored', 'recommendations', 'suggestions', 'changes',
            'improvements', 'revised', 'enhanced version'
        ]
        file_keywords = [
            'file', 'files', 'code', 'script', 'document', 'version'
        ]

        message_lower = message.lower()
        has_generation = any(keyword in message_lower for keyword in generation_keywords)
        has_improvement = any(keyword in message_lower for keyword in improvement_keywords)
        has_file = any(keyword in message_lower for keyword in file_keywords)

        # Also check for specific phrases that indicate implementing improvements
        improvement_phrases = [
            'implement the recommendations',
            'apply the suggestions',
            'generate the improved',
            'create the better',
            'save the enhanced',
            'output the optimized',
            'implement those changes',
            'apply those improvements',
            'generate improved version',
            'create improved files'
        ]

        has_improvement_phrase = any(phrase in message_lower for phrase in improvement_phrases)

        result = (has_generation and has_improvement and has_file) or has_improvement_phrase

        logger.info(f"Improved file generation detection: message='{message}', has_generation={has_generation}, has_improvement={has_improvement}, has_file={has_file}, has_phrase={has_improvement_phrase}, result={result}")

        return result

    def _handle_improved_file_generation_request(self, session_id: str, message: str) -> Dict[str, Any]:
        """Handle requests to generate and save improved files"""
        start_time = datetime.now()
        try:
            # Get uploaded documents for this session
            documents = self._get_session_documents(session_id)

            if not documents:
                return {
                    "success": True,
                    "response": "I don't see any uploaded files to improve and generate. Please upload some files first, ask me to analyze them, then ask me to generate the improved versions.",
                    "model_used": self._get_active_model(),
                    "response_time": (datetime.now() - start_time).total_seconds(),
                    "session_id": session_id,
                    "file_generation": {
                        "documents_found": 0,
                        "files_generated": 0
                    }
                }

            # Generate and save improved files
            generated_files = []

            for doc in documents:
                try:
                    improved_file = self._generate_and_save_improved_file(doc, message)
                    if improved_file["success"]:
                        generated_files.append(improved_file)
                except Exception as e:
                    logger.error(f"Error generating improved file for {doc['filename']}: {e}")
                    generated_files.append({
                        "success": False,
                        "filename": doc["filename"],
                        "error": str(e)
                    })

            # Generate response
            response_text = self._generate_file_generation_response(generated_files, message)

            return {
                "success": True,
                "response": response_text,
                "model_used": self._get_active_model(),
                "response_time": (datetime.now() - start_time).total_seconds(),
                "session_id": session_id,
                "file_generation": {
                    "documents_found": len(documents),
                    "files_generated": len([f for f in generated_files if f["success"]]),
                    "generated_files": generated_files
                }
            }

        except Exception as e:
            logger.error(f"Error in improved file generation: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "response": f"Sorry, I encountered an error while generating improved files: {str(e)}",
                "response_time": (datetime.now() - start_time).total_seconds()
            }

    def _generate_and_save_improved_file(self, document: Dict, user_message: str) -> Dict:
        """Generate an improved version of a file and save it to outputs"""
        try:
            content = self._read_document_content(document)
            if not content:
                return {
                    "success": False,
                    "filename": document["filename"],
                    "error": "Could not read original file content"
                }

            # Create detailed improvement prompt for code generation
            improvement_prompt = f"""
            Generate an improved version of this {document['file_type']} file based on best practices and the user's request.

            ORIGINAL FILENAME: {document['filename']}
            USER REQUEST: {user_message}

            ORIGINAL CONTENT:
            {content}

            IMPORTANT INSTRUCTIONS:
            1. Provide ONLY the improved code/content, no explanations or comments about the changes
            2. Maintain the same functionality while implementing improvements
            3. Follow best practices for {document['file_type']} files
            4. Apply code optimization, better structure, improved readability
            5. Fix any potential issues or inefficiencies
            6. Do NOT include markdown code blocks or language identifiers
            7. Return the complete improved file content that can be saved directly

            Generate the improved {document['file_type']} file content now:
            """

            # Use LLM to generate improved content
            llm = current_app.config.get('LLAMA_INDEX_LLM')
            if not llm:
                return {
                    "success": False,
                    "filename": document["filename"],
                    "error": "LLM not available for file generation"
                }

            improved_content = llm_service.run_llm_chat_prompt(
                improvement_prompt,
                llm_instance=llm,
                messages=[
                    llm_service.ChatMessage(role=llm_service.MessageRole.SYSTEM,
                                           content=f"You are an expert {document['file_type']} developer. Generate clean, improved code without any markdown formatting or explanations."),
                    llm_service.ChatMessage(role=llm_service.MessageRole.USER,
                                           content=improvement_prompt)
                ]
            )

            if not improved_content or not improved_content.strip():
                return {
                    "success": False,
                    "filename": document["filename"],
                    "error": "Generated content was empty"
                }

            # Clean up the content (remove markdown formatting if present)
            cleaned_content = self._clean_generated_content(improved_content, document['file_type'])

            # Generate output filename
            import os
            from datetime import datetime

            output_dir = current_app.config.get("OUTPUT_DIR", "data/outputs")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Create improved filename
            name, ext = os.path.splitext(document["filename"])
            if not ext:
                ext = f".{document['file_type']}"

            improved_filename = f"{name}_improved_{timestamp}{ext}"
            output_path = os.path.join(output_dir, improved_filename)

            # Ensure output directory exists
            os.makedirs(output_dir, exist_ok=True)

            # Save the improved file
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(cleaned_content)

            file_size = os.path.getsize(output_path)

            return {
                "success": True,
                "filename": document["filename"],
                "improved_filename": improved_filename,
                "output_path": output_path,
                "file_size": file_size,
                "file_type": document["file_type"]
            }

        except Exception as e:
            logger.error(f"Error generating improved file for {document['filename']}: {e}")
            return {
                "success": False,
                "filename": document["filename"],
                "error": str(e)
            }

    def _generate_file_generation_response(self, generated_files: List[Dict], user_message: str) -> str:
        """Generate response text for file generation results"""
        if not generated_files:
            return "No files were processed for improvement generation."

        successful_files = [f for f in generated_files if f["success"]]
        failed_files = [f for f in generated_files if not f["success"]]

        response = f"**Improved Files Generated**\n\n"

        if successful_files:
            response += f"**Successfully Generated ({len(successful_files)} files):**\n\n"

            for file_data in successful_files:
                size_kb = file_data["file_size"] / 1024
                response += f"**{file_data['improved_filename']}**\n"
                response += f"   Original: {file_data['filename']}\n"
                response += f"   Type: {file_data['file_type'].upper()}\n"
                response += f"   Size: {size_kb:.1f} KB\n"
                response += f"   Location: `data/outputs/`\n\n"

            response += "**What was improved:**\n"
            response += "Code structure and organization\n"
            response += "Best practices implementation\n"
            response += "Performance optimizations\n"
            response += "Error handling and robustness\n"
            response += "Documentation and readability\n\n"

        if failed_files:
            response += f"**Failed to Generate ({len(failed_files)} files):**\n\n"
            for file_data in failed_files:
                response += f"**{file_data['filename']}**: {file_data['error']}\n"
            response += "\n"

        response += "**Next Steps:**\n"
        response += "Check the `data/outputs/` folder for your improved files\n"
        response += "Review the changes and test the improved code\n"
        response += "Upload more files for further improvements\n"
        response += "Ask for specific optimizations or features\n"

        return response

    def _is_explicit_file_generation_request(self, message: str) -> bool:
        """Check if the message explicitly requests file generation (very conservative)"""
        message_lower = message.lower().strip()

        # Only trigger on very explicit generation requests
        explicit_patterns = [
            'generate file', 'create file', 'save as', 'export as', 'download as',
            'create a file called', 'generate a file called', 'save this as',
            'output to file', 'write to file', 'create .', 'generate .',
            'save code as', 'export code as'
        ]

        # Must have explicit generation language AND file context
        has_explicit_request = any(pattern in message_lower for pattern in explicit_patterns)

        # Additional check for file extensions to confirm intent - comprehensive list
        file_extensions = ['.js', '.jsx', '.ts', '.tsx', '.py', '.php', '.css', '.html', '.json', '.csv', '.txt', '.sql', '.xml', '.yaml', '.yml', '.md', '.sh', '.dockerfile', '.java', '.c', '.cpp', '.h', '.go', '.rs', '.rb', '.swift', '.kt']
        has_file_extension = any(ext in message_lower for ext in file_extensions)

        # OR check for command-like patterns
        command_patterns = ['create:', 'generate:', 'save:', 'export:']
        has_command_pattern = any(pattern in message_lower for pattern in command_patterns)

        result = has_explicit_request or (has_file_extension and has_command_pattern)

        logger.info(f"Explicit file generation detection: '{message[:50]}...' -> {result}")
        return result

    def _retrieve_uploaded_files_context(self, query: str, session_id: str) -> List[Dict[str, Any]]:
        """ENHANCED: Retrieve complete file content from uploaded files with RulesPage integration for analysis."""
        try:
            import os
            import re
            from datetime import datetime, timedelta
            from backend.models import Document as DBDocument, db

            # CRITICAL FIX: Prevent document upload notifications from being processed as queries
            upload_notification_patterns = [
                'document uploaded successfully',
                'file uploaded successfully',
                'uploaded and indexed successfully',
                'rag integration',
                'document id:',
                'file details:',
                'status: uploaded'
            ]

            query_lower = query.lower()
            is_upload_notification = any(pattern in query_lower for pattern in upload_notification_patterns)

            if is_upload_notification:
                logger.info(f"[ENHANCED_FILE_ANALYSIS] Detected upload notification, skipping file retrieval: '{query[:100]}...'")
                return []

            logger.info(f"[ENHANCED_FILE_ANALYSIS] Starting complete file content retrieval for query: '{query}'")

            # Search for uploaded files that match the query (expanded beyond just code files)
            query_lower = query.lower()

            # Get all uploaded files with content (recent first)
            try:
                # ENHANCED: Include all files with content, not just code files
                uploaded_files = db.session.query(DBDocument).filter(
                    DBDocument.content.isnot(None),
                    DBDocument.index_status.in_(["STORED", "INDEXED"])
                ).order_by(DBDocument.uploaded_at.desc()).all()
                logger.info(f"[ENHANCED_FILE_ANALYSIS] Found {len(uploaded_files)} files with content")
            except Exception as query_error:
                logger.error(f"Database query failed: {query_error}")
                raise

            if not uploaded_files:
                logger.info("No files with content found in database")
                return []

            file_contexts = []

            # ENHANCED ANALYSIS KEYWORDS - comprehensive file analysis detection
            analysis_keywords = [
                'analyze', 'analysis', 'review', 'examine', 'inspect', 'check', 'look at',
                'what is', 'what does', 'explain', 'describe', 'tell me about', 'show me',
                'discuss', 'understand', 'read', 'go through', 'summarize', 'breakdown',
                'improve', 'optimize', 'fix', 'debug', 'enhance', 'refactor', 'rewrite'
            ]

            file_keywords = [
                'file', 'files', 'code', 'script', 'uploaded', 'document', 'content',
                'python', 'javascript', 'jsx', 'tsx', 'html', 'css', 'json', 'csv', 'txt',
                'xml', 'yaml', 'yml', 'md', 'sh', 'dockerfile', 'java', 'c', 'cpp', 'go'
            ]

            # Check if this is a file analysis request
            has_analysis_intent = any(keyword in query_lower for keyword in analysis_keywords)
            has_file_reference = any(keyword in query_lower for keyword in file_keywords)
            is_analysis_request = has_analysis_intent and has_file_reference

            logger.info(f"[ENHANCED_FILE_ANALYSIS] Analysis intent: {has_analysis_intent}, File reference: {has_file_reference}")

            for file_doc in uploaded_files:
                filename_lower = file_doc.filename.lower()
                filename_base = os.path.splitext(filename_lower)[0]
                file_extension = os.path.splitext(filename_lower)[1]

                # ENHANCED MATCHING SCORING
                match_score = 0.0
                match_reasons = []

                # 1. EXACT FILENAME MATCHING (Highest priority)
                if filename_lower in query_lower:
                    match_score += 15.0
                    match_reasons.append("exact_filename")

                # 2. BASE NAME MATCHING (High priority)
                if filename_base in query_lower and len(filename_base) > 3:
                    match_score += 12.0
                    match_reasons.append("base_name")

                # 3. PARTIAL FILENAME MATCHING (Medium priority)
                filename_parts = re.split(r'[._-]', filename_base)
                for part in filename_parts:
                    if len(part) > 3 and part in query_lower:
                        match_score += 8.0
                        match_reasons.append(f"partial_name:{part}")

                # 4. EXTENSION-BASED MATCHING (Medium priority)
                supported_extensions = ['.jsx', '.js', '.ts', '.tsx', '.py', '.php', '.css', '.html', '.json', '.xml', '.yaml', '.yml', '.md', '.sh', '.dockerfile', '.java', '.c', '.cpp', '.h', '.go', '.rs', '.rb', '.swift', '.kt', '.sql', '.csv', '.txt']
                if file_extension in supported_extensions:
                    ext_name = file_extension.replace('.', '')
                    if ext_name in query_lower or file_extension in query_lower:
                        match_score += 6.0
                        match_reasons.append("extension")

                # 5. SEMANTIC ANALYSIS REQUEST MATCHING (High priority for analysis)
                if is_analysis_request:
                    match_score += 10.0
                    match_reasons.append("analysis_request")

                # 6. GENERAL FILE KEYWORDS (Lower priority)
                general_file_indicators = ['uploaded', 'code', 'script', 'file', 'document']
                for indicator in general_file_indicators:
                    if indicator in query_lower:
                        match_score += 3.0
                        match_reasons.append(f"general:{indicator}")

                # 7. RECENT UPLOAD BONUS
                if file_doc.uploaded_at:
                    hours_since_upload = (datetime.now() - file_doc.uploaded_at.replace(tzinfo=None)).total_seconds() / 3600
                    if hours_since_upload < 24:
                        match_score += 5.0
                        match_reasons.append("recent_upload")
                    elif hours_since_upload < 168:
                        match_score += 2.0
                        match_reasons.append("recent_upload_week")

                                # ENHANCED: Include files with strong relevance or explicit analysis requests
                has_strong_match = match_score >= 10.0
                has_medium_match = match_score >= 6.0
                logger.info(f"[ENHANCED_FILE_ANALYSIS] {file_doc.filename} - score: {match_score:.1f}, analysis_request: {is_analysis_request}, strong_match: {has_strong_match}, medium_match: {has_medium_match}")

                # CRITICAL FIX: Add context length limits to prevent massive context injection
                max_context_length = 500000  # 500KB limit per file (10x increase for local system)
                max_total_context = 2000000  # 2MB total limit (10x increase for local system)

                # More lenient matching: include files with medium match or any analysis request
                if has_strong_match or has_medium_match or is_analysis_request or match_score >= 3.0:
                    # ENHANCED: Get complete file content for analysis
                    file_content = file_doc.content
                    if file_content:
                        # CRITICAL FIX: Enforce context length limits with user notification
                        if len(file_content) > max_context_length:
                            truncation_warning = f"\n\n[WARNING: File {file_doc.filename} was truncated from {len(file_content)} to {max_context_length} characters due to length limits. Consider splitting large files for better analysis.]"
                            logger.warning(f"[ENHANCED_FILE_ANALYSIS] File {file_doc.filename} exceeds length limit ({len(file_content)} > {max_context_length}), truncating")
                            file_content = file_content[:max_context_length] + truncation_warning

                        # Check total context length
                        current_total_length = sum(len(ctx.get('content', '')) for ctx in file_contexts)
                        if current_total_length + len(file_content) > max_total_context:
                            logger.warning(f"[ENHANCED_FILE_ANALYSIS] Total context length limit reached ({current_total_length + len(file_content)} > {max_total_context}), skipping additional files")
                            break

                        # ENHANCED: Preprocess content for better LLM understanding
                        processed_content = self._preprocess_file_content(file_content, file_doc.filename, file_extension)

                        file_contexts.append({
                            'content': processed_content,  # Use processed content
                            'metadata': {
                                'source_document': file_doc.filename,
                                'file_type': 'uploaded_file',
                                'file_id': file_doc.id,
                                'uploaded_at': file_doc.uploaded_at.isoformat() if file_doc.uploaded_at else None,
                                'size': len(file_content),
                                'original_size': len(file_content),
                                'match_score': match_score,
                                'match_reasons': match_reasons,
                                'is_code_file': file_doc.is_code_file,
                                'file_extension': file_extension
                            },
                            'score': min(match_score / 15.0, 1.0),  # Normalize to 0-1 range
                            'source': f"Complete File: {file_doc.filename}",
                            'content_type': 'uploaded_file',
                            'entity_type': 'file'
                        })

                        logger.info(f"[ENHANCED_FILE_ANALYSIS] Matched file {file_doc.filename} (score: {match_score:.1f}, size: {len(file_content)} chars)")
                    else:
                        logger.warning(f"[ENHANCED_FILE_ANALYSIS] File {file_doc.filename} has no content")
                else:
                    logger.debug(f"[ENHANCED_FILE_ANALYSIS] Skipped file {file_doc.filename} (score: {match_score:.1f})")

            # Sort by match score (highest first)
            file_contexts.sort(key=lambda x: x['metadata']['match_score'], reverse=True)

            # ENHANCED: Include fallback files for explicit analysis requests
            explicit_file_analysis = is_analysis_request and has_file_reference
            if not file_contexts and explicit_file_analysis and uploaded_files:
                logger.info("[ENHANCED_FILE_ANALYSIS] Explicit file analysis request - including recent files as fallback")
                for file_doc in uploaded_files[:3]:  # Include up to 3 most recent files
                    if file_doc.content:
                        processed_content = self._preprocess_file_content(file_doc.content, file_doc.filename, os.path.splitext(file_doc.filename.lower())[1])
                        file_contexts.append({
                            'content': processed_content,
                            'metadata': {
                                'source_document': file_doc.filename,
                                'file_type': 'uploaded_file',
                                'file_id': file_doc.id,
                                'uploaded_at': file_doc.uploaded_at.isoformat() if file_doc.uploaded_at else None,
                                'size': len(file_doc.content),
                                'original_size': len(file_doc.content),
                                'match_score': 1.0,
                                'match_reasons': ['fallback_recent'],
                                'is_code_file': file_doc.is_code_file,
                                'file_extension': os.path.splitext(file_doc.filename.lower())[1]
                            },
                            'score': 0.3,  # Lower confidence for fallback matches
                            'source': f"Recent File: {file_doc.filename}",
                            'content_type': 'uploaded_file',
                            'entity_type': 'file'
                        })
                        logger.info(f"[ENHANCED_FILE_ANALYSIS] Added fallback file: {file_doc.filename}")

            logger.info(f"[ENHANCED_FILE_ANALYSIS] Complete file retrieval: {len(file_contexts)} files included")
            total_content_size = sum(len(ctx['content']) for ctx in file_contexts)
            logger.info(f"[ENHANCED_FILE_ANALYSIS] Total content size: {total_content_size} characters")

            return file_contexts

        except Exception as e:
            logger.error(f"Error in enhanced file content retrieval: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return []

    def _preprocess_file_content(self, content: str, filename: str, file_extension: str) -> str:
        """ENHANCED: Preprocess file content for better LLM understanding and analysis."""
        try:
            if not content:
                return ""

            # Add file header for context
            header = f"=== FILE: {filename} ===\n"
            header += f"=== TYPE: {file_extension.upper()} ===\n"
            header += f"=== SIZE: {len(content)} characters ===\n"
            header += "=== COMPLETE FILE CONTENT ===\n\n"

            # For code files, add syntax highlighting context
            if file_extension in ['.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', '.json', '.xml', '.yaml', '.yml', '.md', '.sh', '.dockerfile', '.java', '.c', '.cpp', '.h', '.go', '.rs', '.rb', '.swift', '.kt', '.sql']:
                header += f"=== PROGRAMMING LANGUAGE: {file_extension.upper()} ===\n"
                header += "=== CODE ANALYSIS MODE ===\n\n"

            # For data files, add data analysis context
            elif file_extension in ['.csv', '.json', '.xml', '.yaml', '.yml']:
                header += f"=== DATA FILE TYPE: {file_extension.upper()} ===\n"
                header += "=== DATA ANALYSIS MODE ===\n\n"

            # For text files, add text analysis context
            elif file_extension in ['.txt', '.md']:
                header += f"=== TEXT FILE TYPE: {file_extension.upper()} ===\n"
                header += "=== TEXT ANALYSIS MODE ===\n\n"

            # Add the complete content
            processed_content = header + content

            # Add footer with analysis instructions
            footer = "\n\n=== ANALYSIS INSTRUCTIONS ===\n"
            footer += "Please analyze this complete file content thoroughly.\n"
            footer += "Consider every line, function, and character.\n"
            footer += "Provide detailed insights about structure, functionality, and potential improvements.\n"
            footer += "=== END FILE ===\n"

            processed_content += footer

            logger.info(f"[ENHANCED_FILE_ANALYSIS] Preprocessed {filename}: {len(content)} -> {len(processed_content)} characters")
            return processed_content

        except Exception as e:
            logger.error(f"Error preprocessing file content for {filename}: {e}")
            return content  # Return original content if preprocessing fails

# Global chat manager instance (lazy-loaded with thread safety)
_chat_manager = None
_chat_manager_lock = threading.Lock()

def get_chat_manager():
    """Get or create the chat manager instance (thread-safe singleton)"""
    global _chat_manager

    is_celery_worker = os.environ.get('CELERY_WORKER_MODE', 'false').lower() == 'true'
    if is_celery_worker:
        raise RuntimeError("Chat manager not available in Celery worker mode")

    if _chat_manager is None:
        with _chat_manager_lock:
            # Double-check locking pattern to prevent race conditions
            if _chat_manager is None:
                logger.info("Creating new EnhancedChatManager instance...")
                try:
                    _chat_manager = EnhancedChatManager()
                    logger.info("EnhancedChatManager created successfully")
                except Exception as e:
                    logger.error(f"Failed to create EnhancedChatManager: {e}")
                    import traceback
                    logger.error(f"Creation traceback:\n{traceback.format_exc()}")
                    raise
    return _chat_manager

@enhanced_chat_bp.route("", methods=["POST"])
@ensure_db_session_cleanup
def enhanced_chat():
    """Enhanced chat endpoint with advanced context and RAG"""
    try:
        is_celery_worker = os.environ.get('CELERY_WORKER_MODE', 'false').lower() == 'true'
        if is_celery_worker:
            return jsonify({"error": "Chat endpoint not available in Celery worker"}), 503

        print(f"🟢 MAIN_ENDPOINT_DEBUG: /api/enhanced-chat endpoint HIT!")
        logger.debug(f"🟢 MAIN_ENDPOINT_DEBUG: /api/enhanced-chat endpoint HIT!")

        # Validate request
        if not request.is_json:
            return error_response("Request must be JSON", 400)

        data = request.get_json()
        session_id = data.get('session_id')
        message = data.get('message')
        use_rag = data.get('use_rag', True)
        debug_mode = data.get('debug', False)
        simple_mode = data.get('simple_mode', False)
        chat_mode = data.get('chat_mode', None)
        request_id = data.get('request_id', f"{session_id}_{int(time.time() * 1000)}")

        if not session_id or not message:
            return error_response("Missing session_id or message", 400)

        print(f"CHAT_DEBUG: Request ID {request_id}, session {session_id}")
        logger.info(f"CHAT_DEBUG: Request ID {request_id}, session {session_id}")

        # DUPLICATE PREVENTION: Check for active or recent duplicate requests
        request_key = f"{session_id}_{message.strip()}"

        with _cache_lock:
            # Check if this exact request is already being processed
            if request_key in _active_requests:
                print(f"CHAT_DEBUG: Blocking duplicate active request: {request_key}")
                logger.warning(f"CHAT_DEBUG: Blocking duplicate active request: {request_key}")
                return error_response("Duplicate request already being processed", 429)

            # Check for recent identical request (within 2 seconds)
            current_time = time.time()
            if request_key in _request_cache:
                last_time, cached_response = _request_cache[request_key]
                if current_time - last_time < 2.0:
                    print(f"CHAT_DEBUG: Returning cached response for recent duplicate: {request_key}")
                    logger.info(f"CHAT_DEBUG: Returning cached response for recent duplicate: {request_key}")
                    return jsonify({
                        "success": True,
                        "data": {
                            **cached_response,
                            "cached": True,
                            "request_id": request_id
                        }
                    })

            # Mark this request as active
            _active_requests.add(request_key)
            print(f"CHAT_DEBUG: Marked request as active: {request_key}")
            logger.info(f"CHAT_DEBUG: Marked request as active: {request_key}")

        # Process message
        chat_manager = get_chat_manager()
        try:
            logger.info(f"Enhanced chat: Processing message for session {session_id}, simple_mode={simple_mode}")
            logger.info(f"Enhanced chat: Chat manager type: {type(chat_manager)}")
            logger.info(f"Enhanced chat: Context manager available: {chat_manager.context_manager is not None}")
            logger.info(f"Enhanced chat: Index manager available: {chat_manager.index_manager is not None}")
            logger.info(f"Enhanced chat: RAG chunker available: {chat_manager.rag_chunker is not None}")

            logger.info(f"Enhanced chat: About to call process_chat_message with chat_mode={chat_mode}...")
            response_data = chat_manager.process_chat_message(session_id, message, use_rag, debug_mode, simple_mode, chat_mode)
            logger.info(f"Enhanced chat: Response received, success={response_data.get('success', False)}")
            logger.info(f"Enhanced chat: Response data keys: {list(response_data.keys()) if isinstance(response_data, dict) else 'Not a dict'}")

            # Cache the successful response
            with _cache_lock:
                _request_cache[request_key] = (time.time(), response_data)
                # Clean old cache entries (keep only last 20)
                if len(_request_cache) > 20:
                    oldest_key = min(_request_cache.keys(), key=lambda k: _request_cache[k][0])
                    del _request_cache[oldest_key]

            response_data['request_id'] = request_id
            print(f"CHAT_DEBUG: Successfully processed request: {request_key}")
            logger.info(f"CHAT_DEBUG: Successfully processed request: {request_key}")

            return success_response(response_data)

        except Exception as e:
            logger.error(f"Enhanced chat: Exception in process_chat_message: {str(e)}")
            logger.error(f"Enhanced chat: Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Enhanced chat: Full traceback:\n{traceback.format_exc()}")
            return error_response(f"Chat processing failed: {str(e)}", 500)

        finally:
            # Always remove from active requests
            with _cache_lock:
                _active_requests.discard(request_key)
                print(f"CHAT_DEBUG: Removed request from active set: {request_key}")
                logger.info(f"CHAT_DEBUG: Removed request from active set: {request_key}")

    except Exception as e:
        import traceback
        logger.error(f"Enhanced chat error (outer): {e}\n{traceback.format_exc()}")
        return error_response(f"Chat processing failed: {str(e)}", 500)

@enhanced_chat_bp.route("/stream", methods=["POST"])
def enhanced_chat_stream():
    """Enhanced streaming chat endpoint"""
    try:
        # INVESTIGATION: Log distinctive message to trace if this endpoint is being called
        print("🔴 STREAMING_ENDPOINT_DEBUG: /api/enhanced-chat/stream endpoint HIT!")
        logger.debug("🔴 STREAMING_ENDPOINT_DEBUG: /api/enhanced-chat/stream endpoint HIT!")
        # Validate request
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400

        data = request.get_json()
        session_id = data.get('session_id')
        message = data.get('message')
        use_rag = data.get('use_rag', True)
        debug_mode = data.get('debug', False)

        if not session_id or not message:
            return jsonify({"error": "Missing session_id or message"}), 400

        def generate_stream():
            try:
                chat_manager = get_chat_manager()
                # Get or create session
                session = chat_manager._get_or_create_session(session_id)

                # Get active model
                model_name = chat_manager._get_active_model()
                model_config = chat_manager._get_model_config(model_name)

                # Retrieve relevant context if RAG is enabled
                rag_context = []
                if use_rag:
                    rag_context = chat_manager._retrieve_relevant_context(message, session_id)

                # Note: User message saving handled by main endpoint, not needed in unused streaming endpoint

                # Create enhanced prompt
                enhanced_message = message
                if rag_context:
                    context_text = "\n\n".join(
                        f"[Source: {chunk['source']}] {chunk['content']}"
                        for chunk in rag_context[:3]
                    )
                    enhanced_message = f"Context:\n{context_text}\n\nUser Question: {message}"

                # Create or get chat engine (TODO: Add web search integration to streaming endpoint)
                chat_engine = chat_manager._create_chat_engine(session_id, model_name, "")

                # Generate streaming response
                response_stream = chat_engine.stream_chat(enhanced_message)

                full_response = ""
                for chunk in response_stream.response_gen:
                    full_response += chunk
                    yield f"data: {json.dumps({'delta': chunk})}\n\n"

                # CRITICAL FIX: Handle LlamaIndex "Empty Response" from context overflow in streaming
                if full_response.strip() == "Empty Response" or full_response.strip() == "":
                    logger.warning("Streaming: LlamaIndex returned 'Empty Response' - using direct LLM fallback.")
                    try:
                        # Direct LLM call for streaming
                        llm = current_app.config.get('LLAMA_INDEX_LLM')
                        if llm:
                            msg_lower = message.lower().strip()
                            if len(message) < 50 and any(word in msg_lower for word in ['hello', 'hi', 'hey', 'how are you']):
                                simple_prompt = f"Respond naturally and briefly to this greeting: {message}"
                            else:
                                simple_prompt = message

                            direct_response = llm.complete(simple_prompt)
                            fallback_message = direct_response.text.strip() if hasattr(direct_response, 'text') else str(direct_response).strip()

                            if fallback_message and fallback_message != "Empty Response":
                                full_response = fallback_message
                                yield f"data: {json.dumps({'delta': fallback_message})}\n\n"
                                logger.info(f"Streaming direct LLM fallback successful, length: {len(fallback_message)}")
                            else:
                                # Use hardcoded responses as final fallback
                                if any(word in msg_lower for word in ['hello', 'hi', 'hey']):
                                    fallback_message = "Hello! How can I help you today?"
                                else:
                                    fallback_message = "I'm ready to help! What would you like to do?"
                                full_response = fallback_message
                                yield f"data: {json.dumps({'delta': fallback_message})}\n\n"
                        else:
                            fallback_message = "Hello! I'm ready to help you."
                            full_response = fallback_message
                            yield f"data: {json.dumps({'delta': fallback_message})}\n\n"

                    except Exception as fallback_error:
                        logger.error(f"Streaming direct LLM fallback failed: {fallback_error}")
                        if any(word in message.lower() for word in ['hello', 'hi', 'hey']):
                            fallback_message = "Hello! How can I help you today?"
                        else:
                            fallback_message = "I'm ready to assist you. What would you like to do?"
                        full_response = fallback_message
                        yield f"data: {json.dumps({'delta': fallback_message})}\n\n"

                # Save assistant response
                chat_manager._save_message(session_id, 'assistant', full_response)
                # db.session.commit() # Removed as per edit hint

                # Send completion event
                yield f"data: {json.dumps({'event': 'complete', 'full_response': full_response})}\n\n"

            except Exception as e:
                logger.error(f"Streaming error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            finally:
                yield "data: [DONE]\n\n"

        return Response(
            stream_with_context(generate_stream()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*'
            }
        )

    except Exception as e:
        logger.error(f"Enhanced streaming chat error: {e}")
        return jsonify({"error": str(e)}), 500

@enhanced_chat_bp.route("/<session_id>/context", methods=["GET"])
def get_context_info(session_id: str):
    """Get context information for a session"""
    try:
        chat_manager = get_chat_manager()

        # Safe context stats retrieval
        context_stats = {}
        if chat_manager.context_manager:
            context_stats = chat_manager.context_manager.get_context_stats(session_id)

        # Safe index stats retrieval
        index_stats = {}
        if chat_manager.index_manager:
            index_stats = chat_manager.index_manager.get_cache_stats()

        return success_response({
            'context_stats': context_stats,
            'index_stats': index_stats,
            'chat_manager_stats': chat_manager.stats
        })

    except Exception as e:
        logger.error(f"Error getting context info: {e}")
        return error_response(str(e), 500)

@enhanced_chat_bp.route("/<session_id>/clear", methods=["POST"])
def clear_session_context(session_id: str):
    """Clear context for a specific session"""
    try:
        chat_manager = get_chat_manager()
        chat_manager.context_manager.clear_session(session_id)

        # Remove cached chat engine
        if session_id in chat_manager.chat_engines:
            del chat_manager.chat_engines[session_id]

        return success_response({'message': f'Context cleared for session {session_id}'})

    except Exception as e:
        logger.error(f"Error clearing context: {e}")
        return error_response(str(e), 500)

@enhanced_chat_bp.route("/stats", methods=["GET"])
def get_chat_stats():
    """Get overall chat statistics"""
    try:
        chat_manager = get_chat_manager()

        # Safely get index manager stats
        index_manager_stats = {}
        if chat_manager.index_manager:
            try:
                index_manager_stats = chat_manager.index_manager.get_cache_stats()
            except Exception as e:
                logger.warning(f"Failed to get index manager stats: {e}")
                index_manager_stats = {"error": "Index manager not available"}
        else:
            index_manager_stats = {"error": "Index manager not initialized"}

        # Safely get RAG chunker stats
        rag_chunker_stats = {}
        if chat_manager.rag_chunker:
            try:
                rag_chunker_stats = chat_manager.rag_chunker.get_chunking_stats()
            except Exception as e:
                logger.warning(f"Failed to get RAG chunker stats: {e}")
                rag_chunker_stats = {"error": "RAG chunker not available"}
        else:
            rag_chunker_stats = {"error": "RAG chunker not initialized"}

        return success_response({
            'chat_manager_stats': chat_manager.stats,
            'context_manager_stats': {
                'total_sessions': len(chat_manager.context_manager.context_windows) if chat_manager.context_manager else 0,
                'total_cached_engines': len(chat_manager.chat_engines)
            },
            'index_manager_stats': index_manager_stats,
            'rag_chunker_stats': rag_chunker_stats
        })

    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return error_response(str(e), 500)

@enhanced_chat_bp.route("/<session_id>/history", methods=["GET"])
def get_chat_history(session_id: str):
    """Get chat history for a session"""
    try:
        from flask import request
        from backend.models import db, LLMMessage, LLMSession

        # Get query parameters
        limit = int(request.args.get('limit', 50))
        before_id = request.args.get('before_id')

        # Ensure session exists
        session = db.session.get(LLMSession, session_id)
        if not session:
            try:
                # Create session if it doesn't exist
                session = LLMSession(id=session_id, user="default")
                db.session.add(session)
                db.session.commit()
                logger.info(f"Created new session: {session_id}")
            except Exception as e:
                # Handle race condition where session was created between check and insert
                db.session.rollback()
                session = db.session.get(LLMSession, session_id)
                if not session:
                    # If still no session, re-raise the error
                    logger.error(f"Failed to create session {session_id}: {e}")
                    raise
                logger.warning(f"Session {session_id} already existed during creation attempt")

        # Query messages from database
        query = db.session.query(LLMMessage).filter(
            LLMMessage.session_id == session_id
        ).order_by(LLMMessage.timestamp.desc())

        # Apply before_id filter if provided
        if before_id:
            try:
                before_message = db.session.get(LLMMessage, int(before_id))
                if before_message:
                    query = query.filter(LLMMessage.timestamp < before_message.timestamp)
            except (ValueError, TypeError):
                logger.warning(f"Invalid before_id: {before_id}")

        # Get total count
        total_count = query.count()

        # Apply limit and get messages
        messages = query.limit(limit).all()

        # Convert to frontend format
        formatted_messages = []
        for msg in reversed(messages):  # Reverse to get chronological order
            formatted_messages.append({
                "id": str(msg.id),
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None
            })

        # Check if there are more messages
        has_more = total_count > len(messages)

        logger.info(f"Retrieved {len(formatted_messages)} messages for session {session_id} (total: {total_count})")

        return jsonify({
            "messages": formatted_messages,
            "has_more": has_more,
            "session_id": session_id,
            "total_count": total_count
        })

    except Exception as e:
        logger.error(f"Error fetching chat history for session {session_id}: {e}")
        return jsonify({
            "error": "Failed to fetch chat history",
            "message": str(e)
        }), 500


@enhanced_chat_bp.route("/history/all", methods=["DELETE"])
def clear_all_chat_history():
    """Clear all chat history"""
    try:
        from backend.models import db, LLMMessage, LLMSession

        # Delete all messages
        deleted_messages = db.session.query(LLMMessage).delete()

        # Delete all sessions
        deleted_sessions = db.session.query(LLMSession).delete()

        db.session.commit()

        # Clear all chat manager caches
        chat_manager = get_chat_manager()
        chat_manager.chat_engines.clear()
        if hasattr(chat_manager, 'context_manager'):
            try:
                # Try to clear context manager if it has a clear method
                if hasattr(chat_manager.context_manager, 'clear_all'):
                    chat_manager.context_manager.clear_all()
                elif hasattr(chat_manager.context_manager, 'clear'):
                    chat_manager.context_manager.clear()
                logger.info("Cleared context manager successfully")
            except Exception as context_error:
                logger.warning(f"Could not clear context manager: {context_error}")

        logger.info(f"Cleared all chat history: {deleted_messages} messages, {deleted_sessions} sessions")

        return jsonify({
            'message': 'All chat history cleared successfully',
            'deleted_messages': deleted_messages,
            'deleted_sessions': deleted_sessions
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error clearing all chat history: {e}")
        return jsonify({
            "error": "Failed to clear chat history",
            "message": str(e)
        }), 500

# ===== VISION CHAT ENDPOINTS =====

@enhanced_chat_bp.route("/vision/analyze", methods=["POST"])
@ensure_db_session_cleanup
def vision_analyze_image():
    """Analyze an uploaded image in chat context"""
    try:
        # Validate request
        if 'image' not in request.files:
            return error_response("No image file provided", 400)

        if 'session_id' not in request.form:
            return error_response("Missing session_id", 400)

        image_file = request.files['image']
        session_id = request.form['session_id']
        user_message = request.form.get('message', '')
        analysis_type = request.form.get('analysis_type', 'describe')

        if image_file.filename == '':
            return error_response("No image file selected", 400)

        # Read image data
        image_data = image_file.read()

        # Save image permanently for chat history
        try:
            import os
            import uuid
            from datetime import datetime
            from pathlib import Path

            # Create permanent storage directory
            from backend.config import UPLOAD_DIR
            permanent_dir = os.path.join(UPLOAD_DIR, "chat_images")
            os.makedirs(permanent_dir, exist_ok=True)

            # Generate permanent filename
            file_extension = Path(image_file.filename).suffix.lower()
            if not file_extension:
                file_extension = '.png'  # Default extension

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            permanent_filename = f"chat_image_{timestamp}_{unique_id}{file_extension}"
            permanent_path = os.path.join(permanent_dir, permanent_filename)

            # Save image permanently
            with open(permanent_path, 'wb') as f:
                f.write(image_data)

            # Create accessible URL for the image
            image_url = f"/api/enhanced-chat/vision/image/{permanent_filename}"

            logger.info(f"Saved permanent chat image: {permanent_path}")

        except Exception as e:
            logger.error(f"Failed to save permanent image: {e}")
            image_url = None
            permanent_filename = image_file.filename

        # Process image using vision chat service
        try:
            from backend.services.vision_chat_service import process_pasted_image

            analysis_result = process_pasted_image(image_data, user_message)

            # Save the user message with image indicator to chat history
            chat_manager = get_chat_manager()
            image_message = f"[Image uploaded: {image_file.filename}]"
            if user_message:
                image_message += f" {user_message}"

            chat_manager._save_message(session_id, 'user', image_message)

            # Save the AI response with image metadata
            if analysis_result.get('analysis_successful'):
                chat_response = analysis_result.get('chat_response', 'Image analysis completed.')

                # Save assistant message with image metadata
                chat_manager._save_message(session_id, 'assistant', chat_response)

                return success_response({
                    "type": "vision_analysis",
                    "success": True,
                    "response": chat_response,
                    "image_url": image_url,
                    "image_filename": permanent_filename,
                    "analysis_details": analysis_result.get('analysis_details', {}),
                    "processing_time": analysis_result.get('processing_time', 0)
                })
            else:
                error_response_text = analysis_result.get('chat_response', 'Image analysis failed.')
                chat_manager._save_message(session_id, 'assistant', error_response_text)

                return success_response({
                    "type": "vision_analysis",
                    "success": False,
                    "response": error_response_text,
                    "image_url": image_url,
                    "image_filename": permanent_filename,
                    "error": analysis_result.get('error', 'Unknown error')
                })

        except ImportError:
            return error_response("Vision chat service not available", 503)
        except Exception as e:
            logger.error(f"Error in vision image analysis: {e}")
            return error_response(f"Vision analysis failed: {str(e)}", 500)

    except Exception as e:
        logger.error(f"Error in vision analyze endpoint: {e}")
        return error_response(f"Request processing failed: {str(e)}", 500)

@enhanced_chat_bp.route("/vision/generate", methods=["POST"])
@ensure_db_session_cleanup
def vision_generate_image():
    """Generate an image based on text prompt in chat context"""
    try:
        # Validate request
        if not request.is_json:
            return error_response("Request must be JSON", 400)

        data = request.get_json()
        session_id = data.get('session_id')
        prompt = data.get('prompt')
        style = data.get('style', 'realistic')
        size = data.get('size', [512, 512])

        if not session_id or not prompt:
            return error_response("Missing session_id or prompt", 400)

        # Process image generation using vision chat service
        try:
            from backend.services.vision_chat_service import generate_chat_image

            generation_result = generate_chat_image(prompt, style, tuple(size))

            # Save the user request to chat history
            chat_manager = get_chat_manager()
            user_message = f"Generate an image: {prompt}"
            if style != 'realistic':
                user_message += f" (style: {style})"

            chat_manager._save_message(session_id, 'user', user_message)

            # Handle generation result
            if generation_result.success:
                # Create response with image info
                chat_response = f"I've generated an image based on your prompt: '{prompt}'"
                if style != 'realistic':
                    chat_response += f" in {style} style"

                chat_response += f"\n\nGenerated using: {generation_result.model_used}"
                chat_response += f"\nGeneration time: {generation_result.generation_time:.2f}s"

                chat_manager._save_message(session_id, 'assistant', chat_response)

                return success_response({
                    "type": "image_generation",
                    "success": True,
                    "response": chat_response,
                    "image_path": generation_result.image_path,
                    "generation_details": {
                        "prompt_used": generation_result.prompt_used,
                        "model_used": generation_result.model_used,
                        "generation_time": generation_result.generation_time,
                        "image_size": generation_result.image_size
                    }
                })
            else:
                error_msg = generation_result.error or "Image generation failed"
                chat_response = f"I wasn't able to generate an image for '{prompt}'. Error: {error_msg}"

                chat_manager._save_message(session_id, 'assistant', chat_response)

                return success_response({
                    "type": "image_generation",
                    "success": False,
                    "response": chat_response,
                    "error": error_msg
                })

        except ImportError:
            return error_response("Vision chat service not available", 503)
        except Exception as e:
            logger.error(f"Error in vision image generation: {e}")
            return error_response(f"Image generation failed: {str(e)}", 500)

    except Exception as e:
        logger.error(f"Error in vision generate endpoint: {e}")
        return error_response(f"Request processing failed: {str(e)}", 500)

@enhanced_chat_bp.route("/vision/status", methods=["GET"])
def vision_chat_status():
    """Get status of vision chat capabilities"""
    try:
        from backend.services.vision_chat_service import get_vision_chat_status

        status = get_vision_chat_status()

        return success_response({
            "type": "vision_status",
            "vision_chat_status": status
        })

    except ImportError:
        return success_response({
            "type": "vision_status",
            "vision_chat_status": {
                "service_available": False,
                "error": "Vision chat service not installed"
            }
        })
    except Exception as e:
        logger.error(f"Error getting vision chat status: {e}")
        return error_response(f"Status check failed: {str(e)}", 500)

@enhanced_chat_bp.route("/vision/image/<image_id>", methods=["GET"])
def serve_chat_image(image_id):
    """Serve uploaded chat images for display in chat history"""
    try:
        import os
        from flask import send_file

        # Sanitize image_id to prevent path traversal
        if not image_id.replace('_', '').replace('-', '').replace('.', '').isalnum():
            return error_response("Invalid image ID", 400)

        # Check in permanent chat images directory
        from backend.config import UPLOAD_DIR, CACHE_DIR
        image_dir = os.path.join(UPLOAD_DIR, "chat_images")
        image_path = os.path.join(image_dir, image_id)

        if not os.path.exists(image_path):
            # Fallback to cache directory for recently uploaded images
            cache_dir = os.path.join(CACHE_DIR, "chat_images")
            image_path = os.path.join(cache_dir, image_id)

            if not os.path.exists(image_path):
                return error_response("Image not found", 404)

        # Verify it's actually an image file
        valid_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
        if not any(image_path.lower().endswith(ext) for ext in valid_extensions):
            return error_response("Invalid image file", 400)

        return send_file(image_path, as_attachment=False)

    except Exception as e:
        logger.error(f"Error serving chat image {image_id}: {e}")
        return error_response(f"Failed to serve image: {str(e)}", 500)
