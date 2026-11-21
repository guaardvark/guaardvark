"""Kokoro reads only local files and only catalog voices.

A voice pack that is not cached, or spaCy's English model missing from the
venv, is refused with the Manage-models hint instead of being downloaded. A
Japanese or Mandarin voice without its language pack is listed, and refused
with "Install <language> first". A
voice id outside backends/kokoro_voices.json never reaches KPipeline, which
would torch.load a value ending in '.pt' and split one containing commas. The
kokoro package and the HF cache are not touched.
"""
from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from backends import kokoro_voices  # noqa: E402
from backends import voice_gen_kokoro as vgk  # noqa: E402
from backends.hub_weights import WeightsNotInstalled  # noqa: E402


@pytest.fixture
def kokoro(tmp_path):
    return vgk.KokoroBackend(output_root=tmp_path)


def test_catalog_voice_resolves_to_its_cached_path(kokoro, monkeypatch):
    seen = []

    def fake_cached(repo, filename):
        seen.append((repo, filename))
        return f"/cache/{filename}"

    monkeypatch.setattr(vgk, "cached_hub_file", fake_cached)
    assert kokoro._voice_path("bf_emma") == "/cache/voices/bf_emma.pt"
    assert seen == [("hexgrad/Kokoro-82M", "voices/bf_emma.pt")]


def test_uncached_voice_says_install(kokoro, monkeypatch):
    monkeypatch.setattr(vgk, "cached_hub_file", lambda repo, filename: None)
    with pytest.raises(WeightsNotInstalled, match="Manage models"):
        kokoro._voice_path("bf_emma")


@pytest.mark.parametrize("voice", ["/etc/passwd.pt", "voices/af_heart.pt", "af_heart,af_bella",
                                   "xyz", "", "AF_HEART"])
def test_non_catalog_voice_is_refused_before_any_lookup(kokoro, monkeypatch, voice):
    monkeypatch.setattr(vgk, "cached_hub_file",
                        lambda *a: pytest.fail("cache looked up for a rejected voice"))
    with pytest.raises(kokoro_voices.UnknownVoice):
        kokoro._voice_path(voice)


def test_english_voices_need_the_spacy_model(monkeypatch):
    real = metadata.distribution

    def hidden(name):
        if name == kokoro_voices.english_g2p()["package"]:
            raise metadata.PackageNotFoundError(name)
        return real(name)

    monkeypatch.setattr(metadata, "distribution", hidden)
    for lang in ("a", "b"):
        with pytest.raises(WeightsNotInstalled, match="en_core_web_sm"):
            vgk.KokoroBackend._require_language(lang)
    for lang in ("e", "f", "h", "i", "p"):  # espeak-ng, which comes with kokoro
        vgk.KokoroBackend._require_language(lang)


def _hide_packages(monkeypatch, hidden_names):
    real = metadata.distribution

    def hidden(name):
        if name in hidden_names:
            raise metadata.PackageNotFoundError(name)
        return real(name)

    monkeypatch.setattr(metadata, "distribution", hidden)


def _pack_packages():
    return {name for pack in kokoro_voices.load_catalog()["language_packs"].values()
            for name in pack["packages"]}


@pytest.mark.parametrize("lang, language", [("j", "Japanese"), ("z", "Mandarin Chinese")])
def test_a_language_without_its_pack_says_install_it_first(monkeypatch, lang, language):
    _hide_packages(monkeypatch, _pack_packages())
    with pytest.raises(WeightsNotInstalled, match=f"Install {language} first") as refused:
        vgk.KokoroBackend._require_language(lang)
    assert "Manage models" in str(refused.value)
    assert kokoro_voices.language_pack(lang)["name"] in str(refused.value)


def test_every_language_pack_is_a_pinned_requirements_file():
    cat = kokoro_voices.load_catalog()
    assert {v.get("pack") for v in cat["languages"].values()} - {None} == set(cat["language_packs"])
    for pack in cat["language_packs"].values():
        lines = (PLUGIN_ROOT / pack["requirements"]).read_text().splitlines()
        pinned = [ln.split("==")[0] for ln in lines if "==" in ln and not ln.startswith(("#", " "))]
        assert sorted(pinned) == sorted(pack["packages"])
        # --require-hashes refuses a file where any requirement lacks one.
        entries = "\n".join(ln for ln in lines if not ln.startswith("#")).split("\n\n")[0]
        for block in entries.replace("\\\n", " ").splitlines():
            assert "--hash=sha256:" in block, block


