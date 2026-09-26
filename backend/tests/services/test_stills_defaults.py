"""Unit tests for shared stills family defaults (chat / batch / CLI parity)."""
import pytest

from backend.services.stills_defaults import (
    model_family,
    resolve_stills_defaults,
)


def test_zimage_family_defaults():
    d = resolve_stills_defaults("zimage-turbo")
    assert d["family"] == "zimage"
    assert d["width"] == 1024 and d["height"] == 1024
    # Official HF recipe: 9 steps / guidance 0.0
    assert d["steps"] == 9
    assert d["guidance"] == 0.0


def test_legacy_sd_markers_replaced_for_modern():
    d = resolve_stills_defaults(
        "zimage-turbo",
        width=512,
        height=512,
        steps=20,
        guidance=7.5,
        replace_legacy_sd_markers=True,
    )
    assert d["width"] == 1024
    assert d["steps"] == 9
    assert d["guidance"] == 0.0


def test_intentional_draft_512_kept_with_turbo_sampling():
    """Draft sizes must not be rewritten when steps/CFG are modern Turbo values."""
    d = resolve_stills_defaults(
        "zimage-turbo",
        width=512,
        height=512,
        steps=9,
        guidance=0.0,
        replace_legacy_sd_markers=True,
    )
    assert d["width"] == 512
    assert d["height"] == 512
    assert d["steps"] == 9
    assert d["guidance"] == 0.0


def test_explicit_overrides_kept_when_not_legacy():
    d = resolve_stills_defaults(
        "zimage-turbo",
        width=832,
        height=1216,
        steps=12,
        guidance=0.0,
        replace_legacy_sd_markers=True,
    )
    assert d["width"] == 832
    assert d["height"] == 1216
    assert d["steps"] == 12


def test_classic_sd_keeps_512():
    d = resolve_stills_defaults("sd-1.5", width=512, height=512, steps=20, guidance=7.5)
    assert d["family"] == "sd"
    assert d["width"] == 512
    assert d["steps"] == 20
    assert d["guidance"] == 7.5


def test_lone_legacy_step_or_guidance_is_an_explicit_choice():
    """Only the full 512/20/7.5 triple is an unset marker.

    SDXL "Fast" is 20 steps and SDXL "High" is guidance 7.5; a lone match on
    one of those used to be swapped for the family default, so the panel showed
    numbers that never ran.
    """
    fast = resolve_stills_defaults("sd-xl", width=1024, height=1024, steps=20, guidance=6.0)
    assert fast["steps"] == 20
    assert fast["guidance"] == 6.0

    high = resolve_stills_defaults("sd-xl", width=1024, height=1024, steps=35, guidance=7.5)
    assert high["steps"] == 35
    assert high["guidance"] == 7.5

    typed = resolve_stills_defaults("krea2-raw", steps=20)
    assert typed["steps"] == 20


def test_krea_raw_vs_turbo():
    assert model_family("krea2-raw") == "krea2-raw"
    assert model_family("krea2-turbo") == "krea2-turbo"
    raw = resolve_stills_defaults("krea2-raw")
    turbo = resolve_stills_defaults("krea2-turbo")
    assert raw["steps"] == 52 and raw["guidance"] == 3.5
    assert turbo["steps"] == 8 and turbo["guidance"] == 0.0


def test_flux_and_sdxl():
    flux = resolve_stills_defaults("flux-dev")
    assert flux["family"] == "flux"
    assert flux["steps"] == 28
    sdxl = resolve_stills_defaults("sd-xl")
    assert sdxl["family"] == "sdxl"
    assert sdxl["width"] == 1024
    assert sdxl["steps"] == 25


def test_csv_form_merge_via_parse(tmp_path=None):
    from backend.services.batch_image_generator import BatchImageGenerator
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    csv = "prompt,width\nhero on rooftop,\n"
    rows = gen._parse_csv_prompts(
        csv,
        form_model="zimage-turbo",
        form_width=None,
        form_height=None,
        form_steps=None,
        form_guidance=None,
    )
    assert len(rows) == 1
    assert rows[0].model == "zimage-turbo"
    assert rows[0].width == 1024
    assert rows[0].steps == 9
    assert rows[0].guidance == 0.0


@pytest.mark.parametrize("steps, expected", [(4, 8), (None, 9), (12, 12)])
def test_measured_floor(monkeypatch, steps, expected):
    from backend.services.stills_defaults import _FAMILY_DEFAULTS
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 8)
    result = resolve_stills_defaults("zimage-turbo", steps=steps)
    assert result["steps"] == expected
    assert result["steps_requested"] == steps
    assert result["steps_floor"] == 8
    assert result["steps_notice"] == (
        "Z-Image Turbo needs at least 8 steps; raised 4 to 8." if steps == 4 else None
    )


def test_floor_applies_to_family_default(monkeypatch):
    from backend.services.stills_defaults import _FAMILY_DEFAULTS
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 10)
    result = resolve_stills_defaults("zimage-turbo")
    assert result["steps"] == 10
    assert result["steps_requested"] is None
    assert "raised 9 to 10" in result["steps_notice"]


@pytest.mark.parametrize("steps", [4, 20])
def test_typed_steps_survive_floor_and_legacy_markers(monkeypatch, steps):
    from backend.services.stills_defaults import _FAMILY_DEFAULTS
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 24)
    result = resolve_stills_defaults(
        "zimage-turbo", steps=steps, steps_explicit=True,
        width=512, height=512, guidance=7.5,
    )
    assert result["steps"] == result["steps_requested"] == steps
    assert result["steps_notice"] is None


@pytest.mark.parametrize("model", ["zimage-turbo", "sd-xl"])
def test_none_or_missing_floor_keeps_steps(monkeypatch, model):
    from backend.services.stills_defaults import _FAMILY_DEFAULTS
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", None)
    result = resolve_stills_defaults(model, steps=1)
    assert result["steps"] == result["steps_requested"] == 1
    assert result["steps_floor"] is None
    assert result["steps_notice"] is None


def test_zimage_declares_its_measured_floor():
    raised = resolve_stills_defaults("zimage-turbo", steps=1)
    kept = resolve_stills_defaults("zimage-turbo", steps=2)
    assert raised["steps"] == 2 and raised["steps_floor"] == 2
    assert raised["steps_notice"] == "Z-Image Turbo needs at least 2 steps; raised 1 to 2."
    assert kept["steps"] == 2 and kept["steps_notice"] is None


def test_comfyui_selector_carries_zimage_floor():
    # The generic ComfyUI backend resolves to Z-Image first, so it must carry the
    # same floor the family declares — a knobs-free selection cannot render at 1.
    assert model_family("comfyui") == "comfyui"
    raised = resolve_stills_defaults("comfyui", steps=1)
    assert raised["steps_floor"] == 2
    assert raised["steps"] == 2


@pytest.mark.parametrize("explicit, expected", [(False, 8), (True, 4)])
def test_csv_carries_step_provenance(monkeypatch, explicit, expected):
    from backend.services.stills_defaults import _FAMILY_DEFAULTS
    from backend.services.batch_image_generator import BatchImageGenerator
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 8)
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    rows = gen._parse_csv_prompts(
        f"prompt,steps,steps_explicit\na brass key,4,{explicit}\n",
        form_model="zimage-turbo",
    )
    assert rows[0].steps == expected
    assert rows[0].metadata["steps_explicit"] is explicit
    assert rows[0].metadata["steps_requested"] == 4
    assert bool(rows[0].metadata["steps_notice"]) is (not explicit)


def test_batch_builder_preserves_prior_resolution(monkeypatch, tmp_path):
    from backend.services.stills_defaults import _FAMILY_DEFAULTS
    from backend.services.batch_image_generator import BatchImageGenerator
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 8)
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    monkeypatch.setattr(gen, "_generate_batch_id", lambda: "test_batch")
    monkeypatch.setattr(gen, "_create_output_directory", lambda _: tmp_path)
    resolved = resolve_stills_defaults("zimage-turbo", steps=4)
    request = gen.create_batch_from_prompts(["a brass key"], **resolved)
    prompt = request.prompts[0]
    assert prompt.steps == 8
    assert prompt.metadata["steps_requested"] == 4
    assert prompt.metadata["steps_notice"] == resolved["steps_notice"]
    assert prompt.metadata["steps_explicit"] is False



def test_csv_row_floor_notice_wins_over_empty_form_notice(monkeypatch):
    from backend.services.stills_defaults import _FAMILY_DEFAULTS
    from backend.services.batch_image_generator import BatchImageGenerator
    monkeypatch.setitem(_FAMILY_DEFAULTS["zimage"], "min_steps", 8)
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    prompt = gen._parse_csv_prompts(
        "prompt,model\na key,zimage-turbo\n", form_model="sd-xl", form_steps=4,
        form_steps_metadata={"steps_requested": 4, "steps_notice": None},
    )[0]
    assert prompt.steps == 8
    assert prompt.metadata["steps_requested"] == 4
    assert "raised 4 to 8" in prompt.metadata["steps_notice"]
