"""llx CLI package.

The version is sourced from the repo-root VERSION file (single source of truth),
resolved relative to this file so it works regardless of the install location.
"""
import os as _os

_version_file = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
    "VERSION",
)
try:
    with open(_version_file, encoding="utf-8") as _f:
        __version__ = _f.read().strip()
except OSError:
    __version__ = "2.5.4"
