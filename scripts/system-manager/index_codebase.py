#!/usr/bin/env python3

import os
import sys
import json
import ast
import re
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
GUAARDVARK_ROOT = SCRIPT_DIR.parent


@dataclass
class CodeArtifact:
    type: str
    name: str
    file_path: str
    line_number: int
    description: str
    status: str
    dependencies: List[str]
    usage_example: Optional[str] = None
    related_docs: List[str] = None
    tags: List[str] = None


class CodeCatalogBuilder:

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.artifacts: List[CodeArtifact] = []
        self.catalog_path = self.root_dir / "data" / "code_catalog.json"

    def scan_python_file(self, file_path: Path) -> List[CodeArtifact]:
        artifacts = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                tree = ast.parse(content)

            relative_path = file_path.relative_to(self.root_dir)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    docstring = ast.get_docstring(node) or "No description"
                    artifacts.append(
                        CodeArtifact(
                            type="class",
                            name=node.name,
                            file_path=str(relative_path),
                            line_number=node.lineno,
                            description=docstring,
                            status=self._infer_status(node.name, docstring),
                            dependencies=self._extract_imports(tree),
                            tags=self._extract_tags(node.name, docstring),
                        )
                    )

                elif isinstance(node, ast.FunctionDef):
                    docstring = ast.get_docstring(node) or "No description"
                    artifacts.append(
                        CodeArtifact(
                            type="function",
                            name=node.name,
                            file_path=str(relative_path),
                            line_number=node.lineno,
                            description=docstring,
                            status=self._infer_status(node.name, docstring),
                            dependencies=[],
                            tags=self._extract_tags(node.name, docstring),
                        )
                    )

        except Exception as e:
            print(f"Error parsing {file_path}: {e}")

        return artifacts

    def scan_javascript_file(self, file_path: Path) -> List[CodeArtifact]:
        artifacts = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            relative_path = file_path.relative_to(self.root_dir)

            import re

            func_pattern = r"(?:export\s+)?(?:const|function)\s+(\w+)\s*=?\s*(?:\([^)]*\))?\s*(?:=>)?\s*\{"
            for match in re.finditer(func_pattern, content):
                func_name = match.group(1)
                line_number = content[: match.start()].count("\n") + 1

                jsdoc_pattern = r"/\*\*(.*?)\*/"
                jsdoc_match = re.search(
                    jsdoc_pattern, content[: match.start()], re.DOTALL
                )
                description = (
                    jsdoc_match.group(1).strip() if jsdoc_match else "No description"
                )

                artifacts.append(
                    CodeArtifact(
                        type=(
                            "component"
                            if file_path.suffix in [".jsx", ".tsx"]
                            else "function"
                        ),
                        name=func_name,
                        file_path=str(relative_path),
                        line_number=line_number,
                        description=description,
                        status="implemented",
                        dependencies=[],
                        tags=self._extract_tags(func_name, description),
                    )
                )

        except Exception as e:
            print(f"Error parsing {file_path}: {e}")

        return artifacts

    def scan_api_endpoints(self) -> List[CodeArtifact]:
        artifacts = []
        api_dir = self.root_dir / "backend" / "api"

        if not api_dir.exists():
            return artifacts

        for api_file in api_dir.glob("*.py"):
            try:
                with open(api_file, "r", encoding="utf-8") as f:
                    content = f.read()

                import re

                route_pattern = (
                    r'@\w+\.route\(["\']([^"\']+)["\']\s*,?\s*methods=\[([^\]]+)\]\)'
                )

                for match in re.finditer(route_pattern, content):
                    route_path = match.group(1)
                    methods = match.group(2).replace('"', "").replace("'", "")

                    func_start = match.end()
                    func_match = re.search(r"def\s+(\w+)", content[func_start:])
                    if func_match:
                        func_name = func_match.group(1)

                        docstring_match = re.search(
                            r'"""(.*?)"""',
                            content[func_start : func_start + 500],
                            re.DOTALL,
                        )
                        description = (
                            docstring_match.group(1).strip()
                            if docstring_match
                            else "No description"
                        )

                        artifacts.append(
                            CodeArtifact(
                                type="endpoint",
                                name=f"{methods} {route_path}",
                                file_path=str(api_file.relative_to(self.root_dir)),
                                line_number=content[: match.start()].count("\n") + 1,
                                description=description,
                                status="implemented",
                                dependencies=[],
                                tags=["api", "endpoint"] + methods.lower().split(","),
                                usage_example=f"curl -X {methods.split(',')[0].strip()} http://localhost:5000{route_path}",
                            )
                        )

            except Exception as e:
                print(f"Error scanning API file {api_file}: {e}")

        return artifacts

    def scan_documentation(self) -> List[CodeArtifact]:
        artifacts = []

        for doc_file in self.root_dir.glob("**/*.md"):
            if "node_modules" in str(doc_file) or ".git" in str(doc_file):
                continue

            try:
                with open(doc_file, "r", encoding="utf-8") as f:
                    content = f.read()

                title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
                title = title_match.group(1) if title_match else doc_file.stem

                para_match = re.search(r"\n\n(.+?)\n\n", content, re.DOTALL)
                description = (
                    para_match.group(1).strip() if para_match else "Documentation file"
                )

                artifacts.append(
                    CodeArtifact(
                        type="documentation",
                        name=title,
                        file_path=str(doc_file.relative_to(self.root_dir)),
                        line_number=1,
                        description=description[:200],
                        status="current",
                        dependencies=[],
                        tags=["documentation"],
                    )
                )

            except Exception as e:
                print(f"Error scanning doc {doc_file}: {e}")

        return artifacts

    def build_catalog(self) -> Dict:
        print("🔍 Scanning codebase...")

        print("  Scanning Python files...")
        for py_file in self.root_dir.glob("**/*.py"):
            if (
                "node_modules" in str(py_file)
                or ".git" in str(py_file)
                or "__pycache__" in str(py_file)
            ):
                continue
            artifacts = self.scan_python_file(py_file)
            self.artifacts.extend(artifacts)

        print("  Scanning JavaScript/React files...")
        for js_file in self.root_dir.glob("**/*.{js,jsx,ts,tsx}"):
            if "node_modules" in str(js_file) or ".git" in str(js_file):
                continue
            artifacts = self.scan_javascript_file(js_file)
            self.artifacts.extend(artifacts)

        print("  Scanning API endpoints...")
        endpoint_artifacts = self.scan_api_endpoints()
        self.artifacts.extend(endpoint_artifacts)

        print("  Scanning documentation...")
        doc_artifacts = self.scan_documentation()
        self.artifacts.extend(doc_artifacts)

        catalog = {
            "metadata": {
                "total_artifacts": len(self.artifacts),
                "generated_at": str(pd.Timestamp.now()),
                "root_dir": str(self.root_dir),
            },
            "by_type": {},
            "by_status": {},
            "by_tag": {},
            "artifacts": [asdict(a) for a in self.artifacts],
        }

        for artifact in self.artifacts:
            artifact_type = artifact.type
            if artifact_type not in catalog["by_type"]:
                catalog["by_type"][artifact_type] = []
            catalog["by_type"][artifact_type].append(artifact.name)

        for artifact in self.artifacts:
            status = artifact.status
            if status not in catalog["by_status"]:
                catalog["by_status"][status] = []
            catalog["by_status"][status].append(f"{artifact.name} ({artifact.type})")

        for artifact in self.artifacts:
            if artifact.tags:
                for tag in artifact.tags:
                    if tag not in catalog["by_tag"]:
                        catalog["by_tag"][tag] = []
                    catalog["by_tag"][tag].append(f"{artifact.name} ({artifact.type})")

        return catalog

    def save_catalog(self, catalog: Dict):
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.catalog_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2)

        print(f"\n✅ Catalog saved to: {self.catalog_path}")
        print(f"   Total artifacts: {catalog['metadata']['total_artifacts']}")
        print(
            f"   By type: {dict([(k, len(v)) for k, v in catalog['by_type'].items()])}"
        )

    def _infer_status(self, name: str, description: str) -> str:
        desc_lower = description.lower()
        name_lower = name.lower()

        if "todo" in desc_lower or "not implemented" in desc_lower:
            return "unused"
        elif "deprecated" in desc_lower:
            return "deprecated"
        elif "partial" in desc_lower or "incomplete" in desc_lower:
            return "partial"
        else:
            return "implemented"

    def _extract_imports(self, tree) -> List[str]:
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    def _extract_tags(self, name: str, description: str) -> List[str]:
        tags = []
        keywords = {
            "task": ["task", "schedule", "scheduler", "cron"],
            "monitoring": ["monitor", "metrics", "tracking", "observability"],
            "generation": ["generate", "generation", "bulk", "csv", "xml"],
            "checkpoint": ["checkpoint", "resume", "pause", "recovery"],
            "progress": ["progress", "status", "tracking"],
            "api": ["api", "endpoint", "route", "rest"],
            "database": ["database", "db", "model", "query"],
            "cache": ["cache", "redis", "memory"],
            "security": ["security", "auth", "permission", "access"],
            "utility": ["util", "helper", "common"],
        }

        text = (name + " " + description).lower()

        for tag, keywords_list in keywords.items():
            if any(keyword in text for keyword in keywords_list):
                tags.append(tag)

        return tags


