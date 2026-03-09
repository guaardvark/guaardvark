"""
Advanced Memory Manager for RAG Chat System
Makes chat memory work like Claude Code's memory system

Features:
- Message importance scoring
- Smart context summarization
- Long-term memory persistence
- Entity and topic tracking
- Conversation threading
- Automatic memory consolidation
"""

import json
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
import re

logger = logging.getLogger(__name__)


class MessageImportance:
    """Score message importance for memory retention"""

    # Importance indicators
    HIGH_VALUE_PATTERNS = [
        r'\b(important|remember|note|key point|crucial|critical)\b',
        r'\b(my name is|I am|I work)\b',  # Identity information
        r'\b(project|client|website|document) (?:called|named|is)\b',  # Entity definitions
        r'\b(always|never|every time|whenever)\b',  # Behavioral rules
        r'\b(prefer|like|want|need)\b',  # Preferences
    ]

    DECISION_PATTERNS = [
        r'\b(decided|agreed|confirmed|approved)\b',
        r'\b(will|going to|planning to)\b',
        r'\b(yes|no|okay|sure|definitely)\b.*\?',  # Responses to decisions
    ]

    FACTUAL_PATTERNS = [
        r'\b(is|are|was|were) (?:a|an|the)?\s*\w+',  # Factual statements
        r'\d+',  # Numbers/data
        r'[A-Z][a-z]+ (?:is|are|was|were)',  # Named entities with facts
    ]

    @classmethod
    def score_message(cls, role: str, content: str, metadata: Dict = None) -> float:
        """
        Score message importance (0-1.0)

        Args:
            role: 'user' or 'assistant'
            content: Message content
            metadata: Additional context

        Returns:
            Importance score 0-1.0
        """
        score = 0.5  # Base score
        content_lower = content.lower()

        # User messages generally more important (contain intent)
        if role == 'user':
            score += 0.1

        # Check high-value patterns
        for pattern in cls.HIGH_VALUE_PATTERNS:
            if re.search(pattern, content_lower):
                score += 0.15
                break

        # Check decision patterns
        for pattern in cls.DECISION_PATTERNS:
            if re.search(pattern, content_lower):
                score += 0.1
                break

        # Check factual patterns
        factual_matches = sum(1 for pattern in cls.FACTUAL_PATTERNS
                            if re.search(pattern, content_lower))
        score += min(factual_matches * 0.05, 0.15)

        # Short messages less important (unless questions)
        if len(content) < 50:
            if '?' in content:
                score += 0.05  # Questions are important
            else:
                score -= 0.1

        # Very long messages often contain context
        if len(content) > 500:
            score += 0.1

        # Code blocks are important
        if '```' in content or 'def ' in content or 'class ' in content:
            score += 0.15

        # Entity mentions
        if metadata and metadata.get('entities'):
            score += len(metadata['entities']) * 0.05

        # Cap at 1.0
        return min(score, 1.0)


class EntityTracker:
    """Track entities (clients, projects, people) across conversation"""

    def __init__(self):
        self.entities = defaultdict(lambda: {
            'mentions': [],
            'facts': [],
            'last_mentioned': None
        })

    def extract_entities(self, content: str, timestamp: datetime) -> List[Dict]:
        """Extract entities from message"""
        entities_found = []

        # Client/Company names (capitalized, possibly with &)
        client_pattern = r'\b([A-Z][a-z]+(?:\s+&?\s+[A-Z][a-z]+)?)\b'
        for match in re.finditer(client_pattern, content):
            entity_name = match.group(1)
            if len(entity_name) > 2 and entity_name not in ['The', 'This', 'That', 'What', 'When']:
                entities_found.append({
                    'type': 'client',
                    'name': entity_name,
                    'context': content[max(0, match.start()-50):min(len(content), match.end()+50)]
                })

        # Project references
        project_pattern = r'project\s+(?:called\s+|named\s+)?[\"\']?([A-Za-z0-9\s-]+)[\"\']?'
        for match in re.finditer(project_pattern, content, re.IGNORECASE):
            entities_found.append({
                'type': 'project',
                'name': match.group(1).strip(),
                'context': content[max(0, match.start()-30):min(len(content), match.end()+30)]
            })

        # Website/URL references
        url_pattern = r'(?:https?://)?(?:www\.)?([a-zA-Z0-9-]+\.[a-zA-Z]{2,})'
        for match in re.finditer(url_pattern, content):
            entities_found.append({
                'type': 'website',
                'name': match.group(1),
                'context': content[max(0, match.start()-20):min(len(content), match.end()+20)]
            })

        # Update entity tracking
        for entity in entities_found:
            key = f"{entity['type']}:{entity['name'].lower()}"
            self.entities[key]['mentions'].append(timestamp)
            self.entities[key]['last_mentioned'] = timestamp

        return entities_found

    def get_recent_entities(self, since: datetime = None) -> List[Dict]:
        """Get recently mentioned entities"""
        if since is None:
            since = datetime.now() - timedelta(hours=1)

        recent = []
        for key, data in self.entities.items():
            if data['last_mentioned'] and data['last_mentioned'] > since:
                entity_type, name = key.split(':', 1)
                recent.append({
                    'type': entity_type,
                    'name': name,
                    'mention_count': len(data['mentions']),
                    'last_mentioned': data['last_mentioned']
                })

        # Sort by recency and mention count
        recent.sort(key=lambda x: (x['last_mentioned'], x['mention_count']), reverse=True)
        return recent


