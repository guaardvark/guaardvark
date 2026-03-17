#!/usr/bin/env python3
"""
Dependency Analyzer - Maps import relationships and detects circular dependencies
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict


class DependencyAnalyzer:
    """Analyzes dependencies between Python modules"""

    def __init__(self, scan_file: str):
        """Initialize with component scan results"""
        with open(scan_file, "r", encoding="utf-8") as f:
            self.scan_data = json.load(f)

        self.components = self.scan_data.get("components", [])
        self.dependencies: Dict[str, Set[str]] = defaultdict(set)
        self.reverse_dependencies: Dict[str, Set[str]] = defaultdict(set)
        self.external_dependencies: Set[str] = set()

    def normalize_module_path(self, module_path: str) -> str:
        """Normalize module path for comparison"""
        # Remove leading/trailing dots
        module_path = module_path.strip(".")

        # Handle relative imports
        if module_path.startswith("backend."):
            return module_path

        # Try to resolve common patterns
        if not module_path.startswith("backend."):
            # Check if it's a known backend module
            for comp in self.components:
                comp_module = comp.get("module_path", "")
                if (
                    comp_module.endswith("." + module_path)
                    or comp_module == module_path
                ):
                    return comp_module

        return module_path

    def build_dependency_graph(self):
        """Build dependency graph from imports"""
        # Create module path to component mapping
        module_map = {}
        for comp in self.components:
            module_path = comp.get("module_path", "")
            if module_path:
                module_map[module_path] = comp

        # Process imports for each component
        for comp in self.components:
            source_module = comp.get("module_path", "")
            if not source_module:
                continue

            imports = comp.get("imports", [])
            for imp in imports:
                if imp.get("type") == "from_import":
                    module = imp.get("module", "")
                else:
                    module = imp.get("module", "")

                if not module:
                    continue

                # Check if it's an external dependency
                if not module.startswith("backend.") and not module.startswith("."):
                    # Check if it's a standard library or third-party
                    if "." in module:
                        parts = module.split(".")
                        if parts[0] not in [
                            "os",
                            "sys",
                            "json",
                            "pathlib",
                            "typing",
                            "collections",
                            "datetime",
                            "logging",
                            "ast",
                            "subprocess",
                            "tempfile",
                        ]:
                            self.external_dependencies.add(parts[0])
                    else:
                        if module not in [
                            "os",
                            "sys",
                            "json",
                            "pathlib",
                            "typing",
                            "collections",
                            "datetime",
                            "logging",
                            "ast",
                            "subprocess",
                            "tempfile",
                        ]:
                            self.external_dependencies.add(module)
                    continue

                # Normalize and find target module
                normalized = self.normalize_module_path(module)

                # Check if target module exists in our codebase
                target_found = False
                for target_module, target_comp in module_map.items():
                    if target_module == normalized or target_module.endswith(
                        "." + normalized
                    ):
                        self.dependencies[source_module].add(target_module)
                        self.reverse_dependencies[target_module].add(source_module)
                        target_found = True
                        break

                # Also check partial matches (e.g., backend.api.generation_api)
                if not target_found:
                    for target_module in module_map.keys():
                        if normalized in target_module or target_module.endswith(
                            "." + normalized
                        ):
                            self.dependencies[source_module].add(target_module)
                            self.reverse_dependencies[target_module].add(source_module)
                            break

    def detect_circular_dependencies(self) -> List[List[str]]:
        """Detect circular dependencies using DFS"""
        cycles = []
        visited = set()
        rec_stack = set()
        path = []

        def dfs(node: str):
            if node in rec_stack:
                # Found a cycle
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                if cycle not in cycles:
                    cycles.append(cycle)
                return

            if node in visited:
                return

            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in self.dependencies.get(node, set()):
                dfs(neighbor)

            path.pop()
            rec_stack.remove(node)

        for module in self.dependencies.keys():
            if module not in visited:
                dfs(module)

        return cycles

    def get_dependency_tree(self, root_module: str, max_depth: int = 3) -> Dict:
        """Get dependency tree for a module"""

        def build_tree(module: str, depth: int, visited: Set[str]) -> Dict:
            if depth > max_depth or module in visited:
                return {"module": module, "dependencies": []}

            visited.add(module)
            deps = []
            for dep in sorted(self.dependencies.get(module, set())):
                deps.append(build_tree(dep, depth + 1, visited.copy()))

            return {"module": module, "dependencies": deps}

        return build_tree(root_module, 0, set())

    def get_module_statistics(self) -> Dict:
        """Get statistics about dependencies"""
        stats = {
            "total_modules": len(self.components),
            "modules_with_dependencies": len(self.dependencies),
            "modules_with_dependents": len(self.reverse_dependencies),
            "total_dependencies": sum(len(deps) for deps in self.dependencies.values()),
            "external_dependencies": sorted(list(self.external_dependencies)),
            "most_dependent_modules": [],
            "most_depended_upon_modules": [],
        }

        # Find modules with most dependencies
        dep_counts = [(mod, len(deps)) for mod, deps in self.dependencies.items()]
        dep_counts.sort(key=lambda x: x[1], reverse=True)
        stats["most_dependent_modules"] = dep_counts[:10]

        # Find modules most depended upon
        rev_dep_counts = [
            (mod, len(deps)) for mod, deps in self.reverse_dependencies.items()
        ]
        rev_dep_counts.sort(key=lambda x: x[1], reverse=True)
        stats["most_depended_upon_modules"] = rev_dep_counts[:10]

        return stats

    def generate_mermaid_graph(self) -> str:
        """Generate Mermaid diagram of dependencies"""
        lines = ["graph TD"]

        # Add nodes
        for comp in self.components:
            module_path = comp.get("module_path", "")
            comp_type = comp.get("component_type", "other")
            if module_path and comp_type in ["api", "service", "utility", "model"]:
                node_id = module_path.replace(".", "_").replace("-", "_")
                label = module_path.split(".")[-1]
                color = {
                    "api": "#FF6B6B",
                    "service": "#4ECDC4",
                    "utility": "#95E1D3",
                    "model": "#F38181",
                }.get(comp_type, "#D3D3D3")
                lines.append(f'    {node_id}["{label}"]')
                lines.append(f"    style {node_id} fill:{color}")

        # Add edges (limit to avoid overwhelming diagram)
        edge_count = 0
        for source, targets in self.dependencies.items():
            if edge_count > 100:  # Limit edges for readability
                break
            source_id = source.replace(".", "_").replace("-", "_")
            for target in list(targets)[:5]:  # Limit per source
                target_id = target.replace(".", "_").replace("-", "_")
                lines.append(f"    {source_id} --> {target_id}")
                edge_count += 1

        return "\n".join(lines)

    def save_results(self, output_file: str):
        """Save analysis results"""
        cycles = self.detect_circular_dependencies()
        stats = self.get_module_statistics()

        results = {
            "statistics": stats,
            "circular_dependencies": cycles,
            "dependency_graph": {
                module: list(deps) for module, deps in self.dependencies.items()
            },
            "reverse_dependency_graph": {
                module: list(deps) for module, deps in self.reverse_dependencies.items()
            },
            "mermaid_diagram": self.generate_mermaid_graph(),
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)

        print(f"Analysis saved to {output_file}")
        print(f"\nStatistics:")
        print(f"  Total modules: {stats['total_modules']}")
        print(f"  Modules with dependencies: {stats['modules_with_dependencies']}")
        print(f"  Total dependency relationships: {stats['total_dependencies']}")
        print(f"  Circular dependencies found: {len(cycles)}")
        print(f"  External dependencies: {len(stats['external_dependencies'])}")


def main():
    import sys

    scan_file = sys.argv[1] if len(sys.argv) > 1 else "component_scan.json"
    output_file = sys.argv[2] if len(sys.argv) > 2 else "dependency_analysis.json"

    if not os.path.exists(scan_file):
        print(f"Error: Scan file {scan_file} not found. Run scan_components.py first.")
        return 1

    analyzer = DependencyAnalyzer(scan_file)
    analyzer.build_dependency_graph()
    analyzer.save_results(output_file)

    return 0


if __name__ == "__main__":
    exit(main())
