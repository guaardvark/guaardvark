#!/usr/bin/env python3
"""
System Coordinator - Unified Architecture Foundation
Provides centralized resource management, error isolation, state consistency, and security enforcement

This is the foundational layer that prevents systemic bugs by managing:
1. Resource Lifecycle Management
2. Error Isolation & Recovery 
3. State Consistency Coordination
4. Security Enforcement
5. Cascading Failure Prevention
"""

import asyncio
import gc
import logging
import os
import threading
import time
import weakref
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Set, Union
from uuid import uuid4

logger = logging.getLogger(__name__)

# =============================================================================
# FOUNDATIONAL ENUMS AND DATATYPES
# =============================================================================

class ResourceType(Enum):
    DATABASE_CONNECTION = "db_connection"
    FILE_HANDLE = "file_handle"
    MEMORY_BUFFER = "memory_buffer"
    NETWORK_CONNECTION = "network_connection"
    LLAMA_INDEX = "llama_index"
    CHAT_ENGINE = "chat_engine"
    SUBPROCESS = "subprocess"
    THREAD = "thread"
    TEMPORARY_FILE = "temp_file"

class ProcessType(Enum):
    FILE_UPLOAD = "file_upload"
    FILE_GENERATION = "file_generation"
    DOCUMENT_INDEXING = "indexing"
    CHAT_SESSION = "chat"
    BULK_GENERATION = "bulk_gen"
    SYSTEM_OPERATION = "system"

class SecurityLevel(Enum):
    PUBLIC = 1
    USER = 2
    ADMIN = 3
    SYSTEM = 4

class ErrorSeverity(Enum):
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4
    FATAL = 5

@dataclass
class ResourceInfo:
    """Information about a managed resource"""
    resource_id: str
    resource_type: ResourceType
    resource_ref: weakref.ref
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    owner_process: Optional[str] = None
    cleanup_callbacks: List[Callable] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass 
class ProcessContext:
    """Context information for a running process"""
    process_id: str
    process_type: ProcessType
    security_level: SecurityLevel
    created_at: datetime
    resources: Set[str] = field(default_factory=set)
    state: Dict[str, Any] = field(default_factory=dict)
    error_count: int = 0
    last_error: Optional[str] = None

@dataclass
class ErrorContext:
    """Context information for error tracking"""
    error_id: str
    severity: ErrorSeverity  
    message: str
    process_id: Optional[str]
    resource_id: Optional[str]
    stack_trace: str
    created_at: datetime
    recovery_attempted: bool = False
    recovery_successful: bool = False

# =============================================================================
# RESOURCE LIFECYCLE MANAGER
# =============================================================================

