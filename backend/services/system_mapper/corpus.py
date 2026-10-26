"""The files one map is built from, their facts, and how their imports resolve.

A Corpus is built once per map and handed to every analyzer, so the tree is
listed once and each file is read at most once (facts.file_facts caches the
rest across maps). It owns the rules for:

  * which files are surveyed (core.source_files: what git lists, minus the
    always-skipped folders) and which of them are tests;
  * a node id per module: the dotted name for Python
    ("backend.services.chat_turn"), the relative path for JavaScript
    ("frontend/src/pages/ChatPage.jsx");
  * which files a script or manifest starts (``entry_points``): a plugin's
    start.sh running ``uvicorn service.app:app`` or ``python3 -m
    plugins.x.service.app``, a .py path in a script or plugin.json;
  * where an import lands. Python imports resolve against the repository
    root and, for a file inside one, the folder that is its own import root:
    each plugins/<name>/ (whose code imports ``service.x`` and ``core.x``)
    and any folder holding its own packaging file (cli/ ships ``llx``).
    Relative imports resolve from the file's package. JavaScript resolves
    relative specifiers, index files, the bundler's ``@/`` alias for
    frontend/src, and import.meta.glob patterns.
"""
from __future__ import annotations

import fnmatch
import posixpath
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from backend.utils import import_resolution

from . import facts as facts_mod
from .core import source_files

CODE_SUFFIXES = facts_mod.PY_SUFFIXES + facts_mod.JS_SUFFIXES
PACKAGING_FILES = ("pyproject.toml", "setup.py", "setup.cfg", "MANIFEST.in")


class _Doc(NamedTuple):
    path: str
    filename: str


class Edge(NamedTuple):
    src: str      # relative path of the importing file
    dst: str      # relative path of the imported file
    kind: str     # "top", "lazy", "type"
    line: int


def module_name(rel: str) -> str:
    """backend/services/foo.py -> backend.services.foo (package __init__ -> package)."""
    parts = list(PurePosixPath(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def is_test_path(rel: str) -> bool:
    """Test code in either language: tests/ and __tests__/ folders, test_*.py,
    *_test.py, conftest.py, *.test.js(x), *.spec.js(x), frontend/src/test/."""
    parts = rel.split("/")
    name = parts[-1]
    if any(p in ("tests", "test", "__tests__", "__mocks__", "e2e") for p in parts[:-1]):
        return True
    if name.endswith(".py"):
        return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
    return ".test." in name or ".spec." in name


def node_id(rel: str) -> str:
    return module_name(rel) if rel.endswith(".py") else rel


@dataclass
class Corpus:
    root: Path
    files: list[str]                               # code files, relative POSIX paths, sorted
    facts: dict[str, dict] = field(default_factory=dict)
    path_lookup: dict[str, str] = field(default_factory=dict)
    module_lookup: dict[str, str] = field(default_factory=dict)
    import_roots: dict[str, dict[str, str]] = field(default_factory=dict)
    js_aliases: dict[str, str] = field(default_factory=dict)
    all_files: list[str] = field(default_factory=list)  # every surveyed file, any type
    _edges: list[Edge] | None = None
    _bindings: dict[str, dict] | None = None
    _entries: dict[str, list[str]] | None = None

    # -- construction --------------------------------------------------------
    @classmethod
    def build(cls, root: Path, extra_excludes: frozenset[str] = frozenset()) -> "Corpus":
        root = Path(root).resolve()
        listed = source_files(root, extra_excludes)
        all_rel = [p.relative_to(root).as_posix() for p in listed]
        code = [r for r in all_rel if r.lower().endswith(CODE_SUFFIXES)]
        corpus = cls(root=root, files=code, all_files=all_rel)
        for rel in code:
            f = facts_mod.file_facts(root / rel, rel)
            if f is not None:
                corpus.facts[rel] = f
        corpus.files = [r for r in code if r in corpus.facts]
        docs = [_Doc(r, PurePosixPath(r).name) for r in corpus.files]
        corpus.path_lookup, corpus.module_lookup = import_resolution.lookups(
            docs, lambda d: d.path, CODE_SUFFIXES)
        corpus._build_import_roots(all_rel)
        if (root / "frontend" / "src").is_dir():
            corpus.js_aliases = {"@/": "frontend/src/"}
        return corpus

    def _build_import_roots(self, all_rel: list[str]) -> None:
        roots: set[str] = set()
        for rel in self.files:
            parts = rel.split("/")
            if len(parts) >= 3 and parts[0] == "plugins" and rel.endswith(".py"):
                roots.add(f"plugins/{parts[1]}")
        for rel in all_rel:
            p = PurePosixPath(rel)
            if p.name in PACKAGING_FILES and len(p.parts) > 1:
                roots.add(str(p.parent))
        for r in sorted(roots):
            prefix = r + "/"
            docs = [_Doc(f, PurePosixPath(f).name) for f in self.files
                    if f.startswith(prefix) and f.endswith(".py")]
            if not docs:
                continue
            _, lookup = import_resolution.lookups(
                docs, lambda d, prefix=prefix: d.path[len(prefix):], facts_mod.PY_SUFFIXES)
            self.import_roots[r] = lookup

    # -- classification ------------------------------------------------------
    @staticmethod
    def is_test(rel: str) -> bool:
        return is_test_path(rel)

    def python_files(self) -> list[str]:
        return [r for r in self.files if r.endswith(".py")]

    def js_files(self) -> list[str]:
        return [r for r in self.files if not r.endswith(".py")]

    def roots_for(self, rel: str) -> list[str]:
        """Import roots containing ``rel``, most specific first."""
        return sorted((r for r in self.import_roots if rel.startswith(r + "/")), key=len, reverse=True)

    # -- resolution ----------------------------------------------------------
    def resolve_python(self, rel: str, level: int, module: str) -> str | None:
        """The file a Python import in ``rel`` lands on, or None when external."""
        if level:
            return import_resolution.resolve_python_import(rel, level, module, self.module_lookup)
        for r in self.roots_for(rel):
            hit = _exact_or_prefix(module, self.import_roots[r])
            if hit:
                return hit
        return import_resolution.resolve_python_import(rel, 0, module, self.module_lookup)

    def _python_module_exact(self, rel: str, level: int, dotted: str) -> str | None:
        """The file for exactly ``dotted`` (no prefix fallback), or None."""
        target = import_resolution.python_import_target(rel, level, dotted)
        if not level:
            for r in self.roots_for(rel):
                hit = self.import_roots[r].get(target)
                if hit:
                    return hit
        return self.module_lookup.get(target)

    def resolve_js(self, rel: str, spec: str) -> str | None:
        if not (spec.startswith(".") or spec.startswith("/") or any(spec.startswith(a) for a in self.js_aliases)):
            return None
        return import_resolution.resolve_js_import(rel, spec, self.path_lookup, self.module_lookup,
                                                   self.js_aliases)

    def resolve_js_glob(self, rel: str, pattern: str) -> list[str]:
        for prefix, folder in self.js_aliases.items():
            if pattern.startswith(prefix):
                pattern = folder + pattern[len(prefix):]
                break
        else:
            if pattern.startswith("."):
                pattern = posixpath.normpath(str(PurePosixPath(rel).parent / pattern))
        return [f for f in self.files if fnmatch.fnmatchcase(f, pattern)]

    # -- edges ---------------------------------------------------------------
    def edges(self) -> list[Edge]:
        """Every resolved in-repository import, one per (src, dst, kind, line)."""
        if self._edges is not None:
            return self._edges
        out: list[Edge] = []
        seen: set[tuple] = set()

        def add(src: str, dst: str | None, kind: str, line: int) -> None:
            if not dst or dst == src:
                return
            key = (src, dst, kind)
            if key in seen:
                return
            seen.add(key)
            out.append(Edge(src, dst, kind, line))

        for rel in self.files:
            f = self.facts[rel]
            if f["lang"] == "py":
                for imp in f["imports"]:
                    kind = imp["scope"]
                    if imp.get("prefix"):
                        for other in self.python_files():
                            if module_name(other).startswith(imp["module"]):
                                add(rel, other, kind, imp["line"])
                        continue
                    if imp["names"] is None:
                        add(rel, self.resolve_python(rel, imp["level"], imp["module"]), kind, imp["line"])
                        continue
                    package = self.resolve_python(rel, imp["level"], imp["module"]) if (
                        imp["module"] or imp["level"]) else None
                    for name, _as in imp["names"]:
                        if name == "*":
                            add(rel, package, kind, imp["line"])
                            continue
                        dotted = f"{imp['module']}.{name}" if imp["module"] else name
                        sub = self._python_module_exact(rel, imp["level"], dotted)
                        add(rel, sub or package, kind, imp["line"])
                # A string naming one of our modules ("commands.chat" in a
                # loader's list) is an import made by name when it runs. Tests
                # name modules in assertions and patch targets, so theirs are not.
                for dotted, line in ({} if is_test_path(rel) else (f.get("dotted") or {})).items():
                    target = self._python_module_exact(rel, 0, dotted)
                    if target and target != rel:
                        add(rel, target, "lazy", line)
            else:
                for imp in f["imports"]:
                    if imp["kind"] == "glob":
                        for target in self.resolve_js_glob(rel, imp["spec"]):
                            add(rel, target, "lazy", imp["line"])
                        continue
                    kind = "lazy" if imp["kind"] == "lazy" else "top"
                    add(rel, self.resolve_js(rel, imp["spec"]), kind, imp["line"])
        self._edges = out
        return out

    # -- entry points --------------------------------------------------------
    _ENTRY_SOURCES = (".sh", ".bash", ".service", "plugin.json", "Procfile", "Dockerfile", ".yml", ".yaml", ".toml")
    _NOT_TARGETS = frozenset({"uvicorn", "gunicorn", "pip", "pytest", "venv", "http.server", "celery",
                              "flask", "playwright", "json.tool", "unittest", "ensurepip", "torch.utils"})

    def entry_points(self) -> dict[str, list[str]]:
        """{file: [scripts and manifests that start it]}."""
        import re as _re

        if self._entries is not None:
            return self._entries
        out: dict[str, list[str]] = {}
        py = set(self.python_files())
        for src in self.all_files:
            name = PurePosixPath(src).name
            if not (src.endswith(self._ENTRY_SOURCES) or name in ("Procfile", "Dockerfile")):
                continue
            try:
                text = (self.root / src).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if len(text) > 400_000:
                continue
            base_dirs = [str(PurePosixPath(src).parent)]
            parts = src.split("/")
            if len(parts) >= 2 and parts[0] == "plugins":
                base_dirs.append(f"plugins/{parts[1]}")
            base_dirs.append("")
            targets: set[str] = set()
            modules = [m.group(1) for m in _re.finditer(r"\buvicorn\s+([A-Za-z_][\w.]*):", text)]
            modules += [m.group(1) for m in _re.finditer(r"\s-m\s+([A-Za-z_][\w.]*)", text)]
            for module in modules:
                if module in self._NOT_TARGETS:
                    continue
                for base in base_dirs:
                    cand = (base.replace("/", ".") + "." if base else "") + module
                    hit = self.module_lookup.get(cand)
                    if hit in py:
                        targets.add(hit)
                        break
            for m in _re.finditer(r"([\w-]+(?:/[\w-]+)*\.py)\b", text):
                tail = m.group(1)
                for base in base_dirs:
                    cand = posixpath.normpath(posixpath.join(base, tail)) if base else tail
                    if cand in py:
                        targets.add(cand)
                        break
            for t in targets:
                if t != src:
                    out.setdefault(t, []).append(src)
        self._entries = out
        return out

    # -- name bindings (for the call graph) ----------------------------------
    def bindings(self, rel: str) -> dict[str, tuple[str, str]]:
        """{local name: (file, symbol)} for what ``rel`` imports; symbol is ""
        when the name is bound to a module itself."""
        if self._bindings is None:
            self._bindings = {}
        if rel in self._bindings:
            return self._bindings[rel]
        out: dict[str, tuple[str, str]] = {}
        f = self.facts.get(rel) or {}
        if f.get("lang") == "py":
            for imp in f.get("imports", []):
                if imp.get("dynamic"):
                    continue  # binds no name
                if imp["names"] is None:
                    module = imp["module"]
                    if imp["alias"]:
                        target = self._python_module_exact(rel, 0, module)
                        if target:
                            out[imp["alias"]] = (target, "")
                    else:
                        head = module.split(".", 1)[0]
                        target = self._python_module_exact(rel, 0, head)
                        if target:
                            out[head] = (target, "")
                    continue
                for name, asname in imp["names"]:
                    if name == "*":
                        continue
                    local = asname or name
                    dotted = f"{imp['module']}.{name}" if imp["module"] else name
                    sub = self._python_module_exact(rel, imp["level"], dotted)
                    if sub:
                        out[local] = (sub, "")
                        continue
                    package = self.resolve_python(rel, imp["level"], imp["module"])
                    if package:
                        out[local] = (package, name)
        elif f.get("lang") == "js":
            for imp in f.get("imports", []):
                target = self.resolve_js(rel, imp["spec"]) if imp["kind"] != "glob" else None
                if not target:
                    continue
                for local, exported in imp["names"].items():
                    out[local] = (target, "" if exported == "*" else exported)
        self._bindings[rel] = out
        return out


def _exact_or_prefix(module: str, lookup: dict[str, str]) -> str | None:
    pieces = module.split(".")
    for end in range(len(pieces), 0, -1):
        hit = lookup.get(".".join(pieces[:end]))
        if hit:
            return hit
    return None