class ConversationSummarizer:
    """Summarize conversation history for efficient memory"""

    @staticmethod
    def create_summary(messages: List[Dict], max_length: int = 500) -> str:
        """
        Create a concise summary of messages

        Args:
            messages: List of message dicts with role, content, timestamp
            max_length: Maximum summary length

        Returns:
            Summary string
        """
        if not messages:
            return ""

        # Extract key points
        key_points = []
        topics = set()
        decisions = []
        entities_mentioned = set()

        for msg in messages:
            content = msg.get('content', '')
            role = msg.get('role', 'unknown')

            # Extract topics (capitalized phrases)
            topic_matches = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b', content)
            topics.update(topic_matches[:3])  # Limit to prevent bloat

            # Extract decisions
            if re.search(r'\b(decided|confirmed|will|going to)\b', content, re.IGNORECASE):
                # Get sentence containing decision
                sentences = content.split('. ')
                for sent in sentences:
                    if re.search(r'\b(decided|confirmed|will|going to)\b', sent, re.IGNORECASE):
                        decisions.append(sent.strip()[:100])
                        break

            # High importance messages become key points
            importance = MessageImportance.score_message(role, content)
            if importance > 0.7:
                # Extract first sentence or first 100 chars
                first_sentence = content.split('.')[0].strip()
                if len(first_sentence) > 100:
                    first_sentence = first_sentence[:100] + "..."
                key_points.append(first_sentence)

        # Build summary
        summary_parts = []

        if topics:
            summary_parts.append(f"Topics: {', '.join(list(topics)[:5])}")

        if decisions:
            summary_parts.append(f"Decisions: {'; '.join(decisions[:2])}")

        if key_points:
            summary_parts.append(f"Key points: {'; '.join(key_points[:3])}")

        summary = " | ".join(summary_parts)

        # Truncate if too long
        if len(summary) > max_length:
            summary = summary[:max_length-3] + "..."

        return summary or "General conversation"


