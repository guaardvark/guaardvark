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
    smi_out = "NVIDIA GeForce RTX 4080 SUPER, 16384, 565.57.01, 8.9\n"
    mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout=smi_out, stderr=""))
    d = HardwareDetector()
    with patch("subprocess.run", mock_run):
        gpu = d._probe_gpu()
    assert gpu["vendor"] == "nvidia"
    assert gpu["model"] == "NVIDIA GeForce RTX 4080 SUPER"
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


def test_nvidia_probe_includes_compute_cap(monkeypatch):
    import subprocess
    from backend.services import hardware_detector as hd

    def fake_run(args, **kwargs):
        # Route nvidia-smi calls; return a non-zero stub for everything else.
        if "--query-gpu=name,memory.total,driver_version,compute_cap" in args:
            class R:
                returncode = 0
                stdout = "NVIDIA GeForce RTX 5060 Ti, 16311, 595.71.05, 12.0\n"
            return R()
        class Empty:
            returncode = 1
            stdout = ""
        return Empty()

    monkeypatch.setattr(subprocess, "run", fake_run)
    det = hd.HardwareDetector(node_id_path="/tmp/_gx_nodeid_test")
    gpu = det._probe_gpu_nvidia()
    assert gpu["vendor"] == "nvidia"
    assert gpu["vram_mb"] == 16311
    assert gpu["compute_cap"] == "12.0"


def test_nvidia_probe_keeps_a_unified_memory_gpu(monkeypatch):
    # GB10 (DGX Spark) shares RAM with the GPU; nvidia-smi prints [N/A] for
    # memory.total. The GPU must still be found, with the RAM as its memory.
    import subprocess
    from backend.services import hardware_detector as hd

    def fake_run(args, **kwargs):
        if "--query-gpu=name,memory.total,driver_version,compute_cap" in args:
            class R:
                returncode = 0
                stdout = "NVIDIA GB10, [N/A], 580.95.05, 12.1\n"
            return R()
        class Empty:
            returncode = 1
            stdout = ""
        return Empty()

    monkeypatch.setattr(subprocess, "run", fake_run)
    det = hd.HardwareDetector(node_id_path="/tmp/_gx_nodeid_test")
    monkeypatch.setattr(det, "_probe_ram", lambda: {"total_gb": 119.7})
    gpu = det._probe_gpu_nvidia()
    assert gpu["vendor"] == "nvidia"
    assert gpu["vram_mb"] is None
    assert gpu["unified_memory_gb"] == 119.7
    assert gpu["compute_cap"] == "12.1"


def test_network_probe_flags_e1000e_quirk(tmp_path):
    """A physical NIC on the e1000e driver carries the tx_unit_hang quirk;
    virtual interfaces (no device entry) are skipped."""
    sysnet = tmp_path / "net"
    # Physical NIC on e1000e: net/eno1/device/driver -> .../drivers/e1000e
    drv_dir = tmp_path / "drivers" / "e1000e"
    drv_dir.mkdir(parents=True)
    dev = sysnet / "eno1" / "device"
    dev.mkdir(parents=True)
    (dev / "driver").symlink_to(drv_dir)
    # Virtual interface: no device entry
    (sysnet / "lo").mkdir(parents=True)

    d = HardwareDetector()
    nics = d._probe_network(sys_class_net=str(sysnet))
    assert len(nics) == 1
    nic = nics[0]
    assert nic["interface"] == "eno1"
    assert nic["driver"] == "e1000e"
    assert nic["quirk"] == "tx_unit_hang"
    assert "tso off gso off gro off" in nic["mitigation"]


def test_network_probe_benign_driver_has_no_quirk(tmp_path):
    sysnet = tmp_path / "net"
    drv_dir = tmp_path / "drivers" / "igc"
    drv_dir.mkdir(parents=True)
    dev = sysnet / "enp5s0" / "device"
    dev.mkdir(parents=True)
    (dev / "driver").symlink_to(drv_dir)

    d = HardwareDetector()
    nics = d._probe_network(sys_class_net=str(sysnet))
    assert nics == [{"interface": "enp5s0", "driver": "igc"}]


def test_network_probe_missing_sysfs_returns_empty(tmp_path):
    d = HardwareDetector()
    assert d._probe_network(sys_class_net=str(tmp_path / "nope")) == []


def test_detect_includes_network_key():
    d = HardwareDetector()
    profile = d.detect()
    assert "network" in profile
    assert isinstance(profile["network"], list)


# ----------------------------------------------------------------------------
# AMD VRAM probe shared with the load gate
# ----------------------------------------------------------------------------

_ROCM_TWO_CARDS = (
    "WARNING: AMD GPU device(s) is/are in a low-power state.\n\n"
    '{"card0": {"VRAM Total Memory (B)": "3221225472", "VRAM Total Used Memory (B)": "778117120"}, '
    '"card1": {"VRAM Total Memory (B)": "25753026560", "VRAM Total Used Memory (B)": "839303168"}}\n'
)


class _RocmProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_probe_amd_vram_bytes_picks_the_card_with_most_vram(monkeypatch):
    from backend.services import hardware_detector as hd
    monkeypatch.setattr(hd.subprocess, "run", lambda *a, **k: _RocmProc(_ROCM_TWO_CARDS))
    assert hd.probe_amd_vram_bytes() == (25753026560, 839303168)


def test_probe_amd_vram_bytes_does_not_read_used_as_total(monkeypatch):
    from backend.services import hardware_detector as hd
    out = '{"card0": {"VRAM Total Used Memory (B)": "111", "VRAM Total Memory (B)": "222"}}'
    monkeypatch.setattr(hd.subprocess, "run", lambda *a, **k: _RocmProc(out))
    assert hd.probe_amd_vram_bytes() == (222, 111)


def test_probe_amd_vram_bytes_none_without_rocm_smi(monkeypatch):
    from backend.services import hardware_detector as hd

    def _missing(*a, **k):
        raise FileNotFoundError("rocm-smi")

    monkeypatch.setattr(hd.subprocess, "run", _missing)
    assert hd.probe_amd_vram_bytes() is None


def test_amd_vram_mb_reports_the_same_card(monkeypatch):
    from backend.services import hardware_detector as hd
    monkeypatch.setattr(hd.subprocess, "run", lambda *a, **k: _RocmProc(_ROCM_TWO_CARDS))
    assert hd.HardwareDetector()._probe_amd_vram_mb() == 24560
