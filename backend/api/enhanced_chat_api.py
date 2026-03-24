# backend/api/enhanced_chat_api.py
# Enhanced Chat API with improved context management, RAG, and token management
# Integrates all the new components for better LLM performance

# Force local LlamaIndex configuration BEFORE any LlamaIndex imports
import backend.utils.llama_index_local_config

import logging
import json
import uuid
import os
import time
import threading
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from flask import Blueprint, current_app, request, jsonify, Response, stream_with_context, send_file

logger = logging.getLogger(__name__)

from backend.utils.settings_utils import get_setting

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
    logger.info("ContextManager imported successfully")
except ImportError as e:
    ContextManager = None
    logger.warning(f"ContextManager not available: {e}")

try:
    from backend.utils.unified_index_manager import get_global_index_manager
    logger.info("Index manager imported successfully")
except ImportError as e:
    get_global_index_manager = None
    logger.warning(f"Index manager not available: {e}")

try:
    from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker
    logger.info("Enhanced RAG chunker imported successfully")
except ImportError as e:
    EnhancedRAGChunker = None
    logger.warning(f"Enhanced RAG chunker not available: {e}")

# Smart Query Routing System
try:
    from backend.utils.intent_classifier import classify_user_intent, IntentType, get_intent_context_limit
    from backend.handlers.database_handler import create_database_handler
    logger.info("Smart routing system imported successfully")
except ImportError as e:
    classify_user_intent = None
    IntentType = None
    get_intent_context_limit = None
    create_database_handler = None
    logger.warning(f"Smart routing system not available: {e}")

try:
    from backend.utils import llm_service
    logger.info("LLM service imported successfully")
except ImportError as e:
    llm_service = None
    logger.warning(f"LLM service not available: {e}")

try:
    from backend.utils.conversation_logger import get_conversation_logger
    logger.info("Conversation logger imported successfully")
