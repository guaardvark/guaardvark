"""Root conftest: the bootstrap extension tests need.

backend/tests/conftest.py inserts the repo root on sys.path and sets the
skip flags for its own tree; tests under extensions/<id>/tests/ are outside
that tree and get the same here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PYTEST_SKIP_MIGRATION_CHECK", "1")
os.environ.setdefault("PYTEST_SKIP_LLAMA_CHECK", "1")
os.environ.setdefault("GUAARDVARK_MODE", "test")
