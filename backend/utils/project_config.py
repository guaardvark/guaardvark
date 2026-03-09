import json
from pathlib import Path

from backend.config import GUAARDVARK_MODE, GUAARDVARK_ROOT


def load_config() -> dict:
    base = Path(GUAARDVARK_ROOT)
    mode_file = base / f"project_config_{GUAARDVARK_MODE}.json"
    default_file = base / "project_config.json"
    path = mode_file if mode_file.is_file() else default_file
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
