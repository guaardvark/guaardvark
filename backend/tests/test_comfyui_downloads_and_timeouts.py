"""ComfyUI result downloads give up on a stalled transfer instead of hanging
with the GPU held, and an image prompt the caller stops waiting for is
removed from ComfyUI. Uses a throwaway local HTTP server, not ComfyUI."""
import http.server
import threading
import time

import pytest
import requests

from backend.services import comfyui_video_generator as cvg
from backend.services.comfyui_image_generator import ComfyUIImageGenerator


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if "stall" in self.path:
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"x" * 10)
            self.wfile.flush()
            time.sleep(3)
            return
        body = b"PNGDATA"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_download_saves_the_file(server, tmp_path):
    dest = tmp_path / "out.png"
    cvg.download_comfyui_view(f"{server}/view", {"filename": "a.png"}, dest)
    assert dest.read_bytes() == b"PNGDATA"


def test_stalled_download_raises_and_leaves_no_file(server, tmp_path, monkeypatch):
    monkeypatch.setattr(cvg, "VIEW_DOWNLOAD_TIMEOUT", (2, 1))
    dest = tmp_path / "out.png"
    with pytest.raises(requests.exceptions.RequestException):
        cvg.download_comfyui_view(f"{server}/stall", {"filename": "a.png"}, dest)
    assert list(tmp_path.iterdir()) == []


def test_timed_out_image_prompt_is_cancelled(monkeypatch):
    gen = ComfyUIImageGenerator.__new__(ComfyUIImageGenerator)
    gen.comfy_url = "http://127.0.0.1:1"
    cancelled = []
    monkeypatch.setattr(gen, "_cancel", cancelled.append, raising=False)
    monkeypatch.setattr("backend.services.comfyui_image_generator.time.sleep", lambda s: None)
    assert gen._wait("p-1", timeout=0) is None
    assert cancelled == ["p-1"]
