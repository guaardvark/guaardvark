"""Guaardvark v2.4.1 — Consolidated PostgreSQL schema.

Single-file schema definition. On fresh installs db.create_all() in
create_app() creates the tables from models.py; this migration ensures
Alembic tracking is in place and back-fills any missing indexes.

Revision ID: v2_4_1_pg
Revises: (none — base)
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa

revision = "v2_4_1_pg"
down_revision = None
branch_labels = None
depends_on = None


def _ensure_indexes(inspector):
    """Idempotently create every index the schema requires."""
    existing = set()
    for tbl in inspector.get_table_names():
        for idx in inspector.get_indexes(tbl):
            existing.add(idx["name"])

    required = [
        # clients
        ("ix_clients_name", "clients", ["name"], False),
        ("ix_clients_email", "clients", ["email"], True),
        # projects
        ("ix_projects_client_id", "projects", ["client_id"], False),
        # rules
        ("ix_rules_name", "rules", ["name"], False),
        ("ix_rules_level", "rules", ["level"], False),
        ("ix_rules_type", "rules", ["type"], False),
        ("ix_rules_command_label", "rules", ["command_label"], True),
        ("ix_rules_reference_id", "rules", ["reference_id"], False),
        ("ix_rules_project_id", "rules", ["project_id"], False),
        ("ix_rule_level_name", "rules", ["level", "name"], False),
        # websites
        ("ix_websites_status", "websites", ["status"], False),
        ("ix_websites_project_id", "websites", ["project_id"], False),
        ("ix_websites_client_id", "websites", ["client_id"], False),
        # tasks
        ("ix_tasks_status", "tasks", ["status"], False),
        ("ix_tasks_type", "tasks", ["type"], False),
        ("ix_tasks_job_id", "tasks", ["job_id"], False),
        ("ix_tasks_created_at", "tasks", ["created_at"], False),
        ("ix_tasks_project_id", "tasks", ["project_id"], False),
        ("ix_tasks_client_id", "tasks", ["client_id"], False),
        ("ix_tasks_website_id", "tasks", ["website_id"], False),
        ("ix_tasks_schedule_type", "tasks", ["schedule_type"], False),
        ("ix_tasks_next_run_at", "tasks", ["next_run_at"], False),
        ("ix_tasks_parent_task_id", "tasks", ["parent_task_id"], False),
        ("ix_tasks_task_handler", "tasks", ["task_handler"], False),
        # folders
        ("ix_folders_parent_id", "folders", ["parent_id"], False),
        ("ix_folders_is_repository", "folders", ["is_repository"], False),
        ("ix_folder_parent_name", "folders", ["parent_id", "name"], False),
        # documents
        ("ix_documents_index_status", "documents", ["index_status"], False),
        ("ix_documents_folder_id", "documents", ["folder_id"], False),
        ("ix_documents_client_id", "documents", ["client_id"], False),
        ("ix_documents_project_id", "documents", ["project_id"], False),
        ("ix_documents_website_id", "documents", ["website_id"], False),
        ("ix_documents_indexing_job_id", "documents", ["indexing_job_id"], False),
        ("ix_documents_uploaded_at", "documents", ["uploaded_at"], False),
        ("ix_documents_source_document_id", "documents", ["source_document_id"], False),
        ("ix_doc_folder_filename", "documents", ["folder_id", "filename"], False),
        ("ix_doc_folder_uploaded", "documents", ["folder_id", "uploaded_at"], False),
        ("ix_doc_folder_size", "documents", ["folder_id", "size"], False),
        # llm_sessions
        ("ix_llm_sessions_user", "llm_sessions", ["user"], False),
        ("ix_llm_sessions_project_id", "llm_sessions", ["project_id"], False),
        # llm_messages
        ("ix_llm_messages_session_id", "llm_messages", ["session_id"], False),
        ("ix_llm_messages_timestamp", "llm_messages", ["timestamp"], False),
        # training_jobs
        ("ix_training_jobs_job_id", "training_jobs", ["job_id"], True),
        # generations
        ("ix_generations_site_key", "generations", ["site_key"], False),
        ("ix_generations_client", "generations", ["client"], False),
        # pages
        ("ix_pages_generation_id", "pages", ["generation_id"], False),
        ("ix_pages_slug", "pages", ["slug"], False),
        ("ix_pages_status", "pages", ["status"], False),
        ("ix_page_generation_created", "pages", ["generation_id", "created_at"], False),
        ("ix_page_slug_generation", "pages", ["slug", "generation_id"], False),
        # images
        ("ix_images_hash", "images", ["hash"], True),
        ("ix_image_hash_filename", "images", ["hash", "file_name"], False),
        ("ix_image_created_used", "images", ["created_at", "last_used_at"], False),
        # interconnector
        (
            "ix_interconnector_sync_profiles_name",
            "interconnector_sync_profiles",
            ["name"],
            False,
        ),
        # wordpress_sites
        ("ix_wordpress_sites_client_id", "wordpress_sites", ["client_id"], False),
        ("ix_wordpress_sites_project_id", "wordpress_sites", ["project_id"], False),
        ("ix_wordpress_sites_website_id", "wordpress_sites", ["website_id"], False),
        ("ix_wordpress_sites_status", "wordpress_sites", ["status"], False),
        # wordpress_pages
        (
            "ix_wordpress_pages_wordpress_site_id",
            "wordpress_pages",
            ["wordpress_site_id"],
            False,
        ),
        (
            "ix_wordpress_pages_wordpress_post_id",
            "wordpress_pages",
            ["wordpress_post_id"],
            False,
        ),
        ("ix_wordpress_pages_post_type", "wordpress_pages", ["post_type"], False),
        ("ix_wordpress_pages_slug", "wordpress_pages", ["slug"], False),
        ("ix_wordpress_pages_status", "wordpress_pages", ["status"], False),
        ("ix_wordpress_pages_pull_status", "wordpress_pages", ["pull_status"], False),
        (
            "ix_wordpress_pages_process_status",
            "wordpress_pages",
            ["process_status"],
            False,
        ),
        (
            "ix_wordpress_pages_review_status",
            "wordpress_pages",
            ["review_status"],
            False,
        ),
        ("ix_wordpress_pages_push_status", "wordpress_pages", ["push_status"], False),
        (
            "ix_wp_page_site_post",
            "wordpress_pages",
            ["wordpress_site_id", "wordpress_post_id"],
            False,
        ),
        (
            "ix_wp_page_process_status",
            "wordpress_pages",
            ["process_status", "pull_status"],
            False,
        ),
        (
            "ix_wp_page_review_status",
            "wordpress_pages",
            ["review_status", "process_status"],
            False,
        ),
        ("ix_wp_page_seo_score", "wordpress_pages", ["seo_score"], False),
        ("ix_wp_page_seo_plugin", "wordpress_pages", ["seo_plugin"], False),
        (
            "ix_wp_page_analytics_synced",
            "wordpress_pages",
            ["analytics_synced_at"],
            False,
        ),
    ]

    for name, table, cols, unique in required:
        if name not in existing:
            try:
                op.create_index(name, table, cols, unique=unique)
            except Exception:
                pass  # Table may not exist yet in partial upgrade


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # If core tables already exist (db.create_all ran first), just ensure indexes.
    if "clients" in existing_tables:
        _ensure_indexes(inspector)
        return

    # --- settings ---
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )

    # --- system_settings ---
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )

    # --- models ---
    op.create_table(
        "models",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("version", sa.String(80), nullable=True),
        sa.Column("quantized", sa.Boolean(), default=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # --- clients ---
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("logo_path", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("contact_url", sa.String(500), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("primary_service", sa.String(255), nullable=True),
        sa.Column("secondary_service", sa.String(255), nullable=True),
        sa.Column("brand_tone", sa.String(50), nullable=True),
        sa.Column("business_hours", sa.Text(), nullable=True),
        sa.Column("social_links", sa.Text(), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("target_audience", sa.Text(), nullable=True),
        sa.Column("unique_selling_points", sa.Text(), nullable=True),
        sa.Column("competitor_urls", sa.Text(), nullable=True),
        sa.Column("brand_voice_examples", sa.Text(), nullable=True),
        sa.Column("keywords", sa.Text(), nullable=True),
        sa.Column("content_goals", sa.Text(), nullable=True),
        sa.Column("regulatory_constraints", sa.Text(), nullable=True),
        sa.Column("geographic_coverage", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # --- projects ---
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("project_type", sa.String(100), nullable=True),
        sa.Column("target_keywords", sa.Text(), nullable=True),
        sa.Column("content_strategy", sa.Text(), nullable=True),
        sa.Column("deliverables", sa.Text(), nullable=True),
        sa.Column("seo_strategy", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name="fk_project_client_id"
        ),
    )

    # --- rules ---
    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("level", sa.String(50), nullable=False),
        sa.Column("type", sa.String(50), nullable=True),
        sa.Column("command_label", sa.String(100), nullable=True),
        sa.Column("reference_id", sa.String(255), nullable=True),
        sa.Column("rule_text", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("output_schema_name", sa.String(100), nullable=True),
        sa.Column("target_models", sa.Text(), nullable=True, default='["__ALL__"]'),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_rule_project_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "level IN ('SYSTEM','PROJECT','CLIENT','USER_GLOBAL','USER_SPECIFIC','PROMPT','LEARNED')",
            name="ck_rule_level_updated",
        ),
        sa.CheckConstraint(
            "type IN ('PROMPT_TEMPLATE','QA_TEMPLATE','COMMAND_RULE','FILTER_RULE','FORMATTING_RULE','SYSTEM_PROMPT','OTHER')",
            name="ck_rule_type",
        ),
        sa.CheckConstraint(
            "LENGTH(rule_text) <= 50000", name="ck_rule_text_length_extended"
        ),
    )

    # --- project_rules_association ---
    op.create_table(
        "project_rules_association",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "rule_id"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_assoc_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["rules.id"], name="fk_assoc_rule_id", ondelete="CASCADE"
        ),
    )

    # --- websites ---
    op.create_table(
        "websites",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("sitemap", sa.String(2048), nullable=True),
        sa.Column("competitor_url", sa.String(2048), nullable=True),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("last_crawled", sa.DateTime(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_website_project_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_website_client_id",
            ondelete="SET NULL",
        ),
    )

    # --- tasks ---
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("priority", sa.Integer(), default=5),
        sa.Column("due_date", sa.DateTime(), nullable=True),
        sa.Column("type", sa.String(100), nullable=True),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("output_filename", sa.String(255), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=True),
        sa.Column("workflow_config", sa.Text(), nullable=True),
        sa.Column("client_name", sa.String(255), nullable=True),
        sa.Column("target_website", sa.String(2048), nullable=True),
        sa.Column("competitor_url", sa.String(2048), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("website_id", sa.Integer(), nullable=True),
        sa.Column("schedule_type", sa.String(50), nullable=True, default="immediate"),
        sa.Column("cron_expression", sa.String(100), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("parent_task_id", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True, default=0),
        sa.Column("max_retries", sa.Integer(), nullable=True, default=3),
        sa.Column("retry_delay", sa.Integer(), nullable=True, default=60),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("task_handler", sa.String(100), nullable=True),
        sa.Column("handler_config", sa.JSON(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=True, default=0),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_task_project_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name="fk_task_client_id", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["website_id"],
            ["websites.id"],
            name="fk_task_website_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_task_id"],
            ["tasks.id"],
            name="fk_task_parent_id",
            ondelete="SET NULL",
        ),
    )

    # --- folders ---
    op.create_table(
        "folders",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_repository", sa.Boolean(), nullable=False, default=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("repo_metadata", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path"),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["folders.id"],
            name="fk_folder_parent_id",
            ondelete="CASCADE",
        ),
    )

    # --- documents ---
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("type", sa.String(50), nullable=True),
        sa.Column("index_status", sa.String(50), default="INDEXING"),
        sa.Column("indexed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("is_code_file", sa.Boolean(), default=False),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("file_metadata", sa.Text(), nullable=True),
        sa.Column("content_category", sa.String(100), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=True, default=5.0),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("rag_context", sa.Text(), nullable=True),
        sa.Column("folder_id", sa.Integer(), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("website_id", sa.Integer(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("indexing_job_id", sa.String(255), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path"),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["folders.id"],
            name="fk_document_folder_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_document_client_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_document_project_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["website_id"],
            ["websites.id"],
            name="fk_document_website_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["documents.id"],
            name="fk_document_source_id",
            ondelete="SET NULL",
        ),
    )

    # --- llm_sessions ---
    op.create_table(
        "llm_sessions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user", sa.String(80), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_llmsession_project_id",
            ondelete="SET NULL",
        ),
    )

    # --- llm_messages ---
    op.create_table(
        "llm_messages",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("extra_data", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["llm_sessions.id"], name="fk_llmmessage_session_id"
        ),
        sa.CheckConstraint(
            "role IN ('user','assistant','system')", name="ck_message_role"
        ),
    )

    # --- training_datasets ---
    op.create_table(
        "training_datasets",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("path", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # --- device_profiles ---
    op.create_table(
        "device_profiles",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("device_type", sa.String(50), nullable=True),
        sa.Column("gpu_vram_mb", sa.Integer(), nullable=True),
        sa.Column("system_ram_mb", sa.Integer(), nullable=True),
        sa.Column("max_batch_size", sa.Integer(), default=2),
        sa.Column("max_seq_length", sa.Integer(), default=2048),
        sa.Column("supports_4bit", sa.Boolean(), default=True),
        sa.Column("requires_cpu_offload", sa.Boolean(), default=False),
        sa.Column("is_default", sa.Boolean(), default=False),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("compute_capability", sa.String(10), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # --- training_jobs ---
    op.create_table(
        "training_jobs",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("job_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("base_model", sa.String(255), nullable=True),
        sa.Column("output_model_name", sa.String(255), nullable=True),
        sa.Column("dataset_id", sa.Integer(), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("device_profile_id", sa.Integer(), nullable=True),
        sa.Column("pipeline_stage", sa.String(50), nullable=True),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("progress", sa.Integer(), default=0),
        sa.Column("current_step", sa.Integer(), default=0),
        sa.Column("total_steps", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.Column("lora_path", sa.String(1024), nullable=True),
        sa.Column("gguf_path", sa.String(1024), nullable=True),
        sa.Column("ollama_model_name", sa.String(255), nullable=True),
        sa.Column("quantization_level", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("celery_task_id", sa.String(64), nullable=True),
        sa.Column("checkpoint_path", sa.String(1024), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("is_resumable", sa.Boolean(), default=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["dataset_id"], ["training_datasets.id"]),
        sa.ForeignKeyConstraint(["device_profile_id"], ["device_profiles.id"]),
    )

    # --- generations ---
    op.create_table(
        "generations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("site_key", sa.String(255), nullable=True),
        sa.Column("delimiter", sa.String(10), nullable=False, default=","),
        sa.Column("structured_html", sa.Boolean(), nullable=False, default=False),
        sa.Column("brand_tone", sa.String(50), nullable=True),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("client", sa.String(255), nullable=True),
        sa.Column("project", sa.String(255), nullable=True),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("competitor", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- pages ---
    op.create_table(
        "pages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("generation_id", sa.String(36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("slug", sa.String(500), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, default="pending"),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["generations.id"], ondelete="CASCADE"
        ),
    )

    # --- images ---
    op.create_table(
        "images",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- interconnector_nodes ---
    op.create_table(
        "interconnector_nodes",
        sa.Column("node_id", sa.String(36), nullable=False),
        sa.Column("node_name", sa.String(255), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("node_mode", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, default="active"),
        sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
        sa.Column("capabilities", sa.Text(), nullable=True),
        sa.Column("sync_entities", sa.Text(), nullable=True),
        sa.Column("registered_at", sa.DateTime(), nullable=True),
        sa.Column("last_sync_time", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("node_id"),
    )

    # --- interconnector_sync_history ---
    op.create_table(
        "interconnector_sync_history",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("node_id", sa.String(36), nullable=False),
        sa.Column("sync_direction", sa.String(50), nullable=False),
        sa.Column("entities_synced", sa.Text(), nullable=True),
        sa.Column("items_processed", sa.Integer(), nullable=False, default=0),
        sa.Column("items_created", sa.Integer(), nullable=False, default=0),
        sa.Column("items_updated", sa.Integer(), nullable=False, default=0),
        sa.Column("conflicts_resolved", sa.Integer(), nullable=False, default=0),
        sa.Column("sync_duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, default="success"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sync_timestamp", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["node_id"], ["interconnector_nodes.node_id"], ondelete="CASCADE"
        ),
    )

    # --- interconnector_conflicts ---
    op.create_table(
        "interconnector_conflicts",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("node_id", sa.String(36), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("local_data", sa.Text(), nullable=True),
        sa.Column("remote_data", sa.Text(), nullable=True),
        sa.Column("conflict_fields", sa.Text(), nullable=True),
        sa.Column("resolution_strategy", sa.String(50), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, default=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["node_id"], ["interconnector_nodes.node_id"], ondelete="CASCADE"
        ),
    )

    # --- interconnector_pending_changes ---
    op.create_table(
        "interconnector_pending_changes",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("change_type", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("entity_data", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, default=0),
        sa.Column("last_retry_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, default="pending"),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- interconnector_sync_profiles ---
    op.create_table(
        "interconnector_sync_profiles",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("profile_type", sa.String(50), nullable=True),
        sa.Column("entity_config", sa.Text(), nullable=True),
        sa.Column("file_config", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), default=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # --- interconnector_broadcasts ---
    op.create_table(
        "interconnector_broadcasts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("sync_type", sa.String(20), nullable=False),
        sa.Column("entities", sa.Text(), nullable=True),
        sa.Column("file_paths", sa.Text(), nullable=True),
        sa.Column("require_approval", sa.Boolean(), default=True),
        sa.Column("priority", sa.String(20), default="normal"),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("initiated_at", sa.DateTime(), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("total_clients", sa.Integer(), default=0),
        sa.Column("successful_count", sa.Integer(), default=0),
        sa.Column("failed_count", sa.Integer(), default=0),
        sa.Column("pending_count", sa.Integer(), default=0),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- interconnector_broadcast_targets ---
    op.create_table(
        "interconnector_broadcast_targets",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("broadcast_id", sa.String(36), nullable=True),
        sa.Column("node_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("items_pushed", sa.Integer(), default=0),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), default=0),
        sa.Column("approval_status", sa.String(50), default="pending"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["broadcast_id"], ["interconnector_broadcasts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["node_id"], ["interconnector_nodes.node_id"], ondelete="CASCADE"
        ),
    )

    # --- interconnector_pending_approvals ---
    op.create_table(
        "interconnector_pending_approvals",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("push_id", sa.String(36), nullable=False),
        sa.Column("source_node", sa.String(36), nullable=False),
        sa.Column("sync_type", sa.String(20), nullable=False),
        sa.Column("files_data", sa.Text(), nullable=True),
        sa.Column("entities_data", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("approved_files", sa.Text(), nullable=True),
        sa.Column("approved_entities", sa.Text(), nullable=True),
        sa.Column("auto_applied", sa.Boolean(), default=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- wordpress_sites ---
    op.create_table(
        "wordpress_sites",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("site_name", sa.String(255), nullable=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column(
            "connection_type", sa.String(50), default="llamanator", nullable=False
        ),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("website_id", sa.Integer(), nullable=True),
        sa.Column("pull_settings", sa.Text(), nullable=True),
        sa.Column("push_settings", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), default="active", nullable=False),
        sa.Column("last_pull_at", sa.DateTime(), nullable=True),
        sa.Column("last_push_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_wp_site_client_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_wp_site_project_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["website_id"],
            ["websites.id"],
            name="fk_wp_site_website_id",
            ondelete="SET NULL",
        ),
    )

    # --- wordpress_pages ---
    op.create_table(
        "wordpress_pages",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("wordpress_site_id", sa.Integer(), nullable=False),
        sa.Column("wordpress_post_id", sa.Integer(), nullable=False),
        sa.Column("post_type", sa.String(50), nullable=False, default="post"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("slug", sa.String(500), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, default="publish"),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("modified", sa.DateTime(), nullable=True),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("author_name", sa.String(255), nullable=True),
        sa.Column("categories", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("featured_image_url", sa.String(2048), nullable=True),
        sa.Column("featured_image_id", sa.Integer(), nullable=True),
        sa.Column("meta_data", sa.Text(), nullable=True),
        sa.Column("sitemap_priority", sa.Float(), nullable=True),
        sa.Column("sitemap_changefreq", sa.String(50), nullable=True),
        sa.Column("seo_title", sa.Text(), nullable=True),
        sa.Column("seo_description", sa.Text(), nullable=True),
        sa.Column("focus_keywords", sa.Text(), nullable=True),
        sa.Column("robots_meta", sa.Text(), nullable=True),
        sa.Column("canonical_url", sa.String(2048), nullable=True),
        sa.Column("schema_markup", sa.Text(), nullable=True),
        sa.Column("seo_plugin", sa.String(50), nullable=True),
        sa.Column("seo_score", sa.Integer(), nullable=True),
        sa.Column("page_score", sa.Integer(), nullable=True),
        sa.Column("seo_score_breakdown", sa.Text(), nullable=True),
        sa.Column("analytics_data", sa.Text(), nullable=True),
        sa.Column("pagespeed_score_mobile", sa.Integer(), nullable=True),
        sa.Column("pagespeed_score_desktop", sa.Integer(), nullable=True),
        sa.Column("pagespeed_data", sa.Text(), nullable=True),
        sa.Column("image_seo_data", sa.Text(), nullable=True),
        sa.Column("seo_score_history", sa.Text(), nullable=True),
        sa.Column("analytics_synced_at", sa.DateTime(), nullable=True),
        sa.Column("pagespeed_synced_at", sa.DateTime(), nullable=True),
        sa.Column("pull_status", sa.String(50), default="pending", nullable=False),
        sa.Column("pulled_at", sa.DateTime(), nullable=True),
        sa.Column("original_content_hash", sa.String(64), nullable=True),
        sa.Column("process_status", sa.String(50), default="pending", nullable=False),
        sa.Column("improved_title", sa.Text(), nullable=True),
        sa.Column("improved_content", sa.Text(), nullable=True),
        sa.Column("improved_excerpt", sa.Text(), nullable=True),
        sa.Column("improved_meta_description", sa.Text(), nullable=True),
        sa.Column("improved_meta_title", sa.Text(), nullable=True),
        sa.Column("improved_schema", sa.Text(), nullable=True),
        sa.Column("improvement_summary", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("review_status", sa.String(50), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("push_status", sa.String(50), nullable=True),
        sa.Column("pushed_at", sa.DateTime(), nullable=True),
        sa.Column("push_error", sa.Text(), nullable=True),
        sa.Column("wordpress_response", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["wordpress_site_id"],
            ["wordpress_sites.id"],
            name="fk_wp_page_site_id",
            ondelete="CASCADE",
        ),
    )

    # Create all indexes
    _ensure_indexes(sa.inspect(bind))


def downgrade():
    tables = [
        "wordpress_pages",
        "wordpress_sites",
        "interconnector_pending_approvals",
        "interconnector_broadcast_targets",
        "interconnector_broadcasts",
        "interconnector_sync_profiles",
        "interconnector_pending_changes",
        "interconnector_conflicts",
        "interconnector_sync_history",
        "interconnector_nodes",
        "images",
        "pages",
        "generations",
        "training_jobs",
        "device_profiles",
        "training_datasets",
        "llm_messages",
        "llm_sessions",
        "documents",
        "folders",
        "tasks",
        "project_rules_association",
        "websites",
        "rules",
        "projects",
        "clients",
        "models",
        "system_settings",
        "settings",
    ]
    for table in tables:
        op.drop_table(table)
