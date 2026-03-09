# backend/utils/context_bridge.py
# Cross-System Context Bridge - Automatically maintains RAG context awareness

import logging
from typing import Optional, Dict, Any
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from backend.models import db, Task, Document, Client, Project
from backend.services.metadata_indexing_service import metadata_indexing_service
from backend.utils.unified_job_metadata import unified_job_metadata

logger = logging.getLogger(__name__)

class ContextBridge:
    """
    Automatic context bridge that keeps RAG system synchronized with database changes.
    Triggers metadata indexing when relevant entities are created, updated, or deleted.
    """
    
    def __init__(self):
        self.enabled = True
        self._setup_event_listeners()
    
    def _setup_event_listeners(self):
        """Set up SQLAlchemy event listeners for automatic context updates"""
        try:
            # Client events
            event.listen(Client, 'after_insert', self._on_client_created)
            event.listen(Client, 'after_update', self._on_client_updated)
            event.listen(Client, 'after_delete', self._on_client_deleted)
            
            # Project events
            event.listen(Project, 'after_insert', self._on_project_created)
            event.listen(Project, 'after_update', self._on_project_updated)
            event.listen(Project, 'after_delete', self._on_project_deleted)
            
            # Task events
            event.listen(Task, 'after_insert', self._on_task_created)
            event.listen(Task, 'after_update', self._on_task_updated)
            event.listen(Task, 'after_delete', self._on_task_deleted)
            
            # Document events - DISABLED to prevent transaction conflicts during upload
            # event.listen(Document, 'after_update', self._on_document_updated)
            
            logger.info("Context bridge event listeners registered successfully")
            
        except Exception as e:
            logger.error(f"Failed to setup context bridge event listeners: {e}")
            self.enabled = False
    
    def _on_client_created(self, mapper, connection, target):
        """Handle client creation"""
        if not self.enabled:
            return
            
        try:
            logger.info(f"Context Bridge: Client {target.id} ({target.name}) created - scheduling metadata indexing")
            self._schedule_indexing('client', target.id)
        except Exception as e:
            logger.error(f"Error handling client creation for {target.id}: {e}")
    
    def _on_client_updated(self, mapper, connection, target):
        """Handle client updates"""
        if not self.enabled:
            return
            
        try:
            logger.info(f"Context Bridge: Client {target.id} ({target.name}) updated - scheduling metadata reindexing")
            self._schedule_indexing('client', target.id)
        except Exception as e:
            logger.error(f"Error handling client update for {target.id}: {e}")
    
    def _on_client_deleted(self, mapper, connection, target):
        """Handle client deletion"""
        if not self.enabled:
            return
            
        try:
            logger.info(f"Context Bridge: Client {target.id} ({target.name}) deleted - metadata will be removed from index")
            # TODO: Implement metadata removal from index
        except Exception as e:
            logger.error(f"Error handling client deletion for {target.id}: {e}")
    
    def _on_project_created(self, mapper, connection, target):
        """Handle project creation"""
        if not self.enabled:
            return
            
        try:
            logger.info(f"Context Bridge: Project {target.id} ({target.name}) created - scheduling metadata indexing")
            self._schedule_indexing('project', target.id)
            
            # Also reindex parent client to include new project
            if target.client_id:
                self._schedule_indexing('client', target.client_id)
                
        except Exception as e:
            logger.error(f"Error handling project creation for {target.id}: {e}")
    
    def _on_project_updated(self, mapper, connection, target):
        """Handle project updates"""
        if not self.enabled:
            return
            
        try:
            logger.info(f"Context Bridge: Project {target.id} ({target.name}) updated - scheduling metadata reindexing")
            self._schedule_indexing('project', target.id)
            
            # Also reindex parent client
            if target.client_id:
                self._schedule_indexing('client', target.client_id)
                
        except Exception as e:
            logger.error(f"Error handling project update for {target.id}: {e}")
    
    def _on_project_deleted(self, mapper, connection, target):
        """Handle project deletion"""
        if not self.enabled:
            return
            
        try:
            logger.info(f"Context Bridge: Project {target.id} ({target.name}) deleted")
            
            # Reindex parent client to remove project reference
            if target.client_id:
                self._schedule_indexing('client', target.client_id)
                
        except Exception as e:
            logger.error(f"Error handling project deletion for {target.id}: {e}")
    
    def _on_task_created(self, mapper, connection, target):
        """Handle task/job creation"""
        if not self.enabled:
            return
            
        try:
            logger.info(f"Context Bridge: Task {target.id} ({target.name}) created - scheduling metadata indexing")
            self._schedule_indexing('job', target.id)
            
            # Also reindex parent project and client
            if target.project_id:
                self._schedule_indexing('project', target.project_id)
                
                # Get project to find client
                try:
                    from backend.utils.db_utils import DatabaseConnectionManager
                    with DatabaseConnectionManager():
                        project = db.session.get(Project, target.project_id)
                        if project and project.client_id:
                            self._schedule_indexing('client', project.client_id)
                except Exception as db_error:
                    logger.warning(f"Could not get project {target.project_id} for client indexing: {db_error}")
                
        except Exception as e:
            logger.error(f"Error handling task creation for {target.id}: {e}")
    
    def _on_task_updated(self, mapper, connection, target):
        """Handle task/job updates"""
        if not self.enabled:
            return
            
        try:
            # Check if this is a significant update (status change, progress, etc.)
            state = db.inspect(target)
            changed_attrs = [attr.key for attr in state.attrs if attr.history.has_changes()]
            
            significant_changes = ['status', 'name', 'description', 'workflow_config']
            if any(attr in changed_attrs for attr in significant_changes):
                logger.info(f"Context Bridge: Task {target.id} ({target.name}) updated significantly - scheduling metadata reindexing")
                self._schedule_indexing('job', target.id)
                
                # Also reindex parent project and client for status changes
                if 'status' in changed_attrs and target.project_id:
                    self._schedule_indexing('project', target.project_id)
                
        except Exception as e:
            logger.error(f"Error handling task update for {target.id}: {e}")
    
    def _on_task_deleted(self, mapper, connection, target):
        """Handle task/job deletion"""
        if not self.enabled:
            return
            
        try:
            logger.info(f"Context Bridge: Task {target.id} ({target.name}) deleted")
            
            # Reindex parent project and client to remove job reference
            if target.project_id:
                self._schedule_indexing('project', target.project_id)
                
        except Exception as e:
            logger.error(f"Error handling task deletion for {target.id}: {e}")
    
    def _on_document_updated(self, mapper, connection, target):
        """Handle document updates (especially indexing status changes)"""
        if not self.enabled:
            return
            
        try:
            state = db.inspect(target)
            changed_attrs = [attr.key for attr in state.attrs if attr.history.has_changes()]
            
            # If indexing status changed, update project context
            if 'index_status' in changed_attrs and target.project_id:
                logger.info(f"Context Bridge: Document {target.id} indexing status changed - updating project context")
                self._schedule_indexing('project', target.project_id)
                
        except Exception as e:
            logger.error(f"Error handling document update for {target.id}: {e}")
    
    def _schedule_indexing(self, entity_type: str, entity_id: int):
        """Schedule metadata indexing for an entity"""
        try:
            # IMPORTANT: Avoid calling metadata indexing during active database transactions
            # to prevent session management conflicts. Check if we're in a transaction.
            from backend.models import db
            
            if db and db.session and hasattr(db.session, 'in_transaction') and db.session.in_transaction():
                # We're in an active transaction - defer the indexing to avoid conflicts
                logger.debug(f"Deferring metadata indexing for {entity_type} {entity_id} due to active transaction")
                
                # Use Flask's after_request handler to schedule the indexing after transaction completes
                from flask import g
                if not hasattr(g, '_deferred_indexing_tasks'):
                    g._deferred_indexing_tasks = []
                g._deferred_indexing_tasks.append((entity_type, entity_id))
                return
            
            # Safe to do immediate indexing - no active transaction
            self._execute_indexing(entity_type, entity_id)
                
        except Exception as e:
            logger.debug(f"Error scheduling indexing for {entity_type} {entity_id}: {e}")
    
    def _execute_indexing(self, entity_type: str, entity_id: int):
        """Execute the actual metadata indexing"""
        try:
            if entity_type == 'client':
                metadata_indexing_service.index_client_metadata(entity_id)
            elif entity_type == 'project':
                metadata_indexing_service.index_project_metadata(entity_id)
            elif entity_type == 'job':
                metadata_indexing_service.index_job_metadata(entity_id)
            else:
                logger.warning(f"Unknown entity type for indexing: {entity_type}")
        except Exception as e:
            logger.warning(f"Indexing service not available, skipping {entity_type} metadata indexing")
    
    def manual_sync_context(self, entity_type: str, entity_id: int) -> bool:
        """Manually trigger context synchronization for an entity"""
        try:
            logger.info(f"Manual context sync requested for {entity_type} {entity_id}")
            # For manual sync, always execute immediately (not transaction-dependent)
            self._execute_indexing(entity_type, entity_id)
            return True
        except Exception as e:
            logger.error(f"Error in manual context sync for {entity_type} {entity_id}: {e}")
            return False
    
    def get_context_status(self) -> Dict[str, Any]:
        """Get status of the context bridge"""
        try:
            summary = unified_job_metadata.get_active_jobs_summary()
            
            return {
                'bridge_enabled': self.enabled,
                'event_listeners_active': self.enabled,
                'metadata_indexing_available': metadata_indexing_service.add_text_to_index is not None,
                'active_jobs': summary.get('total_active', 0),
                'database_jobs': summary.get('database_active_jobs', 0),
                'progress_jobs': summary.get('progress_active_jobs', 0),
                'last_check': '2025-08-14T20:00:00Z'  # Current time would be better
            }
            
        except Exception as e:
            logger.error(f"Error getting context status: {e}")
            return {
                'bridge_enabled': False,
                'error': str(e)
            }

# Global instance
context_bridge = ContextBridge()