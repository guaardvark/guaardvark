# backend/models.py
# Version 1.10: Enhanced Rule model with description, type, project_id, and updated constraints.
# MODIFIED: Added email and phone to Client model.

import json
import logging
import uuid
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship  # Ensure relationship is imported

logger = logging.getLogger(__name__)
db = SQLAlchemy()



# --- Association Table for Project <-> Rule/Prompt ---
project_rules_association = Table(
    "project_rules_association",
    db.metadata,
    Column(
        "project_id",
        Integer,
        ForeignKey("projects.id", name="fk_assoc_project_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "rule_id",
        Integer,
        ForeignKey("rules.id", name="fk_assoc_rule_id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
# --- End Association Table ---


# --- Setting Model ---
class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.Text, primary_key=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )

    def __repr__(self):
        return f"<Setting {self.key}={self.value}>"

    def to_dict(self):
        return {
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# --- SystemSetting Model ---
class SystemSetting(db.Model):
    __tablename__ = "system_settings"
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )

    def __repr__(self):
        return f"<SystemSetting {self.key}={self.value}>"

    def to_dict(self):
        return {
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# --- Model Definitions ---


class Model(db.Model):
    __tablename__ = "models"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    version = db.Column(db.String(80), nullable=True)
    quantized = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )

    def __repr__(self):
        return f"<Model {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "quantized": self.quantized,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True, index=True)
    email = db.Column(db.String(255), nullable=True, unique=True, index=True)  # ADDED
    phone = db.Column(db.String(50), nullable=True)  # ADDED
    description = db.Column(db.Text, nullable=True)  # ADDED for metadata indexing
    logo_path = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    # Extended fields for website metadata
    contact_url = db.Column(db.String(500), nullable=True)  # Contact URL
    location = db.Column(db.String(255), nullable=True)     # City, State
    primary_service = db.Column(db.String(255), nullable=True)
    secondary_service = db.Column(db.String(255), nullable=True)
    brand_tone = db.Column(db.String(50), nullable=True, default='neutral')
    business_hours = db.Column(db.Text, nullable=True)
    social_links = db.Column(db.Text, nullable=True)  # JSON array of {platform, url}

    # RAG Enhancement fields for intelligent content generation
    industry = db.Column(db.String(100), nullable=True)  # Industry vertical
    target_audience = db.Column(db.Text, nullable=True)  # Ideal customer profile
    unique_selling_points = db.Column(db.Text, nullable=True)  # Key differentiators
    competitor_urls = db.Column(db.Text, nullable=True)  # JSON array of competitor URLs
    brand_voice_examples = db.Column(db.Text, nullable=True)  # Sample content showing voice
    keywords = db.Column(db.Text, nullable=True)  # JSON array of target SEO keywords
    content_goals = db.Column(db.Text, nullable=True)  # Content marketing objectives
    regulatory_constraints = db.Column(db.Text, nullable=True)  # Compliance requirements
    geographic_coverage = db.Column(db.String(100), nullable=True)  # Service area scope
    projects = db.relationship(
        "Project", backref="client_ref", lazy="dynamic", cascade="all, delete-orphan"
    )
    # Use back_populates to avoid duplicate backref conflicts
    websites = db.relationship(
        "Website",
        back_populates="client_ref",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )

    def __repr__(self):
        return f"<Client {self.id}: {self.name}>"

    def to_dict(self):
        project_count = self.projects.count()

        # Helper function to safely parse JSON arrays with backward compatibility
        def safe_parse_json_array(value):
            if not value:
                return []
            # If already a list, return it
            if isinstance(value, list):
                return value
            # If it's a string, try to parse as JSON
            if isinstance(value, str):
                value = value.strip()
                # Check if it looks like JSON array
                if value.startswith('['):
                    try:
                        parsed = json.loads(value)
                        return parsed if isinstance(parsed, list) else []
                    except (json.JSONDecodeError, TypeError):
                        return []
                else:
                    # Legacy string format - convert to single-item array
                    return [value] if value else []
            return []

        # Parse social links JSON
        social_links = safe_parse_json_array(self.social_links)

        # Parse competitor URLs JSON
        competitor_urls = safe_parse_json_array(self.competitor_urls)

        # Parse keywords JSON
        keywords = safe_parse_json_array(self.keywords)

        # Parse new array fields (with backward compatibility for old string data)
        industry = safe_parse_json_array(self.industry)
        target_audience = safe_parse_json_array(self.target_audience)
        unique_selling_points = safe_parse_json_array(self.unique_selling_points)
        content_goals = safe_parse_json_array(self.content_goals)
        geographic_coverage = safe_parse_json_array(self.geographic_coverage)

        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "logo_path": self.logo_path,
            "notes": self.notes,
            "contact_url": self.contact_url,
            "location": self.location,
            "primary_service": self.primary_service,
            "secondary_service": self.secondary_service,
            "brand_tone": self.brand_tone,
            "business_hours": self.business_hours,
            "social_links": social_links,
            # RAG enhancement fields (now all arrays)
            "industry": industry,
            "target_audience": target_audience,
            "unique_selling_points": unique_selling_points,
            "competitor_urls": competitor_urls,
            "brand_voice_examples": self.brand_voice_examples,
            "keywords": keywords,
            "content_goals": content_goals,
            "regulatory_constraints": self.regulatory_constraints,
            "geographic_coverage": geographic_coverage,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "project_count": project_count,
        }


class Rule(db.Model):
    __tablename__ = "rules"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True, unique=False, index=True)
    level = db.Column(db.String(50), nullable=False, index=True)
    type = db.Column(
        db.String(50), nullable=True, index=True, default="PROMPT_TEMPLATE"
    )
    command_label = db.Column(db.String(100), nullable=True, unique=True, index=True)
    reference_id = db.Column(db.String(255), nullable=True, index=True)
    rule_text = db.Column(db.Text, nullable=False)

    description = db.Column(db.Text, nullable=True)
    output_schema_name = db.Column(db.String(100), nullable=True)
    target_models_json = db.Column(
        "target_models", db.Text, nullable=True, default='["__ALL__"]'
    )
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("projects.id", name="fk_rule_project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    project = db.relationship(
        "Project",
        backref=db.backref("primary_rules", lazy="dynamic"),
        foreign_keys=[project_id],
    )
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )
    __table_args__ = (
        CheckConstraint(
            level.in_(
                [
                    "SYSTEM",
                    "PROJECT",
                    "CLIENT",
                    "USER_GLOBAL",
                    "USER_SPECIFIC",
                    "PROMPT",
                    "LEARNED",
                ]
            ),
            name="ck_rule_level_updated",
        ),
        CheckConstraint(
            type.in_(
                [
                    "PROMPT_TEMPLATE",
                    "QA_TEMPLATE",
                    "COMMAND_RULE",
                    "FILTER_RULE",
                    "FORMATTING_RULE",
                    "SYSTEM_PROMPT",
                    "OTHER",
                ]
            ),
            name="ck_rule_type",
        ),
        CheckConstraint('LENGTH(rule_text) <= 50000', name='ck_rule_text_length_extended'),
        Index("ix_rule_level_name", "level", "name"),
        Index(
            "uq_rule_identity_active",
            "name",
            "level",
            "type",
            unique=True,
            postgresql_where=text("is_active = TRUE AND name != 'qa_default'"),
        ),
    )
    linked_projects = db.relationship(
        "Project",
        secondary=project_rules_association,
        back_populates="linked_rules",
        lazy="dynamic",
    )

    @property
    def target_models(self):
        if not self.target_models_json:
            return ["__ALL__"]
        try:
            models = json.loads(self.target_models_json)
            return models if isinstance(models, list) else ["__ALL__"]
        except (json.JSONDecodeError, TypeError):
            return ["__ALL__"]

    @target_models.setter
    def target_models(self, value):
        if value is None or not value:
            self.target_models_json = json.dumps(["__ALL__"])
        elif isinstance(value, list):
            if all(isinstance(item, str) for item in value):
                self.target_models_json = json.dumps(value)
            else:
                self.target_models_json = json.dumps(["__ALL__"])
                logger.warning(
                    "Invalid items in target_models list, defaulting to ALL."
                )
        else:
            self.target_models_json = json.dumps(["__ALL__"])
            logger.warning(
                f"Invalid type for target_models ({type(value)}), defaulting to ALL."
            )

    def __repr__(self):
        return f"<Rule id={self.id} level={self.level} type={self.type} name={self.name} cmd={self.command_label} active={self.is_active}>"

    def to_dict(self):
        project_info = (
            {"id": self.project.id, "name": self.project.name} if self.project else None
        )
        return {
            "id": self.id,
            "name": self.name,
            "level": self.level,
            "type": self.type,
            "description": self.description,
            "command_label": self.command_label,
            "output_schema_name": self.output_schema_name,
            "reference_id": self.reference_id,
            "rule_text": self.rule_text,
            "target_models": self.target_models,
            "is_active": self.is_active,
            "project_id": self.project_id,
            "project": project_info,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="pending", index=True)
    priority = db.Column(db.Integer, default=2)

    due_date = db.Column(db.DateTime, nullable=True)
    type = db.Column(db.String(100), nullable=True, index=True)
    job_id = db.Column(db.String(36), nullable=True, index=True)
    output_filename = db.Column(db.String(255), nullable=True)
    prompt_text = db.Column(db.Text, nullable=True)
    model_name = db.Column(db.String(120), nullable=True)
    workflow_config = db.Column(db.Text, nullable=True)  # JSON config for workflow execution
    client_name = db.Column(db.String(255), nullable=True)  # Store client name for display
    target_website = db.Column(db.String(2048), nullable=True)  # Store target website URL
    competitor_url = db.Column(db.String(2048), nullable=True)  # Store competitor website URL for analysis

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(), index=True)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("projects.id", name="fk_task_project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    client_id = db.Column(
        db.Integer,
        db.ForeignKey("clients.id", name="fk_task_client_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    website_id = db.Column(
        db.Integer,
        db.ForeignKey("websites.id", name="fk_task_website_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    # Scheduler fields (from scheduler_001 migration)
    schedule_type = db.Column(db.String(50), nullable=True, default='immediate', index=True)
    cron_expression = db.Column(db.String(100), nullable=True)
    next_run_at = db.Column(db.DateTime, nullable=True, index=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    parent_task_id = db.Column(
        db.Integer,
        db.ForeignKey("tasks.id", name="fk_task_parent_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    retry_count = db.Column(db.Integer, nullable=True, default=0)
    max_retries = db.Column(db.Integer, nullable=True, default=3)
    retry_delay = db.Column(db.Integer, nullable=True, default=60)
    error_message = db.Column(db.Text, nullable=True)
    task_handler = db.Column(db.String(100), nullable=True, index=True)
    handler_config = db.Column(db.JSON, nullable=True)
    progress = db.Column(db.Integer, nullable=True, default=0)  # 0-100
    # Generated output from the task handler — LLM reply, tool output, etc.
    # Written by unified_task_executor.update_task_result() on successful runs.
    result = db.Column(db.Text, nullable=True)
    
    # Relationships
    project = db.relationship("Project", backref=db.backref("tasks", lazy="dynamic"))
    client_ref = db.relationship("Client", backref=db.backref("client_tasks", lazy="dynamic"))
    website_ref = db.relationship("Website", backref=db.backref("website_tasks", lazy="dynamic"))

    def __repr__(self):
        return (
            f"<Task {self.id}: {self.name} ({self.status}) Project:{self.project_id}>"
        )

    def to_dict(self):
        project_info = (
            {"id": self.project.id, "name": self.project.name} if self.project else None
        )
        client_info = (
            {"id": self.client_ref.id, "name": self.client_ref.name} if self.client_ref else None
        )
        website_info = (
            {"id": self.website_ref.id, "name": self.website_ref.name, "url": self.website_ref.url} if self.website_ref else None
        )
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "type": self.type,
            "job_id": self.job_id,
            "output_filename": self.output_filename,
            "prompt_text": self.prompt_text,
            "model_name": self.model_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "project_id": self.project_id,
            "project": project_info,
            "client_id": self.client_id,
            "client": client_info,
            "client_name": self.client_name,
            "website_id": self.website_id,
            "website": website_info,
            "target_website": self.target_website,
            "competitor_url": self.competitor_url,
            # Scheduler fields
            "schedule_type": self.schedule_type,
            "cron_expression": self.cron_expression,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "parent_task_id": self.parent_task_id,
            "retry_count": self.retry_count or 0,
            "max_retries": self.max_retries or 3,
            "retry_delay": self.retry_delay or 60,
            "error_message": self.error_message,
            "task_handler": self.task_handler,
            "handler_config": self.handler_config,
            "progress": self.progress or 0,
            "result": self.result,
        }


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    client_id = db.Column(
        db.Integer,
        db.ForeignKey("clients.id", name="fk_project_client_id"),
        nullable=True,
        index=True,
    )

    # RAG Enhancement fields for project-specific content strategy
    project_type = db.Column(db.String(100), nullable=True)  # Website Redesign, Content Campaign, etc.
    target_keywords = db.Column(db.Text, nullable=True)  # JSON array of project-specific keywords
    content_strategy = db.Column(db.Text, nullable=True)  # Overall content approach
    deliverables = db.Column(db.Text, nullable=True)  # Expected outputs
    seo_strategy = db.Column(db.Text, nullable=True)  # SEO approach

    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )
    documents = db.relationship(
        "Document", backref="project", lazy="dynamic", cascade="all, delete-orphan"
    )
    websites = db.relationship(
        "Website", backref="project", lazy="dynamic", cascade="all, delete-orphan"
    )
    linked_rules = db.relationship(
        "Rule",
        secondary=project_rules_association,
        back_populates="linked_projects",
        lazy="dynamic",
    )

    def to_dict(self):
        from backend.utils.serialization_utils import format_logo_path
        client_info = (
            {
                "id": self.client_ref.id,
                "name": self.client_ref.name,
                "logo_path": format_logo_path(self.client_ref.logo_path),
            }
            if self.client_ref
            else None
        )
        doc_count = self.documents.count()
        website_count = self.websites.count()
        task_count = self.tasks.count()
        rule_count = self.linked_rules.count()
        primary_rule_count = (
            self.primary_rules.count() if hasattr(self, "primary_rules") else 0
        )

        # Parse target keywords JSON
        target_keywords = []
        if self.target_keywords:
            try:
                target_keywords = json.loads(self.target_keywords)
            except (json.JSONDecodeError, TypeError):
                target_keywords = []

        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "client_id": self.client_id,
            "client": client_info,
            # RAG enhancement fields
            "project_type": self.project_type,
            "target_keywords": target_keywords,
            "content_strategy": self.content_strategy,
            "deliverables": self.deliverables,
            "seo_strategy": self.seo_strategy,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "document_count": doc_count,
            "website_count": website_count,
            "task_count": task_count,
            "rule_count": rule_count,
            "primary_rule_count": primary_rule_count,
        }


class Website(db.Model):
    __tablename__ = "websites"
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(2048), nullable=False, unique=True)
    sitemap = db.Column(db.String(2048), nullable=True)
    competitor_url = db.Column(db.String(2048), nullable=True)  # Competitor website for analysis
    status = db.Column(db.String(50), default="pending", index=True)
    last_crawled = db.Column(db.DateTime, nullable=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("projects.id", name="fk_website_project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    client_id = db.Column(
        db.Integer,
        db.ForeignKey("clients.id", name="fk_website_client_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Establish two-way relationship with Client using back_populates
    client_ref = db.relationship(
        "Client", back_populates="websites", foreign_keys=[client_id]
    )
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )

    def to_dict(self):
        from backend.utils.serialization_utils import format_logo_path
        doc_count = self.documents.count() if hasattr(self, "documents") else 0
        project_info = (
            {"id": self.project.id, "name": self.project.name} if self.project else None
        )
        client_info = (
            {"id": self.client_ref.id, "name": self.client_ref.name, "logo_path": format_logo_path(self.client_ref.logo_path)}
            if self.client_ref
            else None
        )
        return {
            "id": self.id,
            "url": self.url,
            "sitemap": self.sitemap,
            "competitor_url": self.competitor_url,
            "status": self.status,
            "last_crawled": (
                self.last_crawled.isoformat() if self.last_crawled else None
            ),
            "project_id": self.project_id,
            "project": project_info,
            "client_id": self.client_id,
            "client": client_info,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "document_count": doc_count,
        }


class Folder(db.Model):
    """File system folder model for organizing documents"""
    __tablename__ = "folders"
    __table_args__ = (
        db.Index("ix_folder_parent_name", "parent_id", "name"),
    )
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(1024), nullable=False, unique=True)  # Full path on disk relative to uploads/
    parent_id = db.Column(
        db.Integer,
        db.ForeignKey("folders.id", name="fk_folder_parent_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )

    # Repository features
    is_repository = db.Column(db.Boolean, default=False, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)  # AI-generated architectural summary
    repo_metadata = db.Column(db.Text, nullable=True)  # JSON: languages, frameworks, key_components

    # Entity links (same as Document model — allows folder-level property assignment)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id", name="fk_folder_client_id"), nullable=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", name="fk_folder_project_id"), nullable=True)
    website_id = db.Column(db.Integer, db.ForeignKey("wordpress_sites.id", name="fk_folder_website_id"), nullable=True)
    tags = db.Column(db.Text, nullable=True)  # Comma-separated tags
    notes = db.Column(db.Text, nullable=True)

    # Relationships
    parent = db.relationship("Folder", remote_side=[id], backref=db.backref("subfolders", lazy="dynamic"))
    documents = db.relationship("Document", back_populates="folder", lazy="dynamic", cascade="all, delete-orphan")
    client = db.relationship("Client", foreign_keys=[client_id])
    project = db.relationship("Project", foreign_keys=[project_id])

    def to_dict(self):
        # Parse repo_metadata if present
        metadata = {}
        if self.repo_metadata:
            try:
                metadata = json.loads(self.repo_metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "parent_id": self.parent_id,
            "is_repository": self.is_repository,
            "description": self.description,
            "repo_metadata": metadata,
            "client_id": self.client_id,
            "project_id": self.project_id,
            "website_id": self.website_id,
            "tags": self.tags,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "subfolder_count": self.subfolders.count() if self.subfolders else 0,
            "document_count": self.documents.count() if self.documents else 0,
            "indexed_document_count": sum(1 for d in self.documents if d.index_status == 'INDEXED') if self.documents else 0,
        }

    def to_dict_light(self):
        """Lightweight serialization - no relationship loads, counts injected externally"""
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "parent_id": self.parent_id,
            "is_repository": self.is_repository,
            "client_id": self.client_id,
            "project_id": self.project_id,
            "website_id": self.website_id,
            "tags": self.tags,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "subfolder_count": 0,
            "document_count": 0,
            "indexed_document_count": 0,
        }

    def __repr__(self):
        return f"<Folder {self.name} ({self.path})>"


class Document(db.Model):
    __tablename__ = "documents"
    __table_args__ = (
        db.Index("ix_doc_folder_filename", "folder_id", "filename"),
        db.Index("ix_doc_folder_uploaded", "folder_id", "uploaded_at"),
        db.Index("ix_doc_folder_size", "folder_id", "size"),
    )
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(1024), nullable=False, unique=True)
    type = db.Column(db.String(50), nullable=True)
    index_status = db.Column(db.String(50), default="INDEXING", index=True)
    indexed_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    # NEW: Enhanced file storage for code files - using LONGTEXT for larger content
    content = db.Column(db.Text, nullable=True)  # Store complete file content for code files (up to 2GB)
    is_code_file = db.Column(db.Boolean, default=False)  # Flag for code files that should be stored for discussion
    size = db.Column(db.Integer, nullable=True)  # File size in bytes
    file_metadata = db.Column(db.Text, nullable=True)  # JSON metadata (renamed from 'metadata' due to SQLAlchemy reservation)

    # RAG Enhancement fields for intelligent document categorization
    content_category = db.Column(db.String(100), nullable=True)  # Style Guide, Brand Assets, Training Data, etc.
    relevance_score = db.Column(db.Float, nullable=True, default=5.0)  # RAG priority (1-10)
    summary = db.Column(db.Text, nullable=True)  # AI-generated summary of document
    rag_context = db.Column(db.Text, nullable=True)  # When/how this document should inform content

    # Folder hierarchy
    folder_id = db.Column(
        db.Integer,
        db.ForeignKey("folders.id", name="fk_document_folder_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Optional entity links (nullable)
    client_id = db.Column(
        db.Integer,
        db.ForeignKey("clients.id", name="fk_document_client_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    project_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "projects.id", name="fk_document_project_id", ondelete="SET NULL"
        ),
        nullable=True,
        index=True,
    )
    website_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "websites.id", name="fk_document_website_id", ondelete="SET NULL"
        ),
        nullable=True,
        index=True,
    )
    tags = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)  # User notes - used for RAG context
    indexing_job_id = db.Column(db.String(255), nullable=True, index=True)  # Progress tracking job ID
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(), index=True)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )
    # Relationships
    folder = db.relationship("Folder", back_populates="documents")
    client = db.relationship("Client", backref=db.backref("client_documents", lazy="dynamic"))
    website = db.relationship(
        "Website", backref=db.backref("documents", lazy="dynamic")
    )

    def to_dict(self):
        from backend.utils.serialization_utils import format_logo_path
        folder_info = (
            {"id": self.folder.id, "name": self.folder.name, "path": self.folder.path} if self.folder else None
        )
        client_info = (
            {"id": self.client.id, "name": self.client.name, "logo_path": format_logo_path(self.client.logo_path)} if self.client else None
        )
        project_info = (
            {"id": self.project.id, "name": self.project.name} if self.project else None
        )
        website_info = (
            {"id": self.website.id, "url": self.website.url} if self.website else None
        )
        parsed_tags = []
        if self.tags:
            try:
                tags_data = json.loads(self.tags)
                if isinstance(tags_data, list):
                    parsed_tags = tags_data
                else:
                    parsed_tags = [
                        tag.strip() for tag in str(tags_data).split(",") if tag.strip()
                    ]
            except json.JSONDecodeError:
                parsed_tags = [
                    tag.strip() for tag in self.tags.split(",") if tag.strip()
                ]

        # Parse metadata if it exists
        parsed_metadata = {}
        if self.file_metadata:
            try:
                parsed_metadata = json.loads(self.file_metadata)
            except json.JSONDecodeError:
                parsed_metadata = {}
                
        return {
            "id": self.id,
            "filename": self.filename,
            "path": self.path,
            "type": self.type,
            "index_status": self.index_status,
            "indexed_at": self.indexed_at.isoformat() if self.indexed_at else None,
            "error_message": self.error_message,

            # Enhanced file storage fields
            "content": self.content,  # Complete file content for code files
            "is_code_file": self.is_code_file,  # Flag for code files
            "size": self.size,  # File size in bytes
            "metadata": parsed_metadata,  # Parsed metadata

            # RAG enhancement fields
            "content_category": self.content_category,
            "relevance_score": self.relevance_score,
            "summary": self.summary,
            "rag_context": self.rag_context,

            # Folder and entity links
            "folder_id": self.folder_id,
            "folder": folder_info,
            "client_id": self.client_id,
            "client": client_info,
            "project_id": self.project_id,
            "project": project_info,
            "website_id": self.website_id,
            "website": website_info,
            "tags": parsed_tags,
            "notes": self.notes,
            "indexing_job_id": self.indexing_job_id,  # Include job ID for progress tracking
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_dict_light(self):
        """Lightweight serialization - no content, no relationship loads, no JSON parsing"""
        return {
            "id": self.id,
            "filename": self.filename,
            "path": self.path,
            "type": self.type,
            "size": self.size,
            "index_status": self.index_status,
            "is_code_file": self.is_code_file,
            "folder_id": self.folder_id,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class LLMSession(db.Model):
    __tablename__ = "llm_sessions"
    id = db.Column(db.String(36), primary_key=True)
    user = db.Column(db.String(80), nullable=False, index=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("projects.id", name="fk_llmsession_project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    messages = db.relationship(
        "LLMMessage", backref="session", lazy="dynamic", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<LLMSession {self.id}>"


class LLMMessage(db.Model):
    __tablename__ = "llm_messages"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.String(36),
        db.ForeignKey("llm_sessions.id", name="fk_llmmessage_session_id"),
        nullable=False,
        index=True,
    )
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("projects.id", name="fk_llmmessage_project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False)
    extra_data = db.Column(db.JSON, nullable=True)
    timestamp = db.Column(
        db.DateTime, default=lambda: datetime.now(), index=True
    )
    __table_args__ = (
        CheckConstraint(
            role.in_(["user", "assistant", "system"]), name="ck_message_role"
        ),
    )

    def __repr__(self):
        return f"<LLMMessage {self.id} ({self.role})>"

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "role": self.role,
            "content": self.content,
            "extra_data": self.extra_data,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class TrainingDataset(db.Model):
    __tablename__ = "training_datasets"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    path = db.Column(db.String(1024), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )

    def __repr__(self):
        return f"<TrainingDataset {self.id}: {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeviceProfile(db.Model):
    __tablename__ = "device_profiles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    device_type = db.Column(db.String(50))  # 'gpu' or 'cpu'
    gpu_vram_mb = db.Column(db.Integer)
    system_ram_mb = db.Column(db.Integer)
    max_batch_size = db.Column(db.Integer, default=2)
    max_seq_length = db.Column(db.Integer, default=2048)
    supports_4bit = db.Column(db.Boolean, default=True)
    requires_cpu_offload = db.Column(db.Boolean, default=False)
    is_default = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    compute_capability = db.Column(db.String(10))  # CUDA compute capability e.g., "8.9"
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())

    def __repr__(self):
        return f"<DeviceProfile {self.id}: {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "device_type": self.device_type,
            "gpu_vram_mb": self.gpu_vram_mb,
            "system_ram_mb": self.system_ram_mb,
            "max_batch_size": self.max_batch_size,
            "max_seq_length": self.max_seq_length,
            "supports_4bit": self.supports_4bit,
            "requires_cpu_offload": self.requires_cpu_offload,
            "is_default": self.is_default,
            "is_active": self.is_active,
            "compute_capability": self.compute_capability,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TrainingJob(db.Model):
    __tablename__ = "training_jobs"
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(64), unique=True, index=True)  # UUID
    name = db.Column(db.String(255))
    
    # Config
    base_model = db.Column(db.String(255))
    output_model_name = db.Column(db.String(255))
    dataset_id = db.Column(db.Integer, db.ForeignKey("training_datasets.id"))
    config_json = db.Column(db.Text)  # JSON: {steps, lr, batch, rank, seq_length}
    device_profile_id = db.Column(db.Integer, db.ForeignKey("device_profiles.id"))
    
    # Status
    pipeline_stage = db.Column(db.String(50))  # parsing, filtering, training, exporting, importing
    status = db.Column(db.String(50), default="pending")  # pending, running, completed, failed, cancelled
    progress = db.Column(db.Integer, default=0)  # 0-100
    current_step = db.Column(db.Integer, default=0)
    total_steps = db.Column(db.Integer)
    error_message = db.Column(db.Text)
    metrics_json = db.Column(db.Text)  # JSON: {loss, epoch, lr, etc}
    
    # Output
    lora_path = db.Column(db.String(1024))
    gguf_path = db.Column(db.String(1024))
    ollama_model_name = db.Column(db.String(255))
    quantization_level = db.Column(db.String(50))  # e.g., q4_k_m, q8_0, f16
    
    # Timing
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    celery_task_id = db.Column(db.String(64))

    # Resume/Abort support
    checkpoint_path = db.Column(db.String(1024))  # Path to last checkpoint for resume
    pid = db.Column(db.Integer)  # Process ID for abort via SIGTERM
    is_resumable = db.Column(db.Boolean, default=False)  # Whether job can be resumed

    # Relationships
    dataset = db.relationship("TrainingDataset", backref="training_jobs")
    device_profile = db.relationship("DeviceProfile", backref="training_jobs")

    def __repr__(self):
        return f"<TrainingJob {self.id}: {self.job_id} - {self.name}>"

    def to_dict(self):
        config = {}
        metrics = {}
        try:
            if self.config_json:
                config = json.loads(self.config_json)
        except (ValueError, TypeError):
            pass
        try:
            if self.metrics_json:
                metrics = json.loads(self.metrics_json)
        except (ValueError, TypeError):
            pass

        return {
            "id": self.id,
            "job_id": self.job_id,
            "name": self.name,
            "base_model": self.base_model,
            "output_model_name": self.output_model_name,
            "dataset_id": self.dataset_id,
            "config": config,
            "device_profile_id": self.device_profile_id,
            "pipeline_stage": self.pipeline_stage,
            "status": self.status,
            "progress": self.progress,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "error_message": self.error_message,
            "metrics": metrics,
            "lora_path": self.lora_path,
            "gguf_path": self.gguf_path,
            "ollama_model_name": self.ollama_model_name,
            "quantization_level": self.quantization_level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "celery_task_id": self.celery_task_id,
            "checkpoint_path": self.checkpoint_path,
            "pid": self.pid,
            "is_resumable": self.is_resumable,
        }


# --- Content Generation Models ---

class Generation(db.Model):
    """Tracks content generation batches for traceability and persistence"""
    __tablename__ = "generations"

    id = db.Column(db.String(36), primary_key=True)  # UUID
    site_key = db.Column(db.String(255), nullable=True, index=True)  # Maps to website selection
    delimiter = db.Column(db.String(10), nullable=False, default=',')  # CSV delimiter
    structured_html = db.Column(db.Boolean, nullable=False, default=False)
    brand_tone = db.Column(db.String(50), nullable=True)
    meta_json = db.Column(db.Text, nullable=True)  # JSON: phone, contactUrl, location, services, hours, socials
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(), nullable=False)

    # FileGenerationPage context fields
    client = db.Column(db.String(255), nullable=True, index=True)
    project = db.Column(db.String(255), nullable=True)
    website = db.Column(db.String(500), nullable=True)
    competitor = db.Column(db.String(500), nullable=True)

    # Relationships
    pages = db.relationship("Page", back_populates="generation", lazy="dynamic", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Generation {self.id}: {self.site_key}>"

    def to_dict(self):
        meta = {}
        if self.meta_json:
            try:
                meta = json.loads(self.meta_json)
            except (json.JSONDecodeError, TypeError):
                meta = {}

        return {
            "id": self.id,
            "site_key": self.site_key,
            "delimiter": self.delimiter,
            "structured_html": self.structured_html,
            "brand_tone": self.brand_tone,
            "meta": meta,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "page_count": self.pages.count() if hasattr(self, 'pages') else 0,
            "client": self.client,
            "project": self.project,
            "website": self.website,
            "competitor": self.competitor
        }


class Page(db.Model):
    """Individual generated pages/posts with content and metadata"""
    __tablename__ = "pages"

    id = db.Column(db.String(36), primary_key=True)  # UUID
    generation_id = db.Column(db.String(36), db.ForeignKey("generations.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.Text, nullable=False)
    slug = db.Column(db.String(500), nullable=False, index=True)
    category = db.Column(db.Text, nullable=True)
    tags = db.Column(db.Text, nullable=True)  # JSON array or comma-separated
    content = db.Column(db.Text, nullable=False)  # HTML or plaintext per toggle
    excerpt = db.Column(db.Text, nullable=True)
    meta_json = db.Column(db.Text, nullable=True)  # JSON: any per-row extras
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(), nullable=False)

    # Approval/deletion status
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)  # pending, approved, deleted
    approved_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    generation = db.relationship("Generation", back_populates="pages")

    # Indexes for performance
    __table_args__ = (
        Index("ix_page_generation_created", "generation_id", "created_at"),
        Index("ix_page_slug_generation", "slug", "generation_id"),
    )

    def __repr__(self):
        return f"<Page {self.id}: {self.title[:50]}>"

    def to_dict(self):
        meta = {}
        if self.meta_json:
            try:
                meta = json.loads(self.meta_json)
            except (json.JSONDecodeError, TypeError):
                meta = {}

        return {
            "id": self.id,
            "generation_id": self.generation_id,
            "title": self.title,
            "slug": self.slug,
            "category": self.category,
            "tags": self.tags,
            "content": self.content,
            "excerpt": self.excerpt,
            "meta": meta,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "status": self.status,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None
        }


class Image(db.Model):
    """Image asset management for future roadmap"""
    __tablename__ = "images"

    id = db.Column(db.String(36), primary_key=True)  # UUID
    hash = db.Column(db.String(64), nullable=False, unique=True, index=True)  # File hash for deduplication
    file_name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(1024), nullable=True)  # Path to stored file
    file_size = db.Column(db.Integer, nullable=True)  # File size in bytes
    mime_type = db.Column(db.String(100), nullable=True)
    tags = db.Column(db.Text, nullable=True)  # JSON array of tags
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(), nullable=False)
    last_used_at = db.Column(db.DateTime, nullable=True)

    # Indexes
    __table_args__ = (
        Index("ix_image_hash_filename", "hash", "file_name"),
        Index("ix_image_created_used", "created_at", "last_used_at"),
    )

    def __repr__(self):
        return f"<Image {self.id}: {self.file_name}>"

    def to_dict(self):
        tags = []
        if self.tags:
            try:
                tags = json.loads(self.tags)
            except (json.JSONDecodeError, TypeError):
                tags = []

        return {
            "id": self.id,
            "hash": self.hash,
            "file_name": self.file_name,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "tags": tags,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None
        }


