"""Tests for the RealLoraTrainer subprocess protocol.

NOTE: The actual training-loop correctness inside run_trainer.py is untestable 
without a GPU. These tests verify the JSON protocol and error propagation of the 
parent driver class without actually spawning the torch subprocess.
"""
import pytest
from pathlib import Path
from plugins.lora_trainer.real_trainer import RealLoraTrainer

def test_real_trainer_is_available_returns_false_when_no_venv(monkeypatch):
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
    assert not RealLoraTrainer.is_available()

def test_real_trainer_train_calls_send_with_correct_params(tmp_path, monkeypatch):
    trainer = RealLoraTrainer()
    
    # Bypass subprocess spawn
    monkeypatch.setattr(trainer, "_ensure_proc", lambda backend="sdxl": None)
    # Bypass backend path resolution: venv-torch is CUDA-only and absent on Macs.
    monkeypatch.setattr(trainer, "_backend_paths", lambda backend: ("python", "runner", "stub-model"))
    
    sends = []
    def fake_send(msg, timeout_s):
        sends.append(msg)
        if msg["op"] == "load":
            return {"ok": True}
        if msg["op"] == "train":
            # The driver validates that a real LoRA file was written; fake one.
            out = Path(msg["params"]["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\0" * 4096)
            return {"ok": True, "lora_path": msg["params"]["output_path"], "lora_version": 1}
        return {"ok": True}
        
    monkeypatch.setattr(trainer, "_send", fake_send)
    
    res = trainer.train_subject_lora(
        subject_id=42,
        subject_name="Test Subject",
        ref_image_paths=["/tmp/a.jpg", "/tmp/b.jpg", "/tmp/c.jpg", "/tmp/d.jpg"],
        output_dir=str(tmp_path)
    )
    
    assert res["status"] == "ok"
    assert len(sends) == 2
    assert sends[0]["op"] == "load"
    assert sends[1]["op"] == "train"
    
    params = sends[1]["params"]
    assert params["subject_id"] == 42
    assert params["subject_name"] == "Test Subject"
    assert params["steps"] == 400  # min capped
    assert "Test_Subject_v1.safetensors" in params["output_path"]

def test_real_trainer_propagates_daemon_failure(tmp_path, monkeypatch):
    trainer = RealLoraTrainer()
    
    monkeypatch.setattr(trainer, "_ensure_proc", lambda backend="sdxl": None)
    monkeypatch.setattr(trainer, "_backend_paths", lambda backend: ("python", "runner", "stub-model"))
    
    def fake_send(msg, timeout_s):
        if msg["op"] == "load":
            return {"ok": True}
        if msg["op"] == "train":
            return {"ok": False, "error": "OOM"}
        return {"ok": True}
        
    monkeypatch.setattr(trainer, "_send", fake_send)
    
    res = trainer.train_subject_lora(
        subject_id=42,
        subject_name="Test Subject",
        ref_image_paths=["/tmp/a.jpg", "/tmp/b.jpg", "/tmp/c.jpg", "/tmp/d.jpg"],
        output_dir=str(tmp_path)
    )
    
    assert res["status"] == "failed"
    assert res["error"] == "OOM"


def _fake_trainer(monkeypatch, sends, train_reply=None):
    trainer = RealLoraTrainer()
    monkeypatch.setattr(trainer, "_ensure_proc", lambda backend="sdxl": None)
    monkeypatch.setattr(trainer, "_backend_paths", lambda backend: ("python", "runner", "stub-model"))

    def fake_send(msg, timeout_s):
        sends.append((msg, timeout_s))
        if msg["op"] == "train":
            out = Path(msg["params"]["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\0" * 4096)
            return {"ok": True, **(train_reply or {})}
        return {"ok": True}

    monkeypatch.setattr(trainer, "_send", fake_send)
    return trainer


def test_zimage_run_sends_the_installed_adapter_and_the_recipe(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.cast_train_adapter.adapter_path",
                        lambda base: "/models/adapter.safetensors")
    monkeypatch.setattr(RealLoraTrainer, "_IS_APPLE_SILICON", False)
    sends = []
    trainer = _fake_trainer(monkeypatch, sends, {"train_adapter": "adapter.safetensors"})

    res = trainer.train_subject_lora(
        subject_id=7, subject_name="Mara", ref_image_paths=[f"/tmp/{i}.jpg" for i in range(31)],
        output_dir=str(tmp_path), base_model_id="zimage-turbo", resolution=1024, rank=32, alpha=32,
        lora_targets="attention_ffn", base_storage="float8",
    )

    assert res["status"] == "ok"
    (load, _), (train, train_timeout) = sends
    assert load["train_adapter"] == "/models/adapter.safetensors"
    assert train["params"]["steps"] == 1500
    assert train["params"]["lora_targets"] == "attention_ffn"
    assert train["params"]["base_storage"] == "float8"
    assert train_timeout == RealLoraTrainer._TRAIN_TIMEOUT_S

    import json
    sidecar = json.loads((tmp_path / "Mara_v1.json").read_text())
    assert sidecar["train_adapter"] == "adapter.safetensors"
    assert sidecar["lora_targets"] == "attention_ffn"


def test_no_adapter_on_disk_sends_none(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.cast_train_adapter.adapter_path", lambda base: None)
    sends = []
    trainer = _fake_trainer(monkeypatch, sends)
    trainer.train_subject_lora(
        subject_id=7, subject_name="Mara", ref_image_paths=["/tmp/a.jpg"] * 4,
        output_dir=str(tmp_path), base_model_id="zimage-turbo",
    )
    assert "train_adapter" not in sends[0][0]


def test_a_short_run_gets_a_short_budget(monkeypatch):
    monkeypatch.setattr(RealLoraTrainer, "_IS_APPLE_SILICON", False)
    assert RealLoraTrainer._train_budget_s("zimage-turbo", 400, 512) == 1800
    assert RealLoraTrainer._train_budget_s("zimage-turbo", 600, 1024) == 6480
    assert RealLoraTrainer._train_budget_s("sdxl-legacy", 1500, 1024) == 1800



def test_step_lines_reach_the_callback_only_while_one_is_set():
    trainer = RealLoraTrainer()
    seen = []
    trainer._report_step("[run_zimage_trainer] step 451/1500 loss=0.23730")
    trainer._on_step = lambda step, total: seen.append((step, total))
    trainer._report_step("[run_zimage_trainer] step 451/1500 loss=0.23730")
    trainer._report_step("OOM during Z-Image training (stage=train)")
    assert seen == [(451, 1500)]


def test_a_failing_step_callback_does_not_raise():
    trainer = RealLoraTrainer()

    def boom(step, total):
        raise RuntimeError("progress store unavailable")

    trainer._on_step = boom
    trainer._report_step("step 1/10")