class MemoryManager:
    """
    Advanced memory management for chat sessions
    Makes chat work like Claude Code with persistent, intelligent memory
    """

    def __init__(self, db_session=None):
        """
        Initialize memory manager

        Args:
            db_session: SQLAlchemy session for persistence
        """
        self.db = db_session
        self.entity_trackers = {}  # session_id -> EntityTracker (per-session isolation)
        self.session_summaries = {}  # session_id -> list of summaries
        self.message_cache = {}  # session_id -> recent messages

    def _get_entity_tracker(self, session_id: str) -> EntityTracker:
        """Get or create a session-scoped EntityTracker"""
        if session_id not in self.entity_trackers:
            self.entity_trackers[session_id] = EntityTracker()
        return self.entity_trackers[session_id]

    def score_conversation_context(self, session_id: str, messages: List[Dict]) -> List[Dict]:
        """
        Score and rank messages by importance for context inclusion

        Args:
            session_id: Chat session ID
            messages: List of messages with role, content, timestamp

        Returns:
            Messages with importance scores added, sorted by importance
        """
        scored_messages = []

        tracker = self._get_entity_tracker(session_id)
        for msg in messages:
            # Extract entities
            entities = tracker.extract_entities(
                msg.get('content', ''),
                msg.get('timestamp', datetime.now())
            )

            # Score importance
            importance = MessageImportance.score_message(
                msg.get('role', 'user'),
                msg.get('content', ''),
                {'entities': entities}
            )

            # Add metadata
            msg_copy = msg.copy()
            msg_copy['importance'] = importance
            msg_copy['entities'] = entities
            scored_messages.append(msg_copy)

        # Sort by importance (descending)
        scored_messages.sort(key=lambda x: x['importance'], reverse=True)

        return scored_messages

    def get_smart_context(self, session_id: str, messages: List[Dict],
                         max_messages: int = 10, max_tokens: int = 8000) -> List[Dict]:
        """
        Get smart context window with most important messages

        Args:
            session_id: Chat session ID
            messages: All available messages
            max_messages: Maximum messages to include
            max_tokens: Approximate token limit

        Returns:
            Optimized list of messages for context
        """
        if not messages:
            return []

        # Always include most recent messages (continuity)
        recent_count = min(5, len(messages))
        recent_messages = messages[-recent_count:]
        recent_ids = {id(msg) for msg in recent_messages}

        # Score remaining messages
        older_messages = messages[:-recent_count] if len(messages) > recent_count else []
        scored_older = self.score_conversation_context(session_id, older_messages)

        # Build context: recent + important older messages
        context = []
        token_estimate = 0

        # Add recent messages first (chronological order)
        for msg in recent_messages:
            msg_tokens = len(msg.get('content', '')) // 4  # Rough estimate
            if token_estimate + msg_tokens < max_tokens:
                context.append(msg)
                token_estimate += msg_tokens

        # Add important older messages (up to limit)
        important_added = 0
        for msg in scored_older:
            if important_added >= (max_messages - recent_count):
                break
            if id(msg) not in recent_ids:
                msg_tokens = len(msg.get('content', '')) // 4
                if token_estimate + msg_tokens < max_tokens:
                    # Insert before recent messages to maintain chronology
                    context.insert(0, msg)
                    token_estimate += msg_tokens
                    important_added += 1

        # Sort by timestamp for correct order
        context.sort(key=lambda x: x.get('timestamp', datetime.min))

        return context

    def create_session_summary(self, session_id: str, messages: List[Dict],
                              window_size: int = 20) -> Dict:
        """
        Create a rolling summary of conversation

        Args:
            session_id: Chat session ID
            messages: Messages to summarize
            window_size: How many messages to summarize at once

        Returns:
            Summary metadata
        """
        if len(messages) < window_size:
            return {}

        # Summarize in windows
        summaries = []
        for i in range(0, len(messages), window_size):
            window = messages[i:i+window_size]
            if len(window) >= 3:  # Only summarize if enough messages
                summary = ConversationSummarizer.create_summary(window)
                summaries.append({
                    'start_index': i,
                    'end_index': min(i + window_size, len(messages)),
                    'summary': summary,
                    'message_count': len(window),
                    'created_at': datetime.now()
                })

        # Store summaries
        if session_id not in self.session_summaries:
            self.session_summaries[session_id] = []
        self.session_summaries[session_id].extend(summaries)

        return {
            'summary_count': len(summaries),
            'total_messages_summarized': len(messages),
            'summaries': summaries
        }

    def get_memory_context(self, session_id: str, current_message: str,
                          all_messages: List[Dict], max_tokens: int = 4000) -> Dict:
        """
        Get comprehensive memory context for a message

        Args:
            session_id: Chat session ID
            current_message: The current user message
            all_messages: All available messages in session
            max_tokens: Maximum context tokens

        Returns:
            Memory context with messages, entities, summaries
        """
        # Get smart context (important + recent messages)
        context_messages = self.get_smart_context(
            session_id,
            all_messages,
            max_messages=15,
            max_tokens=max_tokens
        )

        # Extract entities from current message
        tracker = self._get_entity_tracker(session_id)
        current_entities = tracker.extract_entities(
            current_message,
            datetime.now()
        )

        # Get recently mentioned entities
        recent_entities = tracker.get_recent_entities(
            since=datetime.now() - timedelta(hours=2)
        )

        # Get session summaries
        summaries = self.session_summaries.get(session_id, [])
        recent_summaries = summaries[-3:] if summaries else []  # Last 3 summaries

        return {
            'context_messages': context_messages,
            'current_entities': current_entities,
            'recent_entities': recent_entities,
            'summaries': recent_summaries,
            'total_messages': len(all_messages),
            'context_message_count': len(context_messages),
            'entity_count': len(recent_entities)
        }

    def format_memory_for_prompt(self, memory_context: Dict) -> str:
        """
        Format memory context for system prompt

        Args:
            memory_context: Output from get_memory_context()

        Returns:
            Formatted string for system prompt
        """
        parts = []

        # Add conversation summaries
        if memory_context.get('summaries'):
            summary_text = " → ".join([s['summary'] for s in memory_context['summaries']])
            parts.append(f"**Conversation History**: {summary_text}")

        # Add recent entities
        if memory_context.get('recent_entities'):
            entities = memory_context['recent_entities'][:5]
            entity_list = ", ".join([f"{e['name']} ({e['type']})" for e in entities])
            parts.append(f"**Context Entities**: {entity_list}")

        # Add current entities
        if memory_context.get('current_entities'):
            current = memory_context['current_entities'][:3]
            current_list = ", ".join([e['name'] for e in current])
            parts.append(f"**Referenced**: {current_list}")

        # Add stats
        parts.append(f"**Memory**: {memory_context.get('context_message_count', 0)}/{memory_context.get('total_messages', 0)} messages")

        return " | ".join(parts)

    def persist_memory(self, session_id: str):
        """
        Persist memory state to database

        Args:
            session_id: Chat session ID
        """
        if not self.db:
            logger.warning("No database session available for memory persistence")
            return

        try:
            from backend.models import LLMSession, SystemSetting

            # Store memory metadata in system settings
            memory_key = f"memory_state_{session_id}"
            memory_data = {
                'entities': dict(self._get_entity_tracker(session_id).entities),
                'summaries': self.session_summaries.get(session_id, []),
                'last_updated': datetime.now().isoformat()
            }

            # Serialize for storage
            memory_json = json.dumps(memory_data, default=str)

            # Store or update
            setting = self.db.query(SystemSetting).filter_by(key=memory_key).first()
            if setting:
                setting.value = memory_json
                setting.updated_at = datetime.now()
            else:
                setting = SystemSetting(key=memory_key, value=memory_json)
                self.db.add(setting)

            self.db.commit()
            logger.info(f"💾 Memory persisted for session {session_id}")

        except Exception as e:
            logger.error(f"Failed to persist memory: {e}")
            if self.db:
                self.db.rollback()

    def load_memory(self, session_id: str):
        """
        Load persisted memory state from database

        Args:
            session_id: Chat session ID
        """
        if not self.db:
            logger.warning("No database session available for memory loading")
            return

        try:
            from backend.models import SystemSetting

            memory_key = f"memory_state_{session_id}"
            setting = self.db.query(SystemSetting).filter_by(key=memory_key).first()

            if setting and setting.value:
                memory_data = json.loads(setting.value)

                # Restore entities
                if 'entities' in memory_data:
                    for key, data in memory_data['entities'].items():
                        # Convert ISO strings back to datetime
                        if data.get('last_mentioned'):
                            data['last_mentioned'] = datetime.fromisoformat(data['last_mentioned'])
                        if 'mentions' in data:
                            data['mentions'] = [datetime.fromisoformat(m) if isinstance(m, str) else m
                                              for m in data['mentions']]
                        self._get_entity_tracker(session_id).entities[key] = data

                # Restore summaries
                if 'summaries' in memory_data:
                    self.session_summaries[session_id] = memory_data['summaries']

                tracker = self._get_entity_tracker(session_id)
                logger.info(f"Memory loaded for session {session_id}: {len(tracker.entities)} entities")

        except Exception as e:
            logger.error(f"Failed to load memory: {e}")

    def cleanup_old_memory(self, days: int = 30):
        """
        Clean up old memory states

        Args:
            days: Remove memory older than this many days
        """
        if not self.db:
            return

        try:
            from backend.models import SystemSetting

            cutoff = datetime.now() - timedelta(days=days)

            # Find old memory states
            old_settings = self.db.query(SystemSetting).filter(
                SystemSetting.key.like('memory_state_%'),
                SystemSetting.updated_at < cutoff
            ).all()

            for setting in old_settings:
                self.db.delete(setting)

            self.db.commit()
            logger.info(f"🧹 Cleaned up {len(old_settings)} old memory states")

        except Exception as e:
            logger.error(f"Failed to cleanup memory: {e}")
            if self.db:
                self.db.rollback()


# Singleton instance
_memory_manager = None

def get_memory_manager(db_session=None):
    """Get or create global memory manager instance"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager(db_session)
    elif db_session and not _memory_manager.db:
        _memory_manager.db = db_session
    return _memory_manager
