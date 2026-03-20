"""Shared fixtures and PYTHONPATH setup for vision pipeline tests."""
import sys
import os

# Ensure service package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
