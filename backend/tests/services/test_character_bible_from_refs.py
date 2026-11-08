"""Unit tests: open vision consensus → bible; LoRA compose omits full bible."""
from __future__ import annotations

from backend.services.character_bible_from_refs import (
    _parse_consensus_json,
    consensus_identity_from_descriptions,
    marks_from_bible,
    merge_identity_tags,
    short_identity_marks,
    tags_to_bible,
)
from backend.services.character_generator_service import _compose_prompt
from backend.services.swarm.agents.character_designer import ShotVariation


def test_parse_consensus_json_batman_style():
    raw = """{
      "class_token": "costumed man",
      "marks": "black cowl with horns, white LED eyes, utility belt, cape, armored suit",
      "bible": "Batman 2: a man in a black bat cowl with pointed horns, glowing white LED eyes, armored suit, utility belt, cape, gloves and boots. Keep this exact appearance in every shot."
    }"""
    parsed = _parse_consensus_json(raw)
    assert parsed["class_token"] == "costumed man"
    assert "cowl" in parsed["marks"].lower()
    assert "LED" in parsed["bible"] or "led" in parsed["bible"].lower()


def test_consensus_identity_prefers_llm_json_over_tag_dump():
    descriptions = [
        "A figure in a black bat-like cowl with pointed horns and glowing white eyes, "
        "wearing armored plating, a utility belt, cape, gloves and boots.",
        "Same armored vigilante costume: black cowl, white eye lenses, spiked gauntlets, "
        "cape and detailed utility belt with pouches.",
        "Full-body black armored suit with cape and bat emblem; white glowing eyes in the cowl.",
    ]

    def fake_llm(*, system, user, model="gemma4:12b"):
        return (
            '{"class_token":"costumed man",'
            '"marks":"black cowl with horns, white LED eyes, utility belt, cape, armor",'
            '"bible":"A costumed man in a black bat cowl with horns, white LED eyes, '
            "armored suit, utility belt, cape, gloves and boots. "
            'Keep this exact appearance in every shot."}'
        )

    out = consensus_identity_from_descriptions(
        descriptions, name="Batman 2", llm=fake_llm,
    )
    assert "cowl" in out["bible"].lower()
    assert "shaved" not in out["bible"].lower()
    assert "none" not in out["marks"].lower()
    assert out["class_token"] == "costumed man"


def test_consensus_identity_white_wolf():
    descriptions = [
        "A large white wolf with thick fur, amber eyes, and a black nose, standing in snow.",
        "White wolf, dense winter coat, yellow-amber eyes, alert ears, bushy white tail.",
        "Arctic white wolf facing camera; pale fur, sharp muzzle, no collar or costume.",
    ]

    def fake_llm(*, system, user, model="gemma4:12b"):
        return (
            '{"class_token":"white wolf",'
            '"marks":"thick white fur, amber eyes, black nose, alert ears, bushy tail",'
            '"bible":"A white wolf with thick pale fur, amber eyes, black nose, '
            "alert ears and a bushy tail. Keep this exact appearance in every shot.\"}"
        )

    out = consensus_identity_from_descriptions(
        descriptions, name="Frost", llm=fake_llm,
    )
    assert "wolf" in out["class_token"].lower()
    assert "fur" in out["marks"].lower() or "fur" in out["bible"].lower()
    assert "man" not in out["class_token"]
    assert "shaved" not in out["bible"].lower()


def test_consensus_fallback_without_llm_json():
    def bad_llm(*, system, user, model="gemma4:12b"):
        raise RuntimeError("ollama down")

    out = consensus_identity_from_descriptions(
        ["A white wolf with thick fur and amber eyes."],
        name="Frost",
        llm=bad_llm,
    )
    assert "wolf" in out["bible"].lower()
    assert "Keep this exact appearance" in out["bible"]


