"""Generic .env key read/write used by the profile switch and the Ollama policy."""

from __future__ import annotations

import pytest

from backend import profiles as P


def test_set_and_read_round_trip_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SECRET_KEY=abc\nGUAARDVARK_PROFILE=creator\n")
    P.set_env_value("GUAARDVARK_OLLAMA_KEEP_RUNNING", "1", tmp_path)
    assert P.read_env_value("GUAARDVARK_OLLAMA_KEEP_RUNNING", tmp_path) == "1"
    assert env.read_text() == "SECRET_KEY=abc\nGUAARDVARK_PROFILE=creator\nGUAARDVARK_OLLAMA_KEEP_RUNNING=1\n"


def test_replace_and_remove(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nGUAARDVARK_OLLAMA_EXTERNAL=1\nB=2\n")
    P.set_env_value("GUAARDVARK_OLLAMA_EXTERNAL", "0", tmp_path)
    assert env.read_text() == "A=1\nGUAARDVARK_OLLAMA_EXTERNAL=0\nB=2\n"
    P.set_env_value("GUAARDVARK_OLLAMA_EXTERNAL", None, tmp_path)
    assert env.read_text() == "A=1\nB=2\n"
    assert P.read_env_value("GUAARDVARK_OLLAMA_EXTERNAL", tmp_path) is None


def test_creates_the_file_and_terminates_a_missing_newline(tmp_path):
    env = tmp_path / ".env"
    P.set_env_value("X_KEY", "v", tmp_path)
    assert env.read_text() == "X_KEY=v\n"
    env.write_text("A=1")
    P.set_env_value("X_KEY", "v", tmp_path)
    assert env.read_text() == "A=1\nX_KEY=v\n"


def test_rejects_bad_keys_and_multiline_values(tmp_path):
    with pytest.raises(ValueError):
        P.set_env_value("bad key", "1", tmp_path)
    with pytest.raises(ValueError):
        P.set_env_value("GOOD_KEY", "a\nb", tmp_path)


def test_profile_writer_still_works_through_the_generic_path(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "available_profiles", lambda root=None: {"creator": ("builtin", None)})
    P.set_configured_name("creator", tmp_path)
    assert P.configured_name(tmp_path) == "creator"
