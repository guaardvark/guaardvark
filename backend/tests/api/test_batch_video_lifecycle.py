"""Batch Video API lifecycle: generate → status (stage fields) → cancel → retry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from unittest.mock import MagicMock

import pytest

try:
    from flask import Flask
    from backend.api.batch_video_generation_api import batch_video_bp
    from backend.services.batch_video_generator import (
        BatchVideoResult,
        BatchVideoStatus,
    )
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@dataclass
class _FakeStatus:
    batch_id: str
    status: str = "queued"
    total_videos: int = 1
    completed_videos: int = 0
    failed_videos: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    results: List[BatchVideoResult] = field(default_factory=list)
    error: Optional[str] = None
    output_dir: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    retry_data: Optional[Dict] = None
    stage: str = "queued"
    current_item: Optional[str] = None
    progress_pct: Optional[int] = 0


class _FakeGenerator:
    def __init__(self):
        self.service_available = True
        self._batches: Dict[str, _FakeStatus] = {}
        self._cancelled = set()
        self._queue: List[str] = []

    def start_batch_from_prompts(self, prompts, **params):
        batch_id = params.get("batch_id") or f"VideoBatch_test_{len(self._batches) + 1:03d}"
        st = _FakeStatus(
            batch_id=batch_id,
            status="queued",
            total_videos=len(prompts),
            metadata=dict(params.get("metadata") or {}),
            stage="queued",
            progress_pct=0,
            retry_data={
                "mode": "text",
                "prompts": list(prompts),
                "params": {
                    "model": params.get("model"),
                    "duration_frames": params.get("duration_frames"),
                    "ui_config": params.get("ui_config"),
                },
            },
        )
        self._batches[batch_id] = st
        self._queue.append(batch_id)
        return st

    def start_batch_from_images(self, image_paths, **params):
        batch_id = params.get("batch_id") or f"VideoBatch_img_{len(self._batches) + 1:03d}"
        st = _FakeStatus(
            batch_id=batch_id,
            status="queued",
            total_videos=len(image_paths),
            stage="queued",
            progress_pct=0,
            retry_data={
                "mode": "image",
                "image_paths": list(image_paths),
                "prompt": params.get("prompt", ""),
                "params": {"model": params.get("model")},
            },
        )
        self._batches[batch_id] = st
        self._queue.append(batch_id)
        return st

    def get_batch_status(self, batch_id: str):
        return self._batches.get(batch_id)

    def cancel_batch(self, batch_id: str) -> bool:
        st = self._batches.get(batch_id)
        if not st:
            return False
        st.status = "cancelled"
        st.stage = "done"
        st.error = "Cancelled by user"
        st.end_time = datetime.now()
        self._cancelled.add(batch_id)
        return True

    def list_queue(self):
        out = []
        for i, bid in enumerate(self._queue, start=1):
            st = self._batches[bid]
            out.append({
                "position": i,
                "batch_id": bid,
                "status": st.status,
                "stage": st.stage,
                "progress_pct": st.progress_pct,
                "total_videos": st.total_videos,
                "completed_videos": st.completed_videos,
                "failed_videos": st.failed_videos,
            })
        return out

    def list_batches(self):
        return [
            {
                "batch_id": st.batch_id,
                "status": st.status,
                "stage": st.stage,
                "progress_pct": st.progress_pct,
                "total_videos": st.total_videos,
                "completed_videos": st.completed_videos,
                "failed_videos": st.failed_videos,
            }
            for st in self._batches.values()
        ]


@pytest.fixture
def fake_gen(monkeypatch):
    gen = _FakeGenerator()
    monkeypatch.setattr(
        "backend.api.batch_video_generation_api.get_batch_video_generator",
        lambda: gen,
    )
    monkeypatch.setattr(
        "backend.api.batch_video_generation_api.preflight_video_model",
        lambda model_id: (True, ""),
    )
    monkeypatch.setattr(
        "backend.services.video_model_registry.preflight_video_model",
        lambda model_id: (True, ""),
    )
    monkeypatch.setattr(
        "backend.api.batch_video_generation_api._gpu_queue_hint",
        lambda: {"gpu_busy": False, "owner": None, "message": None},
    )
    return gen


@pytest.fixture
def client(fake_gen):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(batch_video_bp)
    return app.test_client()


def test_generate_text_omitted_model_uses_resolver(client, fake_gen, monkeypatch):
    from backend.api import batch_video_generation_api as api
    monkeypatch.setattr(api, "resolve_active_video_model", lambda role, explicit=None, surface=None: ("wan22-5b", None))
    captured = {}
    orig = fake_gen.start_batch_from_prompts

    def _start(prompts, **params):
        captured.update(params)
        return orig(prompts, **params)

    fake_gen.start_batch_from_prompts = _start
    resp = client.post("/api/batch-video/generate/text", json={"prompts": ["a red cube"]})
    assert resp.status_code == 200
    assert captured.get("model") == "wan22-5b"
    assert captured.get("fps") == 24
    assert captured.get("num_inference_steps") >= 20
    assert captured.get("width", 0) * captured.get("height", 0) > 512 * 512


def test_generate_text_returns_queued_with_stage(client, fake_gen):
    resp = client.post(
        "/api/batch-video/generate/text",
        json={"prompts": ["a cat walks across a kitchen"], "model": "wan22-5b"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["status"] == "queued"
    assert body["data"]["stage"] == "queued"
    assert body["data"]["batch_id"]
    assert "gpu" in body["data"]


def test_status_includes_stage_fields(client, fake_gen):
    create = client.post(
        "/api/batch-video/generate/text",
        json={"prompts": ["ocean waves at dusk"], "model": "wan22-5b"},
    )
    batch_id = create.get_json()["data"]["batch_id"]
    st = fake_gen._batches[batch_id]
    st.status = "running"
    st.stage = "generate"
    st.current_item = "item-1"
    st.progress_pct = 42
    st.completed_videos = 0

    resp = client.get(f"/api/batch-video/status/{batch_id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["stage"] == "generate"
    assert data["current_item"] == "item-1"
    assert data["progress_pct"] == 42
    assert data["status"] == "running"


def test_cancel_batch_lifecycle(client, fake_gen):
    create = client.post(
        "/api/batch-video/generate/text",
        json={"prompts": ["cancel me"], "model": "wan22-5b"},
    )
    batch_id = create.get_json()["data"]["batch_id"]

    # Wire cancel endpoint to fake generator
    # The real cancel route calls generator.cancel_batch
    resp = client.post(f"/api/batch-video/batch/{batch_id}/cancel")
    assert resp.status_code == 200
    assert fake_gen._batches[batch_id].status == "cancelled"
    assert fake_gen._batches[batch_id].stage == "done"


def test_retry_creates_new_batch(client, fake_gen):
    create = client.post(
        "/api/batch-video/generate/text",
        json={"prompts": ["retry me"], "model": "wan22-5b", "high_consistency": True},
    )
    batch_id = create.get_json()["data"]["batch_id"]
    st = fake_gen._batches[batch_id]
    st.status = "error"
    st.error = "boom"
    st.stage = "done"

    original = fake_gen.start_batch_from_prompts

    def retry_start(prompts, **params):
        params = dict(params)
        params.pop("batch_id", None)
        return original(prompts, **params)

    fake_gen.start_batch_from_prompts = retry_start

    resp = client.post(f"/api/batch-video/retry/{batch_id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("success") is True
    new_id = body["data"]["batch_id"]
    assert new_id != batch_id
    assert new_id in fake_gen._batches
    assert body["data"]["retried_from"] == batch_id


def test_preflight_failure_blocks_enqueue(client, monkeypatch, fake_gen):
    monkeypatch.setattr(
        "backend.api.batch_video_generation_api.preflight_video_model",
        lambda model_id: (False, "Wan requires ComfyUI. Start the ComfyUI plugin, then retry."),
    )
    resp = client.post(
        "/api/batch-video/generate/text",
        json={"prompts": ["should not queue"], "model": "wan22-5b"},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False
    err = body.get("error") or body.get("message") or ""
    if isinstance(err, dict):
        err = err.get("message") or str(err)
    assert "ComfyUI" in err


def test_adapt_video_gen_prefers_progress_pct_and_stage():
    from backend.services.job_registry import adapt_video_gen
    from backend.services.job_types import JobStatus

    job = adapt_video_gen({
        "batch_id": "VideoBatch_stage_001",
        "status": "running",
        "stage": "gpu_wait",
        "progress_pct": 2,
        "total_videos": 4,
        "completed_videos": 0,
        "failed_videos": 0,
        "metadata": {"display_name": "wait test"},
        "is_running": True,
    })
    assert job.status == JobStatus.RUNNING
    assert job.progress == 2.0
    assert job.metadata.get("stage") == "gpu_wait"
    assert "[gpu_wait]" in job.label


def test_attach_quality_metrics_fail_open(tmp_path):
    from backend.services.batch_video_generator import BatchVideoGenerator, BatchVideoResult

    # Minimal instance without starting the queue worker thread.
    gen = object.__new__(BatchVideoGenerator)
    br = BatchVideoResult(item_id="x", success=True, video_path=str(tmp_path / "missing.mp4"))
    gen._attach_quality_metrics(
        br,
        video_path=str(tmp_path / "missing.mp4"),
        keyframe_path=None,
        cinematic=True,
        high_consistency=True,
    )
    assert "quality" in br.metadata
    assert br.metadata["quality"]["flagged"] is False