except ImportError as e:
    get_conversation_logger = None
    logger.warning(f"Conversation logger not available: {e}")


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
        
        # Initialize context manager if available (with persistence)
        if ContextManager:
            try:
                from backend.config import CONTEXT_PERSISTENCE_DIR

                if get_setting("enhanced_context_enabled", default=True, cast=bool):
                    self._context_manager = ContextManager(
                        max_tokens=16384,  # Doubled from 8192 for better context retention
                        compression_threshold=0.8,
                        persistence_dir=CONTEXT_PERSISTENCE_DIR
                    )
                    logger.info(f"ContextManager initialized with persistence: {CONTEXT_PERSISTENCE_DIR}")
                else:
                    self._context_manager = ContextManager()
                    logger.info("ContextManager initialized without persistence (disabled)")
            except Exception as e:
                logger.error(f"Failed to initialize ContextManager: {e}")
                self._context_manager = None

        # Initialize MemoryManager for smart context selection
        self._memory_manager = None
        try:
            from backend.utils.memory_manager import MemoryManager
            from backend.models import db
            self._memory_manager = MemoryManager(db_session=db.session)
            logger.info("MemoryManager initialized for smart context selection")
        except Exception as e:
            logger.warning(f"MemoryManager initialization failed: {e}")
            self._memory_manager = None

        # Initialize EntityContextEnhancer for entity relationship context
        self._entity_enhancer = None
        try:
            from backend.utils.entity_context_enhancer import get_entity_context_enhancer
            self._entity_enhancer = get_entity_context_enhancer()
            logger.info("EntityContextEnhancer initialized for entity relationship tracking")
        except Exception as e:
            logger.warning(f"EntityContextEnhancer initialization failed: {e}")
            self._entity_enhancer = None

        # Initialize ConversationLogger for persistent debugging logs (optional)
        self._conversation_logger = None
        try:
            from backend.utils.conversation_logger import ConversationLogger
            self._conversation_logger = ConversationLogger()
            logger.info("ConversationLogger initialized for session tracking")
        except Exception as e:
            logger.debug(f"ConversationLogger initialization skipped: {e}")
            self._conversation_logger = None

        # Chat engine cache
        self.chat_engines = {}

        # Persistent session memory storage
        self.session_memories = {}
        self.session_messages = {}  # Store full conversation history

        # FIX BUG 4: Add session cleanup tracking to prevent memory leaks
        self.session_last_activity = {}  # Track last activity per session
        self.session_creation_time = {}  # Track session creation time
        self.max_session_age = 24 * 60 * 60  # 24 hours in seconds
        self.max_inactive_time = 60 * 60  # 1 hour in seconds
        self.cleanup_interval = 5 * 60  # Clean up every 5 minutes
        self.last_cleanup = time.time()

        # Statistics
        self.stats = {
            'total_conversations': 0,
            'total_messages': 0,
            'context_compressions': 0,
            'rag_queries': 0,
            'avg_response_time': 0.0,
            'sessions_cleaned': 0,  # Track cleanup stats
            'memory_saved_mb': 0.0
        }

        # Intent detection cache (simple replacement for hardcoded methods)
        self.intent_cache = {}

        # BUG FIX #4: Start automatic cleanup thread for guaranteed memory management
        self._cleanup_thread = threading.Thread(
            target=self._periodic_cleanup,
            daemon=True,
            name="ChatSessionCleanup"
        )
        self._cleanup_thread.start()
        logger.info("Started automatic session cleanup thread")

    def _periodic_cleanup(self):
        """Periodically clean up old sessions in background thread"""
        logger.info("Periodic cleanup thread started")
        while True:
            try:
                time.sleep(self.cleanup_interval)
                self._cleanup_old_sessions()
            except Exception as e:
                logger.error(f"Error in periodic cleanup: {e}", exc_info=True)

    # FIX BUG 4: Add comprehensive session cleanup to prevent memory leaks
    def _cleanup_old_sessions(self):
        """Clean up old and inactive sessions to prevent memory leaks"""
        current_time = time.time()

        # Only run cleanup if enough time has passed
        if current_time - self.last_cleanup < self.cleanup_interval:
            return

        self.last_cleanup = current_time
        sessions_before = len(self.session_memories)
        memory_before = sum(len(str(memory)) for memory in self.session_memories.values())

        sessions_to_remove = []

        for session_id in list(self.session_memories.keys()):
            # Check session age
            creation_time = self.session_creation_time.get(session_id, current_time)
            session_age = current_time - creation_time

            # Check last activity
            last_activity = self.session_last_activity.get(session_id, current_time)
            inactive_time = current_time - last_activity

            # Mark for removal if too old or inactive
            if session_age > self.max_session_age or inactive_time > self.max_inactive_time:
                sessions_to_remove.append(session_id)

        # Remove old sessions
        for session_id in sessions_to_remove:
            try:
                # Calculate memory before removal
                session_memory_size = len(str(self.session_memories.get(session_id, "")))
                session_messages_size = len(str(self.session_messages.get(session_id, [])))

                # Remove from all tracking dictionaries with proper cleanup
                memory = self.session_memories.pop(session_id, None)
                if memory and hasattr(memory, 'chat_store') and hasattr(memory.chat_store, 'store'):
                    memory.chat_store.store.clear()
                
                messages = self.session_messages.pop(session_id, None)
                if messages:
                    messages.clear()
                
                self.session_last_activity.pop(session_id, None)
                self.session_creation_time.pop(session_id, None)
                self.chat_engines.pop(session_id, None)

                # Update statistics
                self.stats['sessions_cleaned'] += 1
                self.stats['memory_saved_mb'] += (session_memory_size + session_messages_size) / (1024 * 1024)

                logger.info(f"BUG FIX 4: Cleaned up session {session_id} (age: {session_age/3600:.1f}h, inactive: {inactive_time/3600:.1f}h)")

            except Exception as cleanup_error:
                logger.error(f"BUG FIX 4: Error cleaning up session {session_id}: {cleanup_error}")

        if sessions_to_remove:
            sessions_after = len(self.session_memories)
            memory_after = sum(len(str(memory)) for memory in self.session_memories.values())
            memory_saved = (memory_before - memory_after) / (1024 * 1024)

            logger.info(f"BUG FIX 4: Cleanup completed - removed {len(sessions_to_remove)} sessions, saved {memory_saved:.2f}MB memory")

    def _update_session_activity(self, session_id):
        """Update session activity timestamp"""
        current_time = time.time()
        self.session_last_activity[session_id] = current_time

        # Set creation time if this is the first time we see this session
        if session_id not in self.session_creation_time:
            self.session_creation_time[session_id] = current_time

    def _save_message_to_db(self, session_id: str, role: str, content: str):
        """Save a chat message to the database"""
        from backend.models import db, LLMSession, LLMMessage

        try:
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
            logger.error(f"Failed to save message to database: {e}", exc_info=True)
            try:
                db.session.rollback()
            except Exception as rollback_err:
                logger.error(f"Rollback failed: {rollback_err}")
        finally:
            # Session cleanup is handled by Flask's after-request hook
            pass

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
        # Default configuration - sized for 16GB GPU
        config = {
            'max_context_tokens': 8192,
            'max_response_tokens': 2048,
            'temperature': 0.7,
            'top_p': 0.9
        }

        model_lower = model_name.lower()

        # Adjust based on model size indicators in name
        if any(size in model_lower for size in ['1b', '1.5b']):
            # Smaller models
            config.update({
                'max_context_tokens': 8192,
                'max_response_tokens': 2048,
                'temperature': 0.8,
                'top_p': 0.9
            })
        elif any(size in model_lower for size in ['3b', '4b']):
            # Medium-small models
            config.update({
                'max_context_tokens': 8192,
                'max_response_tokens': 2048,
                'temperature': 0.7,
                'top_p': 0.9
            })
        elif any(size in model_lower for size in ['6.7b', '7b', '8b']):
            # Medium-large models (e.g. llama3:latest) - 16GB GPU safe
            config.update({
                'max_context_tokens': 8192,
                'max_response_tokens': 4096,
                'temperature': 0.7,
                'top_p': 0.9
            })
        elif any(size in model_lower for size in ['13b', '15b', '70b']):
            # Large models
            config.update({
                'max_context_tokens': 16384,
                'max_response_tokens': 4096,
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
                'max_context_tokens': 8192,
                'max_response_tokens': 4096,
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
                'max_context_tokens': 8192,
                'max_response_tokens': 2048,
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
        elif 'what files' in msg_lower or 'what documents' in msg_lower:
            # Force general chat for file listing questions to enable RAG
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
                from backend.config import CONTEXT_PERSISTENCE_DIR

                if get_setting("enhanced_context_enabled", default=True, cast=bool):
                    self._context_manager = ContextManager(
                        max_tokens=16384,  # Doubled from 8192 for better context retention
                        compression_threshold=0.8,
                        persistence_dir=CONTEXT_PERSISTENCE_DIR
                    )
                    logger.info("Enhanced Context Manager activated")
                else:
                    logger.info("Enhanced Context Manager disabled by configuration")

            except Exception as e:
                logger.error(f"Failed to initialize Context Manager: {e}")
                self._context_manager = None
        return self._context_manager

    @property
    def index_manager(self):
        """Initialize index manager with configuration flags"""
        if self._index_manager is None and get_global_index_manager:
            try:
                if get_setting("advanced_rag_enabled", default=True, cast=bool):
                    self._index_manager = get_global_index_manager()
                    logger.info("Advanced Index Manager activated")
                else:
                    logger.info("Advanced Index Manager disabled by configuration")

            except Exception as e:
                logger.error(f"Failed to initialize Index Manager: {e}")
                self._index_manager = None
        return self._index_manager

    @property
    def rag_chunker(self):
        """Initialize RAG chunker with configuration flags"""
        if self._rag_chunker is None and EnhancedRAGChunker:
            try:
                if get_setting("advanced_rag_enabled", default=True, cast=bool):
                    self._rag_chunker = EnhancedRAGChunker()
                    logger.info("Enhanced RAG Chunker activated")
                else:
                    logger.info("Enhanced RAG Chunker disabled by configuration")

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
            logger.warning(f"Error getting active model from app config: {e}")

        # Fallback to saved active model name from file/database
        try:
            from backend.utils.llm_service import get_saved_active_model_name
            saved_model = get_saved_active_model_name()
            if saved_model:
                return saved_model
        except Exception as e:
            logger.warning(f"Error getting saved active model: {e}")

        # Final fallback to config default
        try:
            from backend.config import DEFAULT_LLM
            if DEFAULT_LLM:
                return DEFAULT_LLM
        except Exception as e:
            logger.warning(f"Error getting DEFAULT_LLM: {e}")

        return 'llama3.1:8b'  # Last resort fallback

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for text"""
        # Simple estimation: ~4 characters per token
        return max(1, len(text) // 4)

    def _build_system_prompt(self, model_name: str, context_info: Dict[str, Any], simple_mode: bool = False, web_search_context: str = "", chat_mode: str = None, voice_mode: bool = False, bypass_rules: bool = False) -> str:
        """Build a bulletproof system prompt with anti-fabrication rules and web search integration"""

        # For simple mode or bypass_rules, use a simple prompt without database rules
        if simple_mode or bypass_rules:
            if bypass_rules:
                # Code-optimized prompt when rules are bypassed
                base_prompt = """You are an expert code assistant. Analyze, generate, and improve code efficiently.
Focus on code quality, best practices, and practical solutions.
Provide concise, actionable code recommendations."""
            else:
                # Simple mode prompt
                base_prompt = """You are a friendly and helpful AI assistant. Be natural, conversational, and concise in your responses.
For simple greetings and casual interactions, respond naturally without being overly formal or verbose."""

            # Add minimal honesty framework even for simple mode
            try:
                from backend.utils.capability_awareness import get_honesty_framework
                honesty_rules = get_honesty_framework(model_name)
                if honesty_rules:
                    base_prompt += f"\n\n{honesty_rules}"
            except Exception as e:
                logger.debug(f"Could not add honesty framework to simple mode: {e}")

            # Add voice personality for simple mode if needed
            if voice_mode:
                base_prompt += """

VOICE INTERACTION MODE: You are responding to voice chat, so be extra conversational and natural:
- Use friendly, spoken language: "I see you have..." instead of "Database contains..."
- Be warm and engaging like talking to a colleague
- Avoid technical jargon, explain things naturally
- Show enthusiasm: "Great question!", "That's interesting!"
- Keep responses concise but complete for voice"""

            if bypass_rules:
                logger.info(f"Rules bypass enabled - using code-optimized prompt for model '{model_name}'")

            return base_prompt

        try:
            # Import rule_utils for database-based rule fetching and settings
            from backend import rule_utils
            from backend.models import db
            from backend.utils.settings_utils import get_web_access

            # Detect if web search is available and enabled
            # CHANGE 1: Check setting first, don't require context to exist yet
            web_search_enabled = False
            web_access_setting = False
            try:
                web_access_setting = get_web_access()
                web_search_enabled = web_access_setting  # Enabled if setting is on, regardless of context
                logger.info(f"Web search setting enabled: {web_access_setting}, has context: {bool(web_search_context)}")
            except Exception as e:
                logger.warning(f"Could not determine web search status: {e}")
                web_search_enabled = False
                web_access_setting = False

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

            # CAPABILITY AWARENESS: Add honesty framework and capability context
            # Enhanced chat can't execute tools, so don't inject full tool schemas
            # (saves ~4000 tokens in the system prompt)
            try:
                from backend.utils.capability_awareness import build_capability_prompt_section

                rag_enabled = bool(self._index_manager)

                capability_section = build_capability_prompt_section(
                    model_name=model_name,
                    web_search_enabled=web_access_setting,
                    rag_enabled=rag_enabled,
                    user_message="",
                    tools=[],
                    tools_with_descriptions=[]
                )
                if capability_section:
                    context_parts.append(capability_section)
                    logger.debug(f"Added capability awareness for model '{model_name}' (no tool details - enhanced chat only)")
            except ImportError as e:
                logger.warning(f"capability_awareness module not available: {e}")
            except Exception as e:
                logger.error(f"Error adding capability awareness: {e}")

            # CHANGE 2: Add explicit web access instruction when enabled
            if web_access_setting:
                web_access_instruction = """🌐 WEB ACCESS ENABLED:
You have real-time web search capabilities enabled. When users ask questions that require current information, recent events, real-time data, or information beyond your training data, you should:
- Acknowledge that you can search the web for current information
- Use the web search results provided in the context below to answer accurately
- Cite sources when using web search results
- If web search results are provided, prioritize them over your training data for current information queries
- For questions about current events, weather, recent news, or real-time data, always use the web search results provided"""
                context_parts.append(web_access_instruction)
                logger.info("Added explicit web access instruction to system prompt")

            # Add web search context if available
            if web_search_context:
                context_parts.append(web_search_context)

            # Add conversation context info
            if context_info and context_info.get('total_contexts', 0) > 0:
                context_parts.append(f"CONVERSATION CONTEXT: You have access to {context_info['total_contexts']} conversation contexts.")
            
            # Add conversation history instructions
            context_parts.append("CONVERSATION MEMORY: When provided with conversation history, use it to maintain context and remember previous interactions. Reference past messages when relevant to provide coherent and contextual responses.")

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

            # Add voice personality instructions if in voice mode
            if voice_mode:
                voice_personality = """

🎤 VOICE INTERACTION MODE ACTIVE:
You are responding to voice chat, so adapt your communication style to be natural and conversational:

PERSONALITY GUIDELINES:
- Be warm and engaging like talking to a knowledgeable colleague
- Use natural spoken language patterns: "I see you have..." instead of "Database contains..."
- Show enthusiasm and interest: "Great question!", "That's interesting!", "Let me check that for you..."
- Convert technical language to friendly explanations
- Keep responses concise but complete for voice delivery

TECHNICAL DATA TRANSLATION:
- "Database contains X items" → "I found X items in your system"
- "Query returned..." → "Here's what I discovered..."
- "Analysis shows..." → "Looking at this, I can see..."
- "Error occurred..." → "I noticed a small issue..."
- "Process completed..." → "All done! I've finished that for you"
- "File uploaded..." → "Got your file! Let me take a look..."

VOICE-FRIENDLY RESPONSES:
- Begin responses naturally: "I found...", "Looking at your data...", "Based on what I see..."
- Use contractions and conversational flow
- Avoid reading markdown formatting aloud
- Explain technical details in plain language
- End with helpful next steps when appropriate

Remember: Maintain all factual accuracy while making the delivery natural and engaging for voice conversation."""

                final_prompt += voice_personality

            logger.info(f"Built bulletproof system prompt (web_search_enabled={web_search_enabled}, context_parts={len(context_parts)}, voice_mode={voice_mode})")

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
            
            # Add voice personality to fallback if needed
            if voice_mode:
                fallback_prompt += """

🎤 VOICE MODE: Respond conversationally and naturally. Use friendly language like "I found..." instead of technical terms. Be warm and engaging while maintaining accuracy."""
            
            return fallback_prompt

    def _retrieve_entity_context(self, query: str, max_chunks: int = 3, project_id: int = None) -> List[Dict[str, Any]]:
        """Always retrieve entity context for universal RAG access to CLIENT/WEBSITE/FILE/PROJECT data"""
        entity_context = []
        try:
            from backend.services.indexing_service import search_with_llamaindex

            # Search specifically for entity summaries with high priority
            entity_results = search_with_llamaindex(f"entity_summary {query}", max_chunks=max_chunks * 3, project_id=project_id)

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

    def _retrieve_relevant_context(self, query: str, session_id: str, max_chunks: int = 5, project_id: int = None) -> List[Dict[str, Any]]:
        """Retrieve relevant context from the knowledge base, including entity context and uploaded files"""
        try:
            logger.info(f"DEBUG: _retrieve_relevant_context called with query: '{query}'")
            # UNIVERSAL RAG: Always retrieve entity context for CLIENT/WEBSITE/FILE/PROJECT access
            entity_context = self._retrieve_entity_context(query, max_chunks=2, project_id=project_id)
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
            # When user explicitly asks for a project file (e.g. "read backend/config.py"), inject actual file content
            project_file_chunk = self._detect_and_read_project_file(original_query)
            if project_file_chunk:
                logger.info(f"DEBUG: Injected project file context for query")
            uploaded_file_context = self._retrieve_uploaded_files_context(original_query, session_id, project_id=project_id)
            if project_file_chunk:
                uploaded_file_context = [project_file_chunk] + uploaded_file_context
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
                    search_results = search_with_llamaindex(query, max_chunks=max_chunks * 2, project_id=project_id)
                    logger.info(f"DEBUG: search_with_llamaindex returned {len(search_results) if search_results else 0} results (project_id={project_id})")

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
            
            # CRITICAL FIX: Ensure uploaded file context is always included
            if uploaded_file_chunks and not context_chunks:
                context_chunks = uploaded_file_chunks[:max_chunks]
                logger.info(f"CRITICAL FIX: Using uploaded file context as fallback: {len(context_chunks)} chunks")
            
            # DEBUG: Log what we're actually returning
            logger.info(f"DEBUG: Final context_chunks length: {len(context_chunks)}")
            logger.info(f"DEBUG: uploaded_file_chunks: {len(uploaded_file_chunks)}")
            logger.info(f"DEBUG: entity_context: {len(entity_context)}")
            logger.info(f"DEBUG: additional_entity_chunks: {len(additional_entity_chunks)}")
            logger.info(f"DEBUG: document_chunks: {len(document_chunks)}")
            logger.info(f"DEBUG: all_contexts total: {len(all_contexts)}")

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

    def _create_chat_engine(self, session_id: str, model_name: str, web_search_context: str = "", chat_mode: str = None, voice_mode: bool = False, bypass_rules: bool = False, project_id: int = None):
        """Create or get cached chat engine for session with web search context"""
        if session_id in self.chat_engines:
            cached_project = getattr(self.chat_engines[session_id], '_project_id', None)
            if cached_project == project_id:
                return self.chat_engines[session_id]
            else:
                del self.chat_engines[session_id]

        try:
            # Get model configuration
            model_config = self._get_model_config(model_name)

            # Derive memory token limit from actual LLM context window (not model config)
            llm_instance = current_app.config.get('LLAMA_INDEX_LLM')
            actual_ctx = getattr(llm_instance, 'context_window', 8192) if llm_instance else 8192
            memory_token_limit = max(1024, int(actual_ctx * 0.6))
            logger.info(f"Chat engine memory limit: {memory_token_limit} (context_window={actual_ctx})")

            # Try to get index and create retriever with vector store validation
            retriever = None
            try:
                if self.index_manager:
                    index, _ = self.index_manager.get_index()
                    
                    # Validate vector store has content before creating retriever
                    if self._validate_vector_store_content(index):
                        if project_id is not None:
                            try:
                                from llama_index.core.vector_stores.types import MetadataFilters, MetadataFilter, FilterOperator
                                metadata_filters = MetadataFilters(
                                    filters=[MetadataFilter(key="project_id", value=str(project_id), operator=FilterOperator.EQ)]
                                )
                                base_retriever = index.as_retriever(similarity_top_k=3, filters=metadata_filters)
                            except Exception:
                                base_retriever = index.as_retriever(similarity_top_k=3)
                        else:
                            base_retriever = index.as_retriever(similarity_top_k=3)
                        retriever = base_retriever
                        logger.info(f"Using enhanced chat with RAG capabilities (project_id={project_id})")
                    else:
                        logger.info("Vector store is empty, using basic chat mode")
                        retriever = None
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
                except Exception:
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
                        token_limit=memory_token_limit
                    )
                    logger.info(f"Memory buffer created with {len(chat_history)} messages")
                except Exception as memory_error:
                    logger.warning(f"Failed to create memory with chat history: {memory_error}")
                    memory = ChatMemoryBuffer.from_defaults(token_limit=memory_token_limit)

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
            system_prompt = self._build_system_prompt(model_name, context_info, simple_mode=False, web_search_context=web_search_context, chat_mode=chat_mode, voice_mode=voice_mode, bypass_rules=bypass_rules)

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

            # Cache the engine with project_id tag
            chat_engine._project_id = project_id
            self.chat_engines[session_id] = chat_engine

            return chat_engine

        except Exception as e:
            logger.error(f"Error creating chat engine: {e}")
            raise

    def _validate_vector_store_content(self, index) -> bool:
        """Validate that the vector store has content to avoid AssertionError"""
        try:
            # Check if index has any documents
            if hasattr(index, 'docstore') and hasattr(index.docstore, 'docs'):
                doc_count = len(index.docstore.docs)
                logger.debug(f"Vector store validation: {doc_count} documents found")
                return doc_count > 0
            
            # Check if vector store has embeddings
            if hasattr(index, '_vector_store') and hasattr(index._vector_store, 'data'):
                vector_count = len(getattr(index._vector_store.data, 'embedding_dict', {}))
                logger.debug(f"Vector store validation: {vector_count} vectors found")
                return vector_count > 0
                
            # Default fallback - try a simple query to see if it works
            return True
            
        except Exception as e:
            logger.warning(f"Vector store validation failed: {e}")
            return False
    
    def _create_simple_chat_engine(self, session_id: str, model_name: str, web_search_context: str = "", voice_mode: bool = False, bypass_rules: bool = False):
        """Create a simple chat engine without RAG capabilities but with web search context"""
        if session_id in self.chat_engines:
            return self.chat_engines[session_id]

        try:
            # Get model configuration
            model_config = self._get_model_config(model_name)

            # Derive memory token limit from actual LLM context window (not model config)
            llm_instance = current_app.config.get('LLAMA_INDEX_LLM')
            actual_ctx = getattr(llm_instance, 'context_window', 8192) if llm_instance else 8192
            memory_token_limit = max(1024, int(actual_ctx * 0.6))
            logger.info(f"Simple chat engine memory limit: {memory_token_limit} (context_window={actual_ctx})")

            # Create or retrieve persistent memory buffer with chat history
            if session_id in self.session_memories:
                memory = self.session_memories[session_id]
                # Handle both old and new ChatMemoryBuffer API
                try:
                    history_length = len(memory.chat_history) if hasattr(memory, 'chat_history') else len(memory.chat_store.store)
                except Exception:
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
                        token_limit=memory_token_limit
                    )
                    logger.info(f"Memory buffer created with {len(chat_history)} messages")
                except Exception as memory_error:
                    logger.warning(f"Failed to create memory with chat history: {memory_error}")
                    memory = ChatMemoryBuffer.from_defaults(token_limit=memory_token_limit)

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
            system_prompt = self._build_system_prompt(model_name, context_info, simple_mode=True, web_search_context=web_search_context, voice_mode=voice_mode, bypass_rules=bypass_rules)

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

    def _get_or_create_session(self, session_id: str, project_id: int = None) -> Any:
        """Get or create a chat session"""
        try:
            # Import db within Flask app context
            from backend.models import db, LLMSession
            session = db.session.get(LLMSession, session_id)
            if not session:
                try:
                    session = LLMSession(id=session_id, user="default", project_id=project_id)
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
            # Update project_id on existing session if provided and not set
            if session and project_id and not session.project_id:
                try:
                    session.project_id = project_id
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            return session
        except Exception as e:
            logger.error(f"Error in _get_or_create_session: {e}")
            # Create a minimal session object if database fails
            class MinimalSession:
                def __init__(self, session_id):
                    self.id = session_id
                    self.user = "default"
            return MinimalSession(session_id)

    def _save_message(self, session_id: str, role: str, content: str, project_id: int = None, extra_data: dict = None) -> Optional[int]:
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
                    project_id=project_id,
                    role=role,
                    content=content,
                    extra_data=extra_data,
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

    def _try_media_command(self, session_id: str, message: str,
                           project_id: int = None) -> Optional[Dict]:
        """Check if message is a media command and execute directly, bypassing LLM."""
        import re
        msg = message.strip()
        msg_lower = msg.lower()

        # Determine tool and params from message
        tool_name = None
        params = {}

        # Play commands
        play_match = re.match(r"^(?:please\s+)?play\s+(.+)", msg, re.IGNORECASE | re.DOTALL)
        if play_match:
            tool_name = "media_play"
            params = {"query": play_match.group(1).strip()}
        # Pause / stop / resume
        elif re.match(r"^(?:please\s+)?(pause|stop|resume)(?:\s|$)", msg, re.IGNORECASE):
            m = re.match(r"^(?:please\s+)?(pause|stop|resume)", msg, re.IGNORECASE)
            action = m.group(1).lower()
            tool_name = "media_control"
            params = {"action": "toggle" if action == "resume" else action}
        # Next / skip / previous
        elif re.match(r"^(?:please\s+)?(next|skip|previous|prev)(?:\s|$)", msg, re.IGNORECASE):
            m = re.match(r"^(?:please\s+)?(next|skip|previous|prev)", msg, re.IGNORECASE)
            action = m.group(1).lower()
            tool_name = "media_control"
            params = {"action": "next" if action in ("next", "skip") else "previous"}
        # What's playing
        elif re.match(r"^(?:what'?s|what\s+is)\s+(?:this\s+)?(?:playing|this\s+song)", msg, re.IGNORECASE):
            tool_name = "media_status"
        elif re.match(r"^(?:current|now)\s+(?:playing|song|track)", msg, re.IGNORECASE):
            tool_name = "media_status"
        # Volume with number
        elif re.match(r"^(?:set\s+)?volume\s+(?:to\s+)?(\d+)", msg, re.IGNORECASE):
            vol_match = re.search(r"(\d+)", msg)
            tool_name = "media_volume"
            params = {"level": vol_match.group(1)}
        # Volume up/down
        elif re.match(r"^(?:turn\s+)?(?:the\s+)?volume\s+(up|down)", msg, re.IGNORECASE):
            tool_name = "media_volume"
            params = {"level": "+10" if "up" in msg_lower else "-10"}
        elif msg_lower in ("louder", "quieter", "softer"):
            tool_name = "media_volume"
            params = {"level": "+10" if msg_lower == "louder" else "-10"}
        elif re.match(r"^(mute|unmute)", msg, re.IGNORECASE):
            tool_name = "media_volume"
            params = {"level": "mute" if msg_lower.startswith("mute") else "unmute"}

        if not tool_name:
            return None

        # Get tool registry (same pattern as tools_api.py)
        try:
            from flask import current_app
            registry = None
            if hasattr(current_app, 'tool_registry') and current_app.tool_registry:
                registry = current_app.tool_registry
            if not registry:
                from backend.tools import initialize_all_tools
                registry = initialize_all_tools()
            if not registry or not registry.get_tool(tool_name):
                return None
        except Exception:
            return None

        logger.info(f"Media direct intercept: {tool_name}({params})")

        # Save user message
        self._get_or_create_session(session_id, project_id=project_id)
        self._save_message(session_id, 'user', message, project_id=project_id)

        # Execute
        try:
            result = registry.execute_tool(tool_name, **params)
        except Exception as e:
            error_msg = f"Media command failed: {e}"
            self._save_message(session_id, 'assistant', error_msg, project_id=project_id)
            return {'response': error_msg, 'session_id': session_id, 'enhanced': True}

        if result.success:
            response = str(result.output)
        else:
            response = f"Sorry, that didn't work: {result.error}"

        self._save_message(session_id, 'assistant', response, project_id=project_id)
        return {'response': response, 'session_id': session_id, 'enhanced': True}

    def _is_simple_message(self, message: str) -> bool:
        """Detect if a message is simple and doesn't need RAG processing"""
        message_lower = message.lower().strip()

        # Complex keywords that indicate RAG/analysis is needed
        complex_keywords = [
            'analyze', 'analysis', 'examine', 'review', 'document', 'file', 'csv', 'data',
            'generate', 'create', 'build', 'make', 'produce', 'explain', 'describe',
            'improve', 'optimize', 'fix', 'debug', 'code', 'script', 'programming',
            'best practices', 'how to', 'tell me about', 'research',
            'compare', 'evaluate', 'assess', 'implementation', 'strategy',
            'what files', 'what documents', 'what data', 'what content', 'what information'
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
        """CHANGE 3: More permissive detection - trigger on any question or query that might need current info"""
        message_lower = message.lower().strip()

        # Clear indicators that current/real-time information is needed
        current_indicators = [
            'current', 'today', 'todays', 'now', 'latest', 'recent',
            'what is', 'what are', 'check', 'find', 'search',
            'website', 'site', 'www.', 'http', '.com', '.org', '.net',
            'temperature', 'weather', 'forecast', 'time', 'date',
            'duckduckgo', 'ddg', 'google', 'search for', 'look up',
            'stock price', 'sports score', 'lottery', 'news about'
        ]

        # URL pattern detection
        import re
        has_url = bool(re.search(r'(?:https?://|www\.)[^\s]+', message))

        # Check for current information indicators
        needs_current_info = any(indicator in message_lower for indicator in current_indicators)

        # Question words that often need current information - EXPANDED
        question_patterns = [
            r'what.*(?:is|are|was|were)',
            r'how.*(?:is|are|was|were|to|do|does|did)',
            r'when.*(?:did|will|is|was|are)',
            r'where.*(?:is|are|was|were|can|to)',
            r'who.*(?:is|are|was|were)',
            r'why.*(?:is|are|was|were|do|does|did)',
            r'can you.*(?:find|check|search|get|tell)',
            r'please.*(?:find|check|search|get|tell)'
        ]

        has_question_pattern = any(re.search(pattern, message_lower) for pattern in question_patterns)

        # Check if message ends with question mark
        has_question_mark = message.strip().endswith('?')

        # CHANGE 3: More permissive - trigger on questions, current info indicators, URLs, or longer queries
        # Also check if web access is enabled - if so, be more aggressive about searching
        from backend.utils.settings_utils import get_web_access
        web_access_enabled = get_web_access()
        
        # If web access is enabled, be more permissive
        if web_access_enabled:
            # Trigger on any question, current info indicator, URL, or query with 4+ words
            result = has_url or needs_current_info or has_question_pattern or has_question_mark or len(message.split()) >= 4
        else:
            # If disabled, only trigger on clear indicators
            result = has_url or needs_current_info or has_question_pattern

        if result:
            logger.info(f"Web search ENABLED for: '{message[:50]}...' (web_access_setting={web_access_enabled})")
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
            logger.info(f"CHANGE 5: Starting web search for query: '{query[:100]}...'")
            search_results = enhanced_web_search(query)

            if search_results.get("success"):
                data = search_results.get("data", {})
                strategy = search_results.get("strategy_used", "unknown")

                logger.info(f"CHANGE 5: Web search successful using {strategy}, data type: {data.get('type', 'unknown')}")

                # Format results for LLM context
                formatted_context = self._format_web_search_context(search_results, query)
                logger.info(f"CHANGE 5: Web search context formatted, length: {len(formatted_context)} characters")

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
        """CHANGE 4: Format web search results for LLM context with clear source attribution"""
        data = search_results.get("data", {})
        strategy = search_results.get("strategy_used", "unknown")

        context_parts = [f"=== WEB SEARCH RESULTS FOR: {original_query} ==="]
        context_parts.append(f"Search Strategy: {strategy}")
        context_parts.append(f"Search Timestamp: {datetime.now().isoformat()}")
        context_parts.append("")
        context_parts.append("IMPORTANT: Use this real-time web search information to answer the user's question. This is current, up-to-date information from the internet.")

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
            context_parts.append(f"WEATHER INFORMATION (CURRENT/REAL-TIME):")
            context_parts.append(f"Location: {data.get('location', 'N/A')}")
            context_parts.append(f"Temperature: {data.get('temperature_fahrenheit', 'N/A')}°F ({data.get('temperature_celsius', 'N/A')}°C)")
            context_parts.append(f"Conditions: {data.get('description', 'N/A')}")
            context_parts.append(f"Humidity: {data.get('humidity', 'N/A')}%")
            context_parts.append("NOTE: This is current weather data retrieved from the web.")

        elif data_type == "search_results":
            # CHANGE 4: Handle DuckDuckGo search results properly
            context_parts.append(f"WEB SEARCH RESULTS (DuckDuckGo):")
            context_parts.append(f"Source: {data.get('source', 'DuckDuckGo')}")
            if data.get('results'):
                context_parts.append("")
                context_parts.append("Search Results:")
                for idx, result in enumerate(data.get('results', [])[:5], 1):
                    context_parts.append(f"{idx}. {result.get('title', 'N/A')}")
                    if result.get('url'):
                        context_parts.append(f"   URL: {result.get('url')}")
                    if result.get('snippet'):
                        context_parts.append(f"   Summary: {result.get('snippet')}")
                    context_parts.append("")
            if data.get('snippet'):
                context_parts.append(f"Combined Information: {data.get('snippet')}")

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
        context_parts.append("")
        context_parts.append("INSTRUCTION: Answer the user's question using the web search results above. Cite the sources when referencing this information.")

        formatted = "\n".join(context_parts)
        logger.info(f"CHANGE 4: Formatted web search context (length: {len(formatted)}, type: {data_type})")
        return formatted

    def process_chat_message(self, session_id: str, message: str,
                           use_rag: bool = True, debug_mode: bool = False, simple_mode: bool = False,
                           chat_mode: str = None, voice_mode: bool = False, bypass_rules: bool = False,
                           project_id: int = None) -> Dict[str, Any]:
        """Process a chat message with enhanced features"""
        start_time = datetime.now()

        # FIX BUG 4: Update session activity and perform cleanup
        self._update_session_activity(session_id)
        self._cleanup_old_sessions()

        try:
            # ── Media command shortcut: bypass LLM for play/pause/volume/status ──
            media_result = self._try_media_command(session_id, message, project_id)
            if media_result is not None:
                return media_result

            from backend.utils.prompt_utils import enhance_message_with_time
            enhanced_message = enhance_message_with_time(message)

            logger.info(f"Enhanced chat: process_chat_message called with session_id={session_id}, message='{message[:50]}...', use_rag={use_rag}, simple_mode={simple_mode}, bypass_rules={bypass_rules}")

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
            # Skip intent detection if bypass_rules is enabled (for code intelligence tasks)
            if bypass_rules:
                logger.info(f"Enhanced chat: Rules bypass enabled, skipping intent detection and routing to regular chat")
                detected_intent = "general_chat"
            else:
                logger.debug(f"FLOW DEBUG: About to call rule-based intent detection...")
                logger.info(f"Enhanced chat: About to call rule-based intent detection...")
                try:
                    detected_intent = self._detect_intent_with_rules(enhanced_message)
                    logger.info(f"Enhanced chat: Rule-based detected intent: {detected_intent}")
                    logger.info(f"DEBUG: Intent detection for message: '{message[:100]}...' -> {detected_intent}")
                except Exception as intent_error:
                    logger.error(f"Enhanced chat: Intent detection failed: {intent_error}")
                    import traceback
                    logger.error(f"Enhanced chat: Intent detection traceback: {traceback.format_exc()}")
                    detected_intent = "general_chat"  # Safe fallback
                    logger.info(f"Enhanced chat: Using fallback intent: {detected_intent}")

            # Route to appropriate handler based on detected intent
            # Skip special routing when bypass_rules is enabled
            if not bypass_rules:
                logger.info(f"DEBUG: Routing based on detected intent: {detected_intent}")
                if detected_intent == "explicit_file_generation":
                    logger.info(f"DEBUG: Routing to file generation handler")
                    return self._handle_file_generation_request(session_id, enhanced_message, project_id=project_id)
                elif detected_intent == "file_analysis":
                    logger.info(f"DEBUG: Routing to file analysis handler")
                    return self._handle_file_analysis_request(session_id, enhanced_message, project_id=project_id)
                elif detected_intent == "file_improvement":
                    logger.info(f"DEBUG: Routing to file improvement handler")
                    return self._handle_file_improvement_request(session_id, enhanced_message, project_id=project_id)
                elif detected_intent == "bulk_csv_generation":
                    logger.info(f"DEBUG: Routing to bulk CSV generation handler")
                    return self._handle_file_generation_request(session_id, enhanced_message, project_id=project_id)
                elif detected_intent == "website_analysis":
                    logger.info(f"DEBUG: Routing to website analysis handler")
                    return self._handle_website_analysis_request(session_id, enhanced_message, project_id=project_id)
                elif detected_intent == "file_generation":
                    logger.info(f"DEBUG: Routing to file generation handler")
                    return self._handle_file_generation_request(session_id, enhanced_message, project_id=project_id)
            # For general_chat and other intents, proceed with regular chat
            logger.info(f"DEBUG: Routing to general chat handler (intent: {detected_intent})")

            # Regular chat processing (includes uploaded file discussion)
            logger.info(f"Enhanced chat: Proceeding with regular chat processing...")
            logger.info(f"DEBUG: About to call _process_regular_chat with use_rag={use_rag}, simple_mode={simple_mode}, bypass_rules={bypass_rules}")
            return self._process_regular_chat(session_id, message, use_rag, debug_mode, simple_mode, start_time, chat_mode, voice_mode, bypass_rules, project_id=project_id)

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

    def _handle_file_analysis_request(self, session_id: str, message: str, project_id: int = None) -> Dict[str, Any]:
        """Handle file analysis requests"""
        start_time = datetime.now()
        try:
            # First try session-specific documents
            documents = self._get_session_documents(session_id)

            # If no session documents found, try enhanced file retrieval for any uploaded code files
            if not documents:
                logger.info(f"No session documents found, trying enhanced file retrieval for query: '{message}'")
                file_contexts = self._retrieve_uploaded_files_context(message, session_id, project_id=project_id)

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

    def _handle_website_analysis_request(self, session_id: str, message: str, project_id: int = None) -> Dict[str, Any]:
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

    def _handle_file_generation_request(self, session_id: str, message: str, project_id: int = None) -> Dict[str, Any]:
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

    def _handle_file_improvement_request(self, session_id: str, message: str, project_id: int = None) -> Dict[str, Any]:
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
        except (ValueError, KeyError, TypeError):
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

    def _process_regular_chat(self, session_id: str, message: str, use_rag: bool, debug_mode: bool, simple_mode: bool, start_time: datetime = None, chat_mode: str = None, voice_mode: bool = False, bypass_rules: bool = False, project_id: int = None) -> Dict[str, Any]:
        """Process a regular chat message with enhanced features including bulletproof web search integration"""

        if start_time is None:
            start_time = datetime.now()

        try:
            import sys, os
            sys.path.append(os.path.dirname(os.path.dirname(__file__)))
            from backend.utils.prompt_utils import enhance_message_with_time
            enhanced_message = enhance_message_with_time(message)

            logger.info(f"Enhanced chat: process_chat_message called with session_id={session_id}, message='{message[:50]}...', use_rag={use_rag}, simple_mode={simple_mode}")

            # SMART ROUTING SYSTEM - Route query before heavy processing
            if classify_user_intent and not simple_mode:
                try:
                    intent_type, confidence, intent_metadata = classify_user_intent(message)
                    logger.info(f"Smart Router: Intent detected - {intent_type.value} (confidence: {confidence:.2f})")
                    
                    # Route DATABASE_QUERY to fast handler
                    if intent_type == IntentType.DATABASE_QUERY and confidence > 0.6:
                        logger.info("Smart Router: Routing to database handler for fast response")
                        try:
                            from backend.models import db
                            if create_database_handler and db and db.session:
                                db_handler = create_database_handler(db.session)
                                db_result = db_handler.handle_query(message, intent_metadata)

                                if db_result.get('success'):
                                    # Ensure session exists before saving messages (FK constraint)
                                    self._get_or_create_session(session_id, project_id=project_id)
                                    # Persist both user and assistant messages before returning
                                    self._save_message(session_id, 'user', message, project_id=project_id)
                                    self._save_message(session_id, 'assistant', db_result['response'], project_id=project_id)
                                    return {
                                        'response': db_result['response'],
                                        'session_id': session_id,
                                        'enhanced': True,
                                        'router_used': 'database_handler',
                                        'execution_time': 'fast',
                                        'intent': intent_type.value,
                                        'confidence': confidence
                                    }
                            else:
                                logger.warning("Database handler not available, falling back to RAG")
                        except Exception as db_error:
                            logger.warning(f"Database handler failed, falling back to RAG: {db_error}")
                except Exception as router_error:
                    logger.warning(f"Smart router failed, using default processing: {router_error}")
                    intent_type = None
                    context_limit = 100000
                
                # Route COMMAND using existing Rules system
                if intent_type == IntentType.COMMAND and intent_metadata.get('command'):
                    command = intent_metadata.get('command', 'unknown')
                    logger.info(f"Smart Router: Processing command /{command} with Rules system")
                    try:
                        from backend import rule_utils
                        from backend.models import db
                        
                        # Check if there's a specific rule for this command
                        if hasattr(rule_utils, 'get_active_command_rule'):
                            command_rule = rule_utils.get_active_command_rule(command, db.session)
                            if command_rule:
                                logger.info(f"Smart Router: Found rule for /{command}, using enhanced processing")
                                intent_metadata['has_command_rule'] = True
                                intent_metadata['command_rule_id'] = command_rule.id if hasattr(command_rule, 'id') else None
                            else:
                                logger.info(f"Smart Router: No specific rule for /{command}, using default command processing")
                                intent_metadata['has_command_rule'] = False
                        else:
                            logger.warning(f"Command rule lookup not available, using fallback processing")
                            intent_metadata['has_command_rule'] = False
                            intent_metadata['command_fallback'] = True
                        
                        # Mark this as a command for downstream processing
                        intent_metadata['enhanced_command_processing'] = True
                        intent_metadata['command_name'] = command
                        
                    except Exception as cmd_error:
                        logger.warning(f"Command rule lookup failed: {cmd_error}")
                        intent_metadata['command_fallback'] = True
                        intent_metadata['enhanced_command_processing'] = True

                # CRITICAL FIX: Route WEB_SEARCH intent to trigger web search
                if intent_type == IntentType.WEB_SEARCH:
                    logger.info(f"Smart Router: WEB_SEARCH intent detected (confidence: {confidence:.2f}) - will trigger web search")
                    intent_metadata['force_web_search'] = True
                    intent_metadata['web_search_keywords'] = intent_metadata.get('keywords_found', [])

                # Apply smart context limits based on intent
                if intent_type and get_intent_context_limit:
                    context_limit = get_intent_context_limit(intent_type)
                    logger.info(f"Smart Router: Context limit for {intent_type.value}: {context_limit}")
                else:
                    context_limit = 100000
                    
                # Store intent metadata for later use in processing
                if 'intent_metadata' not in locals():
                    intent_metadata = {}
            else:
                # Default context limit when router is not available
                context_limit = 100000
                intent_metadata = {}

            # Get or create session
            logger.info(f"Enhanced chat: Getting or creating session...")
            session = self._get_or_create_session(session_id, project_id=project_id)
            logger.info(f"Enhanced chat: Session obtained: {session.id}")

            # Save user message first for memory persistence
            user_message_id = self._save_message(session_id, 'user', message, project_id=project_id)

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

            # Check if web search is needed (from intent classifier OR pattern detection)
            force_web_search = intent_metadata.get('force_web_search', False) if 'intent_metadata' in locals() else False
            should_web_search = force_web_search or self._should_use_web_search(enhanced_message)

            if not simple_mode and should_web_search:
                logger.info(f"CHANGE 5: Web search triggered for query")
                logger.info(f"CHANGE 5: Original message to search: '{message}'")
                logger.info(f"CHANGE 5: Enhanced message (not used for search): '{enhanced_message[:200]}...'")
                web_search_result = self._perform_web_search_safe(message)  # Use original message, not enhanced
                web_search_used = True

                if web_search_result.get("success"):
                    web_search_context = web_search_result.get("formatted_context", "")
                    logger.info(f"CHANGE 5: Web search successful, context length: {len(web_search_context)}")
                    logger.info(f"CHANGE 5: Web search context preview: {web_search_context[:200]}...")
                else:
                    # Web search failed - add transparent error message to context
                    error_message = web_search_result.get("user_message", "Web search failed")
                    web_search_context = f"=== WEB SEARCH STATUS ===\n{error_message}\n=== END WEB SEARCH STATUS ==="
                    logger.warning(f"CHANGE 5: Web search failed: {web_search_result.get('error', 'Unknown error')}")
            else:
                logger.info(f"CHANGE 5: Web search not triggered (simple_mode={simple_mode}, should_use={self._should_use_web_search(enhanced_message) if not simple_mode else False})")

            # Retrieve relevant RAG context if enabled and not in simple mode
            rag_context = []
            logger.info(f"DEBUG: RAG check - use_rag={use_rag}, simple_mode={simple_mode}")
            if use_rag and not simple_mode:
                logger.info(f"Enhanced chat: Attempting RAG context retrieval...")
                logger.info(f"DEBUG: use_rag={use_rag}, simple_mode={simple_mode}")
                try:
                    logger.info(f"DEBUG: About to call _retrieve_relevant_context (project_id={project_id})")
                    rag_context = self._retrieve_relevant_context(enhanced_message, session_id, project_id=project_id)
                    logger.info(f"Enhanced chat: RAG context retrieved: {len(rag_context)} chunks")
                    logger.info(f"DEBUG: rag_context type: {type(rag_context)}")
                    if rag_context:
                        logger.info(f"DEBUG: First chunk keys: {list(rag_context[0].keys()) if rag_context else 'None'}")
                except Exception as rag_error:
                    logger.error(f"RAG context retrieval failed with exception: {rag_error}")
                    import traceback
                    logger.error(f"RAG error traceback: {traceback.format_exc()}")

                    # BUG FIX 5: Implement graceful fallback with context preservation
                    logger.warning("BUG FIX 5: RAG failed, implementing graceful fallback with context preservation")

                    # Try to preserve minimal context from previous conversation
                    try:
                        # Get recent conversation history as fallback context
                        memory = self.session_memories.get(session_id)
                        if memory and hasattr(memory, 'chat_store') and memory.chat_store.store:
                            recent_messages = []
                            for msg in list(memory.chat_store.store.values())[-3:]:  # Last 3 messages
                                if hasattr(msg, 'content'):
                                    recent_messages.append({
                                        'content': msg.content[:200],  # Truncate to prevent overflow
                                        'type': 'conversation_history',
                                        'metadata': {'source': 'fallback_context', 'role': getattr(msg, 'role', 'unknown')}
                                    })

                            if recent_messages:
                                rag_context = recent_messages
                                logger.info(f"BUG FIX 5: Preserved {len(recent_messages)} conversation messages as fallback context")
                            else:
                                rag_context = []
                                simple_mode = True
                        else:
                            rag_context = []
                            simple_mode = True

                    except Exception as fallback_error:
                        logger.error(f"BUG FIX 5: Fallback context preservation failed: {fallback_error}")
                        simple_mode = True
                        rag_context = []
            else:
                logger.info(f"DEBUG: Skipping RAG - use_rag={use_rag}, simple_mode={simple_mode}")

            # User message already saved earlier in function (line 2027)
            logger.info(f"Enhanced chat: User message already saved earlier")

            # Get conversation context from session messages (memory) with smart selection
            conversation_context = []
            logger.info(f"Enhanced chat: Getting conversation context with smart selection...")
            
            # First try to get from session messages (in-memory) using MemoryManager for smart context
            if session_id in self.session_messages:
                session_msgs = self.session_messages[session_id]
                logger.info(f"Enhanced chat: Found {len(session_msgs)} messages in session_messages")
                
                # Use MemoryManager for intelligent message selection if available
                if self._memory_manager:
                    try:
                        # Convert to format MemoryManager expects
                        formatted_msgs = []
                        for msg in session_msgs:
                            if hasattr(msg, 'role') and hasattr(msg, 'content'):
                                formatted_msgs.append({
                                    'role': msg.role.value if hasattr(msg.role, 'value') else str(msg.role),
                                    'content': msg.content,
                                    'timestamp': getattr(msg, 'timestamp', datetime.now())
                                })
                        
                        # Get smart context (20 messages based on importance, not just last 5)
                        smart_context = self._memory_manager.get_smart_context(
                            session_id=session_id,
                            messages=formatted_msgs,
                            max_messages=20,  # Increased from 5 for better context retention
                            max_tokens=8000
                        )
                        
                        # Add entity context for the query
                        if self._entity_enhancer:
                            try:
                                entity_context = self._entity_enhancer.get_entity_context_for_chat(message)
                                if entity_context.get('has_entities'):
                                    conversation_context.append({
                                        'role': 'system',
                                        'content': f"ENTITY CONTEXT:\n{entity_context['context']}"
                                    })
                                    logger.info(f"Enhanced chat: Added entity context with {entity_context.get('entity_count', 0)} entities")
                            except Exception as e:
                                logger.debug(f"Entity context extraction failed: {e}")
                        
                        # Add smart context messages
                        conversation_context.extend(smart_context)
                        
                        logger.info(f"Enhanced chat: MemoryManager provided {len(smart_context)} smart context messages (from {len(formatted_msgs)} total)")
                        
                    except Exception as e:
                        logger.warning(f"MemoryManager smart context failed: {e}, falling back to simple selection")
                        # Fallback to last 20 messages (still better than 5)
                        for msg in session_msgs[-20:]:
                            if hasattr(msg, 'role') and hasattr(msg, 'content'):
                                conversation_context.append({
                                    'role': msg.role.value if hasattr(msg.role, 'value') else str(msg.role),
                                    'content': msg.content
                                })
                        logger.info(f"Enhanced chat: Fallback - using last {len(conversation_context)} messages")
                else:
                    # No MemoryManager - use last 20 messages (still better than 5)
                    for msg in session_msgs[-20:]:
                        if hasattr(msg, 'role') and hasattr(msg, 'content'):
                            conversation_context.append({
                                'role': msg.role.value if hasattr(msg.role, 'value') else str(msg.role),
                                'content': msg.content
                            })
                    logger.info(f"Enhanced chat: No MemoryManager - using last {len(conversation_context)} messages")
            
            # Fallback to database if no in-memory session messages
            if not conversation_context:
                logger.info(f"Enhanced chat: No in-memory session messages, trying database fallback...")
                try:
                    from backend.models import db, LLMMessage
                    db_messages = db.session.query(LLMMessage).filter(
                        LLMMessage.session_id == session_id
                    ).order_by(LLMMessage.timestamp.desc()).limit(20).all()
                    
                    if db_messages:
                        for msg in reversed(db_messages):
                            conversation_context.append({
                                'role': msg.role,
                                'content': msg.content
                            })
                        logger.info(f"Enhanced chat: Loaded {len(conversation_context)} messages from database fallback")
                    else:
                        logger.info(f"Enhanced chat: No messages found in database for session {session_id}")
                except Exception as e:
                    logger.warning(f"Database fallback failed: {e}")
            
            # Final fallback to context manager if still no context
            if not conversation_context and self.context_manager:
                logger.info(f"Enhanced chat: Trying context manager as final fallback...")
                try:
                    context_string = self.context_manager.get_context(
                        session_id,
                        max_tokens=model_config['max_context_tokens'] // 3  # Reserve space for response
                    )
                    if context_string:
                        # Parse the context string into conversation format
                        conversation_context = self._parse_context_string(context_string)
                        logger.info(f"Enhanced chat: Context manager provided context string, parsed into {len(conversation_context)} conversation items")
                    else:
                        logger.info(f"Enhanced chat: Context manager returned empty string")
                except Exception as e:
                    logger.warning(f"Context manager failed: {e}, using empty context")
            
            if not conversation_context:
                logger.info(f"Enhanced chat: No conversation context available from any source, starting fresh")

            # ENHANCED PROMPT BUILDING WITH WEB SEARCH + RAG INTEGRATION
            logger.info(f"Enhanced chat: Creating enhanced prompt...")
            enhanced_message_with_context = enhanced_message

            # Combine web search and RAG contexts
            all_context_parts = []

            # Add web search context first (highest priority for current information)
            if web_search_context:
                all_context_parts.append(web_search_context)
                logger.info(f"Enhanced chat: Added web search context to prompt")

            # Add conversation context (for memory/previous messages)
            if conversation_context and not simple_mode:
                # Use all smart context messages (up to 20) - MemoryManager already selected the important ones
                conversation_text = "\n\n".join(
                    f"[{msg['role'].upper()}: {msg['content']}]"
                    for msg in conversation_context  # Use all smart context messages
                )
                if conversation_text:
                    all_context_parts.append(f"=== CONVERSATION HISTORY ===\n{conversation_text}\n=== END CONVERSATION HISTORY ===")
                    logger.info(f"Enhanced chat: Added {len(conversation_context)} conversation context messages to prompt")

            # Add RAG context third (for document/knowledge base information)
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
                
                # ENHANCED: Add code-specific prompting when code files are detected
                code_analysis_prompt = ""
                has_code_files = False
                
                # Check if we have code files in the RAG context (which includes uploaded files)
                if rag_context:
                    code_extensions = {'jsx', 'js', 'ts', 'tsx', 'py', 'php', 'java', 'cpp', 'c', 'go', 'rs', 'rb', 'swift', 'css', 'html', 'json', 'xml', 'yaml', 'sql'}
                    for ctx in rag_context:
                        filename = ctx.get('metadata', {}).get('source_document', '')
                        if filename and '.' in filename:
                            file_extension = filename.split('.')[-1].lower()
                            if file_extension in code_extensions:
                                has_code_files = True
                                break
                
                if has_code_files:
                    code_analysis_prompt = """## CODE ANALYSIS INSTRUCTIONS

You are analyzing code files. When responding to questions about code:

🔍 **ANALYSIS REQUIREMENTS:**
- Show actual code snippets from the provided context
- Explain code structure, functions, components, and logic flow
- Identify patterns, dependencies, and architectural decisions
- Point out potential improvements or issues if asked

💻 **RESPONSE FORMAT:**
- Use proper code formatting with syntax highlighting
- Quote specific lines or sections when referencing code
- Provide clear explanations of what each code section does
- Structure your response with headers and bullet points for clarity

⚠️ **CRITICAL:**
- Answer specifically about the actual code provided in the context
- Do NOT give generic programming advice unless the code context doesn't contain relevant information
- If asked to retrieve or show code, display the actual code from the files
- Reference specific functions, variables, or components by name

"""
                    logger.info("Enhanced chat: Added code analysis prompt for code file context")
                
                enhanced_message_with_context = f"{code_analysis_prompt}{combined_context}\n\nUser Question: {enhanced_message}"
                
                # Final context length check and smart truncation if needed
                # Use smart context limit if router was used, otherwise default
                max_final_context = context_limit if 'context_limit' in locals() else 100000
                if len(enhanced_message_with_context) > max_final_context:
                    logger.warning(f"Enhanced message exceeds final limit ({len(enhanced_message_with_context)} > {max_final_context}), applying smart truncation")
                    
                    # Smart truncation: preserve essential parts and summarize the rest
                    available_space = max_final_context - len(code_analysis_prompt) - len(enhanced_message) - 200
                    
                    if len(combined_context) > available_space:
                        # Use intent-aware truncation if available
                        if 'intent_metadata' in locals() and intent_metadata:
                            intent_type = locals().get('intent_type')
                            if intent_type == IntentType.DATABASE_QUERY:
                                # For database queries, preserve entity information
                                keep_start = int(available_space * 0.4)
                                keep_end = int(available_space * 0.4)
                                start_context = combined_context[:keep_start]
                                end_context = combined_context[-keep_end:]
                                truncated_context = f"{start_context}\n\n[... Context preserved for database query capability ...]\n\n{end_context}"
                            elif intent_type == IntentType.RAG_SEARCH:
                                # For document search, preserve document context
                                keep_start = int(available_space * 0.3)
                                keep_end = int(available_space * 0.5)  # More weight on recent context
                                start_context = combined_context[:keep_start]
                                end_context = combined_context[-keep_end:]
                                truncated_context = f"{start_context}\n\n[... Document context preserved for search ...]\n\n{end_context}"
                            else:
                                # For other intents, keep most recent context
                                truncated_context = combined_context[-available_space:]
                        elif any(word in enhanced_message.lower() for word in ['client', 'project', 'document', 'file', 'how many', 'count']):
                            # Legacy fallback for RAG queries
                            keep_start = int(available_space * 0.3)
                            keep_end = int(available_space * 0.3)
                            start_context = combined_context[:keep_start]
                            end_context = combined_context[-keep_end:]
                            truncated_context = f"{start_context}\n\n[... Context summarized for performance - maintaining database query capability ...]\n\n{end_context}"
                        else:
                            # For general queries, keep most recent context
                            truncated_context = combined_context[-available_space:]
                        
                        enhanced_message_with_context = f"{code_analysis_prompt}{truncated_context}\n\nUser Question: {enhanced_message}"
                    else:
                        enhanced_message_with_context = f"{code_analysis_prompt}{combined_context}\n\nUser Question: {enhanced_message}"
                
                # Enhanced logging with router information
                router_info = ""
                if 'intent_type' in locals() and intent_type:
                    router_info = f", intent: {intent_type.value}"
                if 'intent_metadata' in locals() and intent_metadata:
                    if intent_metadata.get('enhanced_command_processing'):
                        router_info += f", command: /{intent_metadata.get('command_name', 'unknown')}"
                    if intent_metadata.get('router_used'):
                        router_info += f", routed_to: {intent_metadata.get('router_used')}"
                
                logger.info(f"Enhanced chat: Enhanced message created with combined context (length: {len(combined_context)}, final length: {len(enhanced_message_with_context)}, code-aware: {has_code_files}, limit: {max_final_context}{router_info})")
            else:
                logger.info(f"Enhanced chat: Using original message (no additional context)")

            # Create or get chat engine with fallback for simple mode
            logger.info(f"Enhanced chat: Creating chat engine (simple_mode={simple_mode}, bypass_rules={bypass_rules})...")
            try:
                if simple_mode:
                    # Use simple chat engine without RAG
                    logger.info(f"Enhanced chat: Creating simple chat engine...")
                    chat_engine = self._create_simple_chat_engine(session_id, model_name, web_search_context, voice_mode, bypass_rules)
                    logger.info(f"Enhanced chat: Simple chat engine created successfully")
                else:
                    # Use enhanced chat engine with RAG
                    logger.info(f"Enhanced chat: Creating enhanced chat engine...")
                    chat_engine = self._create_chat_engine(session_id, model_name, web_search_context, chat_mode, voice_mode, bypass_rules, project_id=project_id)
                    logger.info(f"Enhanced chat: Enhanced chat engine created successfully")
            except Exception as engine_error:
                logger.warning(f"Enhanced chat engine failed, falling back to simple mode: {engine_error}")
                simple_mode = True
                chat_engine = self._create_simple_chat_engine(session_id, model_name, web_search_context, voice_mode, bypass_rules)

            # Generate response with AssertionError handling for vector store failures
            logger.info(f"Enhanced chat: Generating response with chat engine...")
            print(f"DEBUG TRACE: About to call chat_engine.stream_chat with message length: {len(enhanced_message_with_context)}")
            logger.debug(f"DEBUG TRACE: About to call chat_engine.stream_chat with message length: {len(enhanced_message_with_context)}")
            
            try:
                response_stream = chat_engine.stream_chat(enhanced_message_with_context)
            except ValueError as ve:
                if "available context size" in str(ve):
                    logger.warning(f"Context window overflow: {ve}. Clearing cached engine and retrying with fresh memory.")
                    # Clear stale cached engine/memory for this session
                    self.chat_engines.pop(session_id, None)
                    self.session_memories.pop(session_id, None)
                    # Retry with a fresh engine (which now has proper memory limits)
                    try:
                        if simple_mode:
                            chat_engine = self._create_simple_chat_engine(session_id, model_name, web_search_context, voice_mode, bypass_rules)
                        else:
                            chat_engine = self._create_chat_engine(session_id, model_name, web_search_context, chat_mode, voice_mode, bypass_rules, project_id=project_id)
                        response_stream = chat_engine.stream_chat(enhanced_message_with_context)
                    except Exception as retry_err:
                        logger.error(f"Retry after context overflow also failed: {retry_err}")
                        raise
                else:
                    raise
            except AssertionError as ae:
                logger.warning(f"BUG FIX 7: LlamaIndex AssertionError (vector store query failed): {ae}")
                logger.info("BUG FIX 7: Implementing comprehensive vector store failure recovery")

                # BUG FIX 7: Enhanced vector store failure handling with multiple recovery strategies
                recovery_strategies = [
                    "simple_chat_engine",
                    "direct_llm_with_context",
                    "direct_llm_minimal",
                    "emergency_fallback"
                ]

                for strategy in recovery_strategies:
                    try:
                        if strategy == "simple_chat_engine":
                            # Try creating a simple chat engine without vector store
                            logger.info("BUG FIX 3: Attempting simple chat engine recovery")
                            fallback_engine = self._create_simple_chat_engine(session_id, model_name, "", voice_mode)
                            response_stream = fallback_engine.stream_chat(message)  # Use original message, not enhanced
                            logger.info("BUG FIX 3: Simple chat engine recovery successful")
                            break

                        elif strategy == "direct_llm_with_context":
                            # Use direct LLM with minimal context
                            logger.info("BUG FIX 3: Attempting direct LLM with context recovery")
                            from backend.utils.llm_service import run_llm_chat_prompt
                            from flask import current_app
                            llm_instance = current_app.config.get("LLAMA_INDEX_LLM")

                            if llm_instance:
                                # Prepare minimal context
                                memory = self.session_memories.get(session_id)
                                context_prompt = message
                                if memory and hasattr(memory, 'chat_store') and memory.chat_store.store:
                                    recent_msgs = list(memory.chat_store.store.values())[-2:]
                                    if recent_msgs:
                                        context_summary = " ".join([msg.content[:100] for msg in recent_msgs if hasattr(msg, 'content')])
                                        context_prompt = f"Context: {context_summary}\nUser: {message}"

                                # Generate response using direct LLM
                                direct_response = run_llm_chat_prompt(
                                    context_prompt,
                                    llm_instance=llm_instance,
                                    debug_id=f"vector_store_fallback_{session_id}_{int(time.time())}"
                                )

                                # Create a mock response stream object
                                class MockResponseStream:
                                    def __init__(self, text):
                                        self.response_gen = [text]
                                        self.response = text

                                response_stream = MockResponseStream(direct_response)
                                logger.info("BUG FIX 3: Direct LLM with context recovery successful")
                                break

                        elif strategy == "direct_llm_minimal":
                            # Use direct LLM with just the user message
                            logger.info("BUG FIX 3: Attempting direct LLM minimal recovery")
                            from backend.utils.llm_service import run_llm_chat_prompt
                            from flask import current_app
                            llm_instance = current_app.config.get("LLAMA_INDEX_LLM")

                            if llm_instance:
                                direct_response = run_llm_chat_prompt(
                                    message,
                                    llm_instance=llm_instance,
                                    debug_id=f"minimal_fallback_{session_id}_{int(time.time())}"
                                )

                                class MockResponseStream:
                                    def __init__(self, text):
                                        self.response_gen = [text]
                                        self.response = text

                                response_stream = MockResponseStream(direct_response)
                                logger.info("BUG FIX 3: Direct LLM minimal recovery successful")
                                break

                        elif strategy == "emergency_fallback":
                            # Emergency hardcoded response with helpful guidance
                            logger.info("BUG FIX 3: Using emergency fallback response")
                            emergency_response = "I'm experiencing some technical difficulties with my document search capabilities, but I'm still here to help. I can assist with general questions, explanations, and generating content. Please try rephrasing your question or let me know how else I can help you."

                            class MockResponseStream:
                                def __init__(self, text):
                                    self.response_gen = [text]
                                    self.response = text

                            response_stream = MockResponseStream(emergency_response)
                            logger.info("BUG FIX 3: Emergency fallback response used")
                            break

                    except Exception as strategy_error:
                        logger.warning(f"BUG FIX 3: Strategy '{strategy}' failed: {strategy_error}")
                        continue

                # If all strategies failed, use the original code as final fallback
                if 'response_stream' not in locals():
                    logger.error("BUG FIX 3: All recovery strategies failed, using original fallback")
                    from backend.utils.llm_service import run_llm_chat_prompt
                    from flask import current_app
                    llm_instance = current_app.config.get("LLAMA_INDEX_LLM")
                
                try:
                    # Generate response using direct LLM (no RAG/vector store)
                    direct_response = run_llm_chat_prompt(
                        enhanced_message_with_context,
                        llm_instance=llm_instance,
                        debug_id=f"fallback_chat_{session_id}_{int(time.time())}"
                    )
                    
                    # Create a mock response stream object to maintain compatibility
                    class DirectResponseStream:
                        def __init__(self, response_text):
                            self.response_gen = [response_text]
                    
                    response_stream = DirectResponseStream(direct_response)
                    logger.info("Direct LLM fallback successful")
                    
                except Exception as fallback_error:
                    logger.error(f"Direct LLM fallback failed: {fallback_error}")
                    # Final fallback to simple chat engine
                    simple_mode = True
                    chat_engine = self._create_simple_chat_engine(session_id, model_name, web_search_context, voice_mode)
                    response_stream = chat_engine.stream_chat(enhanced_message_with_context)
            logger.info(f"Enhanced chat: Response stream obtained")
            print(f"DEBUG TRACE: Got response_stream from chat_engine")
            logger.debug(f"DEBUG TRACE: Got response_stream from chat_engine")

            # Collect response chunks
            logger.info(f"Enhanced chat: Collecting response chunks...")
            response_chunks = []
            try:
                for chunk in response_stream.response_gen:
                    response_chunks.append(chunk)
            except ValueError as ve:
                if "multiple blocks" in str(ve) and response_chunks:
                    logger.warning(f"LlamaIndex multi-block error during history write (non-critical, {len(response_chunks)} chunks collected): {ve}")
                else:
                    raise
            logger.info(f"Enhanced chat: Collected {len(response_chunks)} response chunks")

            # Combine response
            full_response = "".join(response_chunks)
            logger.info(f"Enhanced chat: Combined response length: {len(full_response)}")
            print(f"DEBUG TRACE: Full response content: '{full_response}'")
            logger.debug(f"DEBUG TRACE: Full response content: '{full_response}'")

            # BUG FIX 6: Enhanced handling of context overflow with intelligent fallback
            if full_response.strip() == "Empty Response" or full_response.strip() == "":
                logger.warning("BUG FIX 6: LlamaIndex returned 'Empty Response' - implementing intelligent context overflow recovery")

                # Implement multiple fallback strategies
                fallback_success = False
                llm = current_app.config.get('LLAMA_INDEX_LLM')

                if llm:
                    # Strategy 1: Minimal context with conversation history
                    try:
                        memory = self.session_memories.get(session_id)
                        if memory and hasattr(memory, 'chat_store') and memory.chat_store.store:
                            recent_msgs = list(memory.chat_store.store.values())[-2:]
                            context_summary = " ".join([msg.content[:50] for msg in recent_msgs if hasattr(msg, 'content')])
                            fallback_prompt = f"Previous context: {context_summary}\nUser: {message}\nAssistant:"
                        else:
                            fallback_prompt = f"User: {message}\nAssistant:"

                        direct_response = llm.complete(fallback_prompt)
                        candidate_response = direct_response.text.strip() if hasattr(direct_response, 'text') else str(direct_response).strip()

                        if candidate_response and candidate_response != "Empty Response" and len(candidate_response) > 5:
                            full_response = candidate_response
                            fallback_success = True
                            logger.info(f"BUG FIX 6: Successful fallback with minimal context, length: {len(full_response)}")
                    except Exception as fallback_error:
                        logger.warning(f"BUG FIX 6: Minimal context fallback failed: {fallback_error}")

                    # Strategy 2: Pure user message only
                    if not fallback_success:
                        try:
                            direct_response = llm.complete(message)
                            candidate_response = direct_response.text.strip() if hasattr(direct_response, 'text') else str(direct_response).strip()

                            if candidate_response and candidate_response != "Empty Response" and len(candidate_response) > 5:
                                full_response = candidate_response
                                fallback_success = True
                                logger.info(f"BUG FIX 6: Successful fallback with pure message, length: {len(full_response)}")
                        except Exception as fallback_error:
                            logger.warning(f"BUG FIX 6: Pure message fallback failed: {fallback_error}")

                # Strategy 3: Emergency hardcoded responses
                if not fallback_success:
                    msg_lower = message.lower().strip()
                    if any(word in msg_lower for word in ['hello', 'hi', 'hey', 'good morning', 'good afternoon']):
                        full_response = "Hello! How can I help you today?"
                    elif '?' in message:
                        full_response = "I understand your question. Could you please rephrase it or break it into smaller parts?"
                    elif any(word in msg_lower for word in ['help', 'assist', 'support']):
                        full_response = "I'm here to help! Please let me know what specific assistance you need."
                    else:
                        full_response = "I'm experiencing some technical difficulties but I'm still here to help. Please try rephrasing your message."

                    logger.info(f"BUG FIX 2: Used emergency response for context overflow")
                    simple_mode = True
                    rag_context = []

            # Save assistant response
            logger.info(f"Enhanced chat: Saving assistant response...")
            self._save_message(session_id, 'assistant', full_response, project_id=project_id)
            logger.info(f"Enhanced chat: Assistant response saved")

            # Commit database changes
            logger.info(f"Enhanced chat: Committing database changes...")
            from backend.models import db
            try:
                db.session.commit()
            except Exception as commit_err:
                db.session.rollback()
                logger.error(f"Failed to commit session data: {commit_err}")
                raise

            # Calculate response time
            response_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"Enhanced chat: Response time: {response_time:.2f} seconds")

            # Persist MemoryManager state for RAG/LLM memory
            if self._memory_manager:
                try:
                    self._memory_manager.persist_memory(session_id)
                    logger.debug(f"Enhanced chat: MemoryManager state persisted for session {session_id}")
                except Exception as mem_error:
                    logger.debug(f"MemoryManager persist failed: {mem_error}")

            # Log conversation exchange with ConversationLogger for persistent debugging
            if self._conversation_logger:
                try:
                    # Estimate token count
                    estimated_tokens = len(message) // 4 + len(full_response) // 4
                    
                    self._conversation_logger.log_exchange(
                        session_id=session_id,
                        user_message=message,
                        assistant_response=full_response,
                        conversation_type=chat_mode or "general",
                        token_count=estimated_tokens,
                        processing_time=response_time
                    )
                    logger.debug(f"Enhanced chat: Conversation logged with ConversationLogger")
                except Exception as log_error:
                    logger.debug(f"ConversationLogger failed: {log_error}")

            # Update statistics
            if self.stats['total_messages'] > 0:
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
                'rag_context': rag_context,
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

    def _detect_and_read_project_file(self, query: str) -> Optional[Dict[str, Any]]:
        """
        If the user's query explicitly asks for a project file path (e.g. "read backend/config.py"),
        read that file from disk and return a context chunk so the LLM sees the actual file.
        Returns None if no project file path is detected or the file cannot be read.
        """
        import re
        if not query or not query.strip():
            return None
        query_stripped = query.strip()
        query_lower = query_stripped.lower()
        # Patterns that indicate "read project file" intent
        path = None
        # "read the file backend/config.py", "read backend/config.py", "open backend/config.py", "show backend/config.py"
        m = re.search(
            r"(?:read|open|show)\s+(?:the\s+file\s+)?([a-zA-Z0-9_./\-]+\.[a-zA-Z0-9]+)",
            query_stripped,
            re.IGNORECASE,
        )
        if m:
            path = m.group(1).strip()
        if not path:
            # "first N lines of backend/config.py", "contents of backend/config.py"
            m = re.search(
                r"(?:first\s+\d+\s+lines\s+of|contents\s+of)\s+([a-zA-Z0-9_./\-]+\.[a-zA-Z0-9]+)",
                query_stripped,
                re.IGNORECASE,
            )
            if m:
                path = m.group(1).strip()
        if not path and ("read" in query_lower or "file" in query_lower):
            # "backend/config.py" or "frontend/src/App.jsx" when user is asking about a file
            m = re.search(
                r"\b(backend|frontend|src|scripts)/[a-zA-Z0-9_./\-]+\.[a-zA-Z0-9]+\b",
                query_stripped,
            )
            if m:
                path = m.group(0).strip()
        if not path:
            return None
        # Normalize path: no leading slash, use forward slashes
        path = path.lstrip("/").replace("\\", "/")
        try:
            from backend.tools.llama_code_tools import read_code
            result = read_code(path)
            if not result or result.strip().startswith("ERROR"):
                return None
            logger.info(f"Injected project file: {path}")
            return {
                "content": result,
                "source": f"Project file: {path}",
                "metadata": {"source_document": path, "file_type": "project_file"},
                "score": 1.0,
                "content_type": "document",
                "entity_type": "",
            }
        except Exception as e:
            logger.debug(f"Could not read project file {path}: {e}")
            return None

    def _retrieve_uploaded_files_context(self, query: str, session_id: str, project_id: int = None) -> List[Dict[str, Any]]:
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

            # CRITICAL FIX: Prevent web search queries from triggering file retrieval
            web_search_patterns = [
                'duckduckgo', 'ddg', 'google', 'search web', 'search for',
                'weather', 'temperature', 'forecast', 'news', 'current events',
                'stock price', 'sports score', 'lottery', 'what is the',
                'who is', 'when did', 'where is', 'how many people'
            ]
            is_web_search = any(pattern in query_lower for pattern in web_search_patterns)

            # Also check for time/date queries that don't need file context
            time_patterns = ['what time', 'what day', 'what date', 'current time', 'current date']
            is_time_query = any(pattern in query_lower for pattern in time_patterns)

            if is_web_search or is_time_query:
                logger.info(f"[ENHANCED_FILE_ANALYSIS] Detected web/time query, skipping file retrieval: '{query[:100]}...'")
                return []

            logger.info(f"[ENHANCED_FILE_ANALYSIS] Starting complete file content retrieval for query: '{query}'")

            # Search for uploaded files that match the query (expanded beyond just code files)
            query_lower = query.lower()

            # Get all uploaded files with content (recent first), scoped to project if available
            try:
                file_query = db.session.query(DBDocument).filter(
                    DBDocument.content.isnot(None),
                    DBDocument.index_status.in_(["STORED", "INDEXED"])
                )
                # Scope to project if project_id provided (include unscoped docs as fallback)
                if project_id is not None:
                    file_query = file_query.filter(
                        db.or_(
                            DBDocument.project_id == project_id,
                            DBDocument.project_id.is_(None)
                        )
                    )
                uploaded_files = file_query.order_by(DBDocument.uploaded_at.desc()).all()
                logger.info(f"[ENHANCED_FILE_ANALYSIS] Found {len(uploaded_files)} files with content (project_id={project_id})")
            except Exception as query_error:
                logger.error(f"Database query failed: {query_error}")
                raise

            if not uploaded_files:
                logger.info("No files with content found in database")
                return []

            file_contexts = []

            # ENHANCED ANALYSIS KEYWORDS - comprehensive file analysis detection
            # NOTE: Removed overly generic words like 'check', 'what is', 'explain' to prevent false positives
            analysis_keywords = [
                'analyze', 'analysis', 'review', 'examine', 'inspect', 'look at',
                'what does', 'tell me about', 'show me',
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
                # Adaptive context limits based on query complexity (REDUCED 10x for better performance)
                query_words = len(query.lower().split())
                if query_words <= 5 and any(word in query.lower() for word in ['read', 'show', 'what']):
                    # Simple queries - smaller context
                    max_context_length = 5000   # 5KB per file for simple queries
                    max_total_context = 15000   # 15KB total for simple queries
                elif query_words <= 10:
                    # Medium complexity queries
                    max_context_length = 10000  # 10KB per file
                    max_total_context = 30000   # 30KB total
                else:
                    # Complex analysis queries
                    max_context_length = 20000  # 20KB per file for complex analysis
                    max_total_context = 50000   # 50KB total for complex analysis

                logger.info(f"[ENHANCED_FILE_ANALYSIS] Using adaptive context limits - per file: {max_context_length}, total: {max_total_context} (query words: {query_words})")

                # FIXED: Stricter matching to prevent irrelevant files from being included
                # Require either strong match (>=10), medium match (>=6), OR explicit filename match
                # Removed generic "is_analysis_request" and low threshold (>= 1.0) to prevent false positives
                if has_strong_match or has_medium_match or match_score >= 8.0:
                    # ENHANCED: Get complete file content for analysis
                    raw_file_content = file_doc.content
                    if raw_file_content:
                        # ENHANCED RAG CHUNKER INTEGRATION: Use intelligent chunking for code files
                        file_content = raw_file_content
                        if self.rag_chunker and has_strong_match:
                            try:
                                # Determine file type for chunking strategy
                                if '.' in file_doc.filename:
                                    file_extension = file_doc.filename.split('.')[-1].lower()
                                else:
                                    # Try to determine type from filename patterns
                                    filename_lower = file_doc.filename.lower()
                                    if any(pattern in filename_lower for pattern in ['readme', 'makefile', 'dockerfile', 'jenkinsfile']):
                                        file_extension = 'text'
                                    else:
                                        file_extension = 'txt'
                                
                                # Use enhanced chunking for code and structured files
                                # Normalize file extension to handle both .jsx and jsx formats
                                normalized_ext = file_extension.lstrip('.')
                                if normalized_ext in ['jsx', 'js', 'ts', 'tsx', 'py', 'php', 'java', 'cpp', 'c', 'go', 'rs', 'rb', 'swift', 'css', 'html', 'json', 'xml', 'yaml', 'sql']:
                                    logger.info(f"[ENHANCED_RAG] Applying intelligent chunking to {file_doc.filename} ({file_extension})")
                                    
                                    # Create chunking strategy optimized for code analysis
                                    from backend.utils.enhanced_rag_chunking import ChunkingStrategy
                                    code_strategy = ChunkingStrategy(
                                        name=f"code_analysis_{file_extension}",
                                        max_chunk_size=2000,  # Larger chunks for code context
                                        overlap_size=300,     # More overlap for code continuity
                                        use_semantic_splitting=True,
                                        preserve_structure=True,
                                        chunk_by_content_type=True
                                    )
                                    
                                    # Apply query-aware chunking to get most relevant sections
                                    from llama_index.core import Document as LlamaDocument
                                    temp_document = LlamaDocument(
                                        text=raw_file_content,
                                        metadata={
                                            'filename': file_doc.filename,
                                            'file_type': file_extension,
                                            'query': query
                                        }
                                    )
                                    
                                    chunked_nodes = self.rag_chunker.chunk_documents(
                                        documents=[temp_document],
                                        strategy_name='adaptive'  # Use the correct parameter name and a valid strategy
                                    )
                                    
                                    if chunked_nodes and len(chunked_nodes) > 0:
                                        # Extract text from top relevant chunks (limit to 8 chunks)
                                        relevant_chunks = []
                                        query_words = set(query.lower().split())
                                        
                                        # Score chunks by query relevance
                                        scored_chunks = []
                                        for node in chunked_nodes[:16]:  # Consider up to 16 chunks
                                            chunk_text = node.text.lower()
                                            relevance_score = sum(1 for word in query_words if word in chunk_text)
                                            scored_chunks.append((relevance_score, node.text))
                                        
                                        # Sort by relevance and take top chunks
                                        scored_chunks.sort(key=lambda x: x[0], reverse=True)
                                        relevant_chunks = [chunk[1] for chunk in scored_chunks[:8]]
                                        
                                        if relevant_chunks:
                                            file_content = "\n\n".join(relevant_chunks)
                                            logger.info(f"[ENHANCED_RAG] Chunked {file_doc.filename}: {len(raw_file_content)} chars -> {len(file_content)} chars ({len(relevant_chunks)} relevant chunks)")
                                        else:
                                            logger.warning(f"[ENHANCED_RAG] No relevant chunks found for {file_doc.filename}, using original content")
                                    else:
                                        logger.warning(f"[ENHANCED_RAG] Chunking failed for {file_doc.filename}, using original content")
                                        
                            except Exception as e:
                                logger.warning(f"BUG FIX 5: RAG chunking error for {file_doc.filename}: {e}")

                                # FIX BUG 5: Implement comprehensive chunking fallback strategies
                                logger.info("BUG FIX 5: Implementing chunking fallback strategies")

                                # Strategy 1: Simple text splitting by paragraphs
                                try:
                                    paragraphs = file_content.split('\n\n')
                                    if len(paragraphs) > 1:
                                        # Take first few paragraphs that fit in length limit
                                        fallback_content = ""
                                        for para in paragraphs[:5]:  # Limit to first 5 paragraphs
                                            if len(fallback_content + para) < max_context_length:
                                                fallback_content += para + "\n\n"
                                            else:
                                                break

                                        if fallback_content.strip():
                                            file_content = fallback_content.strip()
                                            logger.info(f"BUG FIX 5: Used paragraph-based fallback for {file_doc.filename}")
                                        else:
                                            raise Exception("Paragraph fallback failed")
                                    else:
                                        raise Exception("No paragraphs found")

                                except Exception as para_error:
                                    # Strategy 2: Simple character truncation with word boundary
                                    try:
                                        logger.warning(f"BUG FIX 5: Paragraph fallback failed: {para_error}")
                                        if len(file_content) > max_context_length:
                                            # Find last word boundary before the limit
                                            truncate_point = max_context_length - 100  # Leave some buffer
                                            last_space = file_content.rfind(' ', 0, truncate_point)
                                            if last_space > max_context_length // 2:  # Make sure we don't truncate too much
                                                file_content = file_content[:last_space] + "\n\n[Content truncated due to length limits]"
                                            else:
                                                file_content = file_content[:truncate_point] + "\n\n[Content truncated due to length limits]"

                                        logger.info(f"BUG FIX 5: Used truncation fallback for {file_doc.filename}")

                                    except Exception as truncate_error:
                                        # Strategy 3: Emergency minimal content
                                        logger.error(f"BUG FIX 5: All chunking strategies failed: {truncate_error}")
                                        file_content = f"File: {file_doc.filename}\nSize: {len(file_content)} characters\nContent type: {getattr(file_doc, 'content_type', 'unknown')}\n\n[Content could not be processed due to technical difficulties. File is available but chunking failed.]"
                                        logger.info(f"BUG FIX 5: Used emergency minimal content for {file_doc.filename}")

                                # Log the final fallback strategy used
                                logger.info(f"BUG FIX 5: Successfully recovered from chunking error for {file_doc.filename}, final content length: {len(file_content)}")
                        
                        # CRITICAL FIX: Enforce context length limits with user notification
                        if len(file_content) > max_context_length:
                            truncation_warning = f"\n\n[WARNING: File {file_doc.filename} was truncated from {len(file_content)} to {max_context_length} characters due to length limits. Consider splitting large files for better analysis.]"
                            logger.warning(f"[ENHANCED_FILE_ANALYSIS] File {file_doc.filename} exceeds length limit ({len(file_content)} > {max_context_length}), truncating")
                            # Account for warning message length in truncation
                            adjusted_limit = max_context_length - len(truncation_warning)
                            file_content = file_content[:adjusted_limit] + truncation_warning

                        # Check total context length
                        current_total_length = sum(len(ctx.get('content', '')) for ctx in file_contexts)
                        if current_total_length + len(file_content) > max_total_context:
                            logger.warning(f"[ENHANCED_FILE_ANALYSIS] Total context length limit reached ({current_total_length + len(file_content)} > {max_total_context}), skipping additional files")
                            continue

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
            total_content_size = sum(len(ctx.get('content', '')) for ctx in file_contexts)
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
            if file_extension in ['.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', '.sh', '.dockerfile', '.java', '.c', '.cpp', '.h', '.go', '.rs', '.rb', '.swift', '.kt', '.sql']:
                header += f"=== PROGRAMMING LANGUAGE: {file_extension.upper()} ===\n"
                header += "=== CODE ANALYSIS MODE ===\n\n"

            # For data files, add data analysis context
            elif file_extension in ['.csv', '.json', '.xml', '.yaml', '.yml']:
                header += f"=== DATA FILE TYPE: {file_extension.upper()} ===\n"
                header += "=== DATA ANALYSIS MODE ===\n\n"

            # For markdown files (special case - can be both code and text)
            elif file_extension in ['.md']:
                header += f"=== MARKDOWN FILE: {file_extension.upper()} ===\n"
                header += "=== MARKDOWN ANALYSIS MODE ===\n\n"

            # For text files, add text analysis context
            elif file_extension in ['.txt']:
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

        # Validate request
        if not request.is_json:
            return error_response("Request must be JSON", 400)

        data = request.get_json()
        session_id = data.get('session_id')
        message = data.get('message')

        logger.info(
            f"[ENHANCED_CHAT] session={session_id} "
            f"message={str(data.get('message', ''))[:80]!r} "
            f"(NOTE: tools disabled on this endpoint)"
        )
        use_rag = data.get('use_rag', True)
        debug_mode = data.get('debug', False)
        simple_mode = data.get('simple_mode', False)
        chat_mode = data.get('chat_mode', None)
        voice_mode = data.get('voice_mode', False)  # New: voice conversation mode
        bypass_rules = data.get('bypassRules', False) or data.get('bypass_rules', False)  # Support both naming conventions
        request_id = data.get('request_id', f"{session_id}_{int(time.time() * 1000)}")

        # Extract project_id for context scoping (prevents cross-project contamination)
        project_id = data.get('project_id', None)
        if project_id is not None:
            try:
                project_id = int(project_id)
            except (ValueError, TypeError):
                project_id = None

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
        try:  # noqa: _active_requests cleanup is in finally below
            logger.info(f"Enhanced chat: Processing message for session {session_id}, simple_mode={simple_mode}")
            logger.info(f"Enhanced chat: Chat manager type: {type(chat_manager)}")
            logger.info(f"Enhanced chat: Context manager available: {chat_manager.context_manager is not None}")
            logger.info(f"Enhanced chat: Index manager available: {chat_manager.index_manager is not None}")
            logger.info(f"Enhanced chat: RAG chunker available: {chat_manager.rag_chunker is not None}")

            logger.info(f"Enhanced chat: About to call process_chat_message with chat_mode={chat_mode}, voice_mode={voice_mode}, bypass_rules={bypass_rules}, project_id={project_id}...")
            response_data = chat_manager.process_chat_message(session_id, message, use_rag, debug_mode, simple_mode, chat_mode, voice_mode, bypass_rules, project_id=project_id)
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
        # Ensure _active_requests is cleaned up even if exception occurs before inner try
        try:
            with _cache_lock:
                _active_requests.discard(request_key)
        except NameError:
            pass  # request_key not yet defined if error occurred during request parsing
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
        project_id = data.get('project_id')
        if project_id is not None:
            try:
                project_id = int(project_id)
            except (ValueError, TypeError):
                project_id = None

        if not session_id or not message:
            return jsonify({"error": "Missing session_id or message"}), 400

        def generate_stream():
            try:
                chat_manager = get_chat_manager()
                # Get or create session
                session = chat_manager._get_or_create_session(session_id, project_id=project_id)

                # Get active model
                model_name = chat_manager._get_active_model()
                model_config = chat_manager._get_model_config(model_name)

                # Retrieve relevant context if RAG is enabled
                rag_context = []
                if use_rag:
                    rag_context = chat_manager._retrieve_relevant_context(message, session_id, project_id=project_id)

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
                chat_engine = chat_manager._create_chat_engine(session_id, model_name, "", None, False, project_id=project_id)

                # Generate streaming response
                response_stream = chat_engine.stream_chat(enhanced_message)

                full_response = ""
                try:
                    for chunk in response_stream.response_gen:
                        full_response += chunk
                        yield f"data: {json.dumps({'delta': chunk})}\n\n"
                except ValueError as ve:
                    if "multiple blocks" in str(ve) and full_response:
                        logger.warning(f"Streaming: LlamaIndex multi-block error during history write (non-critical): {ve}")
                    else:
                        raise

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
                chat_manager._save_message(session_id, 'assistant', full_response, project_id=project_id)
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
        if chat_manager.context_manager:
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
            msg_data = {
                "id": str(msg.id),
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None
            }
            # Flatten extra_data image fields into message for frontend rendering
            if msg.extra_data and isinstance(msg.extra_data, dict):
                for key in ('imageUrl', 'imageFileName', 'messageType',
                            'relatedImageUrl', 'imageAnalysis', 'analysisDetails',
                            'generatedImages'):
                    if key in msg.extra_data:
                        msg_data[key] = msg.extra_data[key]
            formatted_messages.append(msg_data)

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


@enhanced_chat_bp.route("/history/all", methods=["GET", "DELETE"])
def clear_all_chat_history():
    """GET: return counts of chat data.  DELETE: clear all chat history."""
    from backend.models import db, LLMMessage, LLMSession
    from backend.config import CONTEXT_PERSISTENCE_DIR, STORAGE_DIR
    import glob as glob_mod

    def _count_json_files(*path_parts):
        d = os.path.join(*path_parts)
        if os.path.isdir(d):
            return len(glob_mod.glob(os.path.join(d, "*.json")))
        return 0

    if request.method == "GET":
        try:
            message_count = db.session.query(LLMMessage).count()
            session_count = db.session.query(LLMSession).count()
            context_files = _count_json_files(CONTEXT_PERSISTENCE_DIR)
            conv_dir = os.path.join(STORAGE_DIR, "conversations")
            conversation_files = (
                _count_json_files(conv_dir, "sessions") +
                _count_json_files(conv_dir, "daily") +
                _count_json_files(conv_dir, "summaries")
            )
            return jsonify({
                "messages": message_count,
                "sessions": session_count,
                "context_files": context_files,
                "conversation_files": conversation_files,
            })
        except Exception as e:
            logger.error(f"Error counting chat history: {e}")
            return jsonify({"error": str(e)}), 500

    # --- DELETE ---
    try:
        # Delete all messages and sessions from database
        deleted_messages = db.session.query(LLMMessage).delete()
        deleted_sessions = db.session.query(LLMSession).delete()
        db.session.commit()

        # Clear all in-memory chat manager caches
        chat_manager = get_chat_manager()
        chat_manager.chat_engines.clear()
        if hasattr(chat_manager, 'session_memories'):
            chat_manager.session_memories.clear()
        if hasattr(chat_manager, 'session_messages'):
            chat_manager.session_messages.clear()
        if hasattr(chat_manager, 'session_last_activity'):
            chat_manager.session_last_activity.clear()
        if hasattr(chat_manager, 'session_creation_time'):
            chat_manager.session_creation_time.clear()
        if hasattr(chat_manager, 'context_manager'):
            try:
                if hasattr(chat_manager.context_manager, 'clear_all'):
                    chat_manager.context_manager.clear_all()
                elif hasattr(chat_manager.context_manager, 'clear'):
                    chat_manager.context_manager.clear()
            except Exception as context_error:
                logger.warning(f"Could not clear context manager: {context_error}")

        # Clear all chat-related files on disk
        deleted_disk_files = 0

        def _remove_json_files(*path_parts):
            nonlocal deleted_disk_files
            d = os.path.join(*path_parts)
            if os.path.isdir(d):
                for f in glob_mod.glob(os.path.join(d, "*.json")):
                    try:
                        os.remove(f)
                        deleted_disk_files += 1
                    except OSError:
                        pass

        _remove_json_files(CONTEXT_PERSISTENCE_DIR)
        conv_dir = os.path.join(STORAGE_DIR, "conversations")
        _remove_json_files(conv_dir, "sessions")
        _remove_json_files(conv_dir, "daily")
        _remove_json_files(conv_dir, "summaries")

        logger.info(f"Cleared all chat history: {deleted_messages} messages, "
                     f"{deleted_sessions} sessions, {deleted_disk_files} disk files")

        parts = []
        if deleted_messages:
            parts.append(f"{deleted_messages} messages")
        if deleted_sessions:
            parts.append(f"{deleted_sessions} sessions")
        if deleted_disk_files:
            parts.append(f"{deleted_disk_files} cached files")
        summary = ", ".join(parts) if parts else "No data found"

        return jsonify({
            'message': f'Cleared {summary}',
            'deleted_messages': deleted_messages,
            'deleted_sessions': deleted_sessions,
            'deleted_disk_files': deleted_disk_files,
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error clearing all chat history: {e}")
        return jsonify({
            "error": "Failed to clear chat history",
            "message": str(e)
        }), 500

# ===== SESSION MANAGEMENT ENDPOINTS =====

@enhanced_chat_bp.route("/sessions", methods=["GET"])
@ensure_db_session_cleanup
def list_chat_sessions():
    """List chat sessions for a project, with preview and message count."""
    try:
        from backend.models import db, LLMMessage, LLMSession
        from sqlalchemy import func

        project_id = request.args.get("project_id", type=int)
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        query = db.session.query(LLMSession)
        if project_id is not None:
            query = query.filter(LLMSession.project_id == project_id)

        total = query.count()

        sessions = query.order_by(LLMSession.created_at.desc()).offset(offset).limit(limit).all()

        results = []
        for session in sessions:
            msg_count = db.session.query(func.count(LLMMessage.id)).filter(
                LLMMessage.session_id == session.id
            ).scalar()

            # Get first user message as preview
            first_msg = db.session.query(LLMMessage.content).filter(
                LLMMessage.session_id == session.id,
                LLMMessage.role == "user"
            ).order_by(LLMMessage.timestamp.asc()).first()

            preview = ""
            if first_msg and first_msg[0]:
                preview = first_msg[0][:100]
                if len(first_msg[0]) > 100:
                    preview += "..."

            results.append({
                "session_id": session.id,
                "project_id": session.project_id,
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "message_count": msg_count,
                "preview": preview
            })

        return jsonify({"sessions": results, "total": total})

    except Exception as e:
        logger.error(f"Error listing chat sessions: {e}")
        return jsonify({"error": "Failed to list sessions", "message": str(e)}), 500


@enhanced_chat_bp.route("/sessions/<session_id>", methods=["DELETE"])
@ensure_db_session_cleanup
def delete_chat_session(session_id):
    """Delete a single chat session and its messages."""
    try:
        from backend.models import db, LLMMessage, LLMSession

        session = db.session.query(LLMSession).filter(LLMSession.id == session_id).first()
        if not session:
            return jsonify({"error": "Session not found"}), 404

        deleted_messages = db.session.query(LLMMessage).filter(
            LLMMessage.session_id == session_id
        ).delete()

        db.session.delete(session)
        db.session.commit()

        # Evict from in-memory cache
        chat_manager = get_chat_manager()
        if session_id in chat_manager.chat_engines:
            del chat_manager.chat_engines[session_id]

        logger.info(f"Deleted session {session_id}: {deleted_messages} messages removed")

        return jsonify({
            "message": "Session deleted successfully",
            "session_id": session_id,
            "deleted_messages": deleted_messages
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting session {session_id}: {e}")
        return jsonify({"error": "Failed to delete session", "message": str(e)}), 500


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
        project_id = request.form.get('project_id')
        if project_id is not None:
            try:
                project_id = int(project_id)
            except (ValueError, TypeError):
                project_id = None

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

            # Save user message with image metadata in extra_data
            user_extra_data = {
                'imageUrl': image_url,
                'imageFileName': permanent_filename,
                'messageType': 'image_upload'
            }
            chat_manager._save_message(session_id, 'user', image_message, project_id=project_id, extra_data=user_extra_data)

            # Save the AI response with image metadata
            if analysis_result.get('analysis_successful'):
                chat_response = analysis_result.get('chat_response', 'Image analysis completed.')

                # Save assistant message with image metadata
                assistant_extra_data = {
                    'relatedImageUrl': image_url,
                    'imageAnalysis': True,
                    'analysisDetails': analysis_result.get('analysis_details', {})
                }
                chat_manager._save_message(session_id, 'assistant', chat_response, project_id=project_id, extra_data=assistant_extra_data)

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
                chat_manager._save_message(session_id, 'assistant', error_response_text, project_id=project_id)

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
        project_id = data.get('project_id')
        if project_id is not None:
            try:
                project_id = int(project_id)
            except (ValueError, TypeError):
                project_id = None

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

            chat_manager._save_message(session_id, 'user', user_message, project_id=project_id)

            # Handle generation result
            if generation_result.success:
                # Create response with image info
                chat_response = f"I've generated an image based on your prompt: '{prompt}'"
                if style != 'realistic':
                    chat_response += f" in {style} style"

                chat_response += f"\n\nGenerated using: {generation_result.model_used}"
                chat_response += f"\nGeneration time: {generation_result.generation_time:.2f}s"

                chat_manager._save_message(session_id, 'assistant', chat_response, project_id=project_id)

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

                chat_manager._save_message(session_id, 'assistant', chat_response, project_id=project_id)

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

    def _parse_context_string(self, context_string: str) -> List[Dict[str, str]]:
        """Parse context string from context manager into conversation format"""
        if not context_string:
            return []
        
        conversation_context = []
        lines = context_string.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Parse different context formats
            if line.startswith('[SYSTEM]'):
                # System messages
                content = line[8:].strip()  # Remove '[SYSTEM] '
                conversation_context.append({
                    'role': 'system',
                    'content': content
                })
            elif line.startswith('[SUMMARY]'):
                # Summary messages
                content = line[10:].strip()  # Remove '[SUMMARY] '
                conversation_context.append({
                    'role': 'assistant',
                    'content': f"Context Summary: {content}"
                })
            elif ':' in line:
                # Regular conversation format (user: message or assistant: message)
                try:
                    role_part, content = line.split(':', 1)
                    role = role_part.strip().lower()
                    content = content.strip()
                    
                    # Normalize role names
                    if role in ['user', 'assistant', 'system']:
                        conversation_context.append({
                            'role': role,
                            'content': content
                        })
                except ValueError:
                    # If splitting fails, treat as assistant message
                    conversation_context.append({
                        'role': 'assistant',
                        'content': line
                    })
            else:
                # Plain text, treat as assistant message
                conversation_context.append({
                    'role': 'assistant',
                    'content': line
                })
        
        return conversation_context
