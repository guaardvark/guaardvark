"""Resolve Python and JavaScript import statements to files in one repository.

Shared by the Code Repository analysis (repository_analysis_service) and the
System Mapper. Pure functions over path strings: nothing here reads a file or
imports a heavy dependency, so the mapper can use them in any process.

Paths are POSIX, relative to the repository root. A "lookup" pair maps those
paths, and the dotted or slashed module names derived from them, to whatever
key the caller stores for the file (a document path, or the relative path
itself).
"""

from __future__ import annotations

import os
import posixpath
import re
from pathlib import PurePosixPath
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# Files whose folder can be imported by the folder's own name.
PACKAGE_INDEX_FILES = frozenset({"__init__.py", "index.js", "index.jsx", "index.ts", "index.tsx"})

JS_RESOLVE_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


def lookups(files: Iterable, rel_of: Callable, code_exts: Iterable[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Path and module lookup tables over ``files`` (each with .path, its key,
    and .filename), whose values are the files' keys. ``rel_of(file)`` is the
    file's path relative to the repository root; ``code_exts`` are the
    extensions (".py", ".jsx") that count as importable modules."""
    code_exts = frozenset(code_exts)
    path_lookup: Dict[str, str] = {}
    module_lookup: Dict[str, str] = {}

    for doc in files:
        rel_path = rel_of(doc)
        if not rel_path:
            continue

        rel_posix = posixpath.normpath(rel_path.replace("\\", "/").lstrip("/"))
        path_lookup[rel_posix] = doc.path

        ext = os.path.splitext(doc.filename)[1].lower()
        if ext not in code_exts:
            continue

        stem = rel_posix[: -len(ext)] if ext else rel_posix
        module_lookup[stem.replace("/", ".")] = doc.path
        module_lookup[stem] = doc.path

        if PurePosixPath(rel_posix).name in PACKAGE_INDEX_FILES:
            package_path = str(PurePosixPath(stem).parent)
            if package_path and package_path != ".":
                module_lookup[package_path.replace("/", ".")] = doc.path
                module_lookup[package_path] = doc.path

    return path_lookup, module_lookup


def python_import_target(rel_path: str, level: int, module: str) -> str:
    """The absolute dotted name a Python import refers to. ``level`` is the
    number of leading dots of a relative import, counted from the package of
    ``rel_path``."""
    if not level:
        return module or ""
    rel_no_ext = os.path.splitext(rel_path)[0]
    parts = [part for part in rel_no_ext.split("/") if part]
    package_parts = parts if parts[-1:] == ["__init__"] else parts[:-1]
    if package_parts[-1:] == ["__init__"]:
        package_parts = package_parts[:-1]
    keep_count = max(0, len(package_parts) - (level - 1))
    target_parts = package_parts[:keep_count]
    if module:
        target_parts.extend(module.split("."))
    return ".".join(part for part in target_parts if part)


def resolve_python_import(
    rel_path: str,
    level: int,
    module: str,
    module_lookup: Dict[str, str],
) -> Optional[str]:
    """Resolve a Python import target to a file key when it is in the repository.

    The longest dotted prefix that names a module wins, so ``a.b.func`` resolves
    to ``a/b.py`` (or ``a/b/__init__.py``)."""
    target = python_import_target(rel_path, level, module)
    if not target:
        return None

    pieces = target.split(".")
    for end in range(len(pieces), 0, -1):
        candidate = ".".join(pieces[:end])
        if candidate in module_lookup:
            return module_lookup[candidate]
    return None


def extract_js_imports(content: str) -> List[str]:
    """Return JS/TS module specifiers from single-line import/export/require statements."""
    patterns = (
        r"(?:import|export)\s+(?:[^'\"\n]+?\s+from\s+)?['\"]([^'\"]+)['\"]",
        r"require\(\s*['\"]([^'\"]+)['\"]\s*\)",
    )
    imports: List[str] = []
    for pattern in patterns:
        imports.extend(match.group(1) for match in re.finditer(pattern, content))
    return imports


def resolve_js_import(
    rel_path: str,
    specifier: str,
    path_lookup: Dict[str, str],
    module_lookup: Dict[str, str],
    aliases: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Resolve a JS/TS module specifier to a file key when it is in the repository.

    ``aliases`` maps a specifier prefix to a folder relative to the root, the
    way a bundler's resolve.alias does ({"@/": "frontend/src/"})."""
    candidates: List[str] = []

    for prefix, folder in (aliases or {}).items():
        if specifier.startswith(prefix):
            specifier = folder.rstrip("/") + "/" + specifier[len(prefix):]
            break
    else:
        prefix = None

    if specifier.startswith("."):
        parent = PurePosixPath(rel_path).parent
        base = posixpath.normpath(str(parent.joinpath(specifier)).replace("\\", "/"))
    else:
        base = specifier.strip("/")
        if prefix is None and base.replace("/", ".") in module_lookup:
            return module_lookup[base.replace("/", ".")]

    base = posixpath.normpath(str(PurePosixPath(base)))
    candidates.append(base)
    candidates.extend(f"{base}{ext}" for ext in JS_RESOLVE_EXTENSIONS)
    candidates.extend(f"{base}/index{ext}" for ext in JS_RESOLVE_EXTENSIONS)

    for candidate in candidates:
        normalized = posixpath.normpath(str(PurePosixPath(candidate)))
        if normalized in path_lookup:
            return path_lookup[normalized]
    return None
