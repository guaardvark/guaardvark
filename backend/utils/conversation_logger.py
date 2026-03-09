"""
Enhanced conversation logging system with detailed summaries and offline storage.
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from collections import Counter

logger = logging.getLogger(__name__)

@dataclass
class ConversationEntry:
    """Single conversation exchange entry"""
    timestamp: datetime
    user_message: str
    assistant_response: str
    conversation_type: str
    token_count: int
    processing_time: float
    context_used: List[str]
    metadata: Dict[str, Any]

@dataclass
class ConversationSession:
    """Complete conversation session with metadata"""
    session_id: str
    start_time: datetime
    end_time: Optional[datetime]
    entries: List[ConversationEntry]
    total_tokens: int
    total_processing_time: float
    summary: str = ""
    
    def get_duration_minutes(self) -> float:
        """Get session duration in minutes"""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds() / 60
        return (datetime.now() - self.start_time).total_seconds() / 60

class ConversationLogger:
    """Offline conversation logging system with detailed summaries"""
    
    def __init__(self, storage_dir: str = None):
        if storage_dir is None:
            from backend.config import STORAGE_DIR
            import os
            storage_dir = os.path.join(STORAGE_DIR, "conversations")
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        (self.storage_dir / "sessions").mkdir(exist_ok=True)
        (self.storage_dir / "summaries").mkdir(exist_ok=True)
        (self.storage_dir / "daily").mkdir(exist_ok=True)
        
        self.active_sessions: Dict[str, ConversationSession] = {}
        
        logger.info(f"ConversationLogger initialized with storage: {self.storage_dir}")
    
    def start_session(self, session_id: str) -> None:
        """Start a new conversation session"""
        session = ConversationSession(
            session_id=session_id,
            start_time=datetime.now(),
            end_time=None,
            entries=[],
            total_tokens=0,
            total_processing_time=0.0
        )
        
        self.active_sessions[session_id] = session
        logger.info(f"Started conversation session: {session_id}")
    
    def log_exchange(self, session_id: str, user_message: str, assistant_response: str,
                     conversation_type: str = "general", token_count: int = 0,
                     processing_time: float = 0.0, context_used: List[str] = None,
                     metadata: Dict[str, Any] = None) -> None:
        """Log a conversation exchange"""
        if session_id not in self.active_sessions:
            self.start_session(session_id)
        
        session = self.active_sessions[session_id]
        
        entry = ConversationEntry(
            timestamp=datetime.now(),
            user_message=user_message,
            assistant_response=assistant_response,
            conversation_type=conversation_type,
            token_count=token_count,
            processing_time=processing_time,
            context_used=context_used or [],
            metadata=metadata or {}
        )
        
        session.entries.append(entry)
        session.total_tokens += token_count
        session.total_processing_time += processing_time
        
        # Save incrementally for crash recovery
        self._save_session_incrementally(session_id)
        
        logger.debug(f"Logged exchange for session {session_id}: {len(user_message)} chars user, {len(assistant_response)} chars assistant")
    
    def end_session(self, session_id: str) -> None:
        """End a conversation session and generate summary"""
        if session_id not in self.active_sessions:
            logger.warning(f"Attempted to end non-existent session: {session_id}")
            return
        
        session = self.active_sessions[session_id]
        session.end_time = datetime.now()
        
        # Generate summary
        session.summary = self._generate_session_summary(session)
        
        # Save complete session
        self._save_complete_session(session)
        
        # Add to daily summary
        self._save_daily_summary(session)
        
        # Remove from active sessions
        del self.active_sessions[session_id]
        
        logger.info(f"Ended conversation session: {session_id}")
    
    def _generate_session_summary(self, session: ConversationSession) -> str:
        """Generate enhanced detailed summary of conversation with comprehensive analysis"""
        if not session.entries:
            return "Empty conversation session"
        
        # Extract enhanced analytics
        topics = self._extract_key_topics(session)
        question_types = self._analyze_question_types([entry.user_message for entry in session.entries])
        response_patterns = self._analyze_response_patterns([entry.assistant_response for entry in session.entries])
        
        # Calculate enhanced statistics
        total_user_tokens = sum(entry.token_count // 2 for entry in session.entries)  # Estimate user portion
        total_assistant_tokens = session.total_tokens - total_user_tokens
        avg_processing_time = session.total_processing_time / len(session.entries)
        
        summary_lines = [
            f"# Enhanced Conversation Summary - {session.session_id}",
            f"",
            f"## Session Overview",
            f"- **Duration**: {session.start_time.strftime('%Y-%m-%d %H:%M:%S')} - {session.end_time.strftime('%H:%M:%S') if session.end_time else 'ongoing'}",
            f"- **Total Exchanges**: {len(session.entries)}",
            f"- **Total Tokens**: {session.total_tokens}",
            f"- **Processing Time**: {session.total_processing_time:.2f}s",
            f"- **Average Response Time**: {avg_processing_time:.2f}s",
            f"",
            f"## Content Analysis",
            f"- **Key Topics**: {', '.join(topics[:5]) if topics else 'General conversation'}",
            f"- **Question Types**: {self._format_question_types(question_types)}",
            f"- **Response Patterns**: {self._format_response_patterns(response_patterns)}",
            f"- **Conversation Complexity**: {self._assess_conversation_complexity_from_entries(session.entries)}",
            f"",
            f"## Token Distribution",
            f"- **User Tokens**: ~{total_user_tokens} ({total_user_tokens/session.total_tokens*100:.1f}%)",
            f"- **Assistant Tokens**: ~{total_assistant_tokens} ({total_assistant_tokens/session.total_tokens*100:.1f}%)",
            f"- **Avg Tokens per Exchange**: {session.total_tokens/len(session.entries):.1f}",
            f"",
            f"## Key Interactions:",
        ]
        
        for i, entry in enumerate(session.entries, 1):
            # Create enhanced bulletpoint summary for each exchange
            user_preview = entry.user_message[:100] + "..." if len(entry.user_message) > 100 else entry.user_message
            response_preview = entry.assistant_response[:150] + "..." if len(entry.assistant_response) > 150 else entry.assistant_response
            
            summary_lines.extend([
                f"",
                f"### Exchange {i} ({entry.timestamp.strftime('%H:%M:%S')})",
                f"- **User**: {user_preview}",
                f"- **Assistant**: {response_preview}",
                f"- **Type**: {entry.conversation_type}",
                f"- **Tokens**: {entry.token_count}",
                f"- **Processing**: {entry.processing_time:.2f}s",
                f"- **Efficiency**: {entry.token_count/entry.processing_time:.1f} tokens/sec" if entry.processing_time > 0 else "- **Efficiency**: N/A"
            ])
            
            if entry.context_used:
                summary_lines.append(f"- **Context Used**: {', '.join(entry.context_used[:3])}{'...' if len(entry.context_used) > 3 else ''}")
            
            if entry.metadata:
                important_metadata = {k: v for k, v in entry.metadata.items() if k in ['project_id', 'document_analyzed', 'error_occurred', 'rag_used']}
                if important_metadata:
                    summary_lines.append(f"- **Metadata**: {important_metadata}")
            
            # Limit detailed exchanges to prevent overly long summaries
            if i >= 15:
                summary_lines.append(f"")
                summary_lines.append(f"... and {len(session.entries) - 15} more exchanges")
                break
        
        # Add session insights
        summary_lines.extend([
            f"",
            f"## Session Insights",
            f"- **Most Active Period**: {self._find_most_active_period_from_entries(session.entries)}",
            f"- **User Engagement**: {self._assess_user_engagement_from_entries(session.entries)}",
            f"- **Context Usage**: {self._analyze_context_usage(session.entries)}",
        ])
        
        return "\\n".join(summary_lines)
    
    def _extract_key_topics(self, session: ConversationSession) -> List[str]:
        """Extract key topics from conversation"""
        topics = set()
        
        for entry in session.entries:
            # Extract topics from user messages and responses
            text = (entry.user_message + " " + entry.assistant_response).lower()
            
            # Topic detection keywords
            topic_keywords = {
                'coding': ['code', 'programming', 'function', 'api', 'debug', 'error', 'script'],
                'documentation': ['docs', 'documentation', 'readme', 'guide', 'manual'],
                'database': ['database', 'sql', 'query', 'table', 'model', 'migration'],
                'frontend': ['frontend', 'react', 'component', 'ui', 'interface', 'css'],
                'backend': ['backend', 'server', 'service', 'celery', 'worker']
            }
            
            for topic, keywords in topic_keywords.items():
                if any(keyword in text for keyword in keywords):
                    topics.add(topic)
        
        return list(topics)
    
    def _analyze_question_types(self, user_messages: List[str]) -> Dict[str, int]:
        """Analyze types of questions asked by the user"""
        question_types = {
            'how_to': 0,
            'what_is': 0,
            'debugging': 0,
            'explanation': 0,
            'implementation': 0,
            'general': 0
        }
        
        for message in user_messages:
            message_lower = message.lower()
            if any(phrase in message_lower for phrase in ['how to', 'how do', 'how can']):
                question_types['how_to'] += 1
            elif any(phrase in message_lower for phrase in ['what is', 'what are', 'what does']):
                question_types['what_is'] += 1
            elif any(phrase in message_lower for phrase in ['error', 'bug', 'fix', 'debug', 'problem']):
                question_types['debugging'] += 1
            elif any(phrase in message_lower for phrase in ['explain', 'why', 'understand']):
                question_types['explanation'] += 1
            elif any(phrase in message_lower for phrase in ['implement', 'create', 'build', 'add']):
                question_types['implementation'] += 1
            else:
                question_types['general'] += 1
        
        return question_types
    
    def _analyze_response_patterns(self, assistant_responses: List[str]) -> Dict[str, int]:
        """Analyze patterns in assistant responses"""
        patterns = {
            'code_provided': 0,
            'explanation_only': 0,
            'step_by_step': 0,
            'error_help': 0,
            'recommendations': 0
        }
        
        for response in assistant_responses:
            response_lower = response.lower()
            if '```' in response or 'code' in response_lower:
                patterns['code_provided'] += 1
            elif any(phrase in response_lower for phrase in ['step 1', 'first', 'then', 'next', 'finally']):
                patterns['step_by_step'] += 1
            elif any(phrase in response_lower for phrase in ['error', 'fix', 'problem', 'issue']):
                patterns['error_help'] += 1
            elif any(phrase in response_lower for phrase in ['recommend', 'suggest', 'consider', 'best practice']):
                patterns['recommendations'] += 1
            else:
                patterns['explanation_only'] += 1
        
        return patterns
    
    def _format_question_types(self, question_types: Dict[str, int]) -> str:
        """Format question types for summary"""
        total = sum(question_types.values())
        if total == 0:
            return "None"
        
        formatted = []
        for q_type, count in question_types.items():
            if count > 0:
                percentage = (count / total) * 100
                formatted.append(f"{q_type.replace('_', ' ').title()}: {count} ({percentage:.1f}%)")
        
        return ", ".join(formatted)
    
    def _format_response_patterns(self, patterns: Dict[str, int]) -> str:
        """Format response patterns for summary"""
        total = sum(patterns.values())
        if total == 0:
            return "None"
        
        formatted = []
        for pattern, count in patterns.items():
            if count > 0:
                percentage = (count / total) * 100
                formatted.append(f"{pattern.replace('_', ' ').title()}: {count} ({percentage:.1f}%)")
        
        return ", ".join(formatted)
    
    def _assess_conversation_complexity_from_entries(self, entries: List[ConversationEntry]) -> str:
        """Assess conversation complexity based on entries"""
        if not entries:
            return "Low"
        
        avg_tokens = sum(entry.token_count for entry in entries) / len(entries)
        avg_processing_time = sum(entry.processing_time for entry in entries) / len(entries)
        
        if avg_tokens > 200 and avg_processing_time > 3.0:
            return "High"
        elif avg_tokens > 100 or avg_processing_time > 1.5:
            return "Medium"
        else:
            return "Low"
    
    def _find_most_active_period_from_entries(self, entries: List[ConversationEntry]) -> str:
        """Find the most active period in the conversation"""
        if not entries:
            return "N/A"
        
        # Group by hour
        hour_counts = Counter(entry.timestamp.hour for entry in entries)
        most_active_hour = hour_counts.most_common(1)[0][0]
        
        return f"{most_active_hour:02d}:00-{most_active_hour+1:02d}:00"
    
    def _assess_user_engagement_from_entries(self, entries: List[ConversationEntry]) -> str:
        """Assess user engagement level"""
        if not entries:
            return "Low"
        
        avg_user_message_length = sum(len(entry.user_message) for entry in entries) / len(entries)
        
        if avg_user_message_length > 100:
            return "High"
        elif avg_user_message_length > 50:
            return "Medium"
        else:
            return "Low"
    
    def _analyze_context_usage(self, entries: List[ConversationEntry]) -> str:
        """Analyze how context was used in the conversation"""
        total_entries = len(entries)
        entries_with_context = sum(1 for entry in entries if entry.context_used)
        
        if total_entries == 0:
            return "None"
        
        percentage = (entries_with_context / total_entries) * 100
        
        if percentage > 70:
            return f"Heavy ({percentage:.1f}%)"
        elif percentage > 30:
            return f"Moderate ({percentage:.1f}%)"
        else:
            return f"Light ({percentage:.1f}%)"
    
    def _save_session_incrementally(self, session_id: str) -> None:
        """Save session incrementally for crash recovery with robust error handling"""
        if session_id not in self.active_sessions:
            return
        
        session = self.active_sessions[session_id]
        session_dir = self.storage_dir / "sessions"
        session_file = session_dir / f"{session_id}_active.json"
        
        try:
            # Ensure sessions directory exists
            if not session_dir.exists():
                try:
                    session_dir.mkdir(parents=True, exist_ok=True)
                    logger.debug(f"Created sessions directory: {session_dir}")
                except Exception as dir_error:
                    logger.error(f"Failed to create sessions directory {session_dir}: {dir_error}")
                    return
            
            # Validate session data before saving
            if not session.entries:
                logger.warning(f"No entries to save for session {session_id}")
                return
            
            # Prepare session data with enhanced error handling
            try:
                session_data = {
                    'session_id': session.session_id,
                    'start_time': session.start_time.isoformat(),
                    'entries': [],
                    'total_tokens': session.total_tokens,
                    'total_processing_time': session.total_processing_time,
                    'last_saved': datetime.now().isoformat()
                }
                
                # Convert entries with individual error handling
                for i, entry in enumerate(session.entries):
                    try:
                        entry_dict = asdict(entry)
                        # Ensure timestamp is properly serialized
                        if isinstance(entry_dict.get('timestamp'), datetime):
                            entry_dict['timestamp'] = entry_dict['timestamp'].isoformat()
                        session_data['entries'].append(entry_dict)
                    except Exception as entry_error:
                        logger.warning(f"Failed to serialize entry {i} for session {session_id}: {entry_error}")
                        continue
                        
            except Exception as data_error:
                logger.error(f"Failed to prepare session data for {session_id}: {data_error}")
                return
            
            # Write to temporary file first for atomic operation
            temp_file = session_dir / f"{session_id}_active.json.tmp"
            
            try:
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(session_data, f, indent=2, default=str, ensure_ascii=False)
                
                # Atomic move to final location
                temp_file.rename(session_file)
                logger.debug(f"Successfully saved session {session_id} incrementally")
                
            except Exception as write_error:
                logger.error(f"Failed to write session file for {session_id}: {write_error}")
                # Clean up temporary file if it exists
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
                return
                
        except Exception as e:
            logger.error(f"Failed to save session incrementally for {session_id}: {e}", exc_info=True)
    
    def _save_complete_session(self, session: ConversationSession) -> None:
        """Save complete session with summary"""
        session_file = self.storage_dir / "sessions" / f"{session.session_id}.json"
        summary_file = self.storage_dir / "summaries" / f"{session.session_id}_summary.md"
        
        try:
            # Save JSON data
            with open(session_file, 'w') as f:
                json.dump(asdict(session), f, indent=2, default=str)
            
            # Save markdown summary
            with open(summary_file, 'w') as f:
                f.write(session.summary)
            
            # Remove active session file
            active_file = self.storage_dir / "sessions" / f"{session.session_id}_active.json"
            if active_file.exists():
                active_file.unlink()
                
        except Exception as e:
            logger.error(f"Failed to save complete session: {e}")
    
    def _save_daily_summary(self, session: ConversationSession) -> None:
        """Add session to daily summary file"""
        today = session.start_time.strftime('%Y-%m-%d')
        daily_file = self.storage_dir / "daily" / f"{today}_summary.md"
        
        try:
            # Load existing daily summary
            daily_content = ""
            if daily_file.exists():
                with open(daily_file, 'r') as f:
                    daily_content = f.read()
            
            # Add session summary
            session_summary = f"""