class ResourceManager:
    """Centralized resource lifecycle management"""
    
    def __init__(self, max_resources: int = 1000, cleanup_interval: int = 300):
        self.max_resources = max_resources
        self.cleanup_interval = cleanup_interval
        self.resources: Dict[str, ResourceInfo] = {}
        self.resource_types: Dict[ResourceType, Set[str]] = defaultdict(set)
        self.process_resources: Dict[str, Set[str]] = defaultdict(set)
        self.lock = threading.RLock()
        
        # Resource limits per type
        self.type_limits = {
            ResourceType.DATABASE_CONNECTION: 50,
            ResourceType.FILE_HANDLE: 200,
            ResourceType.MEMORY_BUFFER: 100,
            ResourceType.CHAT_ENGINE: 20,
            ResourceType.LLAMA_INDEX: 5,
            ResourceType.SUBPROCESS: 10,
            ResourceType.THREAD: 50,
        }
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_loop, 
            daemon=True, 
            name="ResourceCleanup"
        )
        self.running = True
        self.cleanup_thread.start()
        
        logger.info("Resource Manager initialized")
    
    def register_resource(self, resource: Any, resource_type: ResourceType, 
                         owner_process: Optional[str] = None,
                         cleanup_callbacks: Optional[List[Callable]] = None) -> str:
        """Register a resource for lifecycle management"""
        resource_id = str(uuid4())
        
        with self.lock:
            # Check resource limits
            if len(self.resource_types[resource_type]) >= self.type_limits.get(resource_type, 1000):
                logger.warning(f"Resource limit reached for {resource_type.value}, forcing cleanup")
                self._cleanup_type(resource_type)
            
            # Create resource info with weak reference
            try:
                resource_ref = weakref.ref(resource, self._resource_deleted_callback(resource_id))
            except TypeError:
                # Some objects can't be weak referenced, store directly but log warning
                logger.warning(f"Resource {resource_type.value} cannot be weak referenced")
                resource_ref = lambda: resource
            
            resource_info = ResourceInfo(
                resource_id=resource_id,
                resource_type=resource_type,
                resource_ref=resource_ref,
                created_at=datetime.now(),
                last_accessed=datetime.now(),
                owner_process=owner_process,
                cleanup_callbacks=cleanup_callbacks or []
            )
            
            self.resources[resource_id] = resource_info
            self.resource_types[resource_type].add(resource_id)
            
            if owner_process:
                self.process_resources[owner_process].add(resource_id)
            
            logger.debug(f"Registered resource {resource_id} of type {resource_type.value}")
            return resource_id
    
    def access_resource(self, resource_id: str) -> Optional[Any]:
        """Access a registered resource and update usage statistics"""
        with self.lock:
            if resource_id not in self.resources:
                return None
            
            resource_info = self.resources[resource_id]
            resource_info.last_accessed = datetime.now()
            resource_info.access_count += 1
            
            return resource_info.resource_ref()
    
    def release_resource(self, resource_id: str, force: bool = False) -> bool:
        """Release a registered resource and run cleanup"""
        with self.lock:
            if resource_id not in self.resources:
                return False
            
            resource_info = self.resources[resource_id]
            
            # Run cleanup callbacks
            for callback in resource_info.cleanup_callbacks:
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Resource cleanup callback failed for {resource_id}: {e}")
            
            # Get the actual resource for cleanup
            resource = resource_info.resource_ref()
            if resource:
                self._cleanup_resource(resource, resource_info.resource_type)
            
            # Remove from tracking
            self.resource_types[resource_info.resource_type].discard(resource_id)
            if resource_info.owner_process:
                self.process_resources[resource_info.owner_process].discard(resource_id)
            del self.resources[resource_id]
            
            logger.debug(f"Released resource {resource_id}")
            return True
    
    def cleanup_process_resources(self, process_id: str):
        """Clean up all resources owned by a process"""
        with self.lock:
            resource_ids = list(self.process_resources.get(process_id, set()))
            for resource_id in resource_ids:
                self.release_resource(resource_id)
            
            if process_id in self.process_resources:
                del self.process_resources[process_id]
            
            logger.info(f"Cleaned up {len(resource_ids)} resources for process {process_id}")
    
    def _cleanup_resource(self, resource: Any, resource_type: ResourceType):
        """Internal resource cleanup based on type"""
        try:
            if resource_type == ResourceType.FILE_HANDLE:
                if hasattr(resource, 'close'):
                    resource.close()
            elif resource_type == ResourceType.DATABASE_CONNECTION:
                if hasattr(resource, 'close'):
                    resource.close()
                elif hasattr(resource, 'rollback'):
                    resource.rollback()
            elif resource_type == ResourceType.MEMORY_BUFFER:
                if hasattr(resource, 'clear'):
                    resource.clear()
                elif isinstance(resource, list):
                    resource.clear()
                elif isinstance(resource, dict):
                    resource.clear()
            elif resource_type == ResourceType.SUBPROCESS:
                if hasattr(resource, 'terminate'):
                    resource.terminate()
                    resource.wait(timeout=5)
            elif resource_type == ResourceType.TEMPORARY_FILE:
                if isinstance(resource, (str, Path)):
                    Path(resource).unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Failed to cleanup resource of type {resource_type.value}: {e}")
    
    def _cleanup_type(self, resource_type: ResourceType, max_to_cleanup: int = 10):
        """Clean up old resources of a specific type"""
        with self.lock:
            resource_ids = list(self.resource_types[resource_type])
            
            # Sort by last accessed time (oldest first)
            resource_ids.sort(key=lambda rid: self.resources[rid].last_accessed)
            
            for resource_id in resource_ids[:max_to_cleanup]:
                self.release_resource(resource_id)
    
    def _cleanup_loop(self):
        """Background cleanup loop"""
        while self.running:
            try:
                time.sleep(self.cleanup_interval)
                self._periodic_cleanup()
            except Exception as e:
                logger.error(f"Error in resource cleanup loop: {e}")
    
    def _periodic_cleanup(self):
        """Periodic cleanup of stale resources"""
        with self.lock:
            now = datetime.now()
            stale_threshold = 3600  # 1 hour
            
            stale_resources = []
            for resource_id, resource_info in self.resources.items():
                age = (now - resource_info.last_accessed).total_seconds()
                if age > stale_threshold:
                    stale_resources.append(resource_id)
            
            for resource_id in stale_resources:
                logger.info(f"Cleaning up stale resource {resource_id}")
                self.release_resource(resource_id)
            
            # Force garbage collection after cleanup
            if stale_resources:
                gc.collect()
    
    def _resource_deleted_callback(self, resource_id: str):
        """Callback when a weak-referenced resource is deleted"""
        def callback(ref):
            with self.lock:
                if resource_id in self.resources:
                    logger.debug(f"Resource {resource_id} was garbage collected")
                    self.release_resource(resource_id)
        return callback
    
    def get_resource_stats(self) -> Dict[str, Any]:
        """Get resource usage statistics"""
        with self.lock:
            stats = {
                "total_resources": len(self.resources),
                "by_type": {rt.value: len(ids) for rt, ids in self.resource_types.items()},
                "by_process": {pid: len(ids) for pid, ids in self.process_resources.items()},
                "limits": {rt.value: limit for rt, limit in self.type_limits.items()}
            }
            return stats
    
    def shutdown(self):
        """Shutdown the resource manager"""
        self.running = False
        if self.cleanup_thread.is_alive():
            self.cleanup_thread.join(timeout=10)
        
        # Clean up all resources
        with self.lock:
            resource_ids = list(self.resources.keys())
            for resource_id in resource_ids:
                self.release_resource(resource_id, force=True)
        
        logger.info("Resource Manager shutdown complete")