def test_unparseable_consensus_reply_is_not_used_as_bible():
    truncated = (
        '{"class_token":"white wolf","marks":"thick white fur, amber eyes",'
        '"bible":"A white wolf with thick pale fur, amber eyes and a bla'
    )
    assert _parse_consensus_json(truncated) == {}


def test_truncated_consensus_reply_gives_first_description_bible():
    first = "A white wolf with thick fur, amber eyes, and a black nose."
    second = "White wolf, dense winter coat, yellow-amber eyes, bushy tail."

    def truncated_llm(*, system, user, model=None):
        return (
            '{"class_token":"white wolf","marks":"thick white fur, amber eyes",'
            '"bible":"A white wolf with thick pale fur, amber eyes and a bla'
        )

    out = consensus_identity_from_descriptions(
        [first, second], name="Frost", llm=truncated_llm,
    )
    assert out["bible"].startswith(f"Frost: {first}")
    assert "Keep this exact appearance" in out["bible"]
    assert "{" not in out["bible"]
    assert out["class_token"] == ""


_RESOLVER = "backend.services.ollama_chat_model"


def _installed(monkeypatch, tags):
    monkeypatch.setattr(f"{_RESOLVER}.installed_chat_tags", lambda: set(tags))
    monkeypatch.setattr(f"{_RESOLVER}._saved_active_model", lambda: None)
    monkeypatch.setattr(f"{_RESOLVER}._policy_model", lambda: None)


def test_consensus_uses_an_installed_text_model_when_the_preference_is_missing(monkeypatch):
    import ollama
    _installed(monkeypatch, {"qwen3:8b", "llama3.2:1b"})
    monkeypatch.setattr("backend.utils.ollama_resource_manager.think_payload", lambda m: {})
    seen = []

    def chat(*, model, messages, format=None, options=None, **kw):
        seen.append(model)
        return {"message": {"content": (
            '{"class_token":"white wolf","marks":"thick white fur",'
            '"bible":"A white wolf with thick white fur. Keep this exact appearance in every shot."}'
        )}}

    monkeypatch.setattr(ollama, "chat", chat)

    out = consensus_identity_from_descriptions(["A white wolf with thick fur."], name="Frost")

    assert seen and seen[0] != "gemma4:12b"
    assert seen[0] in {"qwen3:8b", "llama3.2:1b"}
    assert out["class_token"] == "white wolf"


def test_consensus_prefers_the_same_family_when_the_exact_tag_is_missing(monkeypatch):
    from backend.services.character_bible_from_refs import resolve_consensus_model
    _installed(monkeypatch, {"gemma4:e4b", "qwen3:8b"})
    assert resolve_consensus_model() == "gemma4:e4b"


def test_bible_rebuild_fails_with_a_reason_when_no_chat_model_is_installed(monkeypatch, tmp_path):
    from PIL import Image
    from backend.services.character_bible_from_refs import (
        NO_CONSENSUS_MODEL,
        rebuild_bible_from_refs,
    )
    _installed(monkeypatch, set())
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)

    class _Analyzer:
        def analyze(self, *a, **k):
            raise AssertionError("vision should not run without a consensus model")

    out = rebuild_bible_from_refs([str(ref)], name="Frost", analyzer=_Analyzer())

    assert out["ok"] is False
    assert out["error"] == NO_CONSENSUS_MODEL
    assert out["bible"] == ""


def test_marks_from_bible():
    marks = marks_from_bible(
        "Frost: thick white fur, amber eyes, bushy tail. "
        "Keep this exact appearance in every shot."
    )
    assert "fur" in marks.lower()
    assert "Keep this" not in marks


def test_legacy_merge_and_tags_to_bible_still_work():
    lists = [
        ["shaved head", "sunglasses", "slim"],
        ["bald", "sunglasses"],
    ]
    merged = merge_identity_tags(lists)
    assert merged
    bible = tags_to_bible(merged, name="Alex")
    assert "Alex:" in bible
    assert short_identity_marks(merged)


