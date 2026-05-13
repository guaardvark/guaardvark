"""Transition catalog tests."""

from __future__ import annotations

from lxml import etree

from mlt import transitions
from mlt.frame_math import FrameRate


def test_hard_cut_emits_nothing():
    parent = etree.Element("tractor")
    transitions.emit_transition(parent, "hard-cut", 0, 30, 0, 1, FrameRate(30))
    assert parent.findall("transition") == []


def test_unknown_transition_falls_back_to_hard_cut():
    parent = etree.Element("tractor")
    transitions.emit_transition(parent, "nonsense", 0, 30, 0, 1, FrameRate(30))
    assert parent.findall("transition") == []


def test_cross_dissolve_emits_luma_with_no_resource():
    parent = etree.Element("tractor")
    transitions.emit_transition(parent, "cross-dissolve", 0, 12, 0, 1, FrameRate(30))
    t = parent.findall("transition")
    assert len(t) == 1
    props = {p.get("name"): p.text for p in t[0].findall("property")}
    assert props["mlt_service"] == "luma"
    assert props["a_track"] == "0"
    assert props["b_track"] == "1"
    # No resource = linear cross-fade
    assert "resource" not in props or props.get("resource") in (None, "")


def test_luma_circle_uses_circle_pgm():
    parent = etree.Element("tractor")
    transitions.emit_transition(parent, "luma-circle", 0, 15, 0, 1, FrameRate(30))
    t = parent.findall("transition")[0]
    props = {p.get("name"): p.text for p in t.findall("property")}
    assert "luma13.pgm" in props.get("resource", "")


def test_overlap_frames_scales_with_fps():
    # cross-dissolve = 0.4s. At 30fps = 12 frames. At 60fps = 24 frames.
    spec = transitions.get("cross-dissolve")
    assert spec.overlap_frames(FrameRate(30)) == 12
    assert spec.overlap_frames(FrameRate(60)) == 24


def test_all_transitions_get_returns_spec():
    for slug in transitions.PRESETS:
        spec = transitions.get(slug)
        assert spec.slug == slug
