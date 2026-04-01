"""
CLI package for Guaardvark.
Version is read from single source of truth (VERSION file in project root).
"""
import os

# Calculate path to VERSION file (two levels up from this __init__.py)
_version_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
if os.path.exists(_version_file):
    with open(_version_file, "r") as f:
        __version__ = f.read().strip()
else:
    __version__ = "2.5.2"  # Fallback if VERSION file missing