"""song_polisher: the words stay as typed and every value says where it came from."""
import pytest

from backend.services import song_polisher as sp

LYRICS = """[Intro: Slow, heavy piano chords and brushed drums]

[Verse 1]
Hey babe let's take a ride today,
Got nothin' to lose and nothing to pay,

[Chorus: Voice breaks with emotion]
Hey babe let's take a ride today,
[Fade out with a final, lingering piano chord]"""


def _sung(text):
    return [l for l in text.splitlines() if l.strip() and not sp._TAG_RE.match(l)]


@pytest.fixture
def model_says(monkeypatch):
    def set_answer(answer):
        monkeypatch.setattr(sp, "_ask_model", lambda *a, **k: answer)
    return set_answer


def test_words_are_untouched_and_long_tags_are_shortened(model_says):
    model_says({"bpm": 58, "key": "E minor", "meter": "4/4", "language": "en",
                "tags": {"0": "[Intro - piano]", "2": "[Chorus - emotional]", "3": "[Fade Out]"}})
    out = sp.polish_song("slow desperate blues, female vocal", LYRICS, model="m")

    assert _sung(out["lyrics"]) == _sung(LYRICS)
    assert ["[Chorus: Voice breaks with emotion]", "[Chorus - emotional]"] in out["tag_changes"]
    assert "[Verse 1]" in out["lyrics"]
    assert out["fields"] == {"bpm": 58, "keyscale": "E minor", "timesignature": "4", "vocal_language": "en",
                             "duration_s": sp.song_seconds(3, 58), "audio_sketch": True}
    assert out["set_by"]["duration_s"] == "lyrics length"


def test_a_value_you_set_wins_then_the_description_then_the_model(model_says):
    model_says({"bpm": 120, "key": "A minor", "meter": "3/4"})
    out = sp.polish_song("lo-fi, 84 bpm, in F major", LYRICS, given={"timesignature": "6"}, model="m")

    assert (out["fields"]["bpm"], out["set_by"]["bpm"]) == (84, "description")
    assert (out["fields"]["keyscale"], out["set_by"]["keyscale"]) == ("F major", "description")
    assert (out["fields"]["timesignature"], out["set_by"]["timesignature"]) == ("6", "you")


def test_values_the_model_cannot_use_are_dropped(model_says):
    model_says({"bpm": 900, "key": "H minor", "meter": "7/8", "language": "xx",
                "tags": {"2": "[Bridge - sad]"}})
    out = sp.polish_song("ambient", LYRICS, model="m")

    assert "bpm" not in out["fields"] and "keyscale" not in out["fields"] and "timesignature" not in out["fields"]
    assert out["fields"]["vocal_language"] == "en"
    assert ["[Chorus: Voice breaks with emotion]", "[Chorus]"] in out["tag_changes"]


def test_an_instrumental_gets_no_lyrics_language_or_sketch(model_says):
    model_says({"bpm": 70, "key": "D minor", "meter": "4/4"})
    out = sp.polish_song("epic orchestral trailer", "", instrumental=True, model="m")

    assert out["lyrics"] == "" and out["tag_changes"] == []
    assert out["fields"]["audio_sketch"] is False and "vocal_language" not in out["fields"]


def test_without_the_chat_model_it_says_so(model_says):
    model_says(None)
    out = sp.polish_song("slow blues", LYRICS, model="m")

    assert out["fallback"] is True
    assert "bpm" not in out["fields"] and out["fields"]["audio_sketch"] is True


def test_song_length_follows_the_measured_bars_per_line():
    assert sp.song_seconds(17, 42) == 190
    assert sp.song_seconds(17, 52) == 150
    assert sp.song_seconds(2, 120) == sp.MIN_SECONDS
    assert sp.song_seconds(60, 40) == sp.MAX_SECONDS