# --- Interconnector Models ---
class InterconnectorNode(db.Model):
    """Track registered interconnector nodes."""
    __tablename__ = "interconnector_nodes"
    node_id = db.Column(db.String(36), primary_key=True)  # UUID
    node_name = db.Column(db.String(255), nullable=False)
    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer(), nullable=False)
    node_mode = db.Column(db.String(50), nullable=False)  # master/client
    status = db.Column(db.String(50), nullable=False, default="active")  # active/inactive/disconnected
    last_heartbeat = db.Column(db.DateTime(), nullable=True)
    capabilities = db.Column(db.Text(), nullable=True)  # JSON: cpu_cores, memory_mb, disk_space_gb, gpu_available
    sync_entities = db.Column(db.Text(), nullable=True)  # JSON array
    registered_at = db.Column(db.DateTime(), default=lambda: datetime.now())
    last_sync_time = db.Column(db.DateTime(), nullable=True)
    model_name = db.Column(db.String(100))
    vram_total = db.Column(db.Integer)  # MB
    vram_free = db.Column(db.Integer)  # MB
    specialties = db.Column(db.Text(), default="[]")  # JSON array
    current_load = db.Column(db.Float, default=0.0)  # 0.0-1.0

    def __repr__(self):
        return f"<InterconnectorNode {self.node_id}: {self.node_name}>"

    def to_dict(self):
        import json
        return {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "host": self.host,
            "port": self.port,
            "node_mode": self.node_mode,
            "status": self.status,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "capabilities": json.loads(self.capabilities) if self.capabilities else {},
            "sync_entities": json.loads(self.sync_entities) if self.sync_entities else [],
            "registered_at": self.registered_at.isoformat() if self.registered_at else None,
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "model_name": self.model_name,
            "vram_total": self.vram_total,
            "vram_free": self.vram_free,
            "specialties": json.loads(self.specialties) if self.specialties else [],
            "current_load": self.current_load,
        }


