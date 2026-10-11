"""Image model limits: declared once in media_model_registry, read through
image_render_limits.

The migration from per-module tables is pinned value for value: the tables the
readers used before (stills_defaults._FAMILY_DEFAULTS, settings_validator
MODEL_SETTINGS, image_resolution_limits' family ceilings, the offline
generator's prices and sampling envelope) are frozen here as literals or as the
old code, and the registry-built versions must equal them. The strict setting
(GUAARDVARK_IMAGE_STRICT_LIMITS) is exercised through the real stills and Cast
paths with ComfyUI's generate_image as the seam.
"""
import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import image_render_limits as irl
from backend.services.image_render_limits import STRICT_LIMITS_ENV
from backend.services.media_model_registry import IMAGE_FAMILY_SPECS, IMAGE_MODEL_LIMITS


@pytest.fixture(autouse=True)
def _strict_off(monkeypatch):
    monkeypatch.delenv(STRICT_LIMITS_ENV, raising=False)


# ── the tables the readers used before ───────────────────────────────────────

OLD_STILLS_DEFAULTS = {
    "zimage": {"min_steps": 2, "width": 1024, "height": 1024, "steps": 9, "guidance": 0.0, "prompt_style": "natural"},
    # The generic "comfyui" selector samples Z-Image first, so it carries Z-Image's
    # row rather than the legacy SD row (stills_defaults._build_family_defaults).
    "comfyui": {"min_steps": 2, "width": 1024, "height": 1024, "steps": 9, "guidance": 0.0, "prompt_style": "natural"},
    "krea2-turbo": {"width": 1024, "height": 1024, "steps": 8, "guidance": 0.0, "prompt_style": "tags"},
    "krea2-raw": {"width": 1024, "height": 1024, "steps": 52, "guidance": 3.5, "prompt_style": "tags"},
    "sdxl": {"width": 1024, "height": 1024, "steps": 25, "guidance": 7.0, "prompt_style": "tags"},
    "sd": {"width": 512, "height": 512, "steps": 20, "guidance": 7.5, "prompt_style": "tags"},
    "flux": {"width": 1024, "height": 1024, "steps": 28, "guidance": 3.5, "prompt_style": "tags"},
    "sana": {"width": 1024, "height": 1024, "steps": 2, "guidance": 4.5, "prompt_style": "tags"},
    # Qwen-Image 2.1: the official Comfy-Org template's values; no measured floor yet.
    "qwen-image-21": {"width": 1024, "height": 1024, "steps": 25, "guidance": 1.0, "prompt_style": "natural"},
}

