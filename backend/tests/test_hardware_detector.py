import json
from unittest.mock import patch, MagicMock
from backend.services.hardware_detector import HardwareDetector


def test_detect_returns_expected_schema():
    d = HardwareDetector()
    profile = d.detect()
    for key in ("node_id", "hostname", "os", "kernel", "arch",
                "master_eligible", "cpu", "ram", "gpu", "disk",
                "services", "generated_at"):
        assert key in profile, f"missing {key}"
    assert profile["arch"] in ("x86_64", "aarch64", "arm64")
    assert isinstance(profile["master_eligible"], bool)
    assert isinstance(profile["services"], dict)


def test_gpu_probe_none_when_no_tools():
    d = HardwareDetector()
    with patch("subprocess.run", side_effect=FileNotFoundError()), \
         patch("shutil.which", return_value=None):
        gpu = d._probe_gpu()
    assert gpu == {"vendor": "none"}


def test_gpu_probe_nvidia_parses_smi_output():
    smi_out = "NVIDIA GeForce RTX 4070 Ti SUPER, 16384, 565.57.01\n"
    mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout=smi_out, stderr=""))
    d = HardwareDetector()
    with patch("subprocess.run", mock_run):
        gpu = d._probe_gpu()
    assert gpu["vendor"] == "nvidia"
    assert gpu["model"] == "NVIDIA GeForce RTX 4070 Ti SUPER"
    assert gpu["vram_mb"] == 16384
    assert gpu["driver"] == "565.57.01"


def test_services_probe_uses_shutil_which():
    d = HardwareDetector()
    def fake_which(n):
        return "/usr/bin/" + n if n == "ffmpeg" else None
    with patch("shutil.which", side_effect=fake_which):
        svc = d._probe_services()
    assert svc["ffmpeg"]["installed"] is True
    assert svc["ollama"]["installed"] is False


def test_node_id_persistence(tmp_path):
    id_file = tmp_path / "node_id"
    d = HardwareDetector(node_id_path=str(id_file))
    id1 = d._get_or_create_node_id()
    id2 = d._get_or_create_node_id()
    assert id1 == id2
    assert id_file.exists()


def test_read_and_detect_changes(tmp_path):
    d = HardwareDetector()
    a = {"cpu": {"cores": 8}, "services": {"ollama": {"installed": True}}}
    b = {"cpu": {"cores": 8}, "services": {"ollama": {"installed": False}}}
    assert d.detect_changes(a, a) == {}
    diff = d.detect_changes(a, b)
    assert diff  # non-empty


def test_main_writes_json_to_output(tmp_path):
    out = tmp_path / "hardware.json"
    import sys
    from backend.services import hardware_detector
    with patch.object(sys, "argv", ["hardware_detector.py", "--output", str(out)]):
        hardware_detector.main()
    assert out.exists()
    data = json.loads(out.read_text())
    assert "node_id" in data
    assert "arch" in data


def test_master_eligible_respects_env_var(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_MASTER_INELIGIBLE", "1")
    d = HardwareDetector()
    profile = d.detect()
    assert profile["master_eligible"] is False
    monkeypatch.delenv("GUAARDVARK_MASTER_INELIGIBLE")
    d2 = HardwareDetector()
    profile2 = d2.detect()
    assert profile2["master_eligible"] is True