class InterconnectorSyncHistory(db.Model):
    """Track synchronization history."""
    __tablename__ = "interconnector_sync_history"
    id = db.Column(db.Integer, primary_key=True)
    node_id = db.Column(db.String(36), db.ForeignKey("interconnector_nodes.node_id", ondelete="CASCADE"), nullable=False)
    sync_direction = db.Column(db.String(50), nullable=False)  # pull/push/bidirectional
    entities_synced = db.Column(db.Text(), nullable=True)  # JSON array
    items_processed = db.Column(db.Integer(), nullable=False, default=0)
    items_created = db.Column(db.Integer(), nullable=False, default=0)
    items_updated = db.Column(db.Integer(), nullable=False, default=0)
    conflicts_resolved = db.Column(db.Integer(), nullable=False, default=0)
    sync_duration_ms = db.Column(db.Integer(), nullable=True)
    status = db.Column(db.String(50), nullable=False, default="success")  # success/failed/partial
    error_message = db.Column(db.Text(), nullable=True)
    sync_timestamp = db.Column(db.DateTime(), default=lambda: datetime.now())

    node = db.relationship("InterconnectorNode", backref="sync_history")

    def __repr__(self):
        return f"<InterconnectorSyncHistory {self.id}: {self.sync_direction} @ {self.sync_timestamp}>"

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "node_id": self.node_id,
            "sync_direction": self.sync_direction,
            "entities_synced": json.loads(self.entities_synced) if self.entities_synced else [],
            "items_processed": self.items_processed,
            "items_created": self.items_created,
            "items_updated": self.items_updated,
            "conflicts_resolved": self.conflicts_resolved,
            "sync_duration_ms": self.sync_duration_ms,
            "status": self.status,
            "error_message": self.error_message,
            "sync_timestamp": self.sync_timestamp.isoformat() if self.sync_timestamp else None,
        }


