"""Client configs the MCP installer and doctor read but did not write."""
import json

import pytest

from backend.mcp import doctor, installer


def test_doctor_reads_an_entry_with_null_args():
    obj = {doctor.SERVER_NAME: {"command": "guaardvark", "args": None}}
    assert list(doctor._scan_mcp_servers_obj(obj)) == [("guaardvark", [])]
    zed = {doctor.SERVER_NAME: {"command": {"path": "guaardvark", "args": None}}}
    assert list(doctor._scan_mcp_servers_obj(zed)) == [("guaardvark", [])]


def test_config_with_comments_is_named_and_left_untouched(tmp_path):
    path = tmp_path / "settings.json"
    original = '// Zed settings\n{"theme": "One Dark"}\n'
    path.write_text(original)
    with pytest.raises(ValueError) as exc:
        installer._merge_json_config(path, lambda data: None, dry_run=False)
    assert str(path) in str(exc.value)
    assert "python -m backend.mcp config --client" in str(exc.value)
    assert path.read_text() == original
