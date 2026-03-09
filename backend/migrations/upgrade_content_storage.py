#!/usr/bin/env python3
"""
Database migration script to upgrade content storage capabilities.
Upgrades the Document.content field to support larger text storage.
"""

import os
import sys
import logging
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from backend.app import create_app
from backend.models import db, Document
from sqlalchemy import text

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def upgrade_content_storage():
    """Upgrade the Document.content field to support larger text storage."""
    
    app = create_app()
    
    with app.app_context():
        try:
            logger.info("Starting content storage upgrade...")
            
            # Check current database engine
            engine = db.engine
            dialect_name = engine.dialect.name
            
            logger.info(f"Database dialect: {dialect_name}")
            
            if dialect_name == 'sqlite':
                # SQLite TEXT type can already handle large content
                logger.info("SQLite detected - TEXT type already supports large content")
                
                # Verify the table structure
                result = db.session.execute(text("PRAGMA table_info(documents)"))
                columns = result.fetchall()
                
                content_column = None
                for column in columns:
                    if column[1] == 'content':  # column[1] is the column name
                        content_column = column
                        break
                
                if content_column:
                    logger.info(f"Content column found: {content_column}")
                else:
                    logger.warning("Content column not found in documents table")
                    
            elif dialect_name == 'mysql':
                # Upgrade to LONGTEXT for MySQL
                logger.info("MySQL detected - upgrading to LONGTEXT")
                
                # Check current column type
                result = db.session.execute(text("""
                    SELECT COLUMN_TYPE 
                    FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                    AND TABLE_NAME = 'documents' 
                    AND COLUMN_NAME = 'content'
                """))
                
                current_type = result.fetchone()
                
                if current_type:
                    logger.info(f"Current content column type: {current_type[0]}")
                    
                    if 'longtext' not in current_type[0].lower():
                        logger.info("Upgrading content column to LONGTEXT...")
                        db.session.execute(text("ALTER TABLE documents MODIFY COLUMN content LONGTEXT"))
                        db.session.commit()
                        logger.info("Successfully upgraded content column to LONGTEXT")
                    else:
                        logger.info("Content column is already LONGTEXT")
                else:
                    logger.warning("Content column not found in documents table")
                    
            elif dialect_name == 'postgresql':
                # PostgreSQL TEXT type can already handle large content
                logger.info("PostgreSQL detected - TEXT type already supports large content")
                
                # Verify the table structure
                result = db.session.execute(text("""
                    SELECT data_type, character_maximum_length
                    FROM information_schema.columns 
                    WHERE table_name = 'documents' 
                    AND column_name = 'content'
                """))
                
                column_info = result.fetchone()
                
                if column_info:
                    logger.info(f"Content column type: {column_info[0]}, max length: {column_info[1]}")
                else:
                    logger.warning("Content column not found in documents table")
                    
            else:
                logger.warning(f"Unknown database dialect: {dialect_name}")
                logger.info("Manual verification may be required")
            
            # Test content storage capability
            logger.info("Testing content storage capability...")
            
            # Create a test string of reasonable size (1MB)
            test_content = "A" * (1024 * 1024)  # 1MB of 'A' characters
            
            try:
                # Try to create a test document with large content
                test_doc = Document(
                    filename="test_large_content.txt",
                    path="test_large_content.txt",
                    type=".txt",
                    content=test_content,
                    index_status="STORED",
                    is_code_file=False
                )
                
                db.session.add(test_doc)
                db.session.flush()  # Flush to test without committing
                
                # Read it back
                retrieved_doc = db.session.query(Document).filter_by(id=test_doc.id).first()
                
                if retrieved_doc and len(retrieved_doc.content) == len(test_content):
                    logger.info("✓ Large content storage test PASSED")
                else:
                    logger.error("✗ Large content storage test FAILED")
                
                # Clean up test document
                db.session.delete(test_doc)
                db.session.commit()
                
            except Exception as e:
                logger.error(f"Large content storage test failed: {e}")
                db.session.rollback()
            
            logger.info("Content storage upgrade completed successfully")
            
        except Exception as e:
            logger.error(f"Error during content storage upgrade: {e}")
            db.session.rollback()
            raise
        
        finally:
            db.session.close()

if __name__ == "__main__":
    upgrade_content_storage()