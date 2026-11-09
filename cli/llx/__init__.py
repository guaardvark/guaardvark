"""llx CLI package.

The version is the repo-root VERSION file (single source of truth) in a
checkout, and the installed distribution's version in a pip install, which has
no VERSION file beside the package.
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
    try:
        from importlib.metadata import version as _dist_version

        __version__ = _dist_version("guaardvark")
    except Exception:  # noqa: BLE001 - a missing version must not stop the CLI
        __version__ = "unknown"