def test_the_catalog_covers_nine_languages_and_leaves_out_withheld_voices():
    cat = kokoro_voices.load_catalog()
    ids = kokoro_voices.voice_ids()
    assert len(ids) == len(set(ids)) == 53
    assert {g["lang"] for g in cat["groups"]} == set(cat["languages"]) == set("abefhijpz")
    for entry in cat["withheld"]:
        assert entry["id"] not in ids and entry["reason"] and entry["measured"]
        with pytest.raises(kokoro_voices.UnknownVoice):
            kokoro_voices.check_voice_id(entry["id"])
    for group in cat["groups"]:
        for voice in group["voices"]:
            assert len(voice["sha256"]) == 64


def test_long_text_is_cut_at_sentences_and_never_over_the_limit():
    count = len  # one phoneme a character
    sentences = ["あ" * 120 + "。", "い" * 120 + "。", "う" * 30 + "。", "え" * 30 + "。"]
    pieces = vgk.fit_to_phoneme_limit("".join(sentences), count, limit=510, pack=200)
    # Whole sentences, joined only while they stay within the packing size.
    assert pieces == [sentences[0], sentences[1] + sentences[2] + sentences[3]]
    assert "".join(pieces) == "".join(sentences)


def test_a_sentence_over_the_limit_is_cut_at_clauses_then_anywhere():
    count = len
    clauses = "か" * 300 + "、" + "き" * 300 + "。"
    pieces = vgk.fit_to_phoneme_limit(clauses, count, limit=510, pack=200)
    assert pieces == ["か" * 300 + "、", "き" * 300 + "。"]
    unbroken = "x" * 1300
    pieces = vgk.fit_to_phoneme_limit(unbroken, count, limit=510, pack=200)
    assert "".join(pieces) == unbroken and all(len(p) <= 510 for p in pieces)


def test_english_text_goes_to_the_pipeline_whole_and_other_languages_in_pieces():
    class _Pipeline:
        def g2p(self, piece):
            return piece, None

    text = "Uno. " * 150
    assert vgk.KokoroBackend._text_for(_Pipeline(), "a", text) == text
    pieces = vgk.KokoroBackend._text_for(_Pipeline(), "e", text)
    assert isinstance(pieces, list) and len(pieces) > 1
    assert all(len(p) <= kokoro_voices.pack_phonemes() for p in pieces)


def test_catalog_ids_match_the_voice_id_shape():
    ids = kokoro_voices.voice_ids()
    assert ids and all(kokoro_voices.VOICE_ID_PATTERN.match(v) for v in ids)
    assert kokoro_voices.default_voice() in ids
    for v in ids:
        assert vgk.KokoroBackend._lang_code_for(v) == v[0]


def test_voices_route_marks_installed_packs_and_sends_no_cors(monkeypatch):
    from fastapi.testclient import TestClient
    from service.app import app

    monkeypatch.setattr("backends.hub_weights.cached_hub_file",
                        lambda repo, f: "/cache/x" if f == "voices/af_heart.pt" else None)
    r = TestClient(app, base_url="http://127.0.0.1:8206").get("/voices", headers={"Origin": "http://elsewhere.example"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
    voices = {v["id"]: v["installed"] for g in r.json()["kokoro"]["groups"] for v in g["voices"]}
    assert list(voices) == kokoro_voices.voice_ids()
    assert voices["af_heart"] is True and voices["bf_emma"] is False


def test_voices_route_lists_a_language_that_needs_installing(monkeypatch):
    from fastapi.testclient import TestClient
    from service.app import app

    _hide_packages(monkeypatch, _pack_packages())
    monkeypatch.setattr("backends.hub_weights.cached_hub_file", lambda repo, f: "/cache/x")
    groups = TestClient(app, base_url="http://127.0.0.1:8206").get("/voices").json()["kokoro"]["groups"]
    by_label = {g["label"]: g for g in groups}

    japanese = by_label["Japanese Female"]
    assert japanese["language"] == "Japanese" and japanese["language_installed"] is False
    assert japanese["install"] == {"id": "kokoro-japanese", "name": "Kokoro Japanese"}
    # Listed, with its voice pack on disk, and marked as needing the language.
    assert [v["id"] for v in japanese["voices"]][0] == "jf_alpha"
    assert all(v["installed"] and v["language_installed"] is False for v in japanese["voices"])

    french = by_label["French Female"]
    assert french["language_installed"] is True and french["install"] is None


def test_a_voice_whose_language_is_not_installed_is_refused_before_a_job(monkeypatch):
    from fastapi.testclient import TestClient
    from service.app import app

    _hide_packages(monkeypatch, _pack_packages())
    r = TestClient(app, base_url="http://127.0.0.1:8206").post(
        "/generate/voice", json={"text": "こんにちは", "voice_id": "jf_alpha"})
    assert r.status_code == 409
    assert "Install Japanese first" in r.json()["detail"]