class InterconnectorConflict(db.Model):
    """Track synchronization conflicts."""
    __tablename__ = "interconnector_conflicts"
    id = db.Column(db.Integer, primary_key=True)
    node_id = db.Column(db.String(36), db.ForeignKey("interconnector_nodes.node_id", ondelete="CASCADE"), nullable=False)
    entity_type = db.Column(db.String(100), nullable=False)  # clients, projects, etc.
    entity_id = db.Column(db.String(255), nullable=False)  # String to handle different ID types
    local_data = db.Column(db.Text(), nullable=True)  # JSON
    remote_data = db.Column(db.Text(), nullable=True)  # JSON
    conflict_fields = db.Column(db.Text(), nullable=True)  # JSON array of conflicting field names
    resolution_strategy = db.Column(db.String(50), nullable=True)  # last_write_wins, manual, etc.
    resolved = db.Column(db.Boolean(), nullable=False, default=False)
    resolved_at = db.Column(db.DateTime(), nullable=True)
    resolved_by = db.Column(db.String(255), nullable=True)  # node_id that resolved it
    created_at = db.Column(db.DateTime(), default=lambda: datetime.now())

    node = db.relationship("InterconnectorNode", backref="conflicts")

    def __repr__(self):
        return f"<InterconnectorConflict {self.id}: {self.entity_type}#{self.entity_id}>"

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "node_id": self.node_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "local_data": json.loads(self.local_data) if self.local_data else None,
            "remote_data": json.loads(self.remote_data) if self.remote_data else None,
            "conflict_fields": json.loads(self.conflict_fields) if self.conflict_fields else [],
            "resolution_strategy": self.resolution_strategy,
            "resolved": self.resolved,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolved_by": self.resolved_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class InterconnectorPendingChange(db.Model):
    """Queue pending changes when offline."""
    __tablename__ = "interconnector_pending_changes"
    id = db.Column(db.Integer, primary_key=True)
    change_type = db.Column(db.String(50), nullable=False)  # create/update/delete
    entity_type = db.Column(db.String(100), nullable=False)
    entity_id = db.Column(db.String(255), nullable=False)
    entity_data = db.Column(db.Text(), nullable=True)  # JSON
    queued_at = db.Column(db.DateTime(), default=lambda: datetime.now())
    retry_count = db.Column(db.Integer(), nullable=False, default=0)
    last_retry_at = db.Column(db.DateTime(), nullable=True)
    status = db.Column(db.String(50), nullable=False, default="pending")  # pending/processing/failed/success

    def __repr__(self):
        return f"<InterconnectorPendingChange {self.id}: {self.change_type} {self.entity_type}#{self.entity_id}>"

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "change_type": self.change_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_data": json.loads(self.entity_data) if self.entity_data else None,
            "queued_at": self.queued_at.isoformat() if self.queued_at else None,
            "retry_count": self.retry_count,
            "last_retry_at": self.last_retry_at.isoformat() if self.last_retry_at else None,
            "status": self.status,
        }


