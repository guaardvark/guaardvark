import os as _os

_version_file = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "VERSION"
)
with open(_version_file) as _f:
    __version__ = _f.read().strip()
