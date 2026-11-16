"""rocm-smi fallback for GPU metrics: fixtures are the JSON of an RX 7900 XTX + Radeon 780M host."""
import json
from types import SimpleNamespace

import pytest

from backend.utils import gpu_metrics as gm

WARNING = "WARNING: AMD GPU device(s) is/are in a low-power state. Check power control/runtime_status\n\n"
DGPU = {
    "Temperature (Sensor edge) (C)": "28.0",
    "Temperature (Sensor junction) (C)": "36.0",
    "Temperature (Sensor memory) (C)": "29.0",
    "Average Graphics Package Power (W)": "10.0",
    "GPU use (%)": "0",
    "GPU Memory Allocated (VRAM%)": "2",
    "VRAM Total Memory (B)": "25753026560",
    "VRAM Total Used Memory (B)": "538509312",
}
IGPU = {
    "Temperature (Sensor edge) (C)": "36.0",
    "Current Socket Graphics Package Power (W)": "6.242",
    "GPU use (%)": "1",
    "GPU Memory Allocated (VRAM%)": "25",
    "VRAM Total Memory (B)": "3221225472",
    "VRAM Total Used Memory (B)": "820285440",
}


def _text(cards):
    return WARNING + json.dumps(cards)


def test_the_card_with_the_most_vram_wins_even_when_listed_second():
    data = gm.parse_rocm_smi_json(_text({"card0": IGPU, "card1": DGPU}))
    m = gm.metrics_from_rocm_json(data)
    assert m["available"] and m["tool"] == "rocm-smi"
    assert m["memory_total_mb"] == 24560.0
    assert m["temperature"] == 28.0
    assert m["utilization"] == 0.0
    assert m["power_draw_w"] == 10.0
    assert m["power_limit_w"] is None and m["power_percent"] is None
    assert m["memory_percent"] == pytest.approx(2.09, abs=0.01)


def test_the_other_power_spelling_is_read():
    m = gm.metrics_from_rocm_json(gm.parse_rocm_smi_json(_text({"card0": IGPU})))
    assert m["power_draw_w"] == 6.242
    assert m["memory_total_mb"] == 3072.0


def test_junction_temperature_is_the_fallback_for_edge():
    card = dict(DGPU)
    del card["Temperature (Sensor edge) (C)"]
    m = gm.metrics_from_rocm_json({"card0": card})
    assert m["temperature"] == 36.0


def test_garbage_and_empty_output_are_not_metrics():
    assert gm.parse_rocm_smi_json("no json here") is None
    assert gm.parse_rocm_smi_json("") is None
    assert gm.metrics_from_rocm_json({})["available"] is False


def _fake_run(stdout):
    return lambda *a, **k: SimpleNamespace(stdout=stdout, stderr="", returncode=0)


def test_rocm_smi_metrics_runs_the_tool(monkeypatch):
    monkeypatch.setattr(gm.subprocess, "run", _fake_run(_text({"card0": DGPU, "card1": IGPU})))
    assert gm.rocm_smi_metrics("/usr/bin/rocm-smi")["memory_total_mb"] == 24560.0


def test_missing_tool_and_timeout(monkeypatch):
    monkeypatch.setattr(gm.shutil, "which", lambda name: None)
    assert gm.rocm_smi_metrics()["available"] is False

    def timeout(*a, **k):
        raise gm.subprocess.TimeoutExpired("rocm-smi", 5)

    monkeypatch.setattr(gm.subprocess, "run", timeout)
    assert gm.rocm_smi_metrics("/usr/bin/rocm-smi")["error"] == "rocm-smi timeout"


def test_gpu_monitor_falls_back_to_rocm_smi(monkeypatch):
    from backend.utils import unified_progress_system as ups
    monkeypatch.setattr(ups.shutil, "which", lambda n: "/usr/bin/rocm-smi" if n == "rocm-smi" else None)
    monkeypatch.setattr(gm.subprocess, "run", _fake_run(_text({"card0": IGPU, "card1": DGPU})))
    m = ups.GPUMonitor().get_gpu_metrics()
    assert m["available"] and m["tool"] == "rocm-smi" and m["memory_total_mb"] == 24560.0


def test_nvidia_smi_still_comes_first(monkeypatch):
    from backend.utils import unified_progress_system as ups
    monkeypatch.setattr(ups.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(ups.GPUMonitor, "_get_nvidia_smi_metrics", lambda self: {"available": True, "tool": "nvidia-smi"})

    def boom(*a, **k):
        raise AssertionError("rocm-smi must not be asked when nvidia-smi is there")

    monkeypatch.setattr(gm, "rocm_smi_metrics", boom)
    assert ups.GPUMonitor().get_gpu_metrics()["tool"] == "nvidia-smi"


def test_metrics_endpoint_reports_the_amd_card(monkeypatch):
    from flask import Flask
    from backend.api import diagnostics_api as da

    monkeypatch.setattr(da.shutil, "which", lambda n: "/usr/bin/rocm-smi" if n == "rocm-smi" else None)
    monkeypatch.setattr(gm.subprocess, "run", _fake_run(_text({"card0": IGPU, "card1": DGPU})))
    with Flask(__name__).test_request_context("/metrics"):
        response, status = da.get_system_metrics()
    body = response.get_json()
    assert status == 200
    assert body["gpu_tools_available"] is True
    assert body["gpu_temp"] == 28.0
    assert body["gpu_percent"] == 0.0
    assert body["gpu_mem_total_gb"] == 23.98
    assert body["gpu_check_error"] is None


def test_metrics_endpoint_names_all_three_tools_when_none_is_there(monkeypatch):
    from flask import Flask
    from backend.api import diagnostics_api as da

    monkeypatch.setattr(da.shutil, "which", lambda n: None)
    with Flask(__name__).test_request_context("/metrics"):
        response, _ = da.get_system_metrics()
    body = response.get_json()
    assert body["gpu_tools_available"] is False
    assert "rocm-smi" in body["gpu_check_error"]
