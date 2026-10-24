"""One table decides what a file is: source, prose, media or build output."""
from backend.utils import file_kinds as fk


def test_kinds_by_name():
    assert fk.kind_of("service/app.py") == fk.CODE
    assert fk.kind_of("web/app.js") == fk.CODE
    assert fk.kind_of("Dockerfile") == fk.CODE
    assert fk.kind_of("README.md") == fk.PROSE
    assert fk.kind_of("config.yaml") == fk.PROSE
    assert fk.kind_of("notes.txt") == fk.PROSE
    assert fk.kind_of("Images/render.PNG") == fk.MEDIA
    assert fk.kind_of("song.wav") == fk.MEDIA
    assert fk.kind_of("tests/__pycache__/test_x.cpython-312.pyc") == fk.BUILD_ARTIFACT
    assert fk.kind_of("node_modules/pkg/index.js") == fk.BUILD_ARTIFACT
    assert fk.kind_of("venv/lib/site.py") == fk.BUILD_ARTIFACT
    assert fk.kind_of("Main.class") == fk.BUILD_ARTIFACT


def test_code_file_flag_keeps_markup_and_markdown():
    assert fk.flags_as_code_file("index.html")
    assert fk.flags_as_code_file("package.json")
    assert fk.flags_as_code_file("README.md")
    assert fk.flags_as_code_file("main.go")
    assert not fk.flags_as_code_file("notes.txt")
    assert not fk.flags_as_code_file("photo.jpg")
    assert not fk.flags_as_code_file("Main.class")


def test_binary_content_is_never_read_as_text(tmp_path):
    text_file = tmp_path / "notes.txt"
    text_file.write_text("plain words\n")
    disguised = tmp_path / "data.txt"
    disguised.write_bytes(b"RIFF\x00\x00WAVEfmt ")
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    assert not fk.never_read_as_text(text_file)
    assert fk.never_read_as_text(disguised)
    assert fk.never_read_as_text(image)
    assert fk.is_image(image)
    assert not fk.is_image(tmp_path / "song.wav")
