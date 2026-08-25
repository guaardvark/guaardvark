"""FLUX batch must not render N copies of the same image.

Regression cover for the hardcoded ``seed=42`` in the Comfy/FLUX batch branch:
with no per-prompt seed every slot in a batch ran at 42, and because FLUX is
deterministic that produced identical pixels N times at full GPU cost.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.services.batch_image_generator import (
    SEED_SPACE,
    BatchImageGenerator,
    BatchPrompt,
    resolve_image_seed,
)


def _prompt(pid: str, seed=None) -> BatchPrompt:
    return BatchPrompt(id=pid, prompt="a lighthouse in fog", seed=seed)


def test_slots_in_one_batch_get_different_seeds():
    seeds = {resolve_image_seed(_prompt(f"prompt_{i}"), "batch-abc") for i in range(1, 33)}
    assert len(seeds) == 32, "every batch slot must render at a distinct seed"


def test_never_returns_the_old_hardcoded_42():
    seeds = [resolve_image_seed(_prompt(f"prompt_{i}"), "batch-abc") for i in range(1, 200)]
    assert seeds.count(42) == 0


def test_explicit_seed_is_honoured_verbatim():
    # Deterministic rerun is a supported workflow; never override an explicit seed.
    assert resolve_image_seed(_prompt("p1", seed=1234), "batch-abc") == 1234
    assert resolve_image_seed(_prompt("p1", seed=42), "batch-abc") == 42


def test_same_slot_same_batch_is_stable():
    a = resolve_image_seed(_prompt("prompt_3"), "batch-abc")
    b = resolve_image_seed(_prompt("prompt_3"), "batch-abc")
    assert a == b, "a batch must be reproducible from its id"


def test_new_batch_id_is_a_real_reroll():
    a = resolve_image_seed(_prompt("prompt_1"), "batch-abc")
    b = resolve_image_seed(_prompt("prompt_1"), "batch-xyz")
    assert a != b, "re-running a batch must produce fresh images"


def test_missing_batch_id_still_varies():
    # Ad-hoc calls with no batch to key on must not collapse to a constant.
    seeds = {resolve_image_seed(_prompt("prompt_1")) for _ in range(32)}
    assert len(seeds) > 1


def test_seeds_stay_in_range():
    for i in range(500):
        s = resolve_image_seed(_prompt(f"prompt_{i}"), "batch-abc")
        assert 0 <= s < SEED_SPACE


def test_flux_branch_passes_and_reports_the_derived_seed():
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    prompt = _prompt("prompt_1")
    fake = MagicMock()
    fake.generate_image.return_value = "/tmp/out.png"
    with patch(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator",
        return_value=fake,
    ):
        result = gen._generate_with_comfy_flux(prompt, "batch-abc")

    expected = resolve_image_seed(prompt, "batch-abc")
    assert fake.generate_image.call_args.kwargs["seed"] == expected
    assert expected != 42
    # seed_used previously reported prompt.seed (None) while rendering at 42.
    assert result.success and result.seed_used == expected


def test_flux_branch_reports_explicit_seed_unchanged():
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    prompt = _prompt("prompt_1", seed=777)
    fake = MagicMock()
    fake.generate_image.return_value = "/tmp/out.png"
    with patch(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator",
        return_value=fake,
    ):
        result = gen._generate_with_comfy_flux(prompt, "batch-abc")

    assert fake.generate_image.call_args.kwargs["seed"] == 777
    assert result.seed_used == 777
