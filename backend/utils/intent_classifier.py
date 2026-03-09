#!/usr/bin/env python3
"""
Intent Classifier - Smart Query Routing
Analyzes user input to determine the most appropriate handler

Supports two classification modes:
1. Semantic classification via SetFit model (when available)
2. Keyword-based classification (fallback)
"""

import logging
import re
from typing import Dict, List, Tuple, Optional
from enum import Enum

logger = logging.getLogger(__name__)

# Try to import semantic classifier
_semantic_classifier = None
try:
    from backend.services.intent_service import get_intent_classifier as get_semantic_classifier
    _semantic_classifier = get_semantic_classifier()
    if _semantic_classifier.is_model_loaded():
        logger.info("Semantic intent classifier loaded successfully")
    else:
        logger.info("Semantic classifier using keyword fallback (no model loaded)")
except ImportError:
    logger.info("Semantic intent service not available, using keyword classification")
except Exception as e:
    logger.warning(f"Error loading semantic classifier: {e}")

class IntentType(Enum):
    """Intent classification types"""
    COMMAND = "COMMAND"              # /codegen, /analyze commands
    DATABASE_QUERY = "DATABASE_QUERY"  # Count/list requests
    RAG_SEARCH = "RAG_SEARCH"        # Document content search
    WEB_SEARCH = "WEB_SEARCH"        # Current info requests
    GENERAL_CHAT = "GENERAL_CHAT"    # Default conversational

class IntentClassifier:
    """
    Smart intent classification for query routing
    Determines the best handler for user queries
    """
    
    def __init__(self):
        # Command pattern - starts with /
        self.command_pattern = re.compile(r'^\s*/(\w+)', re.IGNORECASE)
        
        # Database query patterns
        self.database_keywords = [
            'how many', 'count', 'list', 'show me', 'total', 'number of',
            'clients', 'projects', 'documents', 'files', 'tasks',
            'all clients', 'all projects', 'all documents',
            'tell me how many', 'how many clients', 'how many projects', 'how many documents'
        ]
        
        # Document search patterns  
        self.document_keywords = [
            'document', 'contract', 'agreement', 'uploaded', 'file content',
            'what does', 'says about', 'mentions', 'contains', 'find in',
            'search for', 'look for', 'in the document'
        ]
        
        # Web search patterns (ENHANCED - more comprehensive)
        self.web_keywords = [
            'latest', 'current', 'recent', 'news', 'now',
            'what\'s new', 'update', 'happening', 'online', 'internet',
            'search web', 'look up', 'find out about', 'research',
            'weather', 'temperature', 'forecast', 'stock price',
            'sports score', 'lottery', 'duckduckgo', 'google',
            'check', 'find', 'search for', 'what is the', 'who is',
            'when did', 'where is', 'competitors', 'ranking'
        ]
        
        # Exclude casual "today" usage patterns
        self.casual_today_patterns = [
            'how are you today', 'today?', 'doing today', 'feel today'
        ]
        
        # Context length thresholds
        self.max_context_lengths = {
            IntentType.COMMAND: 5000,        # Minimal context for commands
            IntentType.DATABASE_QUERY: 10000,  # Direct SQL, light context
            IntentType.RAG_SEARCH: 50000,    # Focused document search
            IntentType.WEB_SEARCH: 30000,    # Web results + light context
            IntentType.GENERAL_CHAT: 100000  # Full context allowed
        }
    
    def classify_intent(self, message: str) -> Tuple[IntentType, float, Dict]:
        """
        Classify user intent with confidence score and metadata
        Integrates with existing Rules system for enhanced detection

        Uses semantic classification (SetFit) when available, with keyword fallback.

        Args:
            message: User input message

        Returns:
            Tuple of (IntentType, confidence_score, metadata)
        """
        message_lower = message.lower().strip()
        metadata = {'original_message': message, 'keywords_found': [], 'classifier_type': 'keyword'}

        # 1. Command Detection (highest priority - always keyword-based)
        if self._is_command(message):
            command_match = self.command_pattern.match(message)
            command = command_match.group(1) if command_match else 'unknown'
            metadata['command'] = command
            metadata['route_to'] = 'query_api'  # Route to existing command processor
            logger.info(f"Intent: COMMAND detected - /{command}")
            return IntentType.COMMAND, 0.95, metadata

        # 2. Database Query Detection (keyword-based - specific to this app)
        db_confidence, db_keywords = self._check_keywords(message_lower, self.database_keywords)
        if db_confidence > 0.6:
            metadata['keywords_found'] = db_keywords
            logger.info(f"Intent: DATABASE_QUERY detected - keywords: {db_keywords}")
            return IntentType.DATABASE_QUERY, db_confidence, metadata

        # 3. Document Search Detection (keyword-based - specific to this app)
        doc_confidence, doc_keywords = self._check_keywords(message_lower, self.document_keywords)
        if doc_confidence > 0.5:
            metadata['keywords_found'] = doc_keywords
            logger.info(f"Intent: RAG_SEARCH detected - keywords: {doc_keywords}")
            return IntentType.RAG_SEARCH, doc_confidence, metadata

        # 4. Web Search / Real-time Detection - Use semantic classifier if available
        is_realtime = False
        semantic_confidence = 0.0

        if _semantic_classifier is not None:
            try:
                semantic_intent, semantic_confidence = _semantic_classifier.classify(message)
                metadata['classifier_type'] = 'semantic'
                metadata['semantic_confidence'] = semantic_confidence

                if semantic_intent == 'realtime' and semantic_confidence > 0.6:
                    is_realtime = True
                    metadata['semantic_intent'] = semantic_intent
                    logger.info(f"Intent: WEB_SEARCH detected (semantic) - confidence: {semantic_confidence:.2f}")
                    return IntentType.WEB_SEARCH, semantic_confidence, metadata

            except Exception as e:
                logger.warning(f"Semantic classification failed: {e}, falling back to keywords")
                metadata['classifier_type'] = 'keyword'

        # 5. Keyword-based Web Search Detection (fallback)
        web_confidence, web_keywords = self._check_keywords(message_lower, self.web_keywords)

        # Filter out casual "today" usage
        is_casual_today = any(pattern in message_lower for pattern in self.casual_today_patterns)
        if web_confidence > 0.4 and not is_casual_today:
            metadata['keywords_found'] = web_keywords
            logger.info(f"Intent: WEB_SEARCH detected (keywords) - keywords: {web_keywords}")
            return IntentType.WEB_SEARCH, web_confidence, metadata

        # 6. Default to General Chat
        logger.info("Intent: GENERAL_CHAT (default)")
        return IntentType.GENERAL_CHAT, 0.3, metadata

    def is_realtime_query(self, message: str) -> Tuple[bool, float]:
        """
        Check if a query requires real-time data using semantic classification.

        This is useful for applying honesty steering to queries that the model
        cannot accurately answer without current data.

        Args:
            message: User input message

        Returns:
            Tuple of (is_realtime, confidence)
        """
        if _semantic_classifier is not None:
            try:
                intent, confidence = _semantic_classifier.classify(message)
                return intent == 'realtime', confidence
            except Exception as e:
                logger.warning(f"Semantic realtime check failed: {e}")

        # Fallback to keyword check
        web_confidence, _ = self._check_keywords(message.lower(), self.web_keywords)
        return web_confidence > 0.5, web_confidence
    
    def _is_command(self, message: str) -> bool:
        """Check if message is a command (starts with /)"""
        return bool(self.command_pattern.match(message.strip()))
    
    def _check_keywords(self, message: str, keywords: List[str]) -> Tuple[float, List[str]]:
        """
        Check for keyword matches and return confidence score
        
        Args:
            message: Lowercase message text
            keywords: List of keywords to check
            
        Returns:
            Tuple of (confidence_score, matched_keywords)
        """
        matched_keywords = []
        total_matches = 0
        
        for keyword in keywords:
            if keyword in message:
                matched_keywords.append(keyword)
                # Weight longer keywords more heavily
                total_matches += len(keyword.split())
        
        if not matched_keywords:
            return 0.0, []
        
        # Calculate confidence based on matches and message length
        confidence = min(0.95, (total_matches / max(1, len(message.split()))) + 0.3)
        return confidence, matched_keywords
    
    def get_context_limit(self, intent_type: IntentType) -> int:
        """Get recommended context length limit for intent type"""
        return self.max_context_lengths.get(intent_type, 100000)
    
    def should_use_web_search(self, intent_type: IntentType, message: str) -> bool:
        """Determine if web search should be enabled for this intent"""
        if intent_type == IntentType.WEB_SEARCH:
            return True
        
        # Also enable for general chat if it contains web-related terms
        if intent_type == IntentType.GENERAL_CHAT:
            web_confidence, _ = self._check_keywords(message.lower(), self.web_keywords)
            return web_confidence > 0.2
        
        return False
    
    def get_routing_priority(self, intent_type: IntentType) -> str:
        """Get processing priority for the intent type"""
        priority_map = {
            IntentType.COMMAND: "HIGH",
            IntentType.DATABASE_QUERY: "HIGH", 
            IntentType.RAG_SEARCH: "MEDIUM",
            IntentType.WEB_SEARCH: "MEDIUM",
            IntentType.GENERAL_CHAT: "LOW"
        }
        return priority_map.get(intent_type, "LOW")

