"""Filter catalog tests — each preset emits the expected MLT service."""

from __future__ import annotations

from lxml import etree

from mlt import filters
from mlt.frame_math import FrameRate


def _new_chain():
    return etree.Element("chain", attrib={"id": "test"})


def _filter_services(chain) -> list[str]:
    """Return mlt_service of every <filter> attached to chain."""
    out = []
    for f in chain.findall("filter"):
        for p in f.findall("property"):
            if p.get("name") == "mlt_service":
                out.append(p.text)
    return out


def test_none_is_noop():
    chain = _new_chain()
    filters.apply_filter(chain, "none", duration_frames=60, fps=FrameRate(30))
    assert chain.findall("filter") == []


def test_unknown_preset_is_skipped():
    chain = _new_chain()
    filters.apply_filter(chain, "nonsense", duration_frames=60, fps=FrameRate(30))
    assert chain.findall("filter") == []


def test_sepia_emits_sepia_service():
    chain = _new_chain()
    filters.apply_filter(chain, "sepia", duration_frames=60, fps=FrameRate(30))
    assert "sepia" in _filter_services(chain)


def test_high_contrast_bw_emits_grayscale_and_lift_gamma_gain():
    chain = _new_chain()
    filters.apply_filter(chain, "high-contrast-bw", duration_frames=60, fps=FrameRate(30))
    services = _filter_services(chain)
    assert "grayscale" in services
    assert "lift_gamma_gain" in services


def test_vertigo_uses_affine_with_keyframes():
    chain = _new_chain()
    filters.apply_filter(chain, "vertigo", duration_frames=60, fps=FrameRate(30))
    f = chain.findall("filter")[0]
    geom_props = [p for p in f.findall("property") if p.get("name") == "transition.geometry"]
    assert geom_props
    geom = geom_props[0].text
    # vertigo keyframes: start at 0, mid-point zoom, return to 0
    assert geom.startswith("0=0/0:100%x100%")
    assert "120%" in geom  # mid-zoom


def test_all_presets_callable():
    """Round-trip every registered preset to catch typos in PRESETS keys."""
    for slug in filters.PRESETS:
        chain = _new_chain()
        filters.apply_filter(chain, slug, duration_frames=60, fps=FrameRate(30))
        # 'none' emits nothing; everything else emits ≥1 filter
        if slug == "none":
            assert chain.findall("filter") == []
        else:
            assert chain.findall("filter"), f"preset {slug} emitted no filters"


def test_categories_cover_all_non_none_presets():
    """Every preset slug except 'none' must appear in PRESET_CATEGORIES."""
    categorized = {s for slugs in filters.PRESET_CATEGORIES.values() for s in slugs}
    expected = set(filters.PRESETS.keys()) - {"none"}
    missing = expected - categorized
    assert not missing, f"presets not in any category: {missing}"


def test_list_presets_returns_categories():
    cat = filters.list_presets()
    assert "Color" in cat and "sepia" in cat["Color"]
    assert "Motion" in cat and "vertigo" in cat["Motion"]