# =============================================================================
# ERROR ISOLATION & RECOVERY SYSTEM  
# =============================================================================

class ErrorIsolationManager:
    """Manages error isolation boundaries and recovery mechanisms"""
    
    def __init__(self, max_errors: int = 1000):
        self.max_errors = max_errors
        self.errors: Dict[str, ErrorContext] = {}
        self.error_patterns: Dict[str, int] = defaultdict(int)
        self.circuit_breakers: Dict[str, Dict] = defaultdict(lambda: {
            "failures": 0,
            "last_failure": None,
            "state": "closed"  # closed, open, half_open
        })
        self.lock = threading.RLock()
        
        logger.info("Error Isolation Manager initialized")
    
    @contextmanager
    def error_boundary(self, process_id: str, operation_name: str):
        """Create an error isolation boundary"""
        try:
            yield
        except Exception as e:
            self.handle_error(e, process_id, operation_name)
            raise
    
    def handle_error(self, error: Exception, process_id: Optional[str] = None, 
                    operation_name: Optional[str] = None, resource_id: Optional[str] = None):
        """Handle and log an error with context"""
        error_id = str(uuid4())
        severity = self._classify_error_severity(error)
        
        error_context = ErrorContext(
            error_id=error_id,
            severity=severity,
            message=str(error),
            process_id=process_id,
            resource_id=resource_id,
            stack_trace=self._get_stack_trace(),
            created_at=datetime.now()
        )
        
        with self.lock:
            self.errors[error_id] = error_context
            
            # Track error patterns
            error_pattern = f"{type(error).__name__}:{operation_name}"
            self.error_patterns[error_pattern] += 1
            
            # Update circuit breaker
            if operation_name:
                self._update_circuit_breaker(operation_name, error)
            
            # Attempt recovery if appropriate
            if severity.value <= ErrorSeverity.ERROR.value:
                recovery_success = self._attempt_recovery(error_context)
                error_context.recovery_attempted = True
                error_context.recovery_successful = recovery_success
        
        logger.error(f"Error {error_id} handled: {error}", exc_info=True)
        
        # Trigger system response based on severity
        if severity == ErrorSeverity.CRITICAL:
            self._handle_critical_error(error_context)
        elif severity == ErrorSeverity.FATAL:
            self._handle_fatal_error(error_context)
    
    def _classify_error_severity(self, error: Exception) -> ErrorSeverity:
        """Classify error severity based on type and context"""
        error_type = type(error).__name__
        
        if error_type in ["MemoryError", "SystemError", "KeyboardInterrupt"]:
            return ErrorSeverity.FATAL
        elif error_type in ["DatabaseError", "ConnectionError", "TimeoutError"]:
            return ErrorSeverity.CRITICAL  
        elif error_type in ["ValidationError", "PermissionError", "FileNotFoundError"]:
            return ErrorSeverity.ERROR
        elif error_type in ["DeprecationWarning", "UserWarning"]:
            return ErrorSeverity.WARNING
        else:
            return ErrorSeverity.ERROR
    
    def _update_circuit_breaker(self, operation_name: str, error: Exception):
        """Update circuit breaker state based on error"""
        breaker = self.circuit_breakers[operation_name]
        breaker["failures"] += 1
        breaker["last_failure"] = datetime.now()
        
        # Open circuit if too many failures
        if breaker["failures"] >= 5 and breaker["state"] == "closed":
            breaker["state"] = "open"
            logger.warning(f"Circuit breaker opened for {operation_name}")
        elif breaker["state"] == "open":
            # Check if we should try half-open
            time_since_failure = datetime.now() - breaker["last_failure"]
            if time_since_failure.total_seconds() > 300:  # 5 minutes
                breaker["state"] = "half_open"
                logger.info(f"Circuit breaker half-opened for {operation_name}")
    
    def check_circuit_breaker(self, operation_name: str) -> bool:
        """Check if operation is allowed based on circuit breaker state"""
        breaker = self.circuit_breakers[operation_name]
        
        if breaker["state"] == "open":
            return False
        elif breaker["state"] == "half_open":
            # Allow one attempt, will close on success or open on failure
            return True
        else:
            return True
    
    def _attempt_recovery(self, error_context: ErrorContext) -> bool:
        """Attempt automatic error recovery"""
        try:
            # Database connection recovery
            if "database" in error_context.message.lower():
                from backend.models import db
                if db and db.session:
                    db.session.rollback()
                    return True
            
            # Memory pressure recovery
            if "memory" in error_context.message.lower():
                gc.collect()
                return True
            
            # File handle recovery
            if "file" in error_context.message.lower():
                # Close any open file handles (would need more context)
                pass
            
            return False
        except Exception as e:
            logger.error(f"Recovery attempt failed: {e}")
            return False
    
    def _handle_critical_error(self, error_context: ErrorContext):
        """Handle critical errors that threaten system stability"""
        logger.critical(f"Critical error detected: {error_context.message}")
        
        # Cleanup resources for the affected process
        if error_context.process_id:
            # This would interface with ResourceManager
            pass
        
        # Notify monitoring systems
        self._notify_monitoring(error_context)
    
    def _handle_fatal_error(self, error_context: ErrorContext):
        """Handle fatal errors that require immediate attention"""
        logger.critical(f"FATAL ERROR: {error_context.message}")
        
        # Attempt graceful shutdown
        # This would trigger system-wide cleanup
        pass
    
    def _notify_monitoring(self, error_context: ErrorContext):
        """Notify external monitoring systems"""
        # This would integrate with monitoring/alerting systems
        pass
    
    def _get_stack_trace(self) -> str:
        """Get current stack trace"""
        import traceback
        return traceback.format_exc()
    
    def get_error_stats(self) -> Dict[str, Any]:
        """Get error statistics"""
        with self.lock:
            return {
                "total_errors": len(self.errors),
                "error_patterns": dict(self.error_patterns),
                "circuit_breakers": {name: state for name, state in self.circuit_breakers.items()},
                "recent_errors": len([e for e in self.errors.values() 
                                    if (datetime.now() - e.created_at).total_seconds() < 3600])
            }

