"""Keep request-supplied path pieces inside a directory the server chose.

Two calls cover every case::

    contained_path(root, *parts) -> str   # absolute, normalised, inside root
    contained(root, *parts) -> Path       # the same, as a Path

Both normalise with ``os.path.abspath`` and then check the prefix with
``str.startswith``, followed by a separator check so ``/data/out`` does not
accept ``/data/outside``. ``abspath`` is lexical on purpose: it removes every
``..`` a caller can send without following symlinks, so an output directory the
operator symlinked to another disk keeps working.

The ``abspath`` + ``startswith`` pair is also the shape static analysers
recognise as a traversal guard. ``Path.resolve()`` + ``relative_to()`` does the
same job at runtime but is not recognised, which is why this module avoids it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union

PathLike = Union[str, "os.PathLike[str]"]


class PathEscapesRoot(ValueError):
    """A requested path resolved outside the directory it had to stay in."""


def contained_path(root: PathLike, *parts: PathLike) -> str:
    """Join ``parts`` under ``root`` and return the absolute path, or raise.

    ``root`` itself is a valid result (no parts, or parts that normalise to
    ``.``). Anything that lands outside ``root`` raises :class:`PathEscapesRoot`,
    which is a ``ValueError`` so existing ``except ValueError`` handlers around
    the older ``relative_to`` idiom keep working.
    """
    root_abs = os.path.abspath(os.fspath(root))
    candidate = os.path.abspath(os.path.join(root_abs, *(os.fspath(p) for p in parts)))
    # A prefix match alone would let "/data/out" accept "/data/outside", so the
    # match must also end on a path boundary (or root is "/" and ends with one).
    if candidate.startswith(root_abs) and (
        candidate == root_abs
        or root_abs.endswith(os.sep)
        or candidate[len(root_abs)] == os.sep
    ):
        return candidate
    raise PathEscapesRoot(f"{candidate!r} is outside {root_abs!r}")


def contained(root: PathLike, *parts: PathLike) -> Path:
    """:func:`contained_path` returning a :class:`~pathlib.Path`."""
    return Path(contained_path(root, *parts))
