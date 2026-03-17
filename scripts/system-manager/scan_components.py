#!/usr/bin/env python3
"""
Component Scanner - Extracts metadata from all Python files
"""

import ast
import json
import os
from pathlib import Path
from typing import Dict, List, Any, Set
from collections import defaultdict


class ComponentScanner:
    """Scans Python files and extracts component metadata"""

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.components: List[Dict[str, Any]] = []
        self.exclude_dirs = {
            "venv",
            "__pycache__",
            "node_modules",
            ".git",
            "migrations",
            "tools",
        }
        self.exclude_files = {"__init__.py", "conftest.py"}

    def should_scan_file(self, file_path: Path) -> bool:
        """Check if file should be scanned"""
        if file_path.name in self.exclude_files:
            return False

        # Check if in excluded directory
        parts = file_path.parts
        for exclude_dir in self.exclude_dirs:
            if exclude_dir in parts:
                return False

        return file_path.suffix == ".py"

    def extract_imports(self, node: ast.AST) -> List[Dict[str, str]]:
        """Extract import statements"""
        imports = []

        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    imports.append(
                        {"type": "import", "module": alias.name, "alias": alias.asname}
                    )
            elif isinstance(child, ast.ImportFrom):
                module = child.module or ""
                for alias in child.names:
                    imports.append(
                        {
                            "type": "from_import",
                            "module": module,
                            "name": alias.name,
                            "alias": alias.asname,
                        }
                    )

        return imports

    def extract_decorators(self, node: ast.AST) -> List[str]:
        """Extract decorator names"""
        decorators = []
        if hasattr(node, "decorator_list"):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name):
                    decorators.append(decorator.id)
                elif isinstance(decorator, ast.Attribute):
                    decorators.append(self._get_attr_name(decorator))
                elif isinstance(decorator, ast.Call):
                    if isinstance(decorator.func, ast.Name):
                        decorators.append(decorator.func.id)
                    elif isinstance(decorator.func, ast.Attribute):
                        decorators.append(self._get_attr_name(decorator.func))
        return decorators

    def _get_attr_name(self, node: ast.Attribute) -> str:
        """Get full attribute name"""
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))

    def extract_functions(self, node: ast.AST) -> List[Dict[str, Any]]:
        """Extract function definitions"""
        functions = []

        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef):
                func_info = {
                    "name": child.name,
                    "line": child.lineno,
                    "decorators": self.extract_decorators(child),
                    "args": [arg.arg for arg in child.args.args],
                    "is_async": isinstance(child, ast.AsyncFunctionDef),
                }
                functions.append(func_info)

        return functions

    def extract_classes(self, node: ast.AST) -> List[Dict[str, Any]]:
        """Extract class definitions"""
        classes = []

        for child in ast.walk(node):
            if isinstance(child, ast.ClassDef):
                class_info = {
                    "name": child.name,
                    "line": child.lineno,
                    "bases": [self._get_base_name(base) for base in child.bases],
                    "decorators": self.extract_decorators(child),
                    "methods": [],
                }

                # Extract methods
                for item in child.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_info = {
                            "name": item.name,
                            "line": item.lineno,
                            "decorators": self.extract_decorators(item),
                            "args": [arg.arg for arg in item.args.args],
                        }
                        class_info["methods"].append(method_info)

                classes.append(class_info)

        return classes

    def _get_base_name(self, node: ast.AST) -> str:
        """Get base class name"""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return self._get_attr_name(node)
        return str(node)

    def extract_blueprints(self, node: ast.AST) -> List[Dict[str, Any]]:
        """Extract Flask Blueprint definitions"""
        blueprints = []

        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        if isinstance(child.value, ast.Call):
                            if isinstance(child.value.func, ast.Name):
                                if child.value.func.id == "Blueprint":
                                    # Extract blueprint name and url_prefix
                                    blueprint_info = {
                                        "variable_name": target.id,
                                        "name": None,
                                        "url_prefix": None,
                                    }

                                    # Extract arguments
                                    for keyword in child.value.keywords:
                                        if keyword.arg == "name":
                                            if isinstance(keyword.value, ast.Constant):
                                                blueprint_info["name"] = (
                                                    keyword.value.value
                                                )
                                        elif keyword.arg == "url_prefix":
                                            if isinstance(keyword.value, ast.Constant):
                                                blueprint_info["url_prefix"] = (
                                                    keyword.value.value
                                                )

                                    blueprints.append(blueprint_info)

        return blueprints

    def extract_routes(
        self, node: ast.AST, blueprint_name: str = None
    ) -> List[Dict[str, Any]]:
        """Extract Flask route decorators"""
        routes = []

        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in child.decorator_list:
                    route_info = self._extract_route_from_decorator(
                        decorator, child.name
                    )
                    if route_info:
                        route_info["function"] = child.name
                        route_info["line"] = child.lineno
                        route_info["blueprint"] = blueprint_name
                        routes.append(route_info)

        return routes

    def _extract_route_from_decorator(
        self, decorator: ast.AST, func_name: str
    ) -> Dict[str, Any]:
        """Extract route information from decorator"""
        route_info = None

        # Check for @blueprint.route() or @app.route()
        if isinstance(decorator, ast.Call):
            if isinstance(decorator.func, ast.Attribute):
                if decorator.func.attr == "route":
                    route_info = {"path": None, "methods": ["GET"]}

                    # Extract path (first positional argument)
                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                        route_info["path"] = decorator.args[0].value

                    # Extract methods (keyword argument)
                    for keyword in decorator.keywords:
                        if keyword.arg == "methods":
                            if isinstance(keyword.value, ast.List):
                                route_info["methods"] = [
                                    elt.value
                                    for elt in keyword.value.elts
                                    if isinstance(elt, ast.Constant)
                                ]

        return route_info

    def scan_file(self, file_path: Path) -> Dict[str, Any]:
        """Scan a single Python file"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            tree = ast.parse(content, filename=str(file_path))

            # Determine module type
            rel_path = file_path.relative_to(self.root_dir)
            module_path = (
                str(rel_path).replace("/", ".").replace("\\", ".").replace(".py", "")
            )

            # Extract blueprint name if this is an API file
            blueprint_name = None
            blueprints = self.extract_blueprints(tree)
            if blueprints:
                blueprint_name = blueprints[0].get("name")

            component = {
                "file_path": str(rel_path),
                "module_path": module_path,
                "file_name": file_path.name,
                "line_count": len(content.splitlines()),
                "imports": self.extract_imports(tree),
                "classes": self.extract_classes(tree),
                "functions": self.extract_functions(tree),
                "blueprints": blueprints,
                "routes": self.extract_routes(tree, blueprint_name),
                "component_type": self._determine_component_type(rel_path),
            }

            return component

        except SyntaxError as e:
            return {
                "file_path": str(file_path.relative_to(self.root_dir)),
                "error": f"Syntax error: {e}",
            }
        except Exception as e:
            return {
                "file_path": str(file_path.relative_to(self.root_dir)),
                "error": f"Error scanning: {e}",
            }

    def _determine_component_type(self, file_path: Path) -> str:
        """Determine the type of component based on file path"""
        parts = file_path.parts

        if "api" in parts:
            return "api"
        elif "services" in parts:
            return "service"
        elif "utils" in parts:
            return "utility"
        elif "models.py" in parts:
            return "model"
        elif "tests" in parts:
            return "test"
        elif "routes" in parts:
            return "route"
        elif "tasks" in parts:
            return "task"
        elif file_path.name == "app.py":
            return "application"
        elif file_path.name == "config.py":
            return "configuration"
        elif file_path.name == "celery_app.py":
            return "celery"
        else:
            return "other"

    def scan_directory(self) -> List[Dict[str, Any]]:
        """Scan all Python files in the directory"""
        components = []

        for py_file in self.root_dir.rglob("*.py"):
            if self.should_scan_file(py_file):
                component = self.scan_file(py_file)
                if component:
                    components.append(component)

        return components

    def save_results(self, output_file: str):
        """Save scan results to JSON file"""
        results = {
            "scan_root": str(self.root_dir),
            "total_files": len(self.components),
            "components": self.components,
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)

        print(f"Scanned {len(self.components)} files, saved to {output_file}")


def main():
    import sys

    root_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    output_file = sys.argv[2] if len(sys.argv) > 2 else "component_scan.json"

    scanner = ComponentScanner(root_dir)
    scanner.components = scanner.scan_directory()
    scanner.save_results(output_file)

    # Print summary
    by_type = defaultdict(int)
    for comp in scanner.components:
        comp_type = comp.get("component_type", "unknown")
        by_type[comp_type] += 1

    print("\nComponent Summary by Type:")
    for comp_type, count in sorted(by_type.items()):
        print(f"  {comp_type}: {count}")


if __name__ == "__main__":
    main()