def test_compose_prompt_lora_mode_omits_bible():
    v = ShotVariation(
        framing="medium shot",
        expression="neutral",
        lighting="soft key",
        scene="studio backdrop",
    )
    bible = (
        "Alex: short black hair, square glasses, athletic build. "
        "Keep this exact appearance."
    )
    with_bible = _compose_prompt("ohxalex", bible, v, "face-forward", include_bible=True)
    without = _compose_prompt(
        "ohxalex", bible, v, "face-forward",
        include_bible=False, class_token="man", identity_marks="shaved",
    )
    assert "short black hair" in with_bible
    assert "a photo of ohxalex" in without
    assert "short black hair" not in without


def test_compose_prompt_wolf_class_anchor():
    v = ShotVariation(framing="full body", expression="", lighting="daylight", scene="snow")
    prompt = _compose_prompt(
        "frost_tok", "ignored bible", v, "full-body front",
        include_bible=False, class_token="white wolf",
        identity_marks="thick white fur, amber eyes",
    )
    assert "a photo of frost_tok" in prompt
    assert "white wolf" in prompt
    assert "amber eyes" in prompt
    assert "ignored bible" not in prompt


def _place_refs(tmp_path, n=2):
    from PIL import Image
    paths = []
    for i in range(n):
        p = tmp_path / f"street_{i}.png"
        Image.new("RGB", (8, 8)).save(p)
        paths.append(str(p))
    return paths


class _RecordingAnalyzer:
    """Stands in for VisionAnalyzer and keeps every prompt it was asked."""

    def __init__(self, description):
        self.description = description
        self.prompts = []

    def analyze(self, img, prompt, **kw):
        from types import SimpleNamespace
        self.prompts.append(prompt)
        return SimpleNamespace(success=True, description=self.description)


def test_environment_refs_are_described_as_a_place(tmp_path):
    from backend.services.character_bible_from_refs import rebuild_bible_from_refs
    analyzer = _RecordingAnalyzer(
        "A night harbor city of neon-lit towers over wet streets, with holographic koi.")
    seen = {}

    def fake_llm(*, system, user, model=None):
        seen["user"] = user
        return ('{"class_token":"harbor city",'
                '"marks":"neon towers, wet streets, holographic koi, rooftop gardens",'
                '"bible":"A neon harbor city at night. Keep this exact appearance in every shot."}')

    out = rebuild_bible_from_refs(_place_refs(tmp_path), name="Neon Harbor",
                                  analyzer=analyzer, llm=fake_llm, kind="environment")

    assert out["ok"] is True and out["class_token"] == "harbor city"
    assert all("ONE place" in p and "cast character" not in p for p in analyzer.prompts)
    assert "SAME place" in seen["user"] and "white wolf" not in seen["user"]


def test_place_without_a_class_from_consensus_is_not_a_person(tmp_path):
    from backend.services.character_bible_from_refs import rebuild_bible_from_refs

    def no_class_llm(*, system, user, model=None):
        return '{"marks":"neon towers","bible":"A neon city. Keep this exact appearance in every shot."}'

    out = rebuild_bible_from_refs(_place_refs(tmp_path), name="Neon Harbor",
                                  analyzer=_RecordingAnalyzer("A neon city at night."),
                                  llm=no_class_llm, kind="environment")

    assert out["class_token"] == "place"


def test_prop_refs_are_described_as_an_object(tmp_path):
    from backend.services.character_bible_from_refs import rebuild_bible_from_refs
    analyzer = _RecordingAnalyzer("A red two-seat roadster with chrome bumpers.")

    def fake_llm(*, system, user, model=None):
        return ('{"class_token":"sports car","marks":"red paint, chrome bumpers",'
                '"bible":"A red roadster. Keep this exact appearance in every shot."}')

    rebuild_bible_from_refs(_place_refs(tmp_path), name="The Ruby", analyzer=analyzer,
                            llm=fake_llm, kind="prop")

    assert all("ONE prop" in p for p in analyzer.prompts)
