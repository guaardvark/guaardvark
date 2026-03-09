-- schema.sql
-- Initialize the database schema

DROP TABLE IF EXISTS documents;
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding BLOB NOT NULL
);

DROP TABLE IF EXISTS active_model;
CREATE TABLE active_model (
    id INTEGER PRIMARY KEY,
    model_name TEXT NOT NULL
);

DROP TABLE IF EXISTS tasks;
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending', 'in-progress', 'completed', 'failed', 'cancelled')),
    priority TEXT NOT NULL CHECK(priority IN ('low', 'medium', 'high')),
    due_date DATETIME,
    type VARCHAR(100),
    job_id VARCHAR(36),
    output_filename VARCHAR(255),
    prompt_text TEXT,
    model_name VARCHAR(120),
    workflow_config TEXT,
    client_name VARCHAR(255),
    target_website VARCHAR(2048),
    competitor_url VARCHAR(2048),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    project_id INTEGER,
    client_id INTEGER,
    website_id INTEGER,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE SET NULL,
    FOREIGN KEY (website_id) REFERENCES websites(id) ON DELETE SET NULL
);

DROP TABLE IF EXISTS rules;
CREATE TABLE rules (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    level TEXT NOT NULL,
    reference_id INTEGER,
    rule_text TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DROP TABLE IF EXISTS chat_sessions;
CREATE TABLE chat_sessions (
    session_id TEXT PRIMARY KEY,
    history TEXT,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DROP TABLE IF EXISTS card_states;
CREATE TABLE card_states (
    card_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('open','minimized')),
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DROP TABLE IF EXISTS clients;
CREATE TABLE clients (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

DROP TABLE IF EXISTS projects;
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    client_id INTEGER,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

DROP TABLE IF EXISTS websites;
CREATE TABLE websites (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT,
    project_id INTEGER,
    client_id INTEGER,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

DROP TABLE IF EXISTS sitemaps;
CREATE TABLE sitemaps (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    website_id INTEGER,
    FOREIGN KEY (website_id) REFERENCES websites(id)
);

DROP TABLE IF EXISTS urls;
CREATE TABLE urls (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    sitemap_id INTEGER,
    FOREIGN KEY (sitemap_id) REFERENCES sitemaps(id)
);

