"""The frontend's MODEL_OPTIONS must agree with the registry it mirrors.

videoGeneratorPresets.js has no imports, so node can import it directly.
Every generation entry the registry declares must appear in MODEL_OPTIONS
with the same type, alignment, pixel cap, aspect ratios, step floor and
default, native rate, longest clip and speed profile ids; every MODEL_OPTIONS
id must exist in the registry. The drift this catches is the one that left
MiniMax H3 without aspect ratios on one side and with a step floor only on
the other.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.services import video_model_registry as vmr

ROOT = Path(__file__).resolve().parents[3]
PRESETS = ROOT / "frontend" / "src" / "constants" / "videoGeneratorPresets.js"
# Entries the frontend does not list on purpose: the reference build has no
# reference-media panel yet, so it is reachable through the API and tools only.
FRONTEND_EXEMPT = {"minimax-h3-ref2va-int8"}


@pytest.fixture(scope="module")
def model_options():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = (
        f"import('file://{PRESETS.as_posix()}').then(m => "
        "console.log(JSON.stringify(m.MODEL_OPTIONS)))"
    )
    out = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _generation_ids():
    return [mid for mid, e in vmr.VIDEO_MODEL_REGISTRY.items() if e.get("type") in vmr.GENERATION_TYPES]


def test_every_frontend_model_exists_in_the_registry(model_options):
    for mid, opt in model_options.items():
        assert mid in vmr.VIDEO_MODEL_REGISTRY, mid
        assert opt["type"] == vmr.VIDEO_MODEL_REGISTRY[mid]["type"], mid


def test_every_registry_generation_model_is_listed(model_options):
    missing = [mid for mid in _generation_ids() if mid not in model_options and mid not in FRONTEND_EXEMPT]
    assert missing == []


@pytest.mark.parametrize("mid", _generation_ids())
def test_limits_agree(model_options, mid):
    if mid in FRONTEND_EXEMPT:
        pytest.skip("not listed in the frontend by design")
    opt = model_options[mid]
    entry = vmr.VIDEO_MODEL_REGISTRY[mid]
    caps = vmr.model_capabilities(mid)
    if entry.get("dimension_alignment"):
        assert opt.get("dimensionAlignment") == entry["dimension_alignment"], mid
    if entry.get("max_pixel_area"):
        assert opt.get("maxPixelArea") == entry["max_pixel_area"], mid
    if entry.get("aspect_ratios"):
        assert opt.get("aspectRatios") == entry["aspect_ratios"], mid
    if caps.get("min_steps"):
        assert opt.get("minSteps") == caps["min_steps"], mid
    if caps.get("default_steps"):
        assert opt.get("defaultSteps") == caps["default_steps"], mid
    if caps.get("native_fps"):
        assert opt.get("nativeFps") == caps["native_fps"], mid
    if caps.get("max_frames"):
        assert opt.get("maxFrames") == caps["max_frames"], mid
    if caps.get("speed_profiles"):
        assert opt.get("speedProfiles") == list(caps["speed_profiles"]), mid
    assert opt.get("supportsT2V", False) == caps["supports_t2v"], mid
    assert opt.get("supportsI2V", False) == caps["supports_i2v"], mid