def query_catalog(query: str, catalog_path: Path) -> List[Dict]:
    if not catalog_path.exists():
        print(f"❌ Catalog not found at {catalog_path}")
        print("   Run: python scripts/index_codebase.py --update-all")
        return []

    with open(catalog_path, "r") as f:
        catalog = json.load(f)

    query_lower = query.lower()
    results = []

    for artifact in catalog["artifacts"]:
        searchable = " ".join(
            [
                artifact["name"],
                artifact["description"],
                " ".join(artifact.get("tags", [])),
            ]
        ).lower()

        if query_lower in searchable:
            results.append(artifact)

    return results


def print_search_results(results: List[Dict]):
    if not results:
        print("❌ No results found")
        return

    print(f"\n✅ Found {len(results)} artifact(s):\n")

    for i, artifact in enumerate(results, 1):
        print(f"{i}. [{artifact['type'].upper()}] {artifact['name']}")
        print(f"   📁 {artifact['file_path']}:{artifact['line_number']}")
        print(f"   📝 {artifact['description'][:100]}...")
        print(f"   🏷️  Status: {artifact['status']}")
        if artifact.get("tags"):
            print(f"   🔖 Tags: {', '.join(artifact['tags'])}")
        if artifact.get("usage_example"):
            print(f"   💡 Usage: {artifact['usage_example']}")
        print()