OLD_MODEL_SETTINGS = {'epic-realism': {'best_for': ['faces', 'portraits', 'cinematic'],
                      'guidance_range': (7.0, 9.0),
                      'max_dimensions': (768, 768),
                      'min_dimensions': (512, 512),
                      'recommended_dimensions': (512, 768),
                      'recommended_guidance': 7.5,
                      'recommended_steps': 35,
                      'steps_range': (30, 40),
                      'warnings': []},
     'flux-dev': {'best_for': ['max_quality', 'prompt_adherence', 'photorealism', 'text', 'high_res'],
                  'engine': 'comfy',
                  'force_max_workers': 1,
                  'guidance_range': (1.0, 6.0),
                  'hard_clamp': False,
                  'max_dimensions': (1920, 1920),
                  'max_pixels': 2100000,
                  'min_dimensions': (512, 512),
                  'recommended_dimensions': (1024, 1024),
                  'recommended_guidance': 3.5,
                  'recommended_steps': 28,
                  'steps_range': (8, 50),
                  'warnings': ['Runs through ComfyUI (needs Comfy up + flux1-dev weights).',
                               'Heavy VRAM — batch max_workers forced to 1.',
                               'Flux Dev design range is ~2.0 MP total — not 2048×2048.']},
     'qwen-image-21': {'best_for': ['prompt_adherence', 'text', 'high_res', 'transparency'],
                       'engine': 'comfy',
                       'force_max_workers': 1,
                       'guidance_range': (1.0, 5.0),
                       'hard_clamp': False,
                       'max_dimensions': (2048, 2048),
                       'max_pixels': 4194304,
                       'min_dimensions': (512, 512),
                       'recommended_dimensions': (1024, 1024),
                       'recommended_guidance': 1.0,
                       'recommended_steps': 25,
                       'steps_range': (10, 50),
                       'warnings': ['Runs through ComfyUI 0.37 or newer (needs Comfy up + the Qwen-Image 2.1 weights).',
                                    'Heavy VRAM — batch max_workers forced to 1.',
                                    'Qwen Research License: non-commercial research and evaluation only.']},
     'krea2-raw': {'best_for': ['creative', 'photorealism', 'versatile', 'high_res', 'mature', 'fine_tune_base'],
                   'guidance_range': (1.0, 7.0),
                   'hard_clamp': False,
                   'max_dimensions': (2688, 2688),
                   'max_pixels': 4194304,
                   'min_dimensions': (512, 512),
                   'recommended_dimensions': (1024, 1024),
                   'recommended_guidance': 3.5,
                   'recommended_steps': 52,
                   'steps_range': (20, 80),
                   'warnings': ['Slower than Turbo (~52 steps). Less safety post-training than Turbo.',
                                '2K native is supported; high VRAM on 16GB cards.']},
     'krea2-turbo': {'best_for': ['aesthetic', 'photorealism', 'creative', 'high_res', 'versatile'],
                     'guidance_range': (0.0, 1.0),
                     'hard_clamp': False,
                     'max_dimensions': (2688, 2688),
                     'max_pixels': 4194304,
                     'min_dimensions': (512, 512),
                     'recommended_dimensions': (1024, 1024),
                     'recommended_guidance': 0.0,
                     'recommended_steps': 8,
                     'steps_range': (4, 20),
                     'warnings': ['2K native is supported; high VRAM on 16GB cards.']},
     'realistic-vision': {'best_for': ['faces', 'portraits', 'photorealism'],
                          'guidance_range': (7.0, 10.0),
                          'max_dimensions': (768, 768),
                          'min_dimensions': (512, 512),
                          'recommended_dimensions': (512, 768),
                          'recommended_guidance': 8.0,
                          'recommended_steps': 30,
                          'steps_range': (25, 40),
                          'warnings': []},
     'sd-1.5': {'best_for': ['general', 'speed', 'reliability'],
                'guidance_range': (1.0, 15.0),
                'max_dimensions': (768, 768),
                'min_dimensions': (512, 512),
                'recommended_dimensions': (512, 512),
                'recommended_guidance': 7.5,
                'recommended_steps': 20,
                'steps_range': (10, 50),
                'warnings': []},
     'sd-xl': {'best_for': ['high_res', 'anatomy', 'landscapes'],
               'guidance_range': (4.0, 9.0),
               'max_dimensions': (1536, 1536),
               'min_dimensions': (768, 768),
               'recommended_dimensions': (1024, 1024),
               'recommended_guidance': 7.0,
               'recommended_steps': 25,
               'steps_range': (20, 40),
               'warnings': ['Guidance > 9.0 causes black images']},
     'sdxl-turbo': {'best_for': ['speed', 'previews', 'high_res'],
                    'guidance_range': (0.0, 1.0),
                    'max_dimensions': (1536, 1536),
                    'min_dimensions': (768, 768),
                    'recommended_dimensions': (1024, 1024),
                    'recommended_guidance': 0.0,
                    'recommended_steps': 4,
                    'steps_range': (1, 4),
                    'warnings': ['Not for final quality images', 'Guidance not used by turbo models']},
     'zimage-turbo': {'best_for': ['versatile', 'photorealism', 'faces', 'anatomy', 'text', 'high_res'],
                      'guidance_range': (0.0, 2.0),
                      'hard_clamp': False,
                      'max_dimensions': (2688, 2688),
                      'max_pixels': 4194304,
                      'min_dimensions': (512, 512),
                      'recommended_dimensions': (1024, 1024),
                      'recommended_guidance': 0.0,
                      'recommended_steps': 9,
                      'steps_range': (2, 30),
                      'warnings': []},
     'sana-sprint': {'best_for': ['speed', 'previews', 'high_res', 'low_vram'],
                     'guidance_range': (2.5, 8.0),
                     'hard_clamp': False,
                     'max_dimensions': (2048, 2048),
                     'max_pixels': 1048576,
                     'min_dimensions': (512, 512),
                     'recommended_dimensions': (1024, 1024),
                     'recommended_guidance': 4.5,
                     'recommended_steps': 2,
                     'steps_range': (1, 4),
                     'warnings': ['Ignores negative prompts.']},
     'sana-sprint-0.6b': {'best_for': ['speed', 'previews', 'low_vram'],
                          'guidance_range': (2.5, 8.0),
                          'hard_clamp': False,
                          'max_dimensions': (2048, 2048),
                          'max_pixels': 1048576,
                          'min_dimensions': (512, 512),
                          'recommended_dimensions': (1024, 1024),
                          'recommended_guidance': 4.5,
                          'recommended_steps': 2,
                          'steps_range': (1, 4),
                          'warnings': ['Ignores negative prompts.']}}

