
import sys
import os
import logging

# Ensure backend path is in sys.path
sys.path.append(os.getcwd())

from backend.app import create_app
from backend.api.files_api import browse_folder
from flask import request

# Configure logging
logging.basicConfig(level=logging.INFO)

app = create_app()

with app.app_context():
    with app.test_request_context('/api/files/browse?path=/'):
        try:
            print("Calling browse_folder...")
            response = browse_folder()
            print("Response:", response)
        except Exception as e:
            print("Caught exception:")
            import traceback
            traceback.print_exc()
