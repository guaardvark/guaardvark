import os
import sys
from pathlib import Path
from flask import Flask
from backend.app import create_app
from backend.models import db, Folder, Document
from backend.config import UPLOAD_DIR, DATABASE_URL

# Create app context using the factory
app = create_app()

def check_system():
    print(f"Checking UPLOAD_DIR: {UPLOAD_DIR}")
    upload_path = Path(UPLOAD_DIR)
    if not upload_path.exists():
        print("UPLOAD_DIR does not exist!")
    else:
        print(f"UPLOAD_DIR exists. Writable: {os.access(UPLOAD_DIR, os.W_OK)}")
        try:
             print(f"Contents: {[x.name for x in upload_path.iterdir()]}")
        except Exception as e:
             print(f"Error listing directory: {e}")

    # Mask password in DATABASE_URL for display
    display_url = DATABASE_URL
    if '@' in DATABASE_URL:
        import re
        display_url = re.sub(r'://[^:]+:[^@]+@', '://***:***@', DATABASE_URL)
    print(f"\nChecking Database: {display_url}")

    with app.app_context():
        try:
            folder_count = Folder.query.count()
            doc_count = Document.query.count()
            print(f"Folders in DB: {folder_count}")
            print(f"Documents in DB: {doc_count}")

            folders = Folder.query.all()
            for f in folders:
                print(f"Folder: {f.name}, Path: {f.path}, Parent: {f.parent_id}")

        except Exception as e:
            print(f"Error querying database: {e}")

if __name__ == "__main__":
    check_system()
