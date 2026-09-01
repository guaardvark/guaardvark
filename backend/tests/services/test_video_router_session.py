"""Direct callers of the router get a GPU session around each clip."""
from backend.services import video_generation_router as vgr
from backend.services.comfyui_video_generator import VideoGenerationRequest, VideoGenerationResult


def test_router_wraps_generation_in_a_budgeted_session(monkeypatch):
    calls = {}

    class _Session:
        def __enter__(self):
            calls["entered"] = True
            return True

        def __exit__(self, *a):
            calls["exited"] = True
            return False

    def _gpu_session(kind, op_id, **kw):
        calls["kind"] = kind
        calls["op_id"] = op_id
        calls["kw"] = kw
        return _Session()

    import backend.services.gpu_resource_policy as grp
    monkeypatch.setattr(grp, "gpu_session", _gpu_session)

    class _Gen:
        def generate_video(self, request):
            assert calls.get("entered")
            return VideoGenerationResult(success=True, video_path="x.mp4")

    router = vgr.VideoGenerationRouter.__new__(vgr.VideoGenerationRouter)
    import threading
    router._gen_count_lock = threading.Lock()
    router._active_generation_count = 0
    router._cancel_idle_shutdown = lambda: None
    router._schedule_idle_shutdown = lambda: None
    router.get_active_generator = lambda: _Gen()

    result = router.generate_video(VideoGenerationRequest(model="minimax-h3-int8", metadata={"item_id": "it1"}))
    assert result.success
    assert calls["op_id"] == "router:minimax-h3-int8:it1"
    assert calls["kw"]["evict_ollama"] is True and calls["kw"]["free_comfyui"] is False
    assert calls["kw"]["on_busy"] == "register"  # a held gate degrades, never refuses the clip
    assert calls["kw"]["vram_estimate_mb"] == 11000
    assert calls["exited"] is True
