"""Docling's PDF models are read from this machine only and arrive through an Install.

Every test runs with non-loopback lookups and connects refused, against a
Hugging Face cache built in a temp folder.
"""
import logging
import socket

import pytest

from backend.utils import docling_loader as dl


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = []
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def local(host):
        return str(host) in ("localhost", "::1") or str(host).startswith("127.")

    def getaddrinfo(host, *args, **kwargs):
        if not local(host):
            attempts.append(host)
            raise OSError(f"test blocked lookup of {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local(address[0]):
            attempts.append(address)
            raise OSError(f"test blocked connect to {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    yield attempts
    assert attempts == [], f"reached for the network: {attempts}"


@pytest.fixture
def hf_cache(monkeypatch, tmp_path):
    import huggingface_hub.constants

    cache = tmp_path / "hub"
    cache.mkdir()
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(cache))
    monkeypatch.setenv("GUAARDVARK_CACHE_DIR", str(tmp_path / "gx-cache"))
    monkeypatch.setattr(dl, "_links_dir", lambda: tmp_path / "gx-cache" / "docling_artifacts")
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    monkeypatch.setenv("GUAARDVARK_DOCLING_ENABLED", "true")
    monkeypatch.setattr(dl, "_install", {"state": "idle", "progress": None,
                                         "downloaded_gb": 0.0, "error": None})
    return cache


def _put_models(cache):
    """Lay out the PDF models the way a hub download leaves them."""
    for i, model in enumerate(dl.PDF_MODELS):
        repo = cache / ("models--" + model["repo_id"].replace("/", "--"))
        commit = f"{i:040d}"
        (repo / "refs").mkdir(parents=True)
        (repo / "refs" / model["revision"]).write_text(commit)
        for name in model["files"]:
            f = repo / "snapshots" / commit / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")


def _pdf(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return str(path)


def test_without_models_a_pdf_is_skipped_with_the_reason(hf_cache, tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(dl, "_get_converter", lambda: pytest.fail("built a converter without models"))
    with caplog.at_level(logging.INFO, logger=dl.logger.name):
        assert dl.is_installed() is False
        assert dl.artifacts_path() is None
        assert dl.load_documents(_pdf(tmp_path), "a.pdf", object) is None
    assert "Docling skipped a.pdf" in caplog.text
    assert "Settings > Knowledge" in caplog.text


def test_installed_models_are_linked_where_docling_reads_them(hf_cache):
    _put_models(hf_cache)
    assert dl.is_installed() is True
    folder = dl.artifacts_path()
    for model in dl.PDF_MODELS:
        link = folder / model["repo_id"].replace("/", "--")
        assert link.is_symlink()
        assert all((link / name).is_file() for name in model["files"])
    # A second call keeps the same links.
    assert dl.artifacts_path() == folder


def test_converter_is_always_given_a_local_artifacts_folder(hf_cache, monkeypatch):
    pytest.importorskip("docling")
    monkeypatch.setattr(dl, "_converter", None)
    converter = dl._get_converter()
    try:
        from docling.datamodel.base_models import InputFormat
        options = converter.format_to_options[InputFormat.PDF].pipeline_options
        assert options.artifacts_path == dl._links_dir()
        assert options.do_ocr is False
    finally:
        monkeypatch.setattr(dl, "_converter", None)


def test_install_routes(hf_cache, monkeypatch, tmp_path):
    from flask import Flask
    from backend.api.index_mgmt_api import index_mgmt_bp

    app = Flask(__name__)
    app.register_blueprint(index_mgmt_bp)
    client = app.test_client()
    started = []
    monkeypatch.setattr(dl.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: started.append(k)})())

    status = client.get("/api/meta/docling").get_json()
    assert status["installed"] is False and status["enabled"] is True
    assert status["size_gb"] == round(dl.INSTALL_BYTES / 1e9, 2)

    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(tmp_path / "own"))
    refused = client.post("/api/meta/docling/install").get_json()
    assert refused["install"]["state"] == "failed"
    assert "DOCLING_ARTIFACTS_PATH" in refused["install"]["error"]
    assert started == []
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH")

    resp = client.post("/api/meta/docling/install")
    assert resp.status_code == 202
    assert resp.get_json()["install"]["state"] == "running"
    assert [k["name"] for k in started] == ["docling-models-install"]

    _put_models(hf_cache)
    dl._install.update({"state": "idle"})
    started.clear()
    assert client.post("/api/meta/docling/install").get_json()["installed"] is True
    assert started == []
