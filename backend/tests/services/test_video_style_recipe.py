"""Style recipes from a measured profile, their prose clause, files, and distance."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.video_style import recipe  # noqa: E402


def _analysis(asl=2.9, cct=3400, sat=0.3, key="low", motion=None, on_beat=0.7):
    motion = motion or {"static": 0.4, "handheld": 0.4, "pan": 0.2}
    shots = []
    t = 0.0
    for i in range(6):
        shots.append({
            "i": i, "t0": t, "t1": t + asl, "dur_s": asl,
            "transition_in": {"type": "cut" if i else "none"}, "transition_out": {"type": "cut"},
            "look": {"key": key, "palette_lab": [[40, 10, 20]], "palette_weights": [1.0]},
            "motion": {"label": "handheld" if i % 2 else "static", "magnitude_mean_w_s": 0.1 * i},
            "scale": {"label": "close_up" if i % 2 else "medium"},
        })
        t += asl
    return {
        "name": "fixture",
        "source": {"sha256": "abc", "duration_s": t},
        "provenance": {"engine": "video_style.measure/0.2", "tools": {"ffmpeg": "8"}, "measured_at": "2026-11-03T00:00:00+00:00"},
        "shots": shots,
        "profile": {
            "cutting": {"shot_count": 6, "asl_s": asl, "msl_s": asl, "shot_len_hist_log2": [0, 0, 0, 0.5, 0.5, 0, 0, 0, 0],
                        "shot_len_hist_edges_s": [0.25, 0.5, 1, 2, 4, 8, 16, 32], "shot_len_1f_alpha": 0.8,
                        "adjacent_len_corr": 0.3, "cut_rate_curve": [0.3] * 10, "transition_mix": {"cut": 1.0}},
            "beat_sync": {"bpm": 124, "frac_cuts_on_beat": on_beat, "frac_cuts_on_beat_chance": 0.25},
            "scale_mix": {"close_up": 0.5, "medium": 0.5},
            "scale_transition_matrix": None, "scale_labels": list(recipe.__dict__.get("SCALE", {})) or None,
            "motion_mix": motion,
            "composition": {"symmetry_mean": 0.7, "center_framed_frac": 0.6},
            "look": {"key_mix": {key: 1.0}, "cct_k_median": cct, "saturation_mean": sat, "contrast_ratio_median": 15,
                     "teal_orange_mean": 0.5, "warmth_b_median": 9.0,
                     "tone_curve_p": {"p1": 4, "p10": 18, "p50": 52, "p90": 168, "p99": 231},
                     "global_palette_lab": [[42, 12, 25], [18, -3, -14]]},
            "energy_curve": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            "audio": {"lufs_i": -16.2, "lra": 9.1, "onset_density": 2.1},
        },
    }


def test_recipe_is_a_superset_of_the_editor_recipe():
    rec = recipe.build_recipe(_analysis(), "Tense trailer")
    for key in ("name", "description", "art_director_bias", "filter_palette", "transition_palette", "global_filter",
                "audio_treatment", "measured"):
        assert key in rec
    assert rec["art_director_bias"]["prefer_energy"] == ["high", "medium"]
    assert rec["art_director_bias"]["prefer_motion"] == ["fast", "medium"]
    assert rec["transition_palette"] == ["hard-cut"]
    assert "warm-tint" in rec["filter_palette"] and "vignette" in rec["filter_palette"]
    assert set(rec["filter_palette"]) <= recipe.FILTER_SLUGS
    s = rec["measured"]
    assert s["pacing"]["target_asl_s"] == 2.9
    assert s["pacing"]["cut_on_beat"]["prob"] == 0.7
    assert s["look_targets"]["cct_k"] == 3400
    assert s["provenance"]["derived_from"] == "abc"
    assert [slot["slot"] for slot in s["shot_list_template"]] == ["open", "body", "climax", "end"]


def test_cool_desaturated_look_picks_other_filters():
    rec = recipe.build_recipe(_analysis(cct=8000, sat=0.05, key="normal"), "Cold")
    assert "cool-tint" in rec["filter_palette"]
    assert "high-contrast-bw" in rec["filter_palette"]
    assert "vignette" not in rec["filter_palette"]


def test_style_clause_reads_the_numbers():
    clause = recipe.style_clause(recipe.build_recipe(_analysis(), "Tense trailer"))
    assert clause.startswith("Cut briskly, about 2.9 s a shot")
    assert "close ups and mediums" in clause
    assert "warm (3400 K)" in clause
    assert "cuts land on the beat (70%)" in clause
    assert clause.endswith("hard cuts.")


def test_style_clause_is_empty_without_measurements():
    assert recipe.style_clause({"name": "x"}) == ""


def test_save_list_load_round_trip(tmp_path):
    rec = recipe.build_recipe(_analysis(), "My Trailer Cut")
    path = recipe.save_recipe(rec, recipes_dir_override=tmp_path)
    assert path.name == "my-trailer-cut.json"
    rows = recipe.list_recipes(recipes_dir_override=tmp_path)
    assert rows[0]["slug"] == "my-trailer-cut" and rows[0]["measured"] is True
    assert recipe.load_recipe("My Trailer Cut", recipes_dir_override=tmp_path)["measured"]["pacing"]["target_asl_s"] == 2.9


def test_hand_written_recipe_is_not_overwritten(tmp_path):
    (tmp_path / "grunge.json").write_text(json.dumps({"name": "Grunge", "filter_palette": ["oldfilm"]}))
    rec = recipe.build_recipe(_analysis(), "Grunge")
    with pytest.raises(FileExistsError):
        recipe.save_recipe(rec, recipes_dir_override=tmp_path)
    recipe.save_recipe(rec, recipes_dir_override=tmp_path, overwrite=True)
    assert "measured" in json.loads((tmp_path / "grunge.json").read_text())


def test_distance_is_zero_to_self_and_grows_with_difference():
    a = _analysis()["profile"]
    same = recipe.distance(a, a)
    assert same["total"] == 0.0
    b = _analysis(asl=8.0, cct=8000, key="high", motion={"static": 1.0}, on_beat=0.1)["profile"]
    b["cutting"]["shot_len_hist_log2"] = [0, 0, 0, 0, 0, 1.0, 0, 0, 0]
    far = recipe.distance(a, b)
    assert 0.2 < far["total"] <= 1.0
    assert far["terms"]["shot_length"] > 0 and far["terms"]["key_mix"] > 0 and far["terms"]["beat_sync"] == pytest.approx(0.6)
    pacing = recipe.distance(a, b, preset="pacing-only")
    assert set(pacing["weights"]) <= set(recipe.DISTANCE_PRESETS["pacing-only"])


def test_distance_skips_terms_a_profile_lacks():
    a = _analysis()["profile"]
    b = dict(a)
    b["beat_sync"] = None
    out = recipe.distance(a, b)
    assert out["terms"]["beat_sync"] is None
    assert "beat_sync" not in out["weights"]
