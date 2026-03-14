-- Fix missing primary keys and create missing tables
-- Must run atomically to avoid lock issues

BEGIN;

-- Phase 1: Add missing primary keys to existing tables
DO $$
DECLARE
    tbls TEXT[] := ARRAY[
        'clients','device_profiles','documents','eval_pairs','experiment_runs',
        'folders','generations','images','interconnector_broadcast_targets',
        'interconnector_broadcasts','interconnector_conflicts','interconnector_learnings',
        'interconnector_pending_approvals','interconnector_pending_changes',
        'interconnector_sync_history','interconnector_sync_profiles',
        'llm_messages','llm_sessions','models','pages','projects',
        'research_configs','rules','self_improvement_runs'
    ];
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY tbls LOOP
        -- Check if PK already exists
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE table_name = tbl AND constraint_type = 'PRIMARY KEY'
        ) THEN
            BEGIN
                EXECUTE format('ALTER TABLE %I ADD PRIMARY KEY (id)', tbl);
                RAISE NOTICE 'Added PK to %', tbl;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'Skipped % (PK): %', tbl, SQLERRM;
            END;
        END IF;
    END LOOP;
END $$;

-- Phase 2: Create missing tables

-- system_settings
CREATE TABLE IF NOT EXISTS system_settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP WITHOUT TIME ZONE
);

