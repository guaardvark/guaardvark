-- Migration: Add Folders table and update Documents table for file manager
-- Date: 2025-10-01
-- Description: Creates folders table for hierarchical file organization
--              and adds folder_id, client_id to documents table

-- Create folders table
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(255) NOT NULL,
    path VARCHAR(1024) NOT NULL UNIQUE,
    parent_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE
);

-- Create index on parent_id for faster lookups
CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id);

-- Add new columns to documents table
ALTER TABLE documents ADD COLUMN folder_id INTEGER;
ALTER TABLE documents ADD COLUMN client_id INTEGER;

-- Create foreign key constraints (SQLite doesn't support ALTER TABLE ADD CONSTRAINT)
-- These will be enforced at the application level via SQLAlchemy

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_documents_folder_id ON documents(folder_id);
CREATE INDEX IF NOT EXISTS idx_documents_client_id ON documents(client_id);

-- Migration complete
SELECT 'Migration completed: Folders table created and documents table updated' AS status;
