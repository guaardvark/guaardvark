"""Tests for the RealLoraTrainer subprocess protocol.

NOTE: The actual training-loop correctness inside run_trainer.py is untestable 
without a GPU. These tests verify the JSON protocol and error propagation of the 
parent driver class without actually spawning the torch subprocess.
"""
import pytest
from plugins.lora_trainer.real_trainer import RealLoraTrainer

def test_real_trainer_is_available_returns_false_when_no_venv(monkeypatch):
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
    assert not RealLoraTrainer.is_available()

def test_real_trainer_train_calls_send_with_correct_params(tmp_path, monkeypatch):
    trainer = RealLoraTrainer()
    
    # Bypass subprocess spawn
    monkeypatch.setattr(trainer, "_ensure_proc", lambda: None)
    
    sends = []
    def fake_send(msg, timeout_s):
        sends.append(msg)
        if msg["op"] == "load":
            return {"ok": True}
        if msg["op"] == "train":
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
    
    monkeypatch.setattr(trainer, "_ensure_proc", lambda: None)
    
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
