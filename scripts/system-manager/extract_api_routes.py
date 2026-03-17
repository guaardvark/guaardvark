#!/usr/bin/env python3
"""
API Route Extractor - Catalogs all Flask endpoints from blueprints
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List
from collections import defaultdict


class APIRouteExtractor:
    """Extracts API routes from Flask blueprints"""

    def __init__(self, scan_file: str):
        """Initialize with component scan results"""
        with open(scan_file, "r", encoding="utf-8") as f:
            self.scan_data = json.load(f)

        self.components = self.scan_data.get("components", [])
        self.routes: List[Dict] = []
        self.blueprints: Dict[str, Dict] = {}

    def extract_routes_from_components(self):
        """Extract all routes from API components"""
        for comp in self.components:
            comp_type = comp.get("component_type", "")

            # Only process API and route components
            if comp_type not in ["api", "route"]:
                continue

            file_path = comp.get("file_path", "")
            module_path = comp.get("module_path", "")

            # Get blueprint information
            blueprints = comp.get("blueprints", [])
            blueprint_name = None
            blueprint_url_prefix = None

            if blueprints:
                blueprint_name = blueprints[0].get("name")
                blueprint_url_prefix = blueprints[0].get("url_prefix", "")

            # Get routes
            routes = comp.get("routes", [])

            for route in routes:
                route_path = route.get("path", "")
                methods = route.get("methods", ["GET"])
                function_name = route.get("function", "")
                line_number = route.get("line", 0)

                # Construct full path
                if blueprint_url_prefix:
                    if route_path.startswith("/"):
                        full_path = blueprint_url_prefix + route_path
                    else:
                        full_path = blueprint_url_prefix + "/" + route_path
                else:
                    full_path = route_path if route_path else "/"

                # Normalize path
                if not full_path.startswith("/"):
                    full_path = "/" + full_path

                route_info = {
                    "path": full_path,
                    "methods": methods,
                    "function": function_name,
                    "blueprint": blueprint_name,
                    "blueprint_url_prefix": blueprint_url_prefix,
                    "file_path": file_path,
                    "module_path": module_path,
                    "line_number": line_number,
                }

                self.routes.append(route_info)

            # Store blueprint info
            if blueprint_name:
                self.blueprints[blueprint_name] = {
                    "name": blueprint_name,
                    "url_prefix": blueprint_url_prefix,
                    "file_path": file_path,
                    "module_path": module_path,
                    "route_count": len(routes),
                }

    def get_routes_by_blueprint(self) -> Dict[str, List[Dict]]:
        """Group routes by blueprint"""
        by_blueprint = defaultdict(list)

        for route in self.routes:
            blueprint = route.get("blueprint", "unregistered")
            by_blueprint[blueprint].append(route)

        return dict(by_blueprint)

    def get_routes_by_method(self) -> Dict[str, List[Dict]]:
        """Group routes by HTTP method"""
        by_method = defaultdict(list)

        for route in self.routes:
            methods = route.get("methods", ["GET"])
            for method in methods:
                by_method[method].append(route)

        return dict(by_method)

    def get_route_statistics(self) -> Dict:
        """Get statistics about routes"""
        stats = {
            "total_routes": len(self.routes),
            "total_blueprints": len(self.blueprints),
            "routes_by_method": {
                method: len(routes)
                for method, routes in self.get_routes_by_method().items()
            },
            "routes_by_blueprint": {
                blueprint: len(routes)
                for blueprint, routes in self.get_routes_by_blueprint().items()
            },
            "unique_paths": len(set(route["path"] for route in self.routes)),
        }

        return stats

    def generate_api_catalog_markdown(self) -> str:
        """Generate markdown API catalog"""
        lines = []
        lines.append("# API Endpoint Catalog\n")
        lines.append("Complete list of all API endpoints in the system.\n")

        # Statistics
        stats = self.get_route_statistics()
        lines.append("## Statistics\n")
        lines.append(f"- Total Routes: {stats['total_routes']}")
        lines.append(f"- Total Blueprints: {stats['total_blueprints']}")
        lines.append(f"- Unique Paths: {stats['unique_paths']}\n")
        lines.append("### Routes by HTTP Method\n")
        for method, count in sorted(stats["routes_by_method"].items()):
            lines.append(f"- {method}: {count}")
        lines.append("")

        # Routes by blueprint
        lines.append("## Routes by Blueprint\n")
        by_blueprint = self.get_routes_by_blueprint()

        for blueprint_name in sorted(by_blueprint.keys()):
            routes = sorted(by_blueprint[blueprint_name], key=lambda x: x["path"])
            blueprint_info = self.blueprints.get(blueprint_name, {})

            lines.append(f"### {blueprint_name or 'Unregistered Routes'}\n")
            if blueprint_info:
                lines.append(f"**File**: `{blueprint_info.get('file_path', '')}`")
                lines.append(
                    f"**URL Prefix**: `{blueprint_info.get('url_prefix', 'None')}`"
                )
                lines.append(f"**Route Count**: {len(routes)}\n")

            lines.append("| Method | Path | Function |")
            lines.append("|--------|------|----------|")

            for route in routes:
                methods = ", ".join(route["methods"])
                path = route["path"]
                func = route["function"]
                lines.append(f"| {methods} | `{path}` | `{func}` |")

            lines.append("")

        # Routes by method
        lines.append("## Routes by HTTP Method\n")
        by_method = self.get_routes_by_method()

        for method in sorted(by_method.keys()):
            routes = sorted(by_method[method], key=lambda x: x["path"])
            lines.append(f"### {method}\n")
            lines.append("| Path | Blueprint | Function |")
            lines.append("|------|-----------|----------|")

            for route in routes:
                path = route["path"]
                blueprint = route.get("blueprint", "N/A")
                func = route["function"]
                lines.append(f"| `{path}` | {blueprint} | `{func}` |")

            lines.append("")

        return "\n".join(lines)

    def save_results(self, output_file: str, markdown_file: str = None):
        """Save extraction results"""
        stats = self.get_route_statistics()

        results = {
            "statistics": stats,
            "blueprints": self.blueprints,
            "routes": self.routes,
            "routes_by_blueprint": self.get_routes_by_blueprint(),
            "routes_by_method": self.get_routes_by_method(),
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)

        print(f"API routes extracted, saved to {output_file}")
        print(f"\nRoute Statistics:")
        print(f"  Total routes: {stats['total_routes']}")
        print(f"  Total blueprints: {stats['total_blueprints']}")
        print(f"  Unique paths: {stats['unique_paths']}")

        if markdown_file:
            markdown_content = self.generate_api_catalog_markdown()
            with open(markdown_file, "w", encoding="utf-8") as f:
                f.write(markdown_content)
            print(f"  Markdown catalog saved to {markdown_file}")


def main():
    import sys

    scan_file = sys.argv[1] if len(sys.argv) > 1 else "component_scan.json"
    output_file = sys.argv[2] if len(sys.argv) > 2 else "api_routes.json"
    markdown_file = sys.argv[3] if len(sys.argv) > 3 else None

    if not os.path.exists(scan_file):
        print(f"Error: Scan file {scan_file} not found. Run scan_components.py first.")
        return 1

    extractor = APIRouteExtractor(scan_file)
    extractor.extract_routes_from_components()
    extractor.save_results(output_file, markdown_file)

    return 0


if __name__ == "__main__":
    exit(main())
