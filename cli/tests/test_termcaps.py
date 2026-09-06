from llx.media_preview import extract_media_path
from llx.statusline import render
from llx.termcaps import detect, detect_graphics, detect_terminal_name, tmux_hints


def test_detect_kitty(monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "1")
    monkeypatch.setenv("TERM", "xterm-kitty")
    monkeypatch.delenv("TMUX", raising=False)
    assert detect_terminal_name() == "kitty"
    assert detect_graphics() == "kitty"
    caps = detect()
    assert caps.graphics == "kitty"


def test_detect_iterm(monkeypatch):
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert detect_terminal_name() == "iterm"
    assert detect_graphics() == "iterm"


def test_tmux_hints_nonempty():
    hints = tmux_hints()
    assert any("passthrough" in h for h in hints)
    assert any("RGB" in h for h in hints)


def test_extract_media_path():
    assert extract_media_path({"path": "/tmp/a.png"}) == "/tmp/a.png"
    assert extract_media_path({"audio_url": "/media/x.wav"}, "http://localhost:5000") == (
        "http://localhost:5000/media/x.wav"
    )
    assert extract_media_path({}) is None


def test_statusline_render():
    line = render(
        ["model", "gpu", "jobs", "git", "cwd"],
        {"model": "gemma4", "gpu": "62% VRAM", "jobs": 2, "git": "main*", "cwd": "cli"},
    )
    assert "gemma4" in line
    assert "62% VRAM" in line
    assert "2 jobs" in line
    assert "main*" in line
    assert render(["jobs"], {"jobs": 0}) == ""