OLD_FAMILY_LIMITS = {  # image_resolution_limits: (max_side, max_pixels)
    "zimage": (2688, 2048 * 2048), "krea2": (2688, 2048 * 2048),
    "flux": (1920, 2_100_000), "sdxl": (1536, 1536 * 1536), "sd": (768, 768 * 768),
}


def test_stills_defaults_are_unchanged():
    from backend.services.stills_defaults import _FAMILY_DEFAULTS
    assert _FAMILY_DEFAULTS == OLD_STILLS_DEFAULTS


def test_validator_settings_are_unchanged():
    from backend.services.settings_validator import MODEL_SETTINGS
    assert MODEL_SETTINGS == OLD_MODEL_SETTINGS


@pytest.mark.parametrize("family,limits", sorted(OLD_FAMILY_LIMITS.items()))
def test_family_ceilings_are_unchanged(family, limits):
    from backend.services.image_resolution_limits import family_limits
    assert family_limits(family) == limits


def test_offline_prices_are_unchanged():
    from backend.services.offline_image_generator import OfflineImageGenerator as G
    assert G._FAMILY_VRAM_MB == {"krea2": 14000, "zimage": 11000, "sdxl": 8000, "sana": 6450, "sd": 4000}
    assert G._KREA2_SEQUENTIAL_VRAM_MB == 10000
    assert G._FAMILY_RAM_GB == {"krea2": 24.0, "zimage": 21.0, "sdxl": 10.0, "sana": 19.0, "sd": 6.0}
    assert G._FAMILY_VRAM_SLOPE_MB_PER_MP == {"krea2": 1000, "zimage": 500, "sdxl": 1500, "sd": 800}
    assert G._FAMILY_RAM_SLOPE_GB_PER_MP == {"krea2": 1.0, "zimage": 1.0, "sdxl": 1.0, "sd": 0.5}
    gen = G.__new__(G)
    gen.family_overrides, gen.available_models = {}, {"flux-dev": "comfy:flux-dev"}
    assert gen._vram_estimate_mb("flux-dev", 1024, 1024) == 12000
    assert gen._ram_estimate_gb("flux-dev", 1024, 1024) == 16.0


# ── the data ─────────────────────────────────────────────────────────────────

def test_every_model_row_names_a_family_with_a_full_row():
    required = {"max_side", "max_pixel_area", "min_side", "dimension_alignment", "width", "height",
                "default_steps", "cfg_when_unset", "prompt_style", "engine"}
    for fam, spec in IMAGE_FAMILY_SPECS.items():
        assert required <= set(spec), fam
    for mid, row in IMAGE_MODEL_LIMITS.items():
        assert row["family"] in IMAGE_FAMILY_SPECS, mid
        lo, hi = row["steps_range"]
        assert lo <= irl.limits_for(mid)["default_steps"] <= hi, mid
        glo, ghi = row["cfg_range"]
        assert glo <= irl.limits_for(mid)["cfg_when_unset"] <= ghi, mid


def test_the_catalog_models_all_have_a_row():
    catalog = {"zimage-turbo", "flux-dev", "qwen-image-21", "krea2-turbo", "krea2-raw", "sd-xl", "sdxl-turbo",
               "realistic-vision", "epic-realism", "sana-sprint",
               "sana-sprint-0.6b"}   # OfflineImageGenerator.available_models
    assert catalog <= set(IMAGE_MODEL_LIMITS)


@pytest.mark.parametrize("model,family", [
    ("zimage-turbo", "zimage"), ("Tongyi-MAI/Z-Image-Turbo", "zimage"), ("krea2-raw", "krea2"),
    ("krea/Krea-2-Turbo", "krea2"), ("flux-dev", "flux"), ("flux-schnell", "flux"),
    ("sd-xl", "sdxl"), ("sdxl-turbo", "sdxl"), ("stabilityai/stable-diffusion-xl-base-1.0", "sdxl"),
    ("realistic-vision", "sd"), ("epic-realism", "sd"), ("", "sd"), (None, "sd"),
    ("sana-sprint", "sana"), ("Efficient-Large-Model/Sana_Sprint_0.6B_1024px_diffusers", "sana"),
    ("qwen-image-21", "qwen-image-21"), ("comfy:qwen-image-21", "qwen-image-21"),
    ("user-qwen-image-21-ft-fp8", "qwen-image-21"),
])
def test_one_family_mapper(model, family):
    from backend.services.image_resolution_limits import resolve_family
    assert irl.family_of(model) == family == resolve_family(model)


