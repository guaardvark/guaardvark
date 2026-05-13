"""Make the plugin's top-level packages (`mlt`, `service`) importable from tests.

The live service runs with PYTHONPATH=plugins/video_editor (set by start.sh)
so it imports `mlt.frame_math`, `service.crew_interface`, etc. as top-level.
Tests run from the repo root, so we have to inject the plugin root into
sys.path the same way.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