-- websites (depends on projects, clients)
CREATE TABLE IF NOT EXISTS websites (
    id SERIAL PRIMARY KEY,
    url VARCHAR(2048) NOT NULL UNIQUE,
    sitemap VARCHAR(2048),
    competitor_url VARCHAR(2048),
    status VARCHAR(50) DEFAULT 'pending',
    last_crawled TIMESTAMP WITHOUT TIME ZONE,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_websites_status ON websites(status);
CREATE INDEX IF NOT EXISTS ix_websites_project_id ON websites(project_id);
CREATE INDEX IF NOT EXISTS ix_websites_client_id ON websites(client_id);

-- tasks (depends on projects, clients, websites)
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'pending',
    priority INTEGER DEFAULT 2,
    due_date TIMESTAMP WITHOUT TIME ZONE,
    type VARCHAR(100),
    job_id VARCHAR(36),
    output_filename VARCHAR(255),
    prompt_text TEXT,
    model_name VARCHAR(120),
    workflow_config TEXT,
    client_name VARCHAR(255),
    target_website VARCHAR(2048),
    competitor_url VARCHAR(2048),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    website_id INTEGER REFERENCES websites(id) ON DELETE SET NULL,
    schedule_type VARCHAR(50) DEFAULT 'immediate',
    cron_expression VARCHAR(100),
    next_run_at TIMESTAMP WITHOUT TIME ZONE,
    last_run_at TIMESTAMP WITHOUT TIME ZONE,
    parent_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    retry_delay INTEGER DEFAULT 60,
    error_message TEXT,
    task_handler VARCHAR(100),
    handler_config JSONB,
    progress INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS ix_tasks_type ON tasks(type);
CREATE INDEX IF NOT EXISTS ix_tasks_job_id ON tasks(job_id);
CREATE INDEX IF NOT EXISTS ix_tasks_created_at ON tasks(created_at);
CREATE INDEX IF NOT EXISTS ix_tasks_project_id ON tasks(project_id);
CREATE INDEX IF NOT EXISTS ix_tasks_client_id ON tasks(client_id);
CREATE INDEX IF NOT EXISTS ix_tasks_website_id ON tasks(website_id);
CREATE INDEX IF NOT EXISTS ix_tasks_schedule_type ON tasks(schedule_type);
CREATE INDEX IF NOT EXISTS ix_tasks_next_run_at ON tasks(next_run_at);
CREATE INDEX IF NOT EXISTS ix_tasks_parent_task_id ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS ix_tasks_task_handler ON tasks(task_handler);

-- training_datasets
CREATE TABLE IF NOT EXISTS training_datasets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    path VARCHAR(1024),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- training_jobs (depends on training_datasets, device_profiles)
CREATE TABLE IF NOT EXISTS training_jobs (
    id SERIAL PRIMARY KEY,
    job_id VARCHAR(64) UNIQUE,
    name VARCHAR(255),
    base_model VARCHAR(255),
    output_model_name VARCHAR(255),
    dataset_id INTEGER REFERENCES training_datasets(id),
    config_json TEXT,
    device_profile_id INTEGER REFERENCES device_profiles(id),
    pipeline_stage VARCHAR(50),
    status VARCHAR(50) DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    current_step INTEGER DEFAULT 0,
    total_steps INTEGER,
    error_message TEXT,
    metrics_json TEXT,
    lora_path VARCHAR(1024),
    gguf_path VARCHAR(1024),
    ollama_model_name VARCHAR(255),
    quantization_level VARCHAR(50),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITHOUT TIME ZONE,
    completed_at TIMESTAMP WITHOUT TIME ZONE,
    celery_task_id VARCHAR(64),
    checkpoint_path VARCHAR(1024)
);
CREATE INDEX IF NOT EXISTS ix_training_jobs_job_id ON training_jobs(job_id);

-- wordpress_sites (depends on clients, projects, websites)
CREATE TABLE IF NOT EXISTS wordpress_sites (
    id SERIAL PRIMARY KEY,
    url VARCHAR(2048) NOT NULL UNIQUE,
    site_name VARCHAR(255),
    username VARCHAR(255),
    api_key TEXT NOT NULL DEFAULT '',
    connection_type VARCHAR(50) NOT NULL DEFAULT 'llamanator',
    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    website_id INTEGER REFERENCES websites(id) ON DELETE SET NULL,
    pull_settings TEXT,
    push_settings TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    last_pull_at TIMESTAMP WITHOUT TIME ZONE,
    last_push_at TIMESTAMP WITHOUT TIME ZONE,
    last_test_at TIMESTAMP WITHOUT TIME ZONE,
    error_message TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_wordpress_sites_client_id ON wordpress_sites(client_id);
CREATE INDEX IF NOT EXISTS ix_wordpress_sites_project_id ON wordpress_sites(project_id);
CREATE INDEX IF NOT EXISTS ix_wordpress_sites_website_id ON wordpress_sites(website_id);
CREATE INDEX IF NOT EXISTS ix_wordpress_sites_status ON wordpress_sites(status);

-- wordpress_pages (depends on wordpress_sites)
CREATE TABLE IF NOT EXISTS wordpress_pages (
    id SERIAL PRIMARY KEY,
    wordpress_site_id INTEGER NOT NULL REFERENCES wordpress_sites(id) ON DELETE CASCADE,
    wordpress_post_id INTEGER NOT NULL,
    post_type VARCHAR(50) NOT NULL DEFAULT 'post',
    title TEXT NOT NULL,
    content TEXT,
    excerpt TEXT,
    slug VARCHAR(500),
    status VARCHAR(50) NOT NULL DEFAULT 'publish',
    date TIMESTAMP WITHOUT TIME ZONE,
    modified TIMESTAMP WITHOUT TIME ZONE,
    author_id INTEGER,
    author_name VARCHAR(255),
    categories TEXT,
    tags TEXT,
    featured_image_url VARCHAR(2048),
    featured_image_id INTEGER,
    meta_data TEXT,
    sitemap_priority FLOAT,
    sitemap_changefreq VARCHAR(50),
    pull_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    process_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    push_status VARCHAR(50),
    improved_content TEXT,
    improvement_notes TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_wordpress_pages_site_id ON wordpress_pages(wordpress_site_id);
CREATE INDEX IF NOT EXISTS ix_wordpress_pages_post_type ON wordpress_pages(post_type);
CREATE INDEX IF NOT EXISTS ix_wordpress_pages_pull_status ON wordpress_pages(pull_status);
CREATE INDEX IF NOT EXISTS ix_wordpress_pages_process_status ON wordpress_pages(process_status);
CREATE INDEX IF NOT EXISTS ix_wordpress_pages_push_status ON wordpress_pages(push_status);

COMMIT;