# ── the canvas: the old clamp, frozen ────────────────────────────────────────

def _old_clamp(width, height, family):
    fam = irl.family_of(family)
    max_side, max_pixels = OLD_FAMILY_LIMITS[fam]
    w, h = max(256, int(width or 256)), max(256, int(height or 256))
    if w > max_side or h > max_side:
        w, h = min(w, max_side), min(h, max_side)
    if w * h > max_pixels:
        scale = (max_pixels / float(w * h)) ** 0.5
        w, h = max(256, int(w * scale)), max(256, int(h * scale))
    w, h = max(256, (max(256, w) // 16) * 16), max(256, (max(256, h) // 16) * 16)
    while w * h > max_pixels and (w > 256 or h > 256):
        if w >= h and w > 256:
            w = max(256, w - 16)
        elif h > 256:
            h = max(256, h - 16)
        else:
            break
    return w, h


SIZES = [0, 100, 256, 511, 512, 768, 1000, 1024, 1152, 1448, 1536, 1920, 2048, 2688, 3000, 4096]


@pytest.mark.parametrize("family", sorted(OLD_FAMILY_LIMITS))
def test_resolve_canvas_matches_the_old_clamp(family):
    from backend.services.image_resolution_limits import clamp_image_dimensions
    for w, h in itertools.product(SIZES, SIZES):
        assert clamp_image_dimensions(w, h, family)[:2] == _old_clamp(w, h, family), (family, w, h)


def test_sana_canvas_holds_one_megapixel_on_its_grid():
    # Sana Sprint is newer than the frozen clamp above: 512 px minimum side, 1 MP
    # area (it renders the nearest ~1 MP bin), sides on the DC-AE's 32 px grid.
    from backend.services.image_resolution_limits import clamp_image_dimensions, family_limits
    assert family_limits("sana") == (2048, 1024 * 1024)
    assert clamp_image_dimensions(0, 0, "sana")[:2] == (512, 512)
    assert clamp_image_dimensions(768, 768, "sana")[:2] == (768, 768)
    assert clamp_image_dimensions(1024, 1024, "sana")[:2] == (1024, 1024)
    for w, h in itertools.product(SIZES, SIZES):
        cw, ch = clamp_image_dimensions(w, h, "sana")[:2]
        assert cw * ch <= 1024 * 1024 and cw % 32 == 0 and ch % 32 == 0, (w, h, cw, ch)
        assert min(cw, ch) >= 512, (w, h, cw, ch)


# ── the offline sampling envelope: the old clamp, frozen ─────────────────────

def _old_envelope(key, steps, guidance, explicit):
    steps = int(steps or 0)
    try:
        g = float(guidance)
    except (TypeError, ValueError):
        g = -1.0
    if key == "zimage-turbo":
        if explicit:
            s = steps
        elif steps <= 0 or steps > 30:
            s = 9
        else:
            s = max(steps, 2)
        return s, (0.0 if g < 0.0 or g > 2.0 else g)
    if key == "krea2-raw":
        s = 52 if not explicit and (steps < 20 or steps > 80) else steps
        return s, (3.5 if g < 1.0 or g > 7.0 else g)
    s = 8 if not explicit and (steps < 4 or steps > 20) else steps
    return s, (0.0 if g < 0.0 or g > 1.0 else g)


@pytest.mark.parametrize("key", ["zimage-turbo", "krea2-turbo", "krea2-raw"])
def test_the_offline_envelope_matches_the_old_clamp(key):
    for steps, g, explicit in itertools.product(
        [None, 0, 1, 2, 3, 4, 8, 9, 19, 20, 30, 31, 52, 80, 81, 200],
        [None, "x", -1.0, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.5, 7.0, 7.5, 12.0],
        [False, True],
    ):
        got = (irl.envelope_steps(key, steps, explicit=explicit), irl.envelope_cfg(key, g))
        assert got == _old_envelope(key, steps, g, explicit), (key, steps, g, explicit)


def test_the_generator_soft_clamp_reads_the_envelope():
    from backend.services.offline_image_generator import ImageGenerationRequest, OfflineImageGenerator
    gen = OfflineImageGenerator.__new__(OfflineImageGenerator)
    gen.available_models = {"krea2-raw": "krea/Krea-2-Raw", "krea2-turbo": "krea/Krea-2-Turbo"}
    gen.family_overrides, gen.hidden_models, gen.user_files = {}, set(), {}
    req = ImageGenerationRequest(prompt="x", model="krea2-raw", num_inference_steps=5, guidance_scale=9.0)
    gen._soft_clamp_family_sampling(req, "krea2")
    assert (req.num_inference_steps, req.guidance_scale) == (52, 3.5)
    req = ImageGenerationRequest(prompt="x", model="zimage-turbo", num_inference_steps=1, guidance_scale=0.5)
    gen._soft_clamp_family_sampling(req, "zimage")
    assert (req.num_inference_steps, req.guidance_scale) == (2, 0.5)


# ── strict: each model's own defaults ────────────────────────────────────────

@pytest.mark.parametrize("model,family_start,own", [
    ("sdxl-turbo", (25, 7.0), (4, 0.0)),
    ("realistic-vision", (20, 7.5), (30, 8.0)),
    ("epic-realism", (20, 7.5), (35, 7.5)),
])
def test_strict_starts_a_model_from_its_own_row(monkeypatch, model, family_start, own):
    from backend.services.stills_defaults import resolve_stills_defaults
    d = resolve_stills_defaults(model)
    assert (d["steps"], d["guidance"]) == family_start
    monkeypatch.setenv(STRICT_LIMITS_ENV, "1")
    d = resolve_stills_defaults(model)
    assert (d["steps"], d["guidance"]) == own


# ── strict: the ComfyUI still paths ──────────────────────────────────────────

@pytest.fixture
def comfy_calls(monkeypatch, tmp_path):
    """ComfyUIImageGenerator.generate_image, the seam both paths call: record the
    request and write the image ComfyUI would have written."""
    from backend.services import comfyui_image_generator as cig
    calls = []

    def generate_image(self, **kw):
        calls.append(kw)
        out = Path(kw.get("output_path") or tmp_path / "out.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        Image.new("RGB", (8, 8)).save(out)
        return str(out)

    monkeypatch.setattr(cig.ComfyUIImageGenerator, "generate_image", generate_image)
    return calls


def _flux_still(tmp_path):
    from backend.services.stills_pipeline import _generate_comfy_flux
    return _generate_comfy_flux(
        prompt="a lighthouse", negative="", model="flux-dev", width=2048, height=2048,
        steps=28, guidance=3.5, seed=1, enhance_mode="none", output="path", output_dir=tmp_path,
    )


def test_flux_stills_keep_the_asked_size_unless_strict(comfy_calls, tmp_path, monkeypatch):
    _flux_still(tmp_path)
    assert (comfy_calls[-1]["width"], comfy_calls[-1]["height"]) == (2048, 2048)
    monkeypatch.setenv(STRICT_LIMITS_ENV, "1")
    _flux_still(tmp_path)
    w, h = comfy_calls[-1]["width"], comfy_calls[-1]["height"]
    assert max(w, h) <= 1920 and w * h <= 2_100_000 and w % 16 == 0 and h % 16 == 0


def _cast_flux_still(tmp_path):
    from backend.services.character_still_pipeline import render_character_still
    subject = SimpleNamespace(training_settings_json={"base_model_id": "flux-dev"})
    return render_character_still("a portrait", subjects=[subject], apply_subject_loras=False,
                                  include_bible=False, output_path=tmp_path / "c.png")


def test_cast_flux_still_samples_from_the_flux_row_with_or_without_strict(comfy_calls, tmp_path, monkeypatch):
    for strict in ("", "1"):
        monkeypatch.setenv(STRICT_LIMITS_ENV, strict)
        result = _cast_flux_still(tmp_path)
        assert result.success, result.error
        assert comfy_calls[-1]["cfg"] == 3.5, strict
        assert comfy_calls[-1]["steps"] == 28, strict
        assert comfy_calls[-1]["model"] == "flux-dev", strict


def test_cast_flux_route_has_no_offline_model_key():
    from backend.services.character_still_pipeline import _offline_key_for
    assert _offline_key_for({"family": "flux", "offline_model_key": None}) is None
    assert _offline_key_for({"family": "sdxl"}) is None
    assert _offline_key_for({"family": "zimage"}) == "zimage-turbo"
