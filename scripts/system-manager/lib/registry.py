import json
import os
from datetime import datetime

REGISTRY_PATH = os.path.expanduser("~/.guaardvark_registry.json")


def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        return {"environments": {}, "last_discovery": None}
    try:
        with open(REGISTRY_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"environments": {}, "last_discovery": None}


def save_registry(registry):
    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2)


def register_environment(path, name=None):
    registry = load_registry()
    path = os.path.abspath(path)
    if not name:
        name = os.path.basename(path)

    registry["environments"][path] = {
        "name": name,
        "path": path,
        "registered_at": datetime.now().isoformat(),
        "last_check": None,
        "status": "unknown",
    }
    save_registry(registry)
    return registry["environments"][path]


def get_environment(path):
    registry = load_registry()
    path = os.path.abspath(path)
    return registry["environments"].get(path)


def remove_environment(path):
    registry = load_registry()
    path = os.path.abspath(path)
    if path in registry["environments"]:
        del registry["environments"][path]
        save_registry(registry)
        return True
    return False


def list_environments():
    registry = load_registry()
    return registry["environments"]