## Session {session.session_id} ({session.start_time.strftime('%H:%M:%S')})
- Duration: {session.get_duration_minutes():.1f} minutes
- Exchanges: {len(session.entries)}
- Tokens: {session.total_tokens}
- Topics: {', '.join(self._extract_key_topics(session)[:3])}

"""
            
            daily_content += session_summary
            
            # Save updated daily summary
            with open(daily_file, 'w') as f:
                f.write(daily_content)
                
        except Exception as e:
            logger.error(f"Failed to save daily summary: {e}")
    
    def log_conversation(self, user_message: str, assistant_response: str, 
                        conversation_type: str = "chat", token_count: int = 0,
                        processing_time: float = 0.0, context_used: List[str] = None,
                        metadata: Dict[str, Any] = None, session_id: str = None) -> None:
        """Log a conversation entry"""
        if context_used is None:
            context_used = []
        if metadata is None:
            metadata = {}
        if session_id is None:
            session_id = "default"
            
        # Create or get session
        if session_id not in self.active_sessions:
            self.start_session(session_id)
            
        # Create entry
        entry = ConversationEntry(
            timestamp=datetime.now(),
            user_message=user_message,
            assistant_response=assistant_response,
            conversation_type=conversation_type,
            token_count=token_count,
            processing_time=processing_time,
            context_used=context_used,
            metadata=metadata
        )
        
        # Add to session
        session = self.active_sessions[session_id]
        session.entries.append(entry)
        session.total_tokens += token_count
        session.total_processing_time += processing_time
    
    def get_session_stats(self) -> Dict[str, Any]:
        """Get statistics about active sessions"""
        return {
            'active_sessions': len(self.active_sessions),
            'session_ids': list(self.active_sessions.keys()),
            'total_entries': sum(len(session.entries) for session in self.active_sessions.values()),
            'total_tokens': sum(session.total_tokens for session in self.active_sessions.values())
        }


# Global conversation logger instance
_conversation_logger = None

def get_conversation_logger() -> ConversationLogger:
    """Get or create the global conversation logger instance"""
    global _conversation_logger
    if _conversation_logger is None:
        _conversation_logger = ConversationLogger()
    return _conversation_logger