# --- Interconnector Sync Profiles ---
class InterconnectorSyncProfile(db.Model):
    """Predefined sync configuration profiles."""
    __tablename__ = "interconnector_sync_profiles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True, index=True)
    description = db.Column(db.Text(), nullable=True)
    profile_type = db.Column(db.String(50))  # quick/full/custom/minimal/code_only/data_only
    entity_config = db.Column(db.Text())  # JSON: entity filters
    file_config = db.Column(db.Text())  # JSON: file filters
    is_default = db.Column(db.Boolean(), default=False)
    created_at = db.Column(db.DateTime(), default=lambda: datetime.now())
    updated_at = db.Column(db.DateTime(), onupdate=lambda: datetime.now())

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "profile_type": self.profile_type,
            "entity_config": json.loads(self.entity_config) if self.entity_config else {},
            "file_config": json.loads(self.file_config) if self.file_config else {},
            "is_default": self.is_default,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# --- Interconnector Broadcast Models ---
class InterconnectorBroadcast(db.Model):
    """Track broadcast push operations from master."""
    __tablename__ = "interconnector_broadcasts"

    id = db.Column(db.String(36), primary_key=True)  # UUID
    sync_type = db.Column(db.String(20), nullable=False)  # entities/files/both
    entities = db.Column(db.Text())  # JSON array
    file_paths = db.Column(db.Text())  # JSON array
    require_approval = db.Column(db.Boolean(), default=True)
    priority = db.Column(db.String(20), default="normal")
    status = db.Column(db.String(50), default="pending")
    initiated_at = db.Column(db.DateTime(), default=lambda: datetime.now())
    scheduled_for = db.Column(db.DateTime(), nullable=True)
    completed_at = db.Column(db.DateTime(), nullable=True)
    total_clients = db.Column(db.Integer(), default=0)
    successful_count = db.Column(db.Integer(), default=0)
    failed_count = db.Column(db.Integer(), default=0)
    pending_count = db.Column(db.Integer(), default=0)

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "sync_type": self.sync_type,
            "entities": json.loads(self.entities) if self.entities else [],
            "file_paths": json.loads(self.file_paths) if self.file_paths else [],
            "require_approval": self.require_approval,
            "priority": self.priority,
            "status": self.status,
            "initiated_at": self.initiated_at.isoformat() if self.initiated_at else None,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_clients": self.total_clients,
            "successful_count": self.successful_count,
            "failed_count": self.failed_count,
            "pending_count": self.pending_count,
        }


class InterconnectorBroadcastTarget(db.Model):
    """Track individual client targets in a broadcast."""
    __tablename__ = "interconnector_broadcast_targets"

    id = db.Column(db.Integer, primary_key=True)
    broadcast_id = db.Column(
        db.String(36),
        db.ForeignKey("interconnector_broadcasts.id", ondelete="CASCADE"),
    )
    node_id = db.Column(
        db.String(36),
        db.ForeignKey("interconnector_nodes.node_id", ondelete="CASCADE"),
    )
    status = db.Column(db.String(50), default="pending")
    started_at = db.Column(db.DateTime(), nullable=True)
    completed_at = db.Column(db.DateTime(), nullable=True)
    items_pushed = db.Column(db.Integer(), default=0)
    error_message = db.Column(db.Text(), nullable=True)
    retry_count = db.Column(db.Integer(), default=0)
    approval_status = db.Column(db.String(50), default="pending")  # pending/approved/declined

    def to_dict(self):
        return {
            "id": self.id,
            "broadcast_id": self.broadcast_id,
            "node_id": self.node_id,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "items_pushed": self.items_pushed,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "approval_status": self.approval_status,
        }


# --- Interconnector Approvals ---
class InterconnectorPendingApproval(db.Model):
    """Track pending approval requests for file/entity sync."""
    __tablename__ = "interconnector_pending_approvals"

    id = db.Column(db.Integer, primary_key=True)
    push_id = db.Column(db.String(36), nullable=False)  # From broadcast/manual push
    source_node = db.Column(db.String(36), nullable=False)  # Master node ID
    sync_type = db.Column(db.String(20), nullable=False)  # files/entities/both
    files_data = db.Column(db.Text())  # JSON: list of file changes
    entities_data = db.Column(db.Text())  # JSON: entity changes
    received_at = db.Column(db.DateTime(), default=lambda: datetime.now())
    reviewed_at = db.Column(db.DateTime(), nullable=True)
    status = db.Column(db.String(50), default="pending")  # pending/approved/declined/partial
    decision_reason = db.Column(db.Text(), nullable=True)
    approved_files = db.Column(db.Text(), nullable=True)  # JSON: partial approval
    approved_entities = db.Column(db.Text(), nullable=True)  # JSON: partial approval
    auto_applied = db.Column(db.Boolean(), default=False)

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "push_id": self.push_id,
            "source_node": self.source_node,
            "sync_type": self.sync_type,
            "files_data": json.loads(self.files_data) if self.files_data else [],
            "entities_data": json.loads(self.entities_data) if self.entities_data else {},
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "status": self.status,
            "decision_reason": self.decision_reason,
            "approved_files": json.loads(self.approved_files) if self.approved_files else [],
            "approved_entities": json.loads(self.approved_entities) if self.approved_entities else {},
            "auto_applied": self.auto_applied,
        }


# --- Uncle Claude Family Architecture Models ---
class InterconnectorLearning(db.Model):
    __tablename__ = "interconnector_learnings"

    id = db.Column(db.Integer, primary_key=True)
    source_node_id = db.Column(db.String(36), nullable=False)
    timestamp = db.Column(db.DateTime(), default=lambda: datetime.now(), nullable=False)
    learning_type = db.Column(db.String(50), nullable=False)  # bug_fix, optimization, pattern, model_insight, security
    description = db.Column(db.Text(), nullable=False)
    code_diff = db.Column(db.Text())
    confidence = db.Column(db.Float, default=0.5)
    model_used = db.Column(db.String(100))
    applied_by = db.Column(db.Text(), default="[]")  # JSON array of node_ids
    uncle_reviewed = db.Column(db.Boolean(), default=False)
    uncle_feedback = db.Column(db.Text())

    def to_dict(self):
        return {
            "id": self.id,
            "source_node_id": self.source_node_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "learning_type": self.learning_type,
            "description": self.description,
            "code_diff": self.code_diff,
            "confidence": self.confidence,
            "model_used": self.model_used,
            "applied_by": json.loads(self.applied_by) if self.applied_by else [],
            "uncle_reviewed": self.uncle_reviewed,
            "uncle_feedback": self.uncle_feedback,
        }


class SelfImprovementRun(db.Model):
    __tablename__ = "self_improvement_runs"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime(), default=lambda: datetime.now(), nullable=False)
    node_id = db.Column(db.String(36))
    trigger = db.Column(db.String(50), nullable=False)  # scheduled, reactive, directed, family_learning
    status = db.Column(db.String(50), default="running")  # running, success, failed, blocked_by_guardian
    test_results_before = db.Column(db.Text())  # JSON
    test_results_after = db.Column(db.Text())  # JSON
    changes_made = db.Column(db.Text(), default="[]")  # JSON array of {file, diff}
    uncle_reviewed = db.Column(db.Boolean(), default=False)
    uncle_feedback = db.Column(db.Text())
    learning_id = db.Column(db.Integer, db.ForeignKey("interconnector_learnings.id", name="fk_sir_learning_id", ondelete="SET NULL"), nullable=True)
    error_message = db.Column(db.Text())
    duration_seconds = db.Column(db.Float)

    learning = db.relationship("InterconnectorLearning", backref="improvement_runs", lazy="select")

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "node_id": self.node_id,
            "trigger": self.trigger,
            "status": self.status,
            "test_results_before": json.loads(self.test_results_before) if self.test_results_before else None,
            "test_results_after": json.loads(self.test_results_after) if self.test_results_after else None,
            "changes_made": json.loads(self.changes_made) if self.changes_made else [],
            "uncle_reviewed": self.uncle_reviewed,
            "uncle_feedback": self.uncle_feedback,
            "learning_id": self.learning_id,
            "error_message": self.error_message,
            "duration_seconds": self.duration_seconds,
        }


class PendingFix(db.Model):
    """Staged code fixes awaiting review before application."""
    __tablename__ = "pending_fixes"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("self_improvement_runs.id", name="fk_pendingfix_run_id", ondelete="CASCADE"), nullable=True)
    file_path = db.Column(db.String(1024), nullable=False)
    original_content = db.Column(db.Text())  # old_text passed to edit_code
    proposed_new_content = db.Column(db.Text())  # new_text passed to edit_code
    proposed_diff = db.Column(db.Text(), nullable=False)  # unified diff for display
    fix_description = db.Column(db.Text())
    severity = db.Column(db.String(20), default="medium")  # critical, high, medium, low
    status = db.Column(db.String(20), default="proposed")  # proposed, triaged, approved, applied, rejected
    reviewed_by = db.Column(db.String(50))  # uncle_claude, user, or null
    review_notes = db.Column(db.Text())
    created_at = db.Column(db.DateTime(), default=lambda: datetime.now())
    reviewed_at = db.Column(db.DateTime())
    applied_at = db.Column(db.DateTime())

    run = db.relationship("SelfImprovementRun", backref="pending_fixes", lazy="select")

    def to_dict(self):
        return {
            "id": self.id,
            "run_id": self.run_id,
            "file_path": self.file_path,
            "proposed_diff": self.proposed_diff,
            "fix_description": self.fix_description,
            "severity": self.severity,
            "status": self.status,
            "reviewed_by": self.reviewed_by,
            "review_notes": self.review_notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
        }


