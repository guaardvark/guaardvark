# backend/services/interconnector_sync_service.py
"""
Interconnector Synchronization Service
Handles serialization and synchronization of entities between nodes.
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from backend.models import (
    db, Client, Project, Rule, Task, Document, Website,
    Image, LLMSession, LLMMessage, InterconnectorLearning,
    InterconnectorSyncHistory, InterconnectorConflict, InterconnectorPendingChange,
    project_rules_association
)

logger = logging.getLogger(__name__)


class InterconnectorSyncService:
    """Service for synchronizing entities between interconnector nodes."""

    def __init__(self):
        self.supported_entities = {
            "clients": self._serialize_client,
            "projects": self._serialize_project,
            "rules": self._serialize_rule,
            "websites": self._serialize_website,
            "learnings": self._serialize_learning,
            "tasks": self._serialize_task,
            "documents": self._serialize_document,
            "images": self._serialize_image,
            "chat_history": self._serialize_chat_history,
        }

    def _serialize_learning(self, learning):
        from backend.models import InterconnectorLearning
        return {
            "id": learning.id,
            "source_node_id": learning.source_node_id,
            "timestamp": learning.timestamp.isoformat() if learning.timestamp else None,
            "learning_type": learning.learning_type,
            "description": learning.description,
            "code_diff": learning.code_diff,
            "confidence": learning.confidence,
            "model_used": learning.model_used,
            "uncle_reviewed": learning.uncle_reviewed,
            "uncle_feedback": learning.uncle_feedback,
            "_sync_metadata": {"entity_type": "learnings", "sync_id": f"learning_{learning.id}"},
        }

    def _serialize_image(self, image):
        """Serialize an Image entity (metadata only, no binary data)."""
        return {
            "id": image.id,
            "hash": image.hash,
            "file_name": image.file_name,
            "file_path": image.file_path,
            "file_size": image.file_size,
            "mime_type": image.mime_type,
            "tags": image.tags,
            "created_at": image.created_at.isoformat() if image.created_at else None,
            "last_used_at": image.last_used_at.isoformat() if image.last_used_at else None,
            "_sync_metadata": {"entity_type": "images", "sync_id": f"image_{image.id}"},
        }

    def _serialize_chat_history(self, session):
        """Serialize an LLMSession and its associated LLMMessage records."""
        messages = LLMMessage.query.filter_by(session_id=session.id).order_by(LLMMessage.timestamp).all()
        return {
            "id": session.id,
            "user": session.user,
            "project_id": session.project_id,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "messages": [
                {"id": m.id, "role": m.role, "content": m.content,
                 "project_id": m.project_id,
                 "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                 "extra_data": m.extra_data}
                for m in messages
            ],
            "_sync_metadata": {"entity_type": "chat_history", "sync_id": f"chat_{session.id}"},
        }

    def broadcast_directive(self, directive: str, reason: str):
        """Broadcast an Uncle Claude directive to all connected nodes."""
        from backend.models import db, InterconnectorNode, InterconnectorBroadcast, InterconnectorBroadcastTarget
        import requests

        local_node_id = os.environ.get("GUAARDVARK_NODE_ID", "local")
        nodes = db.session.query(InterconnectorNode).filter(InterconnectorNode.status == "active").all()
        broadcast = InterconnectorBroadcast(
            broadcast_type="uncle_directive",
            payload=json.dumps({"directive": directive, "reason": reason}),
            initiated_by=local_node_id,
        )
        db.session.add(broadcast)
        db.session.flush()

        # Get API key for authenticated inter-node communication
        config = {}
        try:
            from backend.models import Setting
            setting = db.session.get(Setting, "interconnector_config")
            if setting:
                config = json.loads(setting.value) if isinstance(setting.value, str) else setting.value
        except Exception:
            pass
        api_key = config.get("api_key", "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key

        for node in nodes:
            # Skip self to prevent cascade loops
            if node.node_id == local_node_id:
                continue

            target = InterconnectorBroadcastTarget(
                broadcast_id=broadcast.id,
                target_node_id=node.node_id,
                status="pending",
            )
            db.session.add(target)

            api_url = f"http://{node.host}:{node.port}"
            try:
                resp = requests.post(
                    f"{api_url}/api/interconnector/receive-directive",
                    json={"directive": directive, "reason": reason},
                    headers=headers,
                    timeout=10,
                )
                target.status = "delivered" if resp.ok else "failed"
            except Exception as e:
                target.status = "failed"
                logger.error(f"Failed to deliver directive to {node.node_id}: {e}")

        db.session.commit()

    def serialize_entity(self, entity_type: str, entity: Any) -> Optional[Dict]:
        """Serialize an entity to a dictionary for transfer."""
        serializer = self.supported_entities.get(entity_type)
        if not serializer:
            logger.warning(f"Unsupported entity type for sync: {entity_type}")
            return None
        
        try:
            return serializer(entity)
        except Exception as e:
            logger.error(f"Error serializing {entity_type} {entity.id}: {e}")
            return None

    def deserialize_entity(self, entity_type: str, data: Dict) -> Optional[Any]:
        """Deserialize a dictionary to an entity."""
        deserializers = {
            "clients": self._deserialize_client,
            "projects": self._deserialize_project,
            "rules": self._deserialize_rule,
            "websites": self._deserialize_website,
            "tasks": self._deserialize_task,
            "documents": self._deserialize_document,
            "learnings": self._deserialize_learning,
            "images": self._deserialize_image,
            "chat_history": self._deserialize_chat_history,
        }
        
        deserializer = deserializers.get(entity_type)
        if not deserializer:
            logger.warning(f"Unsupported entity type for deserialize: {entity_type}")
            return None
        
        try:
            return deserializer(data)
        except Exception as e:
            logger.error(f"Error deserializing {entity_type}: {e}")
            return None

    def get_entities_for_sync(
        self, 
        entity_type: str, 
        since: Optional[datetime] = None
    ) -> List[Dict]:
        """Get entities of a given type that need to be synced."""
        entities = []
        
        try:
            if entity_type == "clients":
                query = db.session.query(Client)
                if since:
                    query = query.filter(Client.updated_at >= since)
                for client in query.all():
                    serialized = self._serialize_client(client)
                    if serialized:
                        entities.append(serialized)
            
            elif entity_type == "projects":
                query = db.session.query(Project)
                if since:
                    query = query.filter(Project.updated_at >= since)
                for project in query.all():
                    serialized = self._serialize_project(project)
                    if serialized:
                        entities.append(serialized)
            
            elif entity_type == "websites":
                query = db.session.query(Website)
                if since:
                    query = query.filter(Website.updated_at >= since)
                for website in query.all():
                    serialized = self._serialize_website(website)
                    if serialized:
                        entities.append(serialized)
            
            elif entity_type == "rules":
                query = db.session.query(Rule)
                if since:
                    query = query.filter(Rule.updated_at >= since)
                for rule in query.all():
                    serialized = self._serialize_rule(rule)
                    if serialized:
                        entities.append(serialized)

            elif entity_type == "tasks":
                query = db.session.query(Task)
                if since:
                    query = query.filter(Task.updated_at >= since)
                for task in query.all():
                    serialized = self._serialize_task(task)
                    if serialized:
                        entities.append(serialized)

            elif entity_type == "documents":
                query = db.session.query(Document)
                if since:
                    query = query.filter(Document.updated_at >= since)
                for document in query.all():
                    serialized = self._serialize_document(document)
                    if serialized:
                        entities.append(serialized)

            elif entity_type == "learnings":
                query = db.session.query(InterconnectorLearning)
                if since:
                    query = query.filter(InterconnectorLearning.timestamp >= since)
                for learning in query.all():
                    serialized = self._serialize_learning(learning)
                    if serialized:
                        entities.append(serialized)

            elif entity_type == "images":
                query = db.session.query(Image)
                if since:
                    query = query.filter(Image.created_at >= since)
                for image in query.all():
                    serialized = self._serialize_image(image)
                    if serialized:
                        entities.append(serialized)

            elif entity_type == "chat_history":
                query = db.session.query(LLMSession)
                if since:
                    query = query.filter(LLMSession.created_at >= since)
                for session in query.all():
                    serialized = self._serialize_chat_history(session)
                    if serialized:
                        entities.append(serialized)

        except Exception as e:
            logger.error(f"Error getting entities for sync ({entity_type}): {e}")
        
        return entities

    def apply_entity(
        self, 
        entity_type: str, 
        entity_data: Dict,
        conflict_strategy: str = "last_write_wins",
        node_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Dict]:
        """
        Apply an entity to the local database.
        
        Args:
            entity_type: Type of entity (clients, projects, rules)
            entity_data: Serialized entity data
            conflict_strategy: How to handle conflicts (last_write_wins, manual)
            node_id: ID of the node this entity came from (for conflict tracking)
        
        Returns:
            Tuple of (success, conflict_id if conflict, stats dict)
        """
        stats = {"created": False, "updated": False, "skipped": False}
        conflict_id = None
        
        try:
            if entity_type == "clients":
                return self._apply_client(entity_data, conflict_strategy, stats, node_id)
            elif entity_type == "projects":
                return self._apply_project(entity_data, conflict_strategy, stats, node_id)
            elif entity_type == "rules":
                return self._apply_rule(entity_data, conflict_strategy, stats, node_id)
            elif entity_type == "websites":
                return self._apply_website(entity_data, conflict_strategy, stats, node_id)
            elif entity_type == "tasks":
                return self._apply_task(entity_data, conflict_strategy, stats, node_id)
            elif entity_type == "documents":
                return self._apply_document(entity_data, conflict_strategy, stats, node_id)
            elif entity_type == "learnings":
                return self._apply_learning(entity_data, conflict_strategy, stats, node_id)
            elif entity_type == "images":
                return self._apply_image(entity_data, conflict_strategy, stats, node_id)
            elif entity_type == "chat_history":
                return self._apply_chat_history(entity_data, conflict_strategy, stats, node_id)
            else:
                logger.warning(f"Unsupported entity type for apply: {entity_type}")
                stats["skipped"] = True
                return False, None, stats
        
        except Exception as e:
            logger.error(f"Error applying {entity_type}: {e}")
            return False, None, stats

    # --- Serialization Methods ---

    def _serialize_client(self, client: Client) -> Dict:
        """Serialize a Client entity."""
        return {
            "id": client.id,
            "name": client.name,
            "email": client.email,
            "phone": client.phone,
            "description": client.description,
            "logo_path": client.logo_path,
            "notes": client.notes,
            "contact_url": client.contact_url,
            "location": client.location,
            "primary_service": client.primary_service,
            "secondary_service": client.secondary_service,
            "brand_tone": client.brand_tone,
            "business_hours": client.business_hours,
            "social_links": client.social_links,
            "industry": client.industry,
            "target_audience": client.target_audience,
            "unique_selling_points": client.unique_selling_points,
            "competitor_urls": client.competitor_urls,
            "brand_voice_examples": client.brand_voice_examples,
            "keywords": client.keywords,
            "content_goals": client.content_goals,
            "regulatory_constraints": client.regulatory_constraints,
            "geographic_coverage": client.geographic_coverage,
            "created_at": client.created_at.isoformat() if client.created_at else None,
            "updated_at": client.updated_at.isoformat() if client.updated_at else None,
            "_sync_metadata": {
                "entity_type": "clients",
                "sync_id": f"client_{client.id}",
            }
        }

    def _serialize_project(self, project: Project) -> Dict:
        """Serialize a Project entity."""
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "client_id": project.client_id,
            "project_type": project.project_type,
            "target_keywords": project.target_keywords,
            "content_strategy": project.content_strategy,
            "deliverables": project.deliverables,
            "seo_strategy": project.seo_strategy,
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
            "_sync_metadata": {
                "entity_type": "projects",
                "sync_id": f"project_{project.id}",
            }
        }

    def _serialize_rule(self, rule: Rule) -> Dict:
        """Serialize a Rule entity."""
        # Get linked project IDs
        project_ids = [p.id for p in rule.linked_projects.all()] if hasattr(rule, 'linked_projects') else []
        
        return {
            "id": rule.id,
            "name": rule.name,
            "level": rule.level,
            "type": rule.type,
            "command_label": rule.command_label,
            "reference_id": rule.reference_id,
            "rule_text": rule.rule_text,
            "description": rule.description,
            "output_schema_name": rule.output_schema_name,
            "target_models": rule.target_models,
            "is_active": rule.is_active,
            "project_id": rule.project_id,
            "linked_project_ids": project_ids,  # For association sync
            "created_at": rule.created_at.isoformat() if rule.created_at else None,
            "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
            "_sync_metadata": {
                "entity_type": "rules",
                "sync_id": f"rule_{rule.id}",
            }
        }

    def _serialize_website(self, website: Website) -> Dict:
        """Serialize a Website entity."""
        return {
            "id": website.id,
            "url": website.url,
            "sitemap": website.sitemap,
            "competitor_url": website.competitor_url,
            "status": website.status,
            "last_crawled": website.last_crawled.isoformat() if website.last_crawled else None,
            "project_id": website.project_id,
            "client_id": website.client_id,
            "created_at": website.created_at.isoformat() if website.created_at else None,
            "updated_at": website.updated_at.isoformat() if website.updated_at else None,
            "_sync_metadata": {
                "entity_type": "websites",
                "sync_id": f"website_{website.id}",
            }
        }

    def _serialize_task(self, task: Task) -> Dict:
        """Serialize a Task entity."""
        return {
            "id": task.id,
            "name": task.name,
            "description": task.description,
            "status": task.status,
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "type": task.type,
            "job_id": task.job_id,
            "output_filename": task.output_filename,
            "prompt_text": task.prompt_text,
            "model_name": task.model_name,
            "workflow_config": task.workflow_config,
            "client_name": task.client_name,
            "target_website": task.target_website,
            "competitor_url": task.competitor_url,
            "project_id": task.project_id,
            "client_id": task.client_id,
            "website_id": task.website_id,
            "schedule_type": task.schedule_type,
            "cron_expression": task.cron_expression,
            "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
            "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
            "parent_task_id": task.parent_task_id,
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
            "retry_delay": task.retry_delay,
            "error_message": task.error_message,
            "task_handler": task.task_handler,
            "handler_config": task.handler_config,
            "progress": task.progress,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "_sync_metadata": {
                "entity_type": "tasks",
                "sync_id": f"task_{task.id}",
            }
        }

    def _serialize_document(self, document: Document) -> Dict:
        """Serialize a Document entity."""
        return {
            "id": document.id,
            "filename": document.filename,
            "path": document.path,
            "type": document.type,
            "index_status": document.index_status,
            "indexed_at": document.indexed_at.isoformat() if document.indexed_at else None,
            "error_message": document.error_message,
            "content": document.content,
            "is_code_file": document.is_code_file,
            "size": document.size,
            "file_metadata": document.file_metadata,
            "content_category": document.content_category,
            "relevance_score": document.relevance_score,
            "summary": document.summary,
            "rag_context": document.rag_context,
            "folder_id": document.folder_id,
            "client_id": document.client_id,
            "project_id": document.project_id,
            "website_id": document.website_id,
            "tags": document.tags,
            "notes": document.notes,
            "indexing_job_id": document.indexing_job_id,
            "uploaded_at": document.uploaded_at.isoformat() if document.uploaded_at else None,
            "updated_at": document.updated_at.isoformat() if document.updated_at else None,
            "_sync_metadata": {
                "entity_type": "documents",
                "sync_id": f"document_{document.id}",
            }
        }

    # --- Deserialization Methods ---

    def _deserialize_client(self, data: Dict) -> Optional[Client]:
        """Deserialize a Client from dictionary."""
        # Note: This creates a dict representation, not an actual Client object
        # The apply methods will handle actual database operations
        return data

    def _deserialize_project(self, data: Dict) -> Optional[Dict]:
        """Deserialize a Project from dictionary."""
        return data

    def _deserialize_rule(self, data: Dict) -> Optional[Dict]:
        """Deserialize a Rule from dictionary."""
        return data

    def _deserialize_website(self, data: Dict) -> Optional[Dict]:
        """Deserialize a Website from dictionary."""
        return data

    def _deserialize_task(self, data: Dict) -> Optional[Dict]:
        """Deserialize a Task from dictionary."""
        return data

    def _deserialize_document(self, data: Dict) -> Optional[Dict]:
        """Deserialize a Document from dictionary."""
        return data

    def _deserialize_learning(self, data: Dict) -> Optional[Dict]:
        """Deserialize a Learning from dictionary."""
        return data

    def _deserialize_image(self, data: Dict) -> Optional[Dict]:
        """Deserialize an Image from dictionary."""
        return data

    def _deserialize_chat_history(self, data: Dict) -> Optional[Dict]:
        """Deserialize a chat history (LLMSession + messages) from dictionary."""
        return data

    # --- Apply Methods ---

    def _apply_client(
        self, 
        data: Dict, 
        conflict_strategy: str,
        stats: Dict,
        node_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Dict]:
        """Apply a client entity to the database."""
        client_id = data.get("id")
        remote_updated = data.get("updated_at")
        
        # Parse updated_at
        remote_updated_dt = None
        if remote_updated:
            try:
                remote_updated_dt = datetime.fromisoformat(remote_updated.replace('Z', '+00:00'))
            except Exception:
                pass
        
        existing_client = db.session.get(Client, client_id) if client_id else None
        
        if existing_client:
            # Check for conflicts
            local_updated = existing_client.updated_at
            if local_updated and remote_updated_dt and local_updated != remote_updated_dt:
                # Conflict detected
                if conflict_strategy == "last_write_wins":
                    # Use the one with later updated_at
                    if remote_updated_dt > local_updated:
                        # Remote is newer, update
                        self._update_client(existing_client, data)
                        stats["updated"] = True
                        return True, None, stats
                    else:
                        # Local is newer, skip
                        stats["skipped"] = True
                        return True, None, stats
                else:
                    # Manual merge - create conflict record
                    conflict_id = self._create_conflict("clients", client_id, existing_client, data, node_id)
                    return False, conflict_id, stats
            else:
                # No conflict, update
                self._update_client(existing_client, data)
                stats["updated"] = True
                return True, None, stats
        else:
            # New client, create
            new_client = self._create_client(data)
            db.session.add(new_client)
            stats["created"] = True
            return True, None, stats

    def _apply_website(
        self,
        data: Dict,
        conflict_strategy: str,
        stats: Dict,
        node_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Dict]:
        """Apply a website entity to the database."""
        website_id = data.get("id")
        remote_updated = data.get("updated_at")

        remote_updated_dt = None
        if remote_updated:
            try:
                remote_updated_dt = datetime.fromisoformat(remote_updated.replace('Z', '+00:00'))
            except Exception:
                pass

        existing_website = db.session.get(Website, website_id) if website_id else None

        if existing_website:
            local_updated = existing_website.updated_at
            if local_updated and remote_updated_dt and local_updated != remote_updated_dt:
                if conflict_strategy == "last_write_wins":
                    if remote_updated_dt > local_updated:
                        self._update_website(existing_website, data)
                        stats["updated"] = True
                        return True, None, stats
                    else:
                        stats["skipped"] = True
                        return True, None, stats
                else:
                    conflict_id = self._create_conflict("websites", website_id, existing_website, data, node_id)
                    return False, conflict_id, stats
            else:
                self._update_website(existing_website, data)
                stats["updated"] = True
                return True, None, stats
        else:
            # Foreign keys are optional; proceed even if client/project missing
            new_website = self._create_website(data)
            db.session.add(new_website)
            stats["created"] = True
            return True, None, stats

    def _apply_project(
        self, 
        data: Dict, 
        conflict_strategy: str,
        stats: Dict,
        node_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Dict]:
        """Apply a project entity to the database."""
        project_id = data.get("id")
        remote_updated = data.get("updated_at")
        
        # Parse updated_at
        remote_updated_dt = None
        if remote_updated:
            try:
                remote_updated_dt = datetime.fromisoformat(remote_updated.replace('Z', '+00:00'))
            except Exception:
                pass
        
        existing_project = db.session.get(Project, project_id) if project_id else None
        
        if existing_project:
            # Check for conflicts
            local_updated = existing_project.updated_at
            if local_updated and remote_updated_dt and local_updated != remote_updated_dt:
                # Conflict detected
                if conflict_strategy == "last_write_wins":
                    if remote_updated_dt > local_updated:
                        self._update_project(existing_project, data)
                        stats["updated"] = True
                        return True, None, stats
                    else:
                        stats["skipped"] = True
                        return True, None, stats
                else:
                    conflict_id = self._create_conflict("projects", project_id, existing_project, data, node_id)
                    return False, conflict_id, stats
            else:
                self._update_project(existing_project, data)
                stats["updated"] = True
                return True, None, stats
        else:
            # Check if client_id exists (required foreign key)
            client_id = data.get("client_id")
            if client_id and not db.session.get(Client, client_id):
                logger.warning(f"Cannot create project {project_id}: client {client_id} does not exist")
                stats["skipped"] = True
                return False, None, stats
            
            new_project = self._create_project(data)
            db.session.add(new_project)
            stats["created"] = True
            return True, None, stats

    def _apply_rule(
        self, 
        data: Dict, 
        conflict_strategy: str,
        stats: Dict,
        node_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Dict]:
        """Apply a rule entity to the database."""
        rule_id = data.get("id")
        remote_updated = data.get("updated_at")
        
        remote_updated_dt = None
        if remote_updated:
            try:
                remote_updated_dt = datetime.fromisoformat(remote_updated.replace('Z', '+00:00'))
            except Exception:
                pass
        
        existing_rule = db.session.get(Rule, rule_id) if rule_id else None
        
        if existing_rule:
            local_updated = existing_rule.updated_at
            if local_updated and remote_updated_dt and local_updated != remote_updated_dt:
                if conflict_strategy == "last_write_wins":
                    if remote_updated_dt > local_updated:
                        self._update_rule(existing_rule, data)
                        stats["updated"] = True
                        return True, None, stats
                    else:
                        stats["skipped"] = True
                        return True, None, stats
                else:
                    conflict_id = self._create_conflict("rules", rule_id, existing_rule, data, node_id)
                    return False, conflict_id, stats
            else:
                self._update_rule(existing_rule, data)
                stats["updated"] = True
                return True, None, stats
        else:
            new_rule = self._create_rule(data)
            db.session.add(new_rule)
            db.session.flush()  # Get ID before setting associations
            
            # Handle linked projects association
            linked_project_ids = data.get("linked_project_ids", [])
            if linked_project_ids:
                self._update_rule_project_associations(new_rule, linked_project_ids)
            
            stats["created"] = True
            return True, None, stats

    def _apply_task(
        self,
        data: Dict,
        conflict_strategy: str,
        stats: Dict,
        node_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Dict]:
        """Apply a task entity to the database."""
        task_id = data.get("id")
        remote_updated = data.get("updated_at")

        remote_updated_dt = None
        if remote_updated:
            try:
                remote_updated_dt = datetime.fromisoformat(remote_updated.replace('Z', '+00:00'))
            except Exception:
                pass

        existing_task = db.session.get(Task, task_id) if task_id else None

        if existing_task:
            local_updated = existing_task.updated_at
            if local_updated and remote_updated_dt and local_updated != remote_updated_dt:
                if conflict_strategy == "last_write_wins":
                    if remote_updated_dt > local_updated:
                        self._update_task(existing_task, data)
                        stats["updated"] = True
                        return True, None, stats
                    else:
                        stats["skipped"] = True
                        return True, None, stats
                else:
                    conflict_id = self._create_conflict("tasks", task_id, existing_task, data, node_id)
                    return False, conflict_id, stats
            else:
                self._update_task(existing_task, data)
                stats["updated"] = True
                return True, None, stats
        else:
            new_task = self._create_task(data)
            db.session.add(new_task)
            stats["created"] = True
            return True, None, stats

    def _apply_document(
        self,
        data: Dict,
        conflict_strategy: str,
        stats: Dict,
        node_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Dict]:
        """Apply a document entity to the database."""
        document_id = data.get("id")
        remote_updated = data.get("updated_at")

        remote_updated_dt = None
        if remote_updated:
            try:
                remote_updated_dt = datetime.fromisoformat(remote_updated.replace('Z', '+00:00'))
            except Exception:
                pass

        existing_document = db.session.get(Document, document_id) if document_id else None

        if existing_document:
            local_updated = existing_document.updated_at
            if local_updated and remote_updated_dt and local_updated != remote_updated_dt:
                if conflict_strategy == "last_write_wins":
                    if remote_updated_dt > local_updated:
                        self._update_document(existing_document, data)
                        stats["updated"] = True
                        return True, None, stats
                    else:
                        stats["skipped"] = True
                        return True, None, stats
                else:
                    conflict_id = self._create_conflict("documents", document_id, existing_document, data, node_id)
                    return False, conflict_id, stats
            else:
                self._update_document(existing_document, data)
                stats["updated"] = True
                return True, None, stats
        else:
            new_document = self._create_document(data)
            db.session.add(new_document)
            stats["created"] = True
            return True, None, stats

    def _apply_learning(
        self,
        data: Dict,
        conflict_strategy: str,
        stats: Dict,
        node_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Dict]:
        """Apply a learning entity to the database."""
        learning_id = data.get("id")

        existing_learning = db.session.get(InterconnectorLearning, learning_id) if learning_id else None

        if existing_learning:
            # Learnings are append-only by nature; update review fields only
            existing_learning.uncle_reviewed = data.get("uncle_reviewed", existing_learning.uncle_reviewed)
            existing_learning.uncle_feedback = data.get("uncle_feedback", existing_learning.uncle_feedback)
            existing_learning.applied_by = data.get("applied_by", existing_learning.applied_by)
            stats["updated"] = True
            return True, None, stats
        else:
            new_learning = self._create_learning(data)
            db.session.add(new_learning)
            stats["created"] = True
            return True, None, stats

    def _apply_image(
        self,
        data: Dict,
        conflict_strategy: str,
        stats: Dict,
        node_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Dict]:
        """Apply an image entity to the database (metadata only)."""
        image_id = data.get("id")

        existing_image = db.session.get(Image, image_id) if image_id else None

        if existing_image:
            self._update_image(existing_image, data)
            stats["updated"] = True
            return True, None, stats
        else:
            new_image = self._create_image(data)
            db.session.add(new_image)
            stats["created"] = True
            return True, None, stats

    def _apply_chat_history(
        self,
        data: Dict,
        conflict_strategy: str,
        stats: Dict,
        node_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Dict]:
        """Apply a chat history entity (LLMSession + messages) to the database."""
        session_id = data.get("id")

        existing_session = db.session.get(LLMSession, session_id) if session_id else None

        if existing_session:
            # Update session fields
            existing_session.user = data.get("user", existing_session.user)
            existing_session.project_id = data.get("project_id", existing_session.project_id)
            if data.get("created_at"):
                try:
                    existing_session.created_at = datetime.fromisoformat(data["created_at"].replace('Z', '+00:00'))
                except Exception:
                    pass

            # Replace messages: delete existing, bulk insert incoming
            LLMMessage.query.filter_by(session_id=session_id).delete()
            self._insert_messages(session_id, data.get("messages", []))

            stats["updated"] = True
            return True, None, stats
        else:
            new_session = LLMSession(
                id=session_id,
                user=data.get("user", "unknown"),
                project_id=data.get("project_id"),
            )
            if data.get("created_at"):
                try:
                    new_session.created_at = datetime.fromisoformat(data["created_at"].replace('Z', '+00:00'))
                except Exception:
                    pass
            db.session.add(new_session)
            db.session.flush()  # Ensure session exists before inserting messages

            self._insert_messages(session_id, data.get("messages", []))

            stats["created"] = True
            return True, None, stats

    # --- Helper Methods for CRUD Operations ---

    def _create_client(self, data: Dict) -> Client:
        """Create a new Client from data."""
        client = Client(
            id=data.get("id"),  # Use provided ID to maintain consistency
            name=data["name"],
            email=data.get("email"),
            phone=data.get("phone"),
            description=data.get("description"),
            logo_path=data.get("logo_path"),
            notes=data.get("notes"),
            contact_url=data.get("contact_url"),
            location=data.get("location"),
            primary_service=data.get("primary_service"),
            secondary_service=data.get("secondary_service"),
            brand_tone=data.get("brand_tone"),
            business_hours=data.get("business_hours"),
            social_links=data.get("social_links"),
            industry=data.get("industry"),
            target_audience=data.get("target_audience"),
            unique_selling_points=data.get("unique_selling_points"),
            competitor_urls=data.get("competitor_urls"),
            brand_voice_examples=data.get("brand_voice_examples"),
            keywords=data.get("keywords"),
            content_goals=data.get("content_goals"),
            regulatory_constraints=data.get("regulatory_constraints"),
            geographic_coverage=data.get("geographic_coverage"),
        )
        # Set timestamps if provided
        if data.get("created_at"):
            try:
                client.created_at = datetime.fromisoformat(data["created_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("updated_at"):
            try:
                client.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        return client

    def _update_client(self, client: Client, data: Dict):
        """Update an existing Client with data."""
        client.name = data.get("name", client.name)
        client.email = data.get("email", client.email)
        client.phone = data.get("phone", client.phone)
        client.description = data.get("description", client.description)
        client.logo_path = data.get("logo_path", client.logo_path)
        client.notes = data.get("notes", client.notes)
        client.contact_url = data.get("contact_url", client.contact_url)
        client.location = data.get("location", client.location)
        client.primary_service = data.get("primary_service", client.primary_service)
        client.secondary_service = data.get("secondary_service", client.secondary_service)
        client.brand_tone = data.get("brand_tone", client.brand_tone)
        client.business_hours = data.get("business_hours", client.business_hours)
        client.social_links = data.get("social_links", client.social_links)
        client.industry = data.get("industry", client.industry)
        client.target_audience = data.get("target_audience", client.target_audience)
        client.unique_selling_points = data.get("unique_selling_points", client.unique_selling_points)
        client.competitor_urls = data.get("competitor_urls", client.competitor_urls)
        client.brand_voice_examples = data.get("brand_voice_examples", client.brand_voice_examples)
        client.keywords = data.get("keywords", client.keywords)
        client.content_goals = data.get("content_goals", client.content_goals)
        client.regulatory_constraints = data.get("regulatory_constraints", client.regulatory_constraints)
        client.geographic_coverage = data.get("geographic_coverage", client.geographic_coverage)
        # Update timestamp if provided
        if data.get("updated_at"):
            try:
                client.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass

    def _create_project(self, data: Dict) -> Project:
        """Create a new Project from data."""
        project = Project(
            id=data.get("id"),
            name=data["name"],
            description=data.get("description"),
            client_id=data.get("client_id"),
            project_type=data.get("project_type"),
            target_keywords=data.get("target_keywords"),
            content_strategy=data.get("content_strategy"),
            deliverables=data.get("deliverables"),
            seo_strategy=data.get("seo_strategy"),
        )
        if data.get("created_at"):
            try:
                project.created_at = datetime.fromisoformat(data["created_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("updated_at"):
            try:
                project.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        return project

    def _update_project(self, project: Project, data: Dict):
        """Update an existing Project with data."""
        project.name = data.get("name", project.name)
        project.description = data.get("description", project.description)
        project.client_id = data.get("client_id", project.client_id)
        project.project_type = data.get("project_type", project.project_type)
        project.target_keywords = data.get("target_keywords", project.target_keywords)
        project.content_strategy = data.get("content_strategy", project.content_strategy)
        project.deliverables = data.get("deliverables", project.deliverables)
        project.seo_strategy = data.get("seo_strategy", project.seo_strategy)
        if data.get("updated_at"):
            try:
                project.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass

    def _create_rule(self, data: Dict) -> Rule:
        """Create a new Rule from data."""
        rule = Rule(
            id=data.get("id"),
            name=data.get("name"),
            level=data.get("level", "system"),
            type=data.get("type"),
            command_label=data.get("command_label"),
            reference_id=data.get("reference_id"),
            rule_text=data["rule_text"],
            description=data.get("description"),
            output_schema_name=data.get("output_schema_name"),
            target_models=data.get("target_models"),
            is_active=data.get("is_active", True),
            project_id=data.get("project_id"),
        )
        if data.get("created_at"):
            try:
                rule.created_at = datetime.fromisoformat(data["created_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("updated_at"):
            try:
                rule.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        
        return rule

    def _update_rule(self, rule: Rule, data: Dict):
        """Update an existing Rule with data."""
        rule.name = data.get("name", rule.name)
        rule.level = data.get("level", rule.level)
        rule.type = data.get("type", rule.type)
        rule.command_label = data.get("command_label", rule.command_label)
        rule.reference_id = data.get("reference_id", rule.reference_id)
        rule.rule_text = data.get("rule_text", rule.rule_text)
        rule.description = data.get("description", rule.description)
        rule.output_schema_name = data.get("output_schema_name", rule.output_schema_name)
        rule.target_models = data.get("target_models", rule.target_models)
        rule.is_active = data.get("is_active", rule.is_active)
        rule.project_id = data.get("project_id", rule.project_id)
        if data.get("updated_at"):
            try:
                rule.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        
        # Update linked projects if provided
        linked_project_ids = data.get("linked_project_ids")
        if linked_project_ids is not None:
            self._update_rule_project_associations(rule, linked_project_ids)

    def _update_rule_project_associations(self, rule: Rule, project_ids: List[int]):
        """Update the project associations for a rule."""
        try:
            # Clear existing associations
            rule.linked_projects = []
            
            # Add new associations
            for project_id in project_ids:
                project = db.session.get(Project, project_id)
                if project:
                    rule.linked_projects.append(project)
        except Exception as e:
            logger.warning(f"Error updating rule-project associations: {e}")

    def _create_website(self, data: Dict) -> Website:
        """Create a new Website from data."""
        website = Website(
            id=data.get("id"),
            url=data.get("url"),
            sitemap=data.get("sitemap"),
            competitor_url=data.get("competitor_url"),
            status=data.get("status", "pending"),
            last_crawled=None,
            project_id=data.get("project_id"),
            client_id=data.get("client_id"),
        )
        if data.get("last_crawled"):
            try:
                website.last_crawled = datetime.fromisoformat(data["last_crawled"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("created_at"):
            try:
                website.created_at = datetime.fromisoformat(data["created_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("updated_at"):
            try:
                website.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        return website

    def _update_website(self, website: Website, data: Dict):
        """Update an existing Website with data."""
        website.url = data.get("url", website.url)
        website.sitemap = data.get("sitemap", website.sitemap)
        website.competitor_url = data.get("competitor_url", website.competitor_url)
        website.status = data.get("status", website.status)
        website.project_id = data.get("project_id", website.project_id)
        website.client_id = data.get("client_id", website.client_id)
        if data.get("last_crawled"):
            try:
                website.last_crawled = datetime.fromisoformat(data["last_crawled"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("updated_at"):
            try:
                website.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass

    def _create_task(self, data: Dict) -> Task:
        """Create a new Task from data."""
        task = Task(
            id=data.get("id"),
            name=data["name"],
            description=data.get("description"),
            status=data.get("status", "pending"),
            priority=data.get("priority", 2),
            type=data.get("type"),
            job_id=data.get("job_id"),
            output_filename=data.get("output_filename"),
            prompt_text=data.get("prompt_text"),
            model_name=data.get("model_name"),
            workflow_config=data.get("workflow_config"),
            client_name=data.get("client_name"),
            target_website=data.get("target_website"),
            competitor_url=data.get("competitor_url"),
            project_id=data.get("project_id"),
            client_id=data.get("client_id"),
            website_id=data.get("website_id"),
            schedule_type=data.get("schedule_type", "immediate"),
            cron_expression=data.get("cron_expression"),
            parent_task_id=data.get("parent_task_id"),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            retry_delay=data.get("retry_delay", 60),
            error_message=data.get("error_message"),
            task_handler=data.get("task_handler"),
            handler_config=data.get("handler_config"),
            progress=data.get("progress", 0),
        )
        if data.get("due_date"):
            try:
                task.due_date = datetime.fromisoformat(data["due_date"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("next_run_at"):
            try:
                task.next_run_at = datetime.fromisoformat(data["next_run_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("last_run_at"):
            try:
                task.last_run_at = datetime.fromisoformat(data["last_run_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("created_at"):
            try:
                task.created_at = datetime.fromisoformat(data["created_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("updated_at"):
            try:
                task.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        return task

    def _update_task(self, task: Task, data: Dict):
        """Update an existing Task with data."""
        task.name = data.get("name", task.name)
        task.description = data.get("description", task.description)
        task.status = data.get("status", task.status)
        task.priority = data.get("priority", task.priority)
        task.type = data.get("type", task.type)
        task.job_id = data.get("job_id", task.job_id)
        task.output_filename = data.get("output_filename", task.output_filename)
        task.prompt_text = data.get("prompt_text", task.prompt_text)
        task.model_name = data.get("model_name", task.model_name)
        task.workflow_config = data.get("workflow_config", task.workflow_config)
        task.client_name = data.get("client_name", task.client_name)
        task.target_website = data.get("target_website", task.target_website)
        task.competitor_url = data.get("competitor_url", task.competitor_url)
        task.project_id = data.get("project_id", task.project_id)
        task.client_id = data.get("client_id", task.client_id)
        task.website_id = data.get("website_id", task.website_id)
        task.schedule_type = data.get("schedule_type", task.schedule_type)
        task.cron_expression = data.get("cron_expression", task.cron_expression)
        task.parent_task_id = data.get("parent_task_id", task.parent_task_id)
        task.retry_count = data.get("retry_count", task.retry_count)
        task.max_retries = data.get("max_retries", task.max_retries)
        task.retry_delay = data.get("retry_delay", task.retry_delay)
        task.error_message = data.get("error_message", task.error_message)
        task.task_handler = data.get("task_handler", task.task_handler)
        task.handler_config = data.get("handler_config", task.handler_config)
        task.progress = data.get("progress", task.progress)
        if data.get("due_date"):
            try:
                task.due_date = datetime.fromisoformat(data["due_date"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("next_run_at"):
            try:
                task.next_run_at = datetime.fromisoformat(data["next_run_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("last_run_at"):
            try:
                task.last_run_at = datetime.fromisoformat(data["last_run_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("updated_at"):
            try:
                task.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass

    def _create_document(self, data: Dict) -> Document:
        """Create a new Document from data."""
        document = Document(
            id=data.get("id"),
            filename=data["filename"],
            path=data["path"],
            type=data.get("type"),
            index_status=data.get("index_status", "INDEXING"),
            error_message=data.get("error_message"),
            content=data.get("content"),
            is_code_file=data.get("is_code_file", False),
            size=data.get("size"),
            file_metadata=data.get("file_metadata"),
            content_category=data.get("content_category"),
            relevance_score=data.get("relevance_score", 5.0),
            summary=data.get("summary"),
            rag_context=data.get("rag_context"),
            folder_id=data.get("folder_id"),
            client_id=data.get("client_id"),
            project_id=data.get("project_id"),
            website_id=data.get("website_id"),
            tags=data.get("tags"),
            notes=data.get("notes"),
            indexing_job_id=data.get("indexing_job_id"),
        )
        if data.get("indexed_at"):
            try:
                document.indexed_at = datetime.fromisoformat(data["indexed_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("uploaded_at"):
            try:
                document.uploaded_at = datetime.fromisoformat(data["uploaded_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("updated_at"):
            try:
                document.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        return document

    def _update_document(self, document: Document, data: Dict):
        """Update an existing Document with data."""
        document.filename = data.get("filename", document.filename)
        document.path = data.get("path", document.path)
        document.type = data.get("type", document.type)
        document.index_status = data.get("index_status", document.index_status)
        document.error_message = data.get("error_message", document.error_message)
        document.content = data.get("content", document.content)
        document.is_code_file = data.get("is_code_file", document.is_code_file)
        document.size = data.get("size", document.size)
        document.file_metadata = data.get("file_metadata", document.file_metadata)
        document.content_category = data.get("content_category", document.content_category)
        document.relevance_score = data.get("relevance_score", document.relevance_score)
        document.summary = data.get("summary", document.summary)
        document.rag_context = data.get("rag_context", document.rag_context)
        document.folder_id = data.get("folder_id", document.folder_id)
        document.client_id = data.get("client_id", document.client_id)
        document.project_id = data.get("project_id", document.project_id)
        document.website_id = data.get("website_id", document.website_id)
        document.tags = data.get("tags", document.tags)
        document.notes = data.get("notes", document.notes)
        document.indexing_job_id = data.get("indexing_job_id", document.indexing_job_id)
        if data.get("indexed_at"):
            try:
                document.indexed_at = datetime.fromisoformat(data["indexed_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("uploaded_at"):
            try:
                document.uploaded_at = datetime.fromisoformat(data["uploaded_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("updated_at"):
            try:
                document.updated_at = datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
            except Exception:
                pass

    def _create_learning(self, data: Dict) -> InterconnectorLearning:
        """Create a new InterconnectorLearning from data."""
        learning = InterconnectorLearning(
            id=data.get("id"),
            source_node_id=data.get("source_node_id", "unknown"),
            learning_type=data.get("learning_type", "pattern"),
            description=data.get("description", ""),
            code_diff=data.get("code_diff"),
            confidence=data.get("confidence", 0.5),
            model_used=data.get("model_used"),
            applied_by=data.get("applied_by", "[]"),
            uncle_reviewed=data.get("uncle_reviewed", False),
            uncle_feedback=data.get("uncle_feedback"),
        )
        if data.get("timestamp"):
            try:
                learning.timestamp = datetime.fromisoformat(data["timestamp"].replace('Z', '+00:00'))
            except Exception:
                pass
        return learning

    def _create_image(self, data: Dict) -> Image:
        """Create a new Image from data (metadata only)."""
        image = Image(
            id=data.get("id"),
            hash=data.get("hash", ""),
            file_name=data.get("file_name", "unknown"),
            file_path=data.get("file_path"),
            file_size=data.get("file_size"),
            mime_type=data.get("mime_type"),
            tags=data.get("tags"),
        )
        if data.get("created_at"):
            try:
                image.created_at = datetime.fromisoformat(data["created_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        if data.get("last_used_at"):
            try:
                image.last_used_at = datetime.fromisoformat(data["last_used_at"].replace('Z', '+00:00'))
            except Exception:
                pass
        return image

    def _update_image(self, image: Image, data: Dict):
        """Update an existing Image with data."""
        image.file_name = data.get("file_name", image.file_name)
        image.file_path = data.get("file_path", image.file_path)
        image.file_size = data.get("file_size", image.file_size)
        image.mime_type = data.get("mime_type", image.mime_type)
        image.tags = data.get("tags", image.tags)
        if data.get("last_used_at"):
            try:
                image.last_used_at = datetime.fromisoformat(data["last_used_at"].replace('Z', '+00:00'))
            except Exception:
                pass

    def _insert_messages(self, session_id: str, messages: List[Dict]):
        """Bulk insert LLMMessage records for a given session."""
        for msg_data in messages:
            msg = LLMMessage(
                session_id=session_id,
                project_id=msg_data.get("project_id"),
                role=msg_data.get("role", "user"),
                content=msg_data.get("content", ""),
                extra_data=msg_data.get("extra_data"),
            )
            if msg_data.get("timestamp"):
                try:
                    msg.timestamp = datetime.fromisoformat(msg_data["timestamp"].replace('Z', '+00:00'))
                except Exception:
                    pass
            db.session.add(msg)

    def _create_conflict(
        self,
        entity_type: str,
        entity_id: Any,
        local_entity: Any,
        remote_data: Dict,
        node_id: Optional[str] = None
    ) -> Optional[str]:
        """Create a conflict record for manual resolution."""
        try:
            # Serialize local entity
            local_data = {}
            if hasattr(local_entity, 'to_dict'):
                local_data = local_entity.to_dict()
            else:
                # Basic serialization
                for key in dir(local_entity):
                    if not key.startswith('_'):
                        try:
                            value = getattr(local_entity, key)
                            if not callable(value):
                                local_data[key] = str(value) if value is not None else None
                        except Exception:
                            pass
            
            # Find conflicting fields
            conflict_fields = []
            for key, remote_value in remote_data.items():
                if key.startswith('_'):
                    continue
                local_value = local_data.get(key)
                if local_value != remote_value:
                    conflict_fields.append(key)
            
            # Create conflict record
            conflict = InterconnectorConflict(
                node_id=node_id or "unknown",
                entity_type=entity_type,
                entity_id=str(entity_id),
                local_data=json.dumps(local_data),
                remote_data=json.dumps(remote_data),
                conflict_fields=json.dumps(conflict_fields),
                resolution_strategy="manual",
                resolved=False,
            )
            db.session.add(conflict)
            db.session.flush()  # Get ID without committing
            return str(conflict.id)
        
        except Exception as e:
            logger.error(f"Error creating conflict record: {e}")
            return None


# Singleton instance
_sync_service = None

def get_sync_service() -> InterconnectorSyncService:
    """Get the singleton sync service instance."""
    global _sync_service
    if _sync_service is None:
        _sync_service = InterconnectorSyncService()
    return _sync_service