if __name__ == "__main__":
    import argparse
    import re

    try:
        import pandas as pd
    except ImportError:
        # Fallback if pandas not available
        class pd:
            class Timestamp:
                @staticmethod
                def now():
                    from datetime import datetime

                    return datetime.now().isoformat()

    parser = argparse.ArgumentParser(description="Code Catalog Builder")
    parser.add_argument(
        "--update-all", action="store_true", help="Rebuild entire catalog"
    )
    parser.add_argument("--query", type=str, help="Search the catalog")
    parser.add_argument(
        "--show-unused", action="store_true", help="Show unused code artifacts"
    )

    args = parser.parse_args()

    builder = CodeCatalogBuilder(GUAARDVARK_ROOT)

    if args.update_all:
        print("🚀 Building code catalog...")
        catalog = builder.build_catalog()
        builder.save_catalog(catalog)

    elif args.query:
        results = query_catalog(args.query, builder.catalog_path)
        print_search_results(results)

    elif args.show_unused:
        if not builder.catalog_path.exists():
            print("❌ Catalog not found. Run with --update-all first.")
        else:
            with open(builder.catalog_path, "r") as f:
                catalog = json.load(f)

            unused = catalog["by_status"].get("unused", [])
            print(f"\n📦 Found {len(unused)} unused artifacts:\n")
            for artifact_name in unused:
                print(f"  • {artifact_name}")
    else:
        parser.print_help()
