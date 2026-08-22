import pytest
from service.jobs import JobManager, JobStatus


def test_create_job():
    jm = JobManager(max_history=5)
    job = jm.create_job(
        input_path="/tmp/in.mp4",
        output_path="/tmp/out.mp4",
        model="RealESRGAN_x4plus",
        scale=4.0,
    )
    assert job["status"] == JobStatus.PENDING.value
    assert job["input_path"] == "/tmp/in.mp4"
    assert job["job_id"] is not None


def test_get_job():
    jm = JobManager(max_history=5)
    job = jm.create_job("/tmp/in.mp4", "/tmp/out.mp4", "m", 4.0)
    fetched = jm.get_job(job["job_id"])
    assert fetched is not None
    assert fetched["job_id"] == job["job_id"]


def test_get_job_not_found():
    jm = JobManager(max_history=5)
    assert jm.get_job("nonexistent") is None


def test_update_progress():
    jm = JobManager(max_history=5)
    job = jm.create_job("/tmp/in.mp4", "/tmp/out.mp4", "m", 4.0)
    jm.start_job(job["job_id"], total_frames=100)
    jm.update_progress(job["job_id"], frames_done=50, fps=24.0)
    updated = jm.get_job(job["job_id"])
    assert updated["status"] == JobStatus.RUNNING.value
    assert updated["frames_done"] == 50
    assert updated["progress"] == pytest.approx(0.5)


def test_complete_job():
    jm = JobManager(max_history=5)
    job = jm.create_job("/tmp/in.mp4", "/tmp/out.mp4", "m", 4.0)
    jm.start_job(job["job_id"], total_frames=10)
    jm.complete_job(job["job_id"])
    updated = jm.get_job(job["job_id"])
    assert updated["status"] == JobStatus.COMPLETED.value


def test_fail_job():
    jm = JobManager(max_history=5)
    job = jm.create_job("/tmp/in.mp4", "/tmp/out.mp4", "m", 4.0)
    jm.fail_job(job["job_id"], error="OOM")
    updated = jm.get_job(job["job_id"])
    assert updated["status"] == JobStatus.FAILED.value
    assert updated["error"] == "OOM"


def test_cancel_job():
    jm = JobManager(max_history=5)
    job = jm.create_job("/tmp/in.mp4", "/tmp/out.mp4", "m", 4.0)
    jm.start_job(job["job_id"], total_frames=100)
    jm.cancel_job(job["job_id"])
    updated = jm.get_job(job["job_id"])
    assert updated["status"] == JobStatus.CANCELLED.value


def test_list_jobs():
    jm = JobManager(max_history=5)
    jm.create_job("/tmp/a.mp4", "/tmp/a_out.mp4", "m", 4.0)
    jm.create_job("/tmp/b.mp4", "/tmp/b_out.mp4", "m", 4.0)
    jobs = jm.list_jobs()
    assert len(jobs) == 2


def test_ring_buffer_eviction():
    """Old completed jobs are evicted when max_history exceeded."""
    jm = JobManager(max_history=2)
    for i in range(4):
        job = jm.create_job(f"/tmp/{i}.mp4", f"/tmp/{i}_out.mp4", "m", 4.0)
        jm.complete_job(job["job_id"])
    jobs = jm.list_jobs()
    assert len(jobs) == 2


def test_active_job_count():
    """Active jobs include both PENDING and RUNNING."""
    jm = JobManager(max_history=5)
    job1 = jm.create_job("/tmp/a.mp4", "/tmp/a_out.mp4", "m", 4.0)
    job2 = jm.create_job("/tmp/b.mp4", "/tmp/b_out.mp4", "m", 4.0)
    assert jm.active_job_count == 2  # both PENDING
    jm.start_job(job1["job_id"], total_frames=100)
    assert jm.active_job_count == 2  # 1 RUNNING + 1 PENDING
    jm.start_job(job2["job_id"], total_frames=100)
    assert jm.active_job_count == 2  # both RUNNING
    jm.complete_job(job1["job_id"])
    assert jm.active_job_count == 1  # 1 RUNNING


def test_create_job_defaults_to_a_video_job():
    jm = JobManager(max_history=5)
    job = jm.create_job("/tmp/in.mp4", "/tmp/out.mp4", "m", 4.0)
    assert job["kind"] == "video"
    assert job["item_count"] == 1
    assert job["outputs"] == []


def test_image_batch_job_collects_outputs():
    jm = JobManager(max_history=5)
    job = jm.create_job(
        "/tmp/a.png", "/tmp/out", "m", 4.0, kind="image_batch", item_count=2,
    )
    jm.start_job(job["job_id"], total_frames=2)
    jm.add_output(job["job_id"], "/tmp/out/a_upscaled.png")
    jm.add_output(job["job_id"], "/tmp/out/b_upscaled.png")
    jm.complete_job(job["job_id"])

    updated = jm.get_job(job["job_id"])
    assert updated["kind"] == "image_batch"
    assert updated["item_count"] == 2
    assert updated["outputs"] == ["/tmp/out/a_upscaled.png", "/tmp/out/b_upscaled.png"]
    assert updated["status"] == JobStatus.COMPLETED.value


def test_set_error_leaves_status_alone():
    jm = JobManager(max_history=5)
    job = jm.create_job("/tmp/a.png", "/tmp/out", "m", 4.0, kind="image_batch", item_count=2)
    jm.start_job(job["job_id"], total_frames=2)
    jm.set_error(job["job_id"], "1 skipped: b.png: unreadable")
    jm.complete_job(job["job_id"])

    updated = jm.get_job(job["job_id"])
    assert updated["status"] == JobStatus.COMPLETED.value
    assert "unreadable" in updated["error"]

