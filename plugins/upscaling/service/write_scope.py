"""Where this service may write a result.

Every output path arrives in a request body. Results belong in the install's
uploads and output folders; a path that really lands anywhere else (``..`` and
symlinks followed) is refused before a job is made. The watch folder's own
output is set by the operator's config, not by a request, and is not checked
here.
"""
import os
from typing import List, Optional

# Kept in step with ``_GENERATOR_OUTPUT_BASES`` in
# backend/services/document_path_resolver.py: the folders a source file can sit
# in when its result is written beside it.
_INSTALL_OUTPUT_BASES = ("plugins/comfyui/ComfyUI/output",)


class OutsideWriteScope(ValueError):
    """An output path that is not inside a folder results may be written to."""


def _install_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.environ.get("GUAARDVARK_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _setting(env_var: str, default_relative: str) -> str:
    """A data folder the way backend/config.py reads it."""
    path = os.environ.get(env_var) or default_relative
    return path if os.path.isabs(path) else os.path.join(_install_root(), path)


def write_roots() -> List[str]:
    """The real folders a result may be written under."""
    storage = _setting("GUAARDVARK_STORAGE_DIR", "data")
    folders = [
        _setting("GUAARDVARK_UPLOAD_DIR", "data/uploads"),
        _setting("GUAARDVARK_OUTPUT_DIR", "data/outputs"),
        os.path.join(storage, "outputs"),
    ]
    folders += [os.path.join(_install_root(), base) for base in _INSTALL_OUTPUT_BASES]
    # A folder that is, or holds, the install or its data folder is not an
    # output folder: keys and settings live there.
    held = [os.path.realpath(_install_root()), os.path.realpath(storage)]
    roots = []
    for folder in folders:
        real = os.path.realpath(folder)
        if real not in roots and not any(h == real or h.startswith(real.rstrip(os.sep) + os.sep) for h in held):
            roots.append(real)
    return roots


def confined_output(path: Optional[str]) -> str:
    """The real location of ``path`` when it is inside :func:`write_roots`.

    Raises :class:`OutsideWriteScope` otherwise. The returned path is the one to
    write to, so a link swapped in afterwards is not followed.
    """
    if not path or "\x00" in path:
        raise OutsideWriteScope("An output path is required")
    real = os.path.realpath(path)
    for root in write_roots():
        if real.startswith(root.rstrip(os.sep) + os.sep):
            return real
    raise OutsideWriteScope("Results can only be written to the uploads and output folders")