# --- WordPress Integration Models ---
class WordPressSite(db.Model):
    """Track registered WordPress sites for content pull/push operations."""
    __tablename__ = "wordpress_sites"
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(2048), nullable=False, unique=True)
    site_name = db.Column(db.String(255), nullable=True)
    username = db.Column(db.String(255), nullable=True)  # Optional, only for direct WordPress REST API
    api_key = db.Column(db.Text, nullable=False)  # LLAMANATOR2 API key or WordPress Application Password
    connection_type = db.Column(db.String(50), default="llamanator", nullable=False)  # "llamanator" or "wordpress"
    client_id = db.Column(
        db.Integer,
        db.ForeignKey("clients.id", name="fk_wp_site_client_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("projects.id", name="fk_wp_site_project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    website_id = db.Column(
        db.Integer,
        db.ForeignKey("websites.id", name="fk_wp_site_website_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    pull_settings = db.Column(db.Text, nullable=True)  # JSON: what to pull, filters
    push_settings = db.Column(db.Text, nullable=True)  # JSON: what to push, auto-push settings
    status = db.Column(db.String(50), default="active", nullable=False, index=True)  # active/inactive/error
    last_pull_at = db.Column(db.DateTime, nullable=True)
    last_push_at = db.Column(db.DateTime, nullable=True)
    last_test_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )
    
    # Relationships
    client_ref = db.relationship("Client", backref=db.backref("wordpress_sites", lazy="dynamic"))
    project_ref = db.relationship("Project", backref=db.backref("wordpress_sites", lazy="dynamic"))
    website_ref = db.relationship("Website", backref=db.backref("wordpress_sites", lazy="dynamic"))
    pages = db.relationship("WordPressPage", back_populates="wordpress_site", lazy="dynamic", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<WordPressSite {self.id}: {self.url}>"
    
    def to_dict(self):
        pull_settings = {}
        push_settings = {}
        if self.pull_settings:
            try:
                pull_settings = json.loads(self.pull_settings)
            except (json.JSONDecodeError, TypeError):
                pull_settings = {}
        if self.push_settings:
            try:
                push_settings = json.loads(self.push_settings)
            except (json.JSONDecodeError, TypeError):
                push_settings = {}
        
        return {
            "id": self.id,
            "url": self.url,
            "site_name": self.site_name,
            "username": self.username if self.username else None,  # Convert empty string to None for API
            "has_api_key": bool(self.api_key),  # Don't expose actual key
            "connection_type": self.connection_type,
            "client_id": self.client_id,
            "client": {"id": self.client_ref.id, "name": self.client_ref.name} if self.client_ref else None,
            "project_id": self.project_id,
            "project": {"id": self.project_ref.id, "name": self.project_ref.name} if self.project_ref else None,
            "website_id": self.website_id,
            "website": {"id": self.website_ref.id, "url": self.website_ref.url} if self.website_ref else None,
            "pull_settings": pull_settings,
            "push_settings": push_settings,
            "status": self.status,
            "last_pull_at": self.last_pull_at.isoformat() if self.last_pull_at else None,
            "last_push_at": self.last_push_at.isoformat() if self.last_push_at else None,
            "last_test_at": self.last_test_at.isoformat() if self.last_test_at else None,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "page_count": self.pages.count() if hasattr(self, "pages") else 0,
        }


class WordPressPage(db.Model):
    """Store pulled WordPress content for processing and improvement."""
    __tablename__ = "wordpress_pages"
    id = db.Column(db.Integer, primary_key=True)
    wordpress_site_id = db.Column(
        db.Integer,
        db.ForeignKey("wordpress_sites.id", name="fk_wp_page_site_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wordpress_post_id = db.Column(db.Integer, nullable=False, index=True)  # Original WP post ID
    post_type = db.Column(db.String(50), nullable=False, default="post", index=True)  # post/page/custom
    title = db.Column(db.Text, nullable=False)
    content = db.Column(db.Text, nullable=True)  # Full content
    excerpt = db.Column(db.Text, nullable=True)
    slug = db.Column(db.String(500), nullable=True, index=True)
    status = db.Column(db.String(50), nullable=False, default="publish", index=True)
    date = db.Column(db.DateTime, nullable=True)
    modified = db.Column(db.DateTime, nullable=True)
    author_id = db.Column(db.Integer, nullable=True)
    author_name = db.Column(db.String(255), nullable=True)
    categories = db.Column(db.Text, nullable=True)  # JSON array
    tags = db.Column(db.Text, nullable=True)  # JSON array
    featured_image_url = db.Column(db.String(2048), nullable=True)
    featured_image_id = db.Column(db.Integer, nullable=True)
    meta_data = db.Column(db.Text, nullable=True)  # JSON: SEO metadata, custom fields
    sitemap_priority = db.Column(db.Float, nullable=True)
    sitemap_changefreq = db.Column(db.String(50), nullable=True)
    
    # SEO Metadata Fields
    seo_title = db.Column(db.Text, nullable=True)
    seo_description = db.Column(db.Text, nullable=True)
    focus_keywords = db.Column(db.Text, nullable=True)  # JSON array
    robots_meta = db.Column(db.Text, nullable=True)  # JSON array
    canonical_url = db.Column(db.String(2048), nullable=True)
    schema_markup = db.Column(db.Text, nullable=True)  # JSON array of schema objects
    seo_plugin = db.Column(db.String(50), nullable=True)  # 'seo_concept' | 'rank_math' | null

    # SEO Scores
    seo_score = db.Column(db.Integer, nullable=True)  # 0-100
    page_score = db.Column(db.Integer, nullable=True)  # 0-100
    seo_score_breakdown = db.Column(db.Text, nullable=True)  # JSON: {title: score, description: score, etc.}

    # Analytics Data (GSC + GA)
    analytics_data = db.Column(db.Text, nullable=True)  # JSON: {clicks, impressions, position, ctr, keywords, pageviews, visitors}

    # PageSpeed Scores
    pagespeed_score_mobile = db.Column(db.Integer, nullable=True)  # 0-100
    pagespeed_score_desktop = db.Column(db.Integer, nullable=True)  # 0-100
    pagespeed_data = db.Column(db.Text, nullable=True)  # JSON: Full PageSpeed API response

    # Image SEO Data
    image_seo_data = db.Column(db.Text, nullable=True)  # JSON: Array of image objects with alt/title/missing flags

    # Historical Tracking
    seo_score_history = db.Column(db.Text, nullable=True)  # JSON: [{date: str, score: int}, ...]
    analytics_synced_at = db.Column(db.DateTime, nullable=True)
    pagespeed_synced_at = db.Column(db.DateTime, nullable=True)
    
    # Pull status
    pull_status = db.Column(db.String(50), default="pending", nullable=False, index=True)  # pending/pulled/error
    pulled_at = db.Column(db.DateTime, nullable=True)
    original_content_hash = db.Column(db.String(64), nullable=True)  # SHA256 hash for change detection
    
    # Processing status
    process_status = db.Column(db.String(50), default="pending", nullable=False, index=True)  # pending/processing/completed/reviewed/approved/rejected
    improved_title = db.Column(db.Text, nullable=True)
    improved_content = db.Column(db.Text, nullable=True)
    improved_excerpt = db.Column(db.Text, nullable=True)
    improved_meta_description = db.Column(db.Text, nullable=True)
    improved_meta_title = db.Column(db.Text, nullable=True)
    improved_schema = db.Column(db.Text, nullable=True)  # JSON schema markup
    improvement_summary = db.Column(db.Text, nullable=True)  # What was changed and why
    processed_at = db.Column(db.DateTime, nullable=True)
    
    # Review status
    review_status = db.Column(db.String(50), nullable=True, index=True)  # pending/approved/rejected/edited
    reviewed_by = db.Column(db.String(255), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_notes = db.Column(db.Text, nullable=True)
    
    # Push status
    push_status = db.Column(db.String(50), nullable=True, index=True)  # pending/pushing/completed/error
    pushed_at = db.Column(db.DateTime, nullable=True)
    push_error = db.Column(db.Text, nullable=True)
    wordpress_response = db.Column(db.Text, nullable=True)  # JSON response from WordPress API
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )
    
    # Relationships
    wordpress_site = db.relationship("WordPressSite", back_populates="pages")
    
    # Indexes for performance
    __table_args__ = (
        Index("ix_wp_page_site_post", "wordpress_site_id", "wordpress_post_id"),
        Index("ix_wp_page_process_status", "process_status", "pull_status"),
        Index("ix_wp_page_review_status", "review_status", "process_status"),
        Index("ix_wp_page_seo_score", "seo_score"),
        Index("ix_wp_page_seo_plugin", "seo_plugin"),
        Index("ix_wp_page_analytics_synced", "analytics_synced_at"),
    )
    
    def __repr__(self):
        return f"<WordPressPage {self.id}: {self.title[:50]}>"
    
    def to_dict(self):
        categories = []
        tags = []
        meta_data = {}
        
        if self.categories:
            try:
                categories = json.loads(self.categories)
            except (json.JSONDecodeError, TypeError):
                categories = []
        if self.tags:
            try:
                tags = json.loads(self.tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        if self.meta_data:
            try:
                meta_data = json.loads(self.meta_data)
            except (json.JSONDecodeError, TypeError):
                meta_data = {}
        
        # Parse SEO fields
        focus_keywords = []
        if self.focus_keywords:
            try:
                focus_keywords = json.loads(self.focus_keywords)
            except (json.JSONDecodeError, TypeError):
                pass
        
        robots_meta = []
        if self.robots_meta:
            try:
                robots_meta = json.loads(self.robots_meta)
            except (json.JSONDecodeError, TypeError):
                pass
        
        schema_markup = []
        if self.schema_markup:
            try:
                schema_markup = json.loads(self.schema_markup)
            except (json.JSONDecodeError, TypeError):
                pass
        
        analytics_data = {}
        if self.analytics_data:
            try:
                analytics_data = json.loads(self.analytics_data)
            except (json.JSONDecodeError, TypeError):
                pass
        
        pagespeed_data = {}
        if self.pagespeed_data:
            try:
                pagespeed_data = json.loads(self.pagespeed_data)
            except (json.JSONDecodeError, TypeError):
                pass
        
        image_seo_data = []
        if self.image_seo_data:
            try:
                image_seo_data = json.loads(self.image_seo_data)
            except (json.JSONDecodeError, TypeError):
                pass
        
        seo_score_history = []
        if self.seo_score_history:
            try:
                seo_score_history = json.loads(self.seo_score_history)
            except (json.JSONDecodeError, TypeError):
                pass
        
        seo_score_breakdown = {}
        if self.seo_score_breakdown:
            try:
                seo_score_breakdown = json.loads(self.seo_score_breakdown)
            except (json.JSONDecodeError, TypeError):
                pass
        
        improved_schema = {}
        if self.improved_schema:
            try:
                improved_schema = json.loads(self.improved_schema)
            except (json.JSONDecodeError, TypeError):
                improved_schema = {}
        
        wordpress_response = {}
        if self.wordpress_response:
            try:
                wordpress_response = json.loads(self.wordpress_response)
            except (json.JSONDecodeError, TypeError):
                wordpress_response = {}
        
        return {
            "id": self.id,
            "wordpress_site_id": self.wordpress_site_id,
            "wordpress_post_id": self.wordpress_post_id,
            "post_type": self.post_type,
            "title": self.title,
            "content": self.content,
            "excerpt": self.excerpt,
            "slug": self.slug,
            "status": self.status,
            "date": self.date.isoformat() if self.date else None,
            "modified": self.modified.isoformat() if self.modified else None,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "categories": categories,
            "tags": tags,
            "featured_image_url": self.featured_image_url,
            "featured_image_id": self.featured_image_id,
            "meta_data": meta_data,
            "sitemap_priority": self.sitemap_priority,
            "sitemap_changefreq": self.sitemap_changefreq,
            "pull_status": self.pull_status,
            "pulled_at": self.pulled_at.isoformat() if self.pulled_at else None,
            "original_content_hash": self.original_content_hash,
            "process_status": self.process_status,
            "improved_title": self.improved_title,
            "improved_content": self.improved_content,
            "improved_excerpt": self.improved_excerpt,
            "improved_meta_description": self.improved_meta_description,
            "improved_meta_title": self.improved_meta_title,
            "improved_schema": improved_schema,
            "improvement_summary": self.improvement_summary,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "review_status": self.review_status,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "review_notes": self.review_notes,
            "push_status": self.push_status,
            "pushed_at": self.pushed_at.isoformat() if self.pushed_at else None,
            "push_error": self.push_error,
            "wordpress_response": wordpress_response,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "seo": {
                "title": self.seo_title,
                "description": self.seo_description,
                "focus_keywords": focus_keywords,
                "robots_meta": robots_meta,
                "canonical_url": self.canonical_url,
                "schema_markup": schema_markup,
                "seo_plugin": self.seo_plugin,
                "seo_score": self.seo_score,
                "page_score": self.page_score,
                "seo_score_breakdown": seo_score_breakdown,
                "analytics_data": analytics_data,
                "pagespeed_score_mobile": self.pagespeed_score_mobile,
                "pagespeed_score_desktop": self.pagespeed_score_desktop,
                "pagespeed_data": pagespeed_data,
                "image_seo_data": image_seo_data,
                "seo_score_history": seo_score_history,
                "analytics_synced_at": self.analytics_synced_at.isoformat() if self.analytics_synced_at else None,
                "pagespeed_synced_at": self.pagespeed_synced_at.isoformat() if self.pagespeed_synced_at else None
            }
        }


# ---------------------------------------------------------------------------
# RAG Autoresearch Models
# ---------------------------------------------------------------------------

class ExperimentRun(db.Model):
    """Tracks individual autoresearch experiment results."""
    __tablename__ = "experiment_runs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_tag = db.Column(db.String(100), nullable=True, index=True)
    phase = db.Column(db.Integer, nullable=False, default=1)
    parameter_changed = db.Column(db.String(200), nullable=False)
    old_value = db.Column(db.String(500), nullable=True)
    new_value = db.Column(db.String(500), nullable=False)
    hypothesis = db.Column(db.Text, nullable=True)
    composite_score = db.Column(db.Float, nullable=False)
    baseline_score = db.Column(db.Float, nullable=True)
    delta = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="discard")  # keep, discard, crash
    eval_details = db.Column(db.JSON, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    node_id = db.Column(db.String(36), nullable=True)  # nullable for standalone instances
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id, "run_tag": self.run_tag, "phase": self.phase,
            "parameter_changed": self.parameter_changed,
            "old_value": self.old_value, "new_value": self.new_value,
            "hypothesis": self.hypothesis,
            "composite_score": self.composite_score,
            "baseline_score": self.baseline_score, "delta": self.delta,
            "status": self.status, "eval_details": self.eval_details,
            "duration_seconds": self.duration_seconds,
            "node_id": self.node_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class EvalPair(db.Model):
    """Auto-generated Q&A pairs for RAG evaluation."""
    __tablename__ = "eval_pairs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    eval_generation_id = db.Column(db.String(50), nullable=True, index=True)
    question = db.Column(db.Text, nullable=False)
    expected_answer = db.Column(db.Text, nullable=False)
    source_doc_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=True)
    source_chunk_hash = db.Column(db.String(64), nullable=True)
    corpus_type = db.Column(db.String(20), nullable=True)  # code, knowledge, client
    quality_score = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    source_document = db.relationship("Document", backref="eval_pairs", lazy=True)

    def to_dict(self):
        return {
            "id": self.id, "eval_generation_id": self.eval_generation_id,
            "question": self.question, "expected_answer": self.expected_answer,
            "source_doc_id": self.source_doc_id,
            "source_chunk_hash": self.source_chunk_hash,
            "corpus_type": self.corpus_type,
            "quality_score": self.quality_score,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ResearchConfig(db.Model):
    """Snapshot of RAG configuration with its eval score."""
    __tablename__ = "research_configs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    params = db.Column(db.JSON, nullable=False)
    composite_score = db.Column(db.Float, nullable=True)
    is_active = db.Column(db.Boolean, default=False, index=True)
    promoted_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(30), nullable=True)  # local, family_broadcast, uncle_directive
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "params": self.params,
            "composite_score": self.composite_score,
            "is_active": self.is_active,
            "promoted_at": self.promoted_at.isoformat() if self.promoted_at else None,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# --- Demonstration / Learning Models ---

class Demonstration(db.Model):
    __tablename__ = "demonstrations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=True, index=True)
    description = db.Column(db.Text, nullable=False)
    context_url = db.Column(db.String(1024), nullable=True)
    context_app = db.Column(db.String(255), nullable=True)
    tags = db.Column(db.JSON, nullable=True, default=list)
    autonomy_level = db.Column(
        db.String(20),
        nullable=False,
        default="guided",
        index=True,
    )
    success_count = db.Column(db.Integer, nullable=False, default=0)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    parent_demonstration_id = db.Column(
        db.Integer,
        db.ForeignKey("demonstrations.id", name="fk_demo_parent_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_complete = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now())

    steps = db.relationship(
        "DemoStep",
        backref="demonstration",
        lazy="dynamic",
        order_by="DemoStep.step_index",
        cascade="all, delete-orphan",
    )
    attempts = db.relationship(
        "Demonstration",
        backref=db.backref("parent", remote_side="Demonstration.id"),
        lazy="dynamic",
    )

    __table_args__ = (
        CheckConstraint(
            autonomy_level.in_(["guided", "supervised", "autonomous"]),
            name="ck_demo_autonomy_level",
        ),
    )

    def __repr__(self):
        return f"<Demonstration {self.id}: {self.name or self.description[:40]}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "context_url": self.context_url,
            "context_app": self.context_app,
            "tags": self.tags or [],
            "autonomy_level": self.autonomy_level,
            "success_count": self.success_count,
            "attempt_count": self.attempt_count,
            "parent_demonstration_id": self.parent_demonstration_id,
            "is_complete": self.is_complete,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DemoStep(db.Model):
    __tablename__ = "demo_steps"

    id = db.Column(db.Integer, primary_key=True)
    demonstration_id = db.Column(
        db.Integer,
        db.ForeignKey("demonstrations.id", name="fk_demostep_demo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_index = db.Column(db.Integer, nullable=False)
    action_type = db.Column(db.String(20), nullable=False)
    target_description = db.Column(db.Text, nullable=False)
    element_context = db.Column(db.Text, nullable=True)
    coordinates_x = db.Column(db.Integer, nullable=True)
    coordinates_y = db.Column(db.Integer, nullable=True)
    text = db.Column(db.Text, nullable=True)
    keys = db.Column(db.String(255), nullable=True)
    intent = db.Column(db.Text, nullable=True)
    precondition = db.Column(db.Text, nullable=True)
    variability = db.Column(db.Boolean, nullable=False, default=False)
    wait_condition = db.Column(db.Text, nullable=True)
    is_mistake = db.Column(db.Boolean, nullable=False, default=False)
    screenshot_before = db.Column(db.String(1024), nullable=True)
    screenshot_after = db.Column(db.String(1024), nullable=True)

    __table_args__ = (
        CheckConstraint(
            action_type.in_(["click", "type", "hotkey", "scroll"]),
            name="ck_demostep_action_type",
        ),
        db.UniqueConstraint("demonstration_id", "step_index", name="uq_demo_step_index"),
    )

    def __repr__(self):
        return f"<DemoStep {self.demonstration_id}:{self.step_index} {self.action_type}>"

    def to_dict(self):
        return {
            "id": self.id,
            "step_index": self.step_index,
            "action_type": self.action_type,
            "target_description": self.target_description,
            "element_context": self.element_context,
            "coordinates": [self.coordinates_x, self.coordinates_y] if self.coordinates_x is not None else None,
            "text": self.text,
            "keys": self.keys,
            "intent": self.intent,
            "precondition": self.precondition,
            "variability": self.variability,
            "wait_condition": self.wait_condition,
            "is_mistake": self.is_mistake,
            "screenshot_before": self.screenshot_before,
            "screenshot_after": self.screenshot_after,
        }


# --- Helper Functions ---
def get_model_by_name(name: str) -> Model | None:
    if not db:
        return None
    return db.session.query(Model).filter_by(name=name).first()


def get_active_model_name() -> str:
    if not db or not Setting:
        return "default_model_name"
    setting = db.session.get(Setting, "active_model_name")
    return setting.value if setting else "default_model_name"


class ToolFeedback(db.Model):
    """Store user feedback on tool calls for future performance analysis and routing improvements."""
    __tablename__ = "tool_feedback"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36), index=True)
    tool_name = db.Column(db.String(100), nullable=False, index=True)
    task = db.Column(db.Text, nullable=True) # The user's original task or tool parameter
    positive = db.Column(db.Boolean, nullable=False)
    steps = db.Column(db.Integer, nullable=True) # For agent tasks: number of steps taken
    time_seconds = db.Column(db.Float, nullable=True) # Execution time
    model = db.Column(db.String(100), nullable=True) # Model name used for the tool
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "task": self.task,
            "positive": self.positive,
            "steps": self.steps,
            "time_seconds": self.time_seconds,
            "model": self.model,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

class AgentMemory(db.Model):
    """Long-term memory for the agent to store user preferences, facts, and instructions."""
    __tablename__ = "agent_memories"
    id = db.Column(db.String(36), primary_key=True)
    content = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(50), default="manual", index=True)  # manual, chat, cli, auto
    session_id = db.Column(db.String(36), nullable=True, index=True)
    tags = db.Column(db.Text, nullable=True)  # JSON array
    type = db.Column(db.String(50), default="note", index=True)  # note, fact, instruction, snippet
    importance = db.Column(db.Float, default=0.5, index=True)  # 0.0 to 1.0
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now())

    def to_dict(self):
        tags = []
        if self.tags:
            try:
                tags = json.loads(self.tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "session_id": self.session_id,
            "tags": tags,
            "type": self.type,
            "importance": self.importance,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
