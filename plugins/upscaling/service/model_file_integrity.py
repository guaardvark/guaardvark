"""Is a model weight file on disk whole, or the front part of a download?

The backend's check (backend/utils/model_file_integrity.py), loaded from the
checkout by path because sidecars do not import the backend package. One copy
decides for every registry, so an upscaler and a video model cannot disagree
about the same file.
"""

import importlib.util
import sys
from pathlib import Path

_MODULE = "guaardvark_model_file_integrity"


def _load():
    loaded = sys.modules.get(_MODULE)
    if loaded is not None:
        return loaded
    path = Path(__file__).resolve().parents[3] / "backend" / "utils" / "model_file_integrity.py"
    spec = importlib.util.spec_from_file_location(_MODULE, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[_MODULE] = module
    return module


_shared = _load()

MISSING = _shared.MISSING
EMPTY = _shared.EMPTY
file_problem = _shared.file_problem
file_complete = _shared.file_complete
file_incomplete = _shared.file_incomplete
expect_size = _shared.expect_size
