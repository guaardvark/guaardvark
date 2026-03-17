#!/usr/bin/env python3
"""
Generate Component Inventory Document
"""

import json
from pathlib import Path
from typing import Dict, List
from collections import defaultdict


def generate_inventory_document(scan_file: str, dep_file: str, output_file: str):
    """Generate comprehensive component inventory markdown document"""

    # Load scan data
    with open(scan_file, "r", encoding="utf-8") as f:
        scan_data = json.load(f)

    # Load dependency data if available
    dep_data = {}
    if Path(dep_file).exists():
        with open(dep_file, "r", encoding="utf-8") as f:
            dep_data = json.load(f)

    components = scan_data.get("components", [])

    # Group components by type
    by_type = defaultdict(list)
    for comp in components:
        comp_type = comp.get("component_type", "other")
        by_type[comp_type].append(comp)

    lines = []
    lines.append("# System Component Inventory\n")
    lines.append(
        "Comprehensive inventory of all system components, their purposes, and relationships.\n"
    )
    lines.append(f"*Generated from scan of {len(components)} Python files*\n")

    # Statistics
    lines.append("## Statistics\n")
    lines.append(f"- **Total Components**: {len(components)}")
    lines.append(f"- **Component Types**: {len(by_type)}")
    for comp_type, comps in sorted(by_type.items()):
        lines.append(f"  - {comp_type}: {len(comps)}")
    lines.append("")

    # Table of Contents
    lines.append("## Table of Contents\n")
    for comp_type in sorted(by_type.keys()):
        anchor = comp_type.replace(" ", "-").lower()
        lines.append(f"- [{comp_type.title()} Components](#{anchor})")
    lines.append("")

    # Components by type
    for comp_type in sorted(by_type.keys()):
        comps = sorted(by_type[comp_type], key=lambda x: x.get("file_path", ""))
        anchor = comp_type.replace(" ", "-").lower()

        lines.append(f"## {comp_type.title()} Components {{#{anchor}}}\n")
        lines.append(f"*{len(comps)} components*\n")

        for comp in comps:
            file_path = comp.get("file_path", "")
            module_path = comp.get("module_path", "")
            line_count = comp.get("line_count", 0)

            lines.append(f"### `{file_path}`\n")
            lines.append(f"**Module**: `{module_path}`  \n")
            lines.append(f"**Lines**: {line_count}  \n")

            # Classes
            classes = comp.get("classes", [])
            if classes:
                lines.append(f"**Classes ({len(classes)})**:")
                for cls in classes[:5]:  # Limit to first 5
                    lines.append(f"- `{cls['name']}` (line {cls['line']})")
                    if cls.get("bases"):
                        lines.append(f"  - Bases: {', '.join(cls['bases'])}")
                    if cls.get("methods"):
                        lines.append(f"  - Methods: {len(cls['methods'])}")
                if len(classes) > 5:
                    lines.append(f"- ... and {len(classes) - 5} more")
                lines.append("")

            # Functions
            functions = comp.get("functions", [])
            if functions:
                # Filter out private functions for brevity
                public_funcs = [f for f in functions if not f["name"].startswith("_")]
                if public_funcs:
                    lines.append(f"**Public Functions ({len(public_funcs)})**:")
                    for func in public_funcs[:10]:  # Limit to first 10
                        decorators = func.get("decorators", [])
                        decorator_str = (
                            f" @{', @'.join(decorators)}" if decorators else ""
                        )
                        lines.append(
                            f"- `{func['name']}()` (line {func['line']}){decorator_str}"
                        )
                    if len(public_funcs) > 10:
                        lines.append(f"- ... and {len(public_funcs) - 10} more")
                    lines.append("")

            # Blueprints
            blueprints = comp.get("blueprints", [])
            if blueprints:
                lines.append("**Blueprints**:")
                for bp in blueprints:
                    lines.append(f"- `{bp.get('variable_name', 'N/A')}`")
                    lines.append(f"  - Name: {bp.get('name', 'N/A')}")
                    lines.append(f"  - URL Prefix: {bp.get('url_prefix', 'N/A')}")
                lines.append("")

            # Routes
            routes = comp.get("routes", [])
            if routes:
                lines.append(f"**Routes ({len(routes)})**:")
                for route in routes[:5]:  # Limit to first 5
                    methods = ", ".join(route.get("methods", []))
                    path = route.get("path", "")
                    func = route.get("function", "")
                    lines.append(f"- `{methods}` `{path}` → `{func}()`")
                if len(routes) > 5:
                    lines.append(f"- ... and {len(routes) - 5} more routes")
                lines.append("")

            # Dependencies
            imports = comp.get("imports", [])
            if imports:
                # Group by type
                backend_imports = [
                    imp
                    for imp in imports
                    if imp.get("module", "").startswith("backend.")
                ]
                external_imports = [
                    imp
                    for imp in imports
                    if not imp.get("module", "").startswith("backend.")
                ]

                if backend_imports:
                    lines.append(f"**Backend Dependencies ({len(backend_imports)})**:")
                    for imp in backend_imports[:10]:
                        module = imp.get("module", "")
                        name = imp.get("name", "")
                        if name:
                            lines.append(f"- `from {module} import {name}`")
                        else:
                            lines.append(f"- `import {module}`")
                    if len(backend_imports) > 10:
                        lines.append(f"- ... and {len(backend_imports) - 10} more")
                    lines.append("")

            lines.append("---\n")

    # Write document
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Inventory document generated: {output_file}")


if __name__ == "__main__":
    import sys

    scan_file = (
        sys.argv[1] if len(sys.argv) > 1 else "docs/generated/component_scan.json"
    )
    dep_file = (
        sys.argv[2] if len(sys.argv) > 2 else "docs/generated/dependency_analysis.json"
    )
    output_file = sys.argv[3] if len(sys.argv) > 3 else "docs/SYSTEM_INVENTORY.md"

    generate_inventory_document(scan_file, dep_file, output_file)
