import os
import sys
from registry import register_environment, load_registry, save_registry
from datetime import datetime

def discover(search_root=None):
    if not search_root:
        search_root = os.path.expanduser("~")

    found = []
    for root, dirs, files in os.walk(search_root):
        if any(x in root for x in [".cache", ".local", "node_modules", "venv", ".git", ".npm"]):
            dirs[:] = []
            continue

        if "start.sh" in files:
            if os.path.isdir(os.path.join(root, "backend")) and \
               os.path.isdir(os.path.join(root, "frontend")):
                found.append(root)
                dirs[:] = []

    registry = load_registry()
    newly_added = 0
    for path in found:
        if path not in registry["environments"]:
            register_environment(path)
            newly_added += 1

    registry = load_registry()
    registry["last_discovery"] = datetime.now().isoformat()
    save_registry(registry)
    return found, newly_added

if __name__ == "__main__":
    found_paths, count = discover()
    print(f"Discovery complete. Found {len(found_paths)} environments, {count} new ones registered.")
    for p in found_paths:
        print(f"  - {p}")