# =============================================================================
# STATE CONSISTENCY COORDINATOR
# =============================================================================

class StateCoordinator:
    """Coordinates state consistency across the entire system"""
    
    def __init__(self):
        self.processes: Dict[str, ProcessContext] = {}
        self.state_locks: Dict[str, threading.RLock] = defaultdict(threading.RLock)
        self.state_watchers: Dict[str, List[Callable]] = defaultdict(list)
        self.global_lock = threading.RLock()
        
        logger.info("State Coordinator initialized")
    
    def create_process(self, process_type: ProcessType, security_level: SecurityLevel = SecurityLevel.USER) -> str:
        """Create a new process context"""
        process_id = str(uuid4())
        
        with self.global_lock:
            process_context = ProcessContext(
                process_id=process_id,
                process_type=process_type,
                security_level=security_level,
                created_at=datetime.now()
            )
            
            self.processes[process_id] = process_context
            
        logger.debug(f"Created process {process_id} of type {process_type.value}")
        return process_id
    
    @contextmanager 
    def state_transaction(self, process_id: str, state_key: str):
        """Create a state transaction with consistency guarantees"""
        lock_key = f"{process_id}:{state_key}"
        
        with self.state_locks[lock_key]:
            try:
                yield
                # Notify watchers of state change
                self._notify_state_watchers(process_id, state_key)
            except Exception as e:
                # Rollback state changes if needed
                self._rollback_state(process_id, state_key)
                raise
    
    def update_process_state(self, process_id: str, state_key: str, state_value: Any):
        """Update process state with consistency checks"""
        with self.global_lock:
            if process_id not in self.processes:
                raise ValueError(f"Process {process_id} not found")
            
            with self.state_transaction(process_id, state_key):
                self.processes[process_id].state[state_key] = state_value
    
    def get_process_state(self, process_id: str, state_key: str = None) -> Any:
        """Get process state"""
        with self.global_lock:
            if process_id not in self.processes:
                return None
            
            if state_key:
                return self.processes[process_id].state.get(state_key)
            else:
                return dict(self.processes[process_id].state)
    
    def watch_state(self, process_id: str, state_key: str, callback: Callable):
        """Watch for state changes"""
        watch_key = f"{process_id}:{state_key}"
        self.state_watchers[watch_key].append(callback)
    
    def _notify_state_watchers(self, process_id: str, state_key: str):
        """Notify watchers of state changes"""
        watch_key = f"{process_id}:{state_key}"
        for callback in self.state_watchers[watch_key]:
            try:
                callback(process_id, state_key)
            except Exception as e:
                logger.error(f"State watcher callback failed: {e}")
    
    def _rollback_state(self, process_id: str, state_key: str):
        """Rollback state changes on error"""
        # This would implement state rollback logic
        pass
    
    def cleanup_process(self, process_id: str):
        """Clean up process context"""
        with self.global_lock:
            if process_id in self.processes:
                del self.processes[process_id]
                
                # Clean up state locks and watchers
                keys_to_remove = [key for key in self.state_locks.keys() if key.startswith(f"{process_id}:")]
                for key in keys_to_remove:
                    del self.state_locks[key]
                    if key in self.state_watchers:
                        del self.state_watchers[key]
        
        logger.debug(f"Cleaned up process {process_id}")