# Singleton instance for global use
intent_classifier = IntentClassifier()

def classify_user_intent(message: str) -> Tuple[IntentType, float, Dict]:
    """
    Convenience function for intent classification
    
    Args:
        message: User input message
        
    Returns:
        Tuple of (IntentType, confidence_score, metadata)
    """
    return intent_classifier.classify_intent(message)

def get_intent_context_limit(intent_type: IntentType) -> int:
    """Get context limit for intent type"""
    return intent_classifier.get_context_limit(intent_type)


# Re-export real-time data detection from capability_awareness for convenience
try:
    from backend.utils.capability_awareness import requires_realtime_data
except ImportError:
    # Fallback if capability_awareness not available
    def requires_realtime_data(message: str) -> Tuple[bool, str]:
        """Fallback: Check if query requires real-time data."""
        realtime_keywords = ['lottery', 'lotto', 'stock price', 'weather', 'score', 'tonight']
        msg_lower = message.lower()
        for kw in realtime_keywords:
            if kw in msg_lower:
                return True, f"Query contains '{kw}' which requires real-time data"
        return False, ""

def should_enable_web_search(intent_type: IntentType, message: str) -> bool:
    """Check if web search should be enabled"""
    return intent_classifier.should_use_web_search(intent_type, message)


def is_realtime_query(message: str) -> Tuple[bool, float]:
    """
    Check if a query requires real-time data.

    Uses semantic classification when available.

    Args:
        message: User input message

    Returns:
        Tuple of (is_realtime, confidence)
    """
    return intent_classifier.is_realtime_query(message)


def get_classifier_status() -> Dict:
    """Get information about the intent classifier configuration."""
    status = {
        "keyword_classifier": "active",
        "semantic_classifier": "not_loaded"
    }

    if _semantic_classifier is not None:
        try:
            model_info = _semantic_classifier.get_model_info()
            status["semantic_classifier"] = "active" if model_info.get("model_loaded") else "keyword_fallback"
            status["semantic_model_info"] = model_info
        except Exception:
            pass

    return status