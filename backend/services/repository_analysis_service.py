"""Repository analysis service — generates architectural summaries and metadata for code repos."""

import ast
import hashlib
import json
import logging
import os
import posixpath
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Dict, List, NamedTuple, Optional, Tuple

from backend.models import Folder, Document, db
from backend.services.indexing_service import add_text_to_index

logger = logging.getLogger(__name__)

FRAMEWORK_INDICATORS = {
    "package.json": "Node.js",
    "requirements.txt": "Python",
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "Pipfile": "Python",
    "pom.xml": "Java/Maven",
    "build.gradle": "Java/Gradle",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "Package.swift": "Swift",
}

REACT_INDICATORS = {".jsx", ".tsx"}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A name defined in more files than this (execute, get, __init__, render) says
# nothing about which definition a file uses, so it links nothing in the map.
MAP_MAX_DEFINERS = 3
# Files that call into the code but are not where a reader looks for it:
# tests and bundled or minified output. Their calls still rank what they use.
def _distinctive(name: str) -> bool:
    """A name unlikely to be an ordinary word: get_media_service, NewJobModal."""
    return len(name) >= 6 and ("_" in name.strip("_") or any(c.isupper() for c in name[1:]))


_MAP_UNLISTED = re.compile(
    r"(^|/)(tests?|__tests__|__mocks__|e2e)/|(^|/)test_[^/]*\.py$|_test\.py$|\.(test|spec)\.[jt]sx?$"
    r"|\.min\.js$|\.bundle\.js$|(^|/)(dist|build|vendor)/"
)
VUE_INDICATORS = {".vue"}
ANGULAR_INDICATOR = "angular.json"


