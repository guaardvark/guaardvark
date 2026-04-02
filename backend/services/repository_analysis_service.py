"""Repository analysis service — generates architectural summaries and metadata for code repos."""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

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
        summary = RepositoryAnalysisService._generate_llm_summary(
            folder.name, frameworks, languages, file_list_str, key_file_contents
        )

        # Save metadata
        metadata = {
            "languages": languages,
            "frameworks": frameworks,
            "file_count": len(all_files),
            "analyzed_at": datetime.now().isoformat(),
            "analysis_version": "1.0",
        }

        folder.description = summary
        folder.repo_metadata = json.dumps(metadata)
        folder.is_repository = True
        db.session.commit()

        # Index summary for RAG
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
    def build_dependency_graph(folder_id: int) -> Dict[str, List[str]]:
        """Build import-based dependency graph for a repository.

        Returns an adjacency list: {file_path: [imported_file_paths]}.
        Only includes edges where the imported file exists in the repo.
        """
        from backend.utils.code_symbol_extractor import extract_symbols
        from backend.utils.code_chunker import CODE_LANGUAGE_MAP

        folder = db.session.get(Folder, folder_id)
        if not folder:
            return {}

        all_files = RepositoryAnalysisService._get_all_files_recursive(folder)

        # Build a lookup from filename/partial-path to full path
        path_lookup = {}
        for doc in all_files:
            path_lookup[doc.filename] = doc.path
            parts = doc.path.split("/")
            for i in range(len(parts)):
                partial = "/".join(parts[i:])
                path_lookup[partial] = doc.path

        graph = {}

        for doc in all_files:
            ext = os.path.splitext(doc.filename)[1].lower()
            language = CODE_LANGUAGE_MAP.get(ext)
            if not language or not doc.content:
                continue

            symbols = extract_symbols(doc.content, language)
            imports = [s["name"] for s in symbols if s["type"] == "import"]

            resolved = []
            for imp in imports:
                candidates = [
                    imp, imp.replace(".", "/"),
                    imp + ext, imp.replace(".", "/") + ext,
                    imp + ".py", imp + ".js", imp + ".ts",
                    imp + "/index.js", imp + "/index.ts",
                ]
                candidates += [c.lstrip("./") for c in candidates]

                for candidate in candidates:
                    if candidate in path_lookup:
                        resolved.append(path_lookup[candidate])
                        break

            if resolved:
                graph[doc.path] = resolved

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
    def generate_repository_map(folder_id: int, token_budget: int = 4096) -> str:
        """Generate a compressed, PageRank-ranked repository map.

        Lists the most important symbols across all files, fitting within
        the token budget. Indexed as a document for RAG context injection.
        """
        import networkx as nx
        from backend.utils.code_symbol_extractor import extract_symbols
        from backend.utils.code_chunker import CODE_LANGUAGE_MAP

        folder = db.session.get(Folder, folder_id)
        if not folder:
            return ""

        all_files = RepositoryAnalysisService._get_all_files_recursive(folder)

        # Collect all symbols per file
        file_symbols = {}
        for doc in all_files:
            ext = os.path.splitext(doc.filename)[1].lower()
            language = CODE_LANGUAGE_MAP.get(ext)
            if not language or not doc.content:
                continue
            symbols = extract_symbols(doc.content, language)
            if symbols:
                file_symbols[doc.path] = symbols

        if not file_symbols:
            return ""

        # Build a simple reference graph for PageRank
        G = nx.DiGraph()
        for path, syms in file_symbols.items():
            for s in syms:
                if s["type"] in ("function", "class", "method"):
                    node_id = f"{path}::{s['name']}"
                    G.add_node(node_id, path=path, name=s["name"], type=s["type"])

        # Add edges: if a file's code mentions a symbol defined in another file
        for path, syms in file_symbols.items():
            doc = next((d for d in all_files if d.path == path), None)
            if not doc or not doc.content:
                continue
            for other_path, other_syms in file_symbols.items():
                if other_path == path:
                    continue
                for s in other_syms:
                    if s["type"] in ("function", "class", "method") and s["name"] in doc.content:
                        src = f"{path}::caller"
                        dst = f"{other_path}::{s['name']}"
                        if dst in G:
                            G.add_edge(src, dst)

        # Rank by PageRank
        try:
            ranks = nx.pagerank(G, max_iter=50) if len(G) > 0 else {}
        except Exception:
            ranks = {n: 1.0 for n in G.nodes()}

        # Build the map text, fitting within token budget
        file_ranked = {}
        for node_id, rank in ranks.items():
            data = G.nodes.get(node_id, {})
            path = data.get("path")
            if path:
                if path not in file_ranked:
                    file_ranked[path] = []
                file_ranked[path].append((rank, data.get("name", ""), data.get("type", "")))

        sorted_files = sorted(
            file_ranked.items(),
            key=lambda item: max(r for r, _, _ in item[1]),
            reverse=True,
        )

        lines = [f"# Repository Map: {folder.name}\n"]
        char_budget = token_budget * 4  # ~4 chars per token

        for path, ranked_syms in sorted_files:
            section = f"\n## {path}\n"
            ranked_syms.sort(reverse=True)
            for _, name, sym_type in ranked_syms[:10]:
                section += f"- {sym_type}: {name}\n"

            if len("\n".join(lines)) + len(section) > char_budget:
                lines.append("\n... (truncated to fit token budget)")
                break
            lines.append(section)

        repo_map = "\n".join(lines)

        # Store in folder metadata and index
        try:
            existing = json.loads(folder.repo_metadata) if folder.repo_metadata else {}
            existing["repository_map"] = repo_map
            folder.repo_metadata = json.dumps(existing)
            db.session.commit()
        except Exception as e:
            logger.error(f"Failed to save repository map: {e}")

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
            )
            logger.info(f"Indexed repository map for {folder.name}")
        except Exception as e:
            logger.error(f"Failed to index repository map: {e}")

        return repo_map

    @staticmethod
    def _generate_llm_summary(
        name: str,
        frameworks: List[str],
        languages: Dict[str, int],
        file_tree: str,
        key_files: Dict[str, str],
    ) -> str:
        """Generate an architectural summary using the local LLM."""
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

        try:
            from backend.services.llm_service import LLMService
            response = LLMService.generate(prompt)
            if response and len(response.strip()) > 50:
                return response.strip()
            logger.warning("LLM returned empty/short response, using fallback")
        except Exception as e:
            logger.warning(f"LLM summary generation failed: {e}")

        # Fallback: structured summary without LLM
        top_dirs = sorted(
            set(f.split("/")[0] for f in file_tree.splitlines() if "/" in f),
        )[:10]
        return (
            f"Repository: {name}\n"
            f"Frameworks: {', '.join(frameworks) if frameworks else 'Unknown'}\n"
            f"Total Files: {sum(languages.values())}\n"
            f"Languages: {json.dumps(languages)}\n"
            f"Top Directories: {', '.join(top_dirs)}\n"
        )

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