# =============================================================================
# SECURITY ENFORCEMENT LAYER
# =============================================================================

class SecurityEnforcer:
    """Unified security enforcement across all system operations"""
    
    def __init__(self):
        self.security_policies: Dict[str, Dict] = {}
        self.access_log: deque = deque(maxlen=10000)
        self.blocked_operations: Set[str] = set()
        self.lock = threading.RLock()
        
        # Initialize default security policies
        self._init_default_policies()
        
        logger.info("Security Enforcer initialized")
    
    def _init_default_policies(self):
        """Initialize default security policies"""
        self.security_policies.update({
            "file_access": {
                "allowed_extensions": {
                    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
                    ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
                    ".pdf", ".docx", ".doc", ".odt"
                },
                "blocked_paths": {
                    "/etc", "/var", "/usr", "/boot", "/proc", "/sys",
                    "..", "~", "$HOME", "%USERPROFILE%"
                },
                "max_file_size": 100 * 1024 * 1024,  # 100MB
                "scan_content": True
            },
            "api_access": {
                "rate_limit": 1000,  # requests per hour
                "max_request_size": 10 * 1024 * 1024,  # 10MB
                "require_auth": False,
                "allowed_origins": ["http://localhost:3000", "http://localhost:5173"]
            },
            "llm_prompts": {
                "max_length": 100000,
                "scan_injection": True,
                "blocked_patterns": {
                    "system_commands", "file_operations", "network_operations",
                    "code_execution", "credential_extraction"
                }
            }
        })
    
    def validate_file_operation(self, file_path: str, operation: str, 
                               security_level: SecurityLevel = SecurityLevel.USER) -> bool:
        """Validate file operations for security compliance"""
        with self.lock:
            try:
                # Path traversal protection
                resolved_path = Path(file_path).resolve()
                
                # Check for blocked paths
                policy = self.security_policies["file_access"]
                for blocked_path in policy["blocked_paths"]:
                    if blocked_path in str(resolved_path):
                        self._log_security_violation("path_traversal", file_path, security_level)
                        return False
                
                # Extension validation
                file_ext = resolved_path.suffix.lower()
                if file_ext not in policy["allowed_extensions"]:
                    self._log_security_violation("invalid_extension", file_path, security_level)
                    return False
                
                # File size check (if file exists)
                if resolved_path.exists() and resolved_path.stat().st_size > policy["max_file_size"]:
                    self._log_security_violation("file_too_large", file_path, security_level)
                    return False
                
                return True
                
            except Exception as e:
                logger.error(f"File validation error: {e}")
                return False
    
    def validate_llm_prompt(self, prompt: str, security_level: SecurityLevel = SecurityLevel.USER) -> bool:
        """Validate LLM prompts for injection attacks"""
        with self.lock:
            policy = self.security_policies["llm_prompts"]
            
            # Length check
            if len(prompt) > policy["max_length"]:
                self._log_security_violation("prompt_too_long", f"Length: {len(prompt)}", security_level)
                return False
            
            # Injection pattern detection
            if policy["scan_injection"]:
                dangerous_patterns = [
                    r'system\s*\(',
                    r'exec\s*\(',
                    r'eval\s*\(',
                    r'__import__',
                    r'open\s*\(',
                    r'file\s*\(',
                    r'subprocess',
                    r'os\.',
                    r'sys\.',
                    r'\$\(',
                    r'`[^`]*`',
                    r'password|token|key|secret',
                ]
                
                import re
                for pattern in dangerous_patterns:
                    if re.search(pattern, prompt, re.IGNORECASE):
                        self._log_security_violation("prompt_injection", pattern, security_level)
                        return False
            
            return True
    
    def validate_api_request(self, request_size: int, origin: str, 
                           security_level: SecurityLevel = SecurityLevel.USER) -> bool:
        """Validate API requests for security compliance"""
        with self.lock:
            policy = self.security_policies["api_access"]
            
            # Size check
            if request_size > policy["max_request_size"]:
                self._log_security_violation("request_too_large", f"Size: {request_size}", security_level)
                return False
            
            # Origin check
            if origin and origin not in policy["allowed_origins"]:
                self._log_security_violation("invalid_origin", origin, security_level)
                return False
            
            return True
    
    def _log_security_violation(self, violation_type: str, details: str, security_level: SecurityLevel):
        """Log security violations"""
        violation = {
            "timestamp": datetime.now(),
            "type": violation_type,
            "details": details,
            "security_level": security_level.name
        }
        
        self.access_log.append(violation)
        logger.warning(f"Security violation: {violation_type} - {details}")
    
    def get_security_stats(self) -> Dict[str, Any]:
        """Get security statistics"""
        with self.lock:
            violations_last_hour = [
                v for v in self.access_log 
                if (datetime.now() - v["timestamp"]).total_seconds() < 3600
            ]
            
            return {
                "total_violations": len(self.access_log),
                "violations_last_hour": len(violations_last_hour),
                "blocked_operations": len(self.blocked_operations),
                "violation_types": {}
            }

