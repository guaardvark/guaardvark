"""Stated tempo, key, meter and language are locked; everything else is left for the planner."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from backends.music_prompt_facts import (  # noqa: E402
    extract_music_facts,
    require_bpm,
    resolve_accepted_params,
    resolve_plan_params,
)


def test_prose_locks_bpm_key_meter_and_language_and_ignores_vocal_gender():
    facts = extract_music_facts(
        "a quiet Tuesday-night song about missing a train, male vocal, 84 bpm, in A minor, 6/8, English"
    )
    assert facts["bpm"] == 84
    assert facts["keyscale"] == "A minor"
    assert facts["timesignature"] == "6"
    assert facts["vocal_language"] == "en"
    assert "instrumental" not in facts


def test_short_keys_and_words_that_are_not_keys():
    assert extract_music_facts("Am ballad")["keyscale"] == "A minor"
    assert extract_music_facts("f#m groove")["keyscale"] == "F# minor"
    assert extract_music_facts("Bb major, brass")["keyscale"] == "Bb major"
    assert "keyscale" not in extract_music_facts("I am leaving on a train")
    assert "bpm" not in extract_music_facts("the year 1990 was loud")
    assert "bpm" not in extract_music_facts("1200 bpm is not a tempo")
    assert "timesignature" not in extract_music_facts("played in 5/4")


def test_language_adjectives_and_articles_are_not_locked():
    assert extract_music_facts("Spanish guitar, English vocals")["vocal_language"] == "en"
    assert "vocal_language" not in extract_music_facts("French horn and strings")
    assert "vocal_language" not in extract_music_facts("German techno at 130 bpm")
    assert "vocal_language" not in extract_music_facts("add some polish")
    assert extract_music_facts("Polish lyrics, slow")["vocal_language"] == "pl"
    assert extract_music_facts("a love song sung in Japanese")["vocal_language"] == "ja"
    assert "keyscale" not in extract_music_facts("a major hit")
    assert "keyscale" not in extract_music_facts("a minor change of plan")
    assert extract_music_facts("in a B minor ballad")["keyscale"] == "B minor"
    assert extract_music_facts("a b minor tune")["keyscale"] == "B minor"
    assert extract_music_facts("ab minor drone")["keyscale"] == "Ab minor"


def test_explicit_field_wins_over_the_description():
    resolved, locks = resolve_plan_params({
        "style_prompt": "train song, 84 bpm, A minor",
        "bpm": 100,
    })
    assert resolved["bpm"] == 100
    assert locks["keyscale"] == "A minor"
    assert "bpm" not in locks


def test_description_fills_a_missing_field_and_the_switch_wins():
    resolved, locks = resolve_plan_params({
        "style_prompt": "no vocals, 90 bpm",
        "instrumental_only": False,
    })
    assert resolved["bpm"] == 90
    assert resolved["instrumental_only"] is False
    assert locks == {"bpm": 90}

    filled, filled_locks = resolve_plan_params({"style_prompt": "instrumental piano, 90 bpm"})
    assert filled["instrumental_only"] is True
    assert filled_locks["instrumental"] is True


def test_an_explicit_value_outside_the_model_range_is_rejected():
    with pytest.raises(ValueError, match="30 and 300"):
        require_bpm(20)
    with pytest.raises(ValueError, match="major or minor"):
        resolve_accepted_params({"keyscale": "funky"})
    with pytest.raises(ValueError, match="2/4"):
        resolve_accepted_params({"timesignature": "5/4"})


def test_an_accepted_card_is_not_reparsed_from_its_caption():
    accepted = resolve_accepted_params({
        "style_prompt": "A quiet song at 84 bpm in A minor",
        "bpm": 72,
        "keyscale": "C major",
        "timesignature": "4/4",
        "vocal_language": "English",
    })
    assert accepted["bpm"] == 72
    assert accepted["keyscale"] == "C major"
    assert accepted["timesignature"] == "4"
    assert accepted["vocal_language"] == "en"