class RepositoryAnalysisService:

    @staticmethod
    def detect_frameworks(file_paths: List[str]) -> List[str]:
        """Detect frameworks from file names."""
        frameworks = []
        filenames = {f.rsplit("/", 1)[-1] if "/" in f else f for f in file_paths}
        extensions = {f.rsplit(".", 1)[-1].lower() for f in file_paths if "." in f.rsplit("/", 1)[-1]}

        for indicator_file, framework in FRAMEWORK_INDICATORS.items():
            if indicator_file in filenames and framework not in frameworks:
                frameworks.append(framework)

        ext_set = {"." + e for e in extensions}
        if ext_set & REACT_INDICATORS:
            if "React" not in frameworks:
                frameworks.append("React")
        if ext_set & VUE_INDICATORS:
            if "Vue" not in frameworks:
                frameworks.append("Vue")
        if ANGULAR_INDICATOR in filenames:
            if "Angular" not in frameworks:
                frameworks.append("Angular")

        return frameworks

    @staticmethod
    def get_language_breakdown(file_paths: List[str]) -> Dict[str, int]:
        """Count files by extension."""
        breakdown: Dict[str, int] = {}
        for path in file_paths:
            name = path.rsplit("/", 1)[-1] if "/" in path else path
            if "." in name:
                ext = name.rsplit(".", 1)[-1].lower()
                breakdown[ext] = breakdown.get(ext, 0) + 1
        return breakdown

    @staticmethod
    def analyze_repository(folder_id: int) -> Optional[Dict]:
        """Analyze a folder as a code repository.

        1. Collect file stats and detect languages/frameworks.
        2. Generate LLM architectural summary via Ollama.
        3. Index the summary for RAG retrieval.
        """
        folder = db.session.get(Folder, folder_id)
        if not folder:
            logger.error(f"Folder {folder_id} not found")
            return None

        logger.info(f"Analyzing repository: {folder.name}")

        all_files = RepositoryAnalysisService._get_all_files_recursive(folder)
        file_paths = [doc.path for doc in all_files]

        languages = RepositoryAnalysisService.get_language_breakdown(file_paths)
        frameworks = RepositoryAnalysisService.detect_frameworks(file_paths)

        # Build file tree string (limit to 500)
        file_list_str = "\n".join(file_paths[:500])
        if len(file_paths) > 500:
            file_list_str += f"\n... (and {len(file_paths) - 500} more files)"

        # Read key config files for richer analysis
        key_file_contents = RepositoryAnalysisService._read_key_files(all_files)

        # Generate LLM summary
        summary, summary_error = RepositoryAnalysisService._generate_llm_summary(
            folder.name, frameworks, languages, file_list_str, key_file_contents
        )

        # Save metadata. summary_status is how a caller tells a repository whose
        # summary is still owed from one that has been analysed: a template
        # standing in for the LLM's answer is indistinguishable from a real one
        # once it is in the index, so nothing is indexed until the LLM answers.
        metadata = {
            "languages": languages,
            "frameworks": frameworks,
            "file_count": len(all_files),
            "analyzed_at": datetime.now().isoformat(),
            "analysis_version": "1.0",
            "summary_status": "ok" if summary else "pending",
        }
        if summary_error:
            metadata["summary_error"] = summary_error

        if summary:
            folder.description = summary
        else:
            # A previous real summary, if any, stays: it is still the best
            # description on record and still the copy in the index.
            logger.warning(
                "Repository summary for %s is pending: %s", folder.name, summary_error
            )
        folder.repo_metadata = json.dumps(metadata)
        folder.is_repository = True
        db.session.commit()

        # Index summary for RAG
        if summary:
            try:
                add_text_to_index(
                    text=summary,
                    metadata={
                        "type": "repository_summary",
                        "folder_id": folder.id,
                        "folder_name": folder.name,
                        "folder_path": folder.path,
                        "source": "repository_analysis",
                        "content_type": "repository_summary",
                    },
                    # Re-analysing a repository must replace its previous summary, not
                    # add a second one. Both stay retrievable otherwise, and the stale
                    # one competes with the current at query time.
                    replace_where=["type", "folder_id"],
                )
                logger.info(f"Indexed repository summary for {folder.name}")
            except Exception as e:
                logger.error(f"Failed to index summary: {e}")

        # Build dependency graph
        try:
            dep_graph = RepositoryAnalysisService.build_dependency_graph(folder_id)
            if dep_graph:
                logger.info(f"Dependency graph built: {len(dep_graph)} files mapped")
        except Exception as e:
            logger.warning(f"Dependency graph building failed: {e}")

        # Generate repository map
        try:
            map_token_budget = int(os.environ.get("GUAARDVARK_REPO_MAP_TOKEN_BUDGET", "4096"))
            repo_map = RepositoryAnalysisService.generate_repository_map(folder_id, map_token_budget)
            if repo_map:
                logger.info(f"Repository map generated: {len(repo_map)} chars")
        except Exception as e:
            logger.warning(f"Repository map generation failed: {e}")

        logger.info(f"Repository analysis complete for {folder.name}")
        return metadata

    @staticmethod
    def _repo_relative_path(folder: Folder, doc_path: str) -> str:
        """Return a document path relative to the repository root folder."""
        folder_path = (folder.path or "").strip("/")
        normalized = (doc_path or "").strip("/")
        if folder_path and normalized == folder_path:
            return ""
        if folder_path and normalized.startswith(f"{folder_path}/"):
            return normalized[len(folder_path) + 1:]
        return normalized

    @staticmethod
    def _build_module_lookup(folder: Folder, documents: list) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Build path/module lookup tables whose values are stored document paths."""
        return RepositoryAnalysisService._lookups(
            documents, lambda doc: RepositoryAnalysisService._repo_relative_path(folder, doc.path)
        )

    @staticmethod
    def _lookups(files: list, rel_of) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Path and module lookup tables over ``files`` (each with .path, its key, and
        .filename), whose values are the files' keys. ``rel_of(file)`` is the
        file's path relative to the repository root."""
        from backend.utils.code_chunker import CODE_LANGUAGE_MAP

        path_lookup: Dict[str, str] = {}
        module_lookup: Dict[str, str] = {}

        for doc in files:
            rel_path = rel_of(doc)
            if not rel_path:
                continue

            rel_posix = posixpath.normpath(rel_path.replace("\\", "/").lstrip("/"))
            path_lookup[rel_posix] = doc.path

            ext = os.path.splitext(doc.filename)[1].lower()
            if ext not in CODE_LANGUAGE_MAP:
                continue

            stem = rel_posix[: -len(ext)] if ext else rel_posix
            module_lookup[stem.replace("/", ".")] = doc.path
            module_lookup[stem] = doc.path

            if PurePosixPath(rel_posix).name in {"__init__.py", "index.js", "index.jsx", "index.ts", "index.tsx"}:
                package_path = str(PurePosixPath(stem).parent)
                if package_path and package_path != ".":
                    module_lookup[package_path.replace("/", ".")] = doc.path
                    module_lookup[package_path] = doc.path

        return path_lookup, module_lookup

    @staticmethod
    def _extract_python_imports(content: str) -> List[Tuple[int, str]]:
        """Return Python import targets as (relative_level, module_name)."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        imports: List[Tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend((0, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                for alias in node.names:
                    if alias.name == "*":
                        imports.append((node.level, base))
                    elif base:
                        imports.append((node.level, f"{base}.{alias.name}"))
                        imports.append((node.level, base))
                    else:
                        imports.append((node.level, alias.name))
        return imports

    @staticmethod
    def _resolve_python_import(
        rel_path: str,
        level: int,
        module: str,
        module_lookup: Dict[str, str],
    ) -> Optional[str]:
        """Resolve a Python import target to a stored document path when it is in-repo."""
        if level:
            rel_no_ext = os.path.splitext(rel_path)[0]
            parts = [part for part in rel_no_ext.split("/") if part]
            package_parts = parts if parts[-1:] == ["__init__"] else parts[:-1]
            if package_parts[-1:] == ["__init__"]:
                package_parts = package_parts[:-1]
            keep_count = max(0, len(package_parts) - (level - 1))
            target_parts = package_parts[:keep_count]
            if module:
                target_parts.extend(module.split("."))
            target = ".".join(part for part in target_parts if part)
        else:
            target = module

        if not target:
            return None

        pieces = target.split(".")
        for end in range(len(pieces), 0, -1):
            candidate = ".".join(pieces[:end])
            if candidate in module_lookup:
                return module_lookup[candidate]
        return None

    @staticmethod
    def _extract_js_imports(content: str) -> List[str]:
        """Return JS/TS module specifiers from import/export/require statements."""
        patterns = (
            r"(?:import|export)\s+(?:[^'\"\n]+?\s+from\s+)?['\"]([^'\"]+)['\"]",
            r"require\(\s*['\"]([^'\"]+)['\"]\s*\)",
        )
        imports: List[str] = []
        for pattern in patterns:
            imports.extend(match.group(1) for match in re.finditer(pattern, content))
        return imports

    @staticmethod
    def _resolve_js_import(
        rel_path: str,
        specifier: str,
        path_lookup: Dict[str, str],
        module_lookup: Dict[str, str],
    ) -> Optional[str]:
        """Resolve a JS/TS module specifier to a stored document path when it is in-repo."""
        candidates: List[str] = []
        js_exts = (".js", ".jsx", ".ts", ".tsx")

        if specifier.startswith("."):
            parent = PurePosixPath(rel_path).parent
            base = posixpath.normpath(str(parent.joinpath(specifier)).replace("\\", "/"))
        else:
            base = specifier.strip("/")
            if base.replace("/", ".") in module_lookup:
                return module_lookup[base.replace("/", ".")]

        base = posixpath.normpath(str(PurePosixPath(base)))
        candidates.append(base)
        candidates.extend(f"{base}{ext}" for ext in js_exts)
        candidates.extend(f"{base}/index{ext}" for ext in js_exts)

        for candidate in candidates:
            normalized = posixpath.normpath(str(PurePosixPath(candidate)))
            if normalized in path_lookup:
                return path_lookup[normalized]
        return None

    @staticmethod
    def build_dependency_graph(folder_id: int) -> Dict[str, List[str]]:
        """Build import-based dependency graph for a repository.

        Returns an adjacency list: {file_path: [imported_file_paths]}.
        Only includes edges where the imported file exists in the repo.
        """
        from backend.utils.code_chunker import CODE_LANGUAGE_MAP

        folder = db.session.get(Folder, folder_id)
        if not folder:
            return {}

        all_files = RepositoryAnalysisService._get_all_files_recursive(folder)
        graph = RepositoryAnalysisService._dependency_graph(
            all_files, lambda doc: RepositoryAnalysisService._repo_relative_path(folder, doc.path)
        )

        # Store in folder metadata
        try:
            existing = json.loads(folder.repo_metadata) if folder.repo_metadata else {}
            existing["dependency_graph"] = graph
            folder.repo_metadata = json.dumps(existing)
            db.session.commit()
            logger.info(f"Built dependency graph for {folder.name}: {len(graph)} files with imports")
        except Exception as e:
            logger.error(f"Failed to save dependency graph: {e}")

        return graph

    @staticmethod
    def _dependency_graph(files: list, rel_of) -> Dict[str, List[str]]:
        """{file key: [in-repository file keys it imports]} over ``files`` (each with
        .path, its key, .filename and .content), for Python and JS/TS imports.
        ``rel_of(file)`` is the file's path relative to the repository root."""
        from backend.utils.code_chunker import CODE_LANGUAGE_MAP

        path_lookup, module_lookup = RepositoryAnalysisService._lookups(files, rel_of)
        graph = {}

        for doc in files:
            ext = os.path.splitext(doc.filename)[1].lower()
            language = CODE_LANGUAGE_MAP.get(ext)
            if not language or not doc.content:
                continue

            rel_path = rel_of(doc)
            resolved = []

            if language == "python":
                for level, module in RepositoryAnalysisService._extract_python_imports(doc.content):
                    target = RepositoryAnalysisService._resolve_python_import(
                        rel_path, level, module, module_lookup
                    )
                    if target:
                        resolved.append(target)
            elif language in {"javascript", "typescript"}:
                for specifier in RepositoryAnalysisService._extract_js_imports(doc.content):
                    target = RepositoryAnalysisService._resolve_js_import(
                        rel_path, specifier, path_lookup, module_lookup
                    )
                    if target:
                        resolved.append(target)

            if resolved:
                graph[doc.path] = sorted(set(resolved))

        return graph

    @staticmethod
    def _store_repository_map(folder, repo_map: str) -> None:
        try:
            existing = json.loads(folder.repo_metadata) if folder.repo_metadata else {}
            existing["repository_map"] = repo_map
            folder.repo_metadata = json.dumps(existing)
            db.session.commit()
        except Exception as e:
            logger.error(f"Failed to save repository map: {e}")

    @staticmethod
    def generate_repository_map(folder_id: int, token_budget: int = 4096) -> str:
        """Generate a compressed, PageRank-ranked repository map.

        Lists the most important symbols across all files, fitting within
        the token budget. Indexed as a document for RAG context injection.
        """
        folder = db.session.get(Folder, folder_id)
        if not folder:
            return ""

        all_files = RepositoryAnalysisService._get_all_files_recursive(folder)
        repo_map = RepositoryAnalysisService._repository_map(folder.name, all_files, token_budget)
        if not repo_map:
            # Stored empty, so readers can tell "nothing to map" from "not built yet".
            RepositoryAnalysisService._store_repository_map(folder, "")
            return ""

        # Store in folder metadata and index
        RepositoryAnalysisService._store_repository_map(folder, repo_map)

        try:
            add_text_to_index(
                text=repo_map,
                metadata={
                    "type": "repository_map",
                    "folder_id": folder.id,
                    "folder_name": folder.name,
                    "content_type": "repository_map",
                    "source": "repository_analysis",
                },
                # Re-analysing a repository must replace its previous summary, not
                # add a second one. Both stay retrievable otherwise, and the stale
                # one competes with the current at query time.
                replace_where=["type", "folder_id"],
            )
            logger.info(f"Indexed repository map for {folder.name}")
        except Exception as e:
            logger.error(f"Failed to index repository map: {e}")

        return repo_map

    @staticmethod
    def _repository_map(title: str, files: list, token_budget: int = 4096) -> str:
        """A ranked map of the classes, functions and methods in ``files`` (each
        with .path, .filename and .content), within ``token_budget``.

        Files are ranked by PageRank over who uses whom: a file uses another
        when it imports it, or names a definition found only there. Within a
        file, definitions come in order of how many other files use them.
        Tests and bundled output are left out of the listing but their uses
        still count. "" when no file has symbols."""
        import networkx as nx
        from backend.utils.code_symbol_extractor import extract_symbols
        from backend.utils.code_chunker import CODE_LANGUAGE_MAP

        file_symbols = {}
        for doc in files:
            ext = os.path.splitext(doc.filename)[1].lower()
            language = CODE_LANGUAGE_MAP.get(ext)
            if not language or not doc.content:
                continue
            symbols = extract_symbols(doc.content, language)
            if symbols:
                file_symbols[doc.path] = symbols
        if not file_symbols:
            return ""

        listed: Dict[str, List[Tuple[str, str]]] = {}
        defined_in: Dict[str, List[str]] = {}
        for path, syms in file_symbols.items():
            if _MAP_UNLISTED.search(path):
                continue
            seen = set()
            for sym in syms:
                if sym["type"] in ("function", "class", "method") and sym["name"] not in seen:
                    seen.add(sym["name"])
                    listed.setdefault(path, []).append((sym["name"], sym["type"]))
                    defined_in.setdefault(sym["name"], []).append(path)
        if not listed:
            return ""
        linkable = {name for name, paths in defined_in.items()
                    if len(paths) <= MAP_MAX_DEFINERS and not (name.startswith("__") and name.endswith("__"))}

        # A file uses a definition from a file it imports. Without that edge a
        # shared word ("start", "first", "show") would link unrelated files, so
        # only a distinctive name defined in exactly one place links on its own.
        imports = RepositoryAnalysisService._dependency_graph(files, lambda f: f.path)
        G = nx.DiGraph()
        G.add_nodes_from(listed)
        users: Dict[Tuple[str, str], set] = {}
        for doc in files:
            if not doc.content:
                continue
            imported = set(imports.get(doc.path, ()))
            for target in imported:
                if target in listed and target != doc.path:
                    G.add_edge(doc.path, target)
            # One pass over each file's identifiers instead of searching its
            # text for every symbol in the repository.
            for name in set(_IDENTIFIER.findall(doc.content)) & linkable:
                definers = defined_in[name]
                for other_path in definers:
                    if other_path != doc.path and (
                            other_path in imported or (len(definers) == 1 and _distinctive(name))):
                        G.add_edge(doc.path, other_path)
                        users.setdefault((other_path, name), set()).add(doc.path)

        try:
            ranks = nx.pagerank(G, max_iter=100)
        except Exception:
            ranks = {path: float(G.in_degree(path)) for path in G.nodes()}

        lines = [f"# Repository Map: {title}\n"]
        char_budget = token_budget * 4  # ~4 chars per token
        for path in sorted(listed, key=lambda p: (-ranks.get(p, 0.0), p)):
            syms = sorted(listed[path], key=lambda ns: -len(users.get((path, ns[0]), ())))
            section = f"\n## {path}\n" + "".join(f"- {sym_type}: {name}\n" for name, sym_type in syms[:10])
            if len("\n".join(lines)) + len(section) > char_budget:
                lines.append("\n... (truncated to fit token budget)")
                break
            lines.append(section)
        return "\n".join(lines)

    @staticmethod
    def _generate_llm_summary(
        name: str,
        frameworks: List[str],
        languages: Dict[str, int],
        file_tree: str,
        key_files: Dict[str, str],
    ) -> Tuple[Optional[str], Optional[str]]:
        """Generate an architectural summary using the local LLM.

        Returns (summary, None) on success and (None, reason) when the LLM did
        not answer. There is deliberately no template fallback: a summary built
        from the file list is indexed as `repository_summary` and retrieved with
        the same confidence as a real one, and the reader cannot tell them apart.
        """
        key_files_section = ""
        for fname, content in key_files.items():
            truncated = content[:2000] + "\n...(truncated)" if len(content) > 2000 else content
            key_files_section += f"\n--- {fname} ---\n{truncated}\n"

        prompt = f"""Analyze this code repository and provide a concise architectural summary.

Project: {name}
Detected Frameworks: {', '.join(frameworks) if frameworks else 'None detected'}
Language Breakdown: {json.dumps(languages, indent=2)}

File Structure:
{file_tree}

Key Configuration Files:
{key_files_section if key_files_section else '(none found)'}

Provide:
1. Project purpose (1-2 sentences)
2. Architecture pattern (MVC, microservices, monolith, etc.)
3. Key modules and their responsibilities (bullet list)
4. Entry points (main files that start the application)
5. Data flow summary (how data moves through the system)

Be concise. Focus on what a developer needs to understand the codebase."""

        # generate_text_basic answers None when no LLM is configured and "" when
        # the call failed or came back empty; both mean the summary is still owed.
        try:
            from backend.utils.llm_service import generate_text_basic
            response = generate_text_basic(prompt=prompt)
        except Exception as e:
            reason = f"LLM call failed: {e.__class__.__name__}: {str(e)[:200]}"
            logger.warning("LLM summary generation failed for %s: %s", name, reason)
            return None, reason

        if response is None:
            reason = "no LLM available"
        elif len(response.strip()) <= 50:
            reason = "LLM returned an empty or too-short response"
        else:
            return response.strip(), None
        logger.warning("LLM summary generation failed for %s: %s", name, reason)
        return None, reason

    @staticmethod
    def _read_key_files(documents: list) -> Dict[str, str]:
        """Read contents of key configuration files for analysis."""
        key_filenames = {
            "README.md", "readme.md", "README.rst",
            "package.json", "requirements.txt", "pyproject.toml",
            "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
            "Makefile", "Dockerfile", "docker-compose.yml",
        }
        result = {}
        for doc in documents:
            if doc.filename in key_filenames and doc.content:
                result[doc.filename] = doc.content
        return result

    @staticmethod
    def _get_all_files_recursive(folder) -> list:
        """Get all documents in a folder tree."""
        files = list(folder.documents.all())
        for sub in folder.subfolders.all():
            files.extend(RepositoryAnalysisService._get_all_files_recursive(sub))
        return files


# A project folder's import graph and repository map, built from the files on
# disk and kept in data/code_maps/<key>.json until the folder's source changes.
MAX_MAPPED_FILES = 4000
MAX_MAPPED_FILE_BYTES = 512 * 1024


class _DiskFile(NamedTuple):
    path: str  # relative to the folder; the key in the graph and the map
    filename: str
    content: str


def _code_maps_dir() -> Path:
    from backend.config import STORAGE_DIR

    return Path(STORAGE_DIR) / "code_maps"


def _listed_source(root: str) -> Tuple[List[str], float]:
    """The folder's source files the code tools would list (git-aware, without
    hidden, dependency and build folders), and their newest modification time."""
    from backend.tools.llama_code_tools import _source_files, rooted_at
    from backend.utils.code_chunker import CODE_LANGUAGE_MAP

    with rooted_at(root):
        rels = _source_files("")
    rels = sorted(r for r in rels if os.path.splitext(r)[1].lower() in CODE_LANGUAGE_MAP)[:MAX_MAPPED_FILES]
    newest = 0.0
    for rel in rels:
        try:
            newest = max(newest, os.stat(os.path.join(root, rel)).st_mtime)
        except OSError:
            continue
    return rels, newest


def _read_disk_files(root: str, rels: List[str]) -> List[_DiskFile]:
    files = []
    for rel in rels:
        full = os.path.join(root, rel)
        try:
            if os.path.getsize(full) > MAX_MAPPED_FILE_BYTES:
                continue
            with open(full, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        files.append(_DiskFile(rel, os.path.basename(rel), raw.decode("utf-8", errors="replace")))
    return files


def project_code_map(key: str, root: str, token_budget: Optional[int] = None, force: bool = False) -> Dict:
    """The import graph and repository map of the folder ``root``, from disk.

    Returns {root, analyzed_at, file_count, languages, dependency_graph,
    repository_map}; graph and map paths are relative to ``root``. Built at most
    once per change: the cache under ``key`` is reused while no listed file is
    newer than it and the list of files is the same.
    """
    root = os.path.realpath(root)
    rels, newest = _listed_source(root)
    listing = hashlib.sha1("\n".join(rels).encode()).hexdigest()
    cache = _code_maps_dir() / f"{key}.json"
    if not force and cache.is_file():
        try:
            data = json.loads(cache.read_text())
            if data.get("root") == root and data.get("listing") == listing and data.get("newest_mtime", 0) >= newest:
                return data
        except (OSError, ValueError):
            pass

    budget = token_budget or int(os.environ.get("GUAARDVARK_REPO_MAP_TOKEN_BUDGET", "4096"))
    files = _read_disk_files(root, rels)
    data = {
        "root": root,
        "analyzed_at": datetime.now().isoformat(timespec="seconds"),
        "file_count": len(rels),
        "newest_mtime": newest,
        "listing": listing,
        "languages": RepositoryAnalysisService.get_language_breakdown(rels),
        "dependency_graph": RepositoryAnalysisService._dependency_graph(files, lambda f: f.path),
        "repository_map": RepositoryAnalysisService._repository_map(os.path.basename(root), files, budget),
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(cache.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, cache)
    logger.info("Mapped %s: %d source files, %d with in-folder imports",
                root, len(rels), len(data["dependency_graph"]))
    return data
