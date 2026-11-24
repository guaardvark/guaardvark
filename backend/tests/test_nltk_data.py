"""The app's private NLTK data folder (backend/nltk_data.py)."""
import os
import stat

import pytest

from backend import nltk_data


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    """A stand-in for llama-index's packaged data, group-writable like a
    virtualenv created under umask 0002."""
    src = tmp_path / "site-packages" / "nltk_cache"
    (src / "corpora" / "stopwords").mkdir(parents=True)
    (src / "corpora" / "stopwords" / "english").write_text("the\nand\n")
    (src / "corpora" / "stopwords.zip").write_bytes(b"zip")
    (src / ".gitignore").write_text("*\n")
    for current, dirs, files in os.walk(src):
        os.chmod(current, 0o775)
        for name in files:
            os.chmod(os.path.join(current, name), 0o664)
    monkeypatch.setattr(nltk_data, "_bundled_cache", lambda: src)
    monkeypatch.setattr(nltk_data, "_installed", None)
    monkeypatch.delenv("NLTK_DATA", raising=False)
    return src


def test_copy_is_private_and_exported(bundled, tmp_path):
    cache = tmp_path / "cache"
    folder = nltk_data.install(str(cache))

    assert folder == str(cache / "nltk_data") == os.environ["NLTK_DATA"]
    assert (cache / "nltk_data" / "corpora" / "stopwords" / "english").read_text() == "the\nand\n"
    if os.name == "posix":
        for current, dirs, files in os.walk(folder):
            assert _mode(current) == 0o700
            for name in files:
                assert _mode(os.path.join(current, name)) == 0o600
    assert sorted(p.name for p in cache.iterdir()) == ["nltk_data"]


def test_user_nltk_data_is_left_alone(bundled, tmp_path, monkeypatch):
    monkeypatch.setenv("NLTK_DATA", "/somewhere/else")

    assert nltk_data.install(str(tmp_path / "cache")) is None
    assert os.environ["NLTK_DATA"] == "/somewhere/else"
    assert not (tmp_path / "cache").exists()


def test_second_call_follows_the_resolved_cache_root(bundled, tmp_path):
    nltk_data.install(str(tmp_path / "first"))
    folder = nltk_data.install(str(tmp_path / "second"))

    assert folder == os.environ["NLTK_DATA"] == str(tmp_path / "second" / "nltk_data")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_loosened_folder_is_tightened_again(bundled, tmp_path):
    folder = nltk_data.install(str(tmp_path / "cache"))
    english = os.path.join(folder, "corpora", "stopwords", "english")
    os.chmod(folder, 0o775)
    os.chmod(english, 0o664)

    nltk_data.install(str(tmp_path / "cache"))

    assert _mode(folder) == 0o700
    assert _mode(english) == 0o600


def test_copy_is_rebuilt_when_the_source_changes(bundled, tmp_path, monkeypatch):
    folder = nltk_data.install(str(tmp_path / "cache"))
    (bundled / "corpora" / "stopwords" / "english").write_text("changed\n")
    monkeypatch.setattr(nltk_data, "_source_id", lambda src: "llama-index-core next\n")

    nltk_data.install(str(tmp_path / "cache"))

    assert open(os.path.join(folder, "corpora", "stopwords", "english")).read() == "changed\n"


def test_missing_bundled_data_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(nltk_data, "_bundled_cache", lambda: None)
    monkeypatch.setattr(nltk_data, "_installed", None)
    monkeypatch.delenv("NLTK_DATA", raising=False)

    assert nltk_data.install(str(tmp_path / "cache")) is None
    assert "NLTK_DATA" not in os.environ


@pytest.mark.skipif(os.name != "posix", reason="NLTK enforces this on POSIX only")
def test_nltk_reads_the_real_copy(tmp_path, monkeypatch):
    """With the data llama-index really ships: NLTK accepts the private copy."""
    if nltk_data._bundled_cache() is None:
        pytest.skip("llama-index-core's bundled NLTK data is not installed")
    nltk = pytest.importorskip("nltk")
    monkeypatch.setattr(nltk_data, "_installed", None)
    monkeypatch.delenv("NLTK_DATA", raising=False)

    folder = nltk_data.install(str(tmp_path / "cache"))

    assert nltk.data.find("corpora/stopwords", paths=[folder])
    assert nltk.data.find("tokenizers/punkt_tab", paths=[folder])