# =============================================================================
# UNIFIED SYSTEM COORDINATOR
# =============================================================================

class SystemCoordinator:
    """Main coordinator that orchestrates all system components"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.resource_manager = ResourceManager()
        self.error_manager = ErrorIsolationManager()
        self.state_coordinator = StateCoordinator()
        self.security_enforcer = SecurityEnforcer()
        
        self.active_processes: Set[str] = set()
        self.system_health = {
            "status": "healthy",
            "last_check": datetime.now(),
            "issues": []
        }
        
        self._initialized = True
        logger.info("System Coordinator initialized")
    
    @contextmanager
    def managed_operation(self, operation_name: str, process_type: ProcessType, 
                         security_level: SecurityLevel = SecurityLevel.USER):
        """Create a fully managed operation with all protections"""
        process_id = None
        try:
            # Create process context
            process_id = self.state_coordinator.create_process(process_type, security_level)
            self.active_processes.add(process_id)
            
            # Check circuit breaker
            if not self.error_manager.check_circuit_breaker(operation_name):
                raise RuntimeError(f"Circuit breaker open for {operation_name}")
            
            # Create error boundary
            with self.error_manager.error_boundary(process_id, operation_name):
                yield process_id
                
        except Exception as e:
            self.error_manager.handle_error(e, process_id, operation_name)
            raise
        finally:
            # Clean up process resources
            if process_id:
                self.resource_manager.cleanup_process_resources(process_id)
                self.state_coordinator.cleanup_process(process_id)
                self.active_processes.discard(process_id)
    
    def register_resource(self, resource: Any, resource_type: ResourceType, 
                         process_id: Optional[str] = None) -> str:
        """Register a resource with the system"""
        return self.resource_manager.register_resource(resource, resource_type, process_id)
    
    def validate_security(self, operation_type: str, **kwargs) -> bool:
        """Validate security for any operation"""
        if operation_type == "file_operation":
            return self.security_enforcer.validate_file_operation(
                kwargs.get("file_path"), 
                kwargs.get("operation"),
                kwargs.get("security_level", SecurityLevel.USER)
            )
        elif operation_type == "llm_prompt":
            return self.security_enforcer.validate_llm_prompt(
                kwargs.get("prompt"),
                kwargs.get("security_level", SecurityLevel.USER)
            )
        elif operation_type == "api_request":
            return self.security_enforcer.validate_api_request(
                kwargs.get("request_size"),
                kwargs.get("origin"),
                kwargs.get("security_level", SecurityLevel.USER)
            )
        else:
            return True
    
    def get_system_health(self) -> Dict[str, Any]:
        """Get comprehensive system health status"""
        return {
            "coordinator": {
                "status": self.system_health["status"],
                "active_processes": len(self.active_processes),
                "last_check": self.system_health["last_check"].isoformat()
            },
            "resources": self.resource_manager.get_resource_stats(),
            "errors": self.error_manager.get_error_stats(),
            "security": self.security_enforcer.get_security_stats()
        }
    
    def shutdown(self):
        """Gracefully shutdown the system coordinator"""
        logger.info("System Coordinator shutting down...")
        
        # Clean up all active processes
        for process_id in list(self.active_processes):
            self.resource_manager.cleanup_process_resources(process_id)
            self.state_coordinator.cleanup_process(process_id)
        
        # Shutdown managers
        self.resource_manager.shutdown()
        
        logger.info("System Coordinator shutdown complete")

# =============================================================================
# GLOBAL INSTANCE AND HELPER FUNCTIONS
# =============================================================================

# Global system coordinator instance
_system_coordinator: Optional[SystemCoordinator] = None

def get_system_coordinator() -> SystemCoordinator:
    """Get the global system coordinator instance"""
    global _system_coordinator
    if _system_coordinator is None:
        _system_coordinator = SystemCoordinator()
    return _system_coordinator

# Convenience functions for easy integration
def managed_operation(operation_name: str, process_type: ProcessType, security_level: SecurityLevel = SecurityLevel.USER):
    """Decorator for managed operations"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            coordinator = get_system_coordinator()
            with coordinator.managed_operation(operation_name, process_type, security_level) as process_id:
                return func(*args, process_id=process_id, **kwargs)
        return wrapper
    return decorator

def register_resource(resource: Any, resource_type: ResourceType, process_id: Optional[str] = None) -> str:
    """Register a resource with the system"""
    coordinator = get_system_coordinator()
    return coordinator.register_resource(resource, resource_type, process_id)

def validate_security(operation_type: str, **kwargs) -> bool:
    """Validate security for any operation"""
    coordinator = get_system_coordinator()
    return coordinator.validate_security(operation_type, **kwargs)

if __name__ == "__main__":
    # Basic testing
    coordinator = get_system_coordinator()
    
    with coordinator.managed_operation("test_operation", ProcessType.SYSTEM_OPERATION) as process_id:
        print(f"Test operation running in process {process_id}")
        
        # Test resource registration
        test_resource = {"test": "data"}
        resource_id = coordinator.register_resource(test_resource, ResourceType.MEMORY_BUFFER, process_id)
        print(f"Registered resource {resource_id}")
        
        # Test security validation
        valid = coordinator.validate_security("file_operation", 
                                             file_path="test.txt", 
                                             operation="read")
        print(f"Security validation: {valid}")
    
    print("System health:", coordinator.get_system_health())
    coordinator.shutdown() 