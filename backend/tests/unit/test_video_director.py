"""video_director is a shim over director_service.plan (since 2026-06-23).

These pin the shim's contract — never raises, count-preserving, exactly N shots —
by patching ``director_service.plan``, the seam it actually calls. (They used to
patch ``vd._chat_json``, a helper the shim never invoked; the mocks never fired.)
"""
from backend.services import director_service as ds
from backend.services import video_director as vd


def _result(prompts):
    return ds.DirectorResult(
        treatment=None,
        shots=[ds.ShotPrompt(prompt=p, index=i) for i, p in enumerate(prompts)],
    )


def _plan_returning(prompts):
    return lambda brief: _result(prompts)


# ---- direct_prompts: never raises, count-preserving ------------------------------

def test_direct_prompts_uses_director_when_available(monkeypatch):
    monkeypatch.setattr(ds, "plan", _plan_returning(["DIRECTED one", "DIRECTED two"]))
    assert vd.direct_prompts(["one", "two"], style="noir") == ["DIRECTED one", "DIRECTED two"]


def test_direct_prompts_count_invariant_on_empty_director(monkeypatch):
    # Director returns nothing -> every item falls back, count preserved.
    monkeypatch.setattr(ds, "plan", _plan_returning([]))
    monkeypatch.setattr(vd, "_light_fallback", lambda p, s: f"FB:{p}")
    assert vd.direct_prompts(["a", "b", "c"], style="x") == ["FB:a", "FB:b", "FB:c"]


def test_direct_prompts_partial_response_fills_gaps(monkeypatch):
    # Director only returned one of two -> the second falls back, count preserved.
    monkeypatch.setattr(ds, "plan", _plan_returning(["only first"]))
    monkeypatch.setattr(vd, "_light_fallback", lambda p, s: f"FB:{p}")
    assert vd.direct_prompts(["a", "b"]) == ["only first", "FB:b"]


def test_direct_prompts_blank_shot_falls_back(monkeypatch):
    monkeypatch.setattr(ds, "plan", _plan_returning(["  ", "real"]))
    monkeypatch.setattr(vd, "_light_fallback", lambda p, s: f"FB:{p}")
    assert vd.direct_prompts(["a", "b"]) == ["FB:a", "real"]


def test_direct_prompts_empty_input():
    assert vd.direct_prompts([]) == []


def test_direct_prompt_single(monkeypatch):
    monkeypatch.setattr(ds, "plan", _plan_returning(["cinematic version"]))
    assert vd.direct_prompt("plain", style="s") == "cinematic version"


def test_never_raises_when_director_throws(monkeypatch):
    def boom(brief):
        raise RuntimeError("director exploded")

    monkeypatch.setattr(ds, "plan", boom)
    monkeypatch.setattr(vd, "_light_fallback", lambda p, s: f"FB:{p}")
    assert vd.direct_prompts(["a"]) == ["FB:a"]


# ---- storyboard_from_concept: always exactly N -------------------------------------

def test_storyboard_returns_exactly_n(monkeypatch):
    monkeypatch.setattr(ds, "plan", _plan_returning(["s1", "s2", "s3"]))
    assert vd.storyboard_from_concept("a lighthouse at dawn", 3) == ["s1", "s2", "s3"]


def test_storyboard_pads_short_response(monkeypatch):
    monkeypatch.setattr(ds, "plan", _plan_returning(["s1"]))
    monkeypatch.setattr(vd, "_light_fallback", lambda p, s: f"FB:{p}")
    out = vd.storyboard_from_concept("concept", 3)
    assert out == ["s1", "FB:concept (shot 2 of 3)", "FB:concept (shot 3 of 3)"]


def test_storyboard_truncates_long_response(monkeypatch):
    monkeypatch.setattr(ds, "plan", _plan_returning(["s1", "s2", "s3", "s4", "s5"]))
    assert vd.storyboard_from_concept("concept", 2) == ["s1", "s2"]


def test_storyboard_empty_concept():
    assert vd.storyboard_from_concept("", 3) == []


def test_storyboard_never_raises_when_director_throws(monkeypatch):
    def boom(brief):
        raise RuntimeError("director exploded")

    monkeypatch.setattr(ds, "plan", boom)
    monkeypatch.setattr(vd, "_light_fallback", lambda p, s: f"FB:{p}")
    assert vd.storyboard_from_concept("c", 2) == ["FB:c (shot 1 of 2)", "FB:c (shot 2 of 2)"]
