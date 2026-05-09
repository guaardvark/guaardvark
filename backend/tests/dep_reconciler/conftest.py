"""Shared fixtures for dep_reconciler tests."""
import sys
from pathlib import Path

# Make scripts/ importable from the test process.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
