"""The chat:tool_result "artifact" object describes a file a tool wrote."""
import pytest

import backend.config as cfg
from backend.services.agent_tools import ToolResult
from backend.services.unified_chat_engine import _ARTIFACT_INLINE_MAX_BYTES, _artifact_for_result

ARTIFACT_KEYS = {"filename", "file_type", "size_bytes", "url", "content_truncated"}


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    monkeypatch.setattr(cfg, "OUTPUT_DIR", str(out))
    return out


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestArtifactShape:
    def test_text_file_inside_output_dir(self, output_dir):
        path = _write(output_dir / "files" / "report.MD", "# hello\n")
        res = ToolResult(success=True, output={"content": "# hello\n", "output_path": str(path)})

        artifact = _artifact_for_result(res)

        assert set(artifact) == ARTIFACT_KEYS | {"content"}
        assert artifact["filename"] == "report.MD"
        assert artifact["file_type"] == "md"
        assert artifact["size_bytes"] == len("# hello\n")
        assert artifact["url"] == "/api/outputs/files/report.MD"
        assert artifact["content"] == "# hello\n"
        assert artifact["content_truncated"] is False

    def test_file_path_in_metadata_is_detected(self, output_dir):
        path = _write(output_dir / "csv" / "rows.csv", "a,b\n1,2\n")
        res = ToolResult(success=True, output="wrote 2 rows", metadata={"file_path": str(path)})

        artifact = _artifact_for_result(res)

        assert artifact["url"] == "/api/outputs/csv/rows.csv"
        assert artifact["file_type"] == "csv"

    def test_url_is_null_outside_output_dir(self, output_dir, tmp_path):
        path = _write(tmp_path / "elsewhere" / "notes.txt", "outside")
        res = ToolResult(success=True, output={"output_path": str(path)})

        artifact = _artifact_for_result(res)

        assert artifact["url"] is None
        assert artifact["content"] == "outside"

    def test_large_file_omits_content(self, output_dir):
        path = _write(output_dir / "big.txt", "x" * (_ARTIFACT_INLINE_MAX_BYTES + 1))
        artifact = _artifact_for_result(ToolResult(success=True, output={"output_path": str(path)}))

        assert "content" not in artifact
        assert artifact["content_truncated"] is True
        assert artifact["size_bytes"] == _ARTIFACT_INLINE_MAX_BYTES + 1

    def test_binary_file_has_url_but_no_content(self, output_dir):
        path = output_dir / "images" / "pic.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\x89PNG\x00\x00binary")
        artifact = _artifact_for_result(ToolResult(success=True, output={"output_path": str(path)}))

        assert artifact["url"] == "/api/outputs/images/pic.png"
        assert "content" not in artifact
        assert artifact["content_truncated"] is False

    def test_undecodable_bytes_are_replaced(self, output_dir):
        path = output_dir / "odd.txt"
        path.write_bytes(b"caf\xe9")
        artifact = _artifact_for_result(ToolResult(success=True, output={"output_path": str(path)}))
        assert artifact["content"] == "caf�"


class TestNoArtifact:
    def test_missing_file_gives_none(self, output_dir):
        res = ToolResult(success=True, output={"output_path": str(output_dir / "nope.txt")})
        assert _artifact_for_result(res) is None

    def test_no_path_keys_gives_none(self, output_dir):
        assert _artifact_for_result(ToolResult(success=True, output={"content": "x"})) is None
        assert _artifact_for_result(ToolResult(success=True, output="plain text")) is None

    def test_directory_path_gives_none(self, output_dir):
        assert _artifact_for_result(ToolResult(success=True, output={"output_path": str(output_dir)})) is None
