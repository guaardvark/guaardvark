"""The batch image route refuses a model name the catalog does not have.

It used to queue the batch on 'auto' while the reply named the model asked
for. generate_image over MCP queues through this route, so it reports the
refusal, and otherwise the model and warnings the route answered with. The
batch start, the catalog and the installed-model check are stand-ins: no GPU,
network or database, and the same answers whatever models are on the machine.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

import backend.api.batch_image_generation_api as api
from backend.services import offline_image_generator
from backend.services.job_types import RenderErrorKind, RenderFailure
from backend.tools import image_tools as it
from backend.utils.backend_http import BackendError

CATALOG = {"zimage-turbo": "repo/z", "flux-dev": "comfy:flux-dev", "sd-xl": "repo/sdxl", "user-lora-1": "user:lora"}


@pytest.fixture
def route(monkeypatch):
    started, probes = [], []
    # refusal_before_start answers None (installed) until a test sets installed.refusal.
    installed = SimpleNamespace(refusal=None)
    installed.refusal_before_start = lambda prompts: probes.extend(prompts) or installed.refusal
    monkeypatch.setattr(offline_image_generator, "get_image_generator", lambda: SimpleNamespace(
        available_models=dict(CATALOG), hidden_models={"user-lora-1"}))
    monkeypatch.setattr(api, "get_batch_image_generator", lambda: installed, raising=False)
    monkeypatch.setattr(api, "service_available", True)
    monkeypatch.setattr(api, "settings_validator_available", False)
    monkeypatch.setattr(api, "start_batch_from_prompts",
                        lambda prompts, **params: started.append(params) or "ImageBatch_test_1", raising=False)
    monkeypatch.setattr(api, "_apply_character_casting", lambda data, params: None)
    app = Flask(__name__)
    app.register_blueprint(api.batch_image_bp)
    return SimpleNamespace(client=app.test_client(), started=started, installed=installed, probes=probes)


def _post(route, **body):
    return route.client.post("/api/batch-image/generate/prompts", json={"prompts": ["a brass key"], **body})


@pytest.mark.parametrize("name", ["flux", "zimage", "Z-Image-Turbo", "sd-2.1"])
def test_an_unknown_model_is_refused_with_the_known_names(route, name):
    resp = _post(route, model=name)
    assert resp.status_code == 400 and route.started == []
    message = str(resp.get_json()["error"])
    assert f"Unknown image model '{name}'" in message
    assert "Known: auto, flux-dev, sd-xl, zimage-turbo." in message     # the hidden LoRA entry is not offered


@pytest.mark.parametrize("sent,queued", [("flux-dev", "flux-dev"), (" zimage-turbo ", "zimage-turbo"),
                                         ("auto", "auto"), ("", "auto"), (None, "auto")])
def test_a_catalog_name_or_auto_is_queued_under_that_name(route, sent, queued):
    resp = _post(route, model=sent)
    assert resp.status_code == 201
    assert resp.get_json()["data"]["parameters"]["model"] == queued == route.started[-1]["model"]


def test_no_model_in_the_request_means_auto(route):
    assert _post(route).get_json()["data"]["parameters"]["model"] == "auto"


@pytest.mark.parametrize("sent,probed,message", [
    (None, "auto", "No image model is downloaded yet. Open Settings → Image Models and download one."),
    ("zimage-turbo", "zimage-turbo", "'zimage-turbo' is not downloaded yet (repo/z)."),
])
def test_a_model_that_is_not_installed_is_refused_before_anything_is_queued(route, sent, probed, message):
    route.installed.refusal = RenderFailure(RenderErrorKind.MODEL_NOT_INSTALLED, message)
    resp = _post(route, model=sent)
    assert resp.status_code == 409 and route.started == []
    assert route.probes[-1].model == probed
    body = resp.get_json()
    assert body["error"]["code"] == "MODEL_NOT_INSTALLED" and message in str(body["error"])
    failure = body["error"]["context"]["failure"]
    assert failure["kind"] == "model_not_installed" and failure["message"] == message


def test_a_request_that_may_download_its_model_is_not_asked(route):
    route.installed.refusal = "not installed"
    assert _post(route, model="zimage-turbo", allow_model_download=True).status_code == 201
    assert route.probes == []


def test_a_name_is_left_to_the_generator_when_the_catalog_cannot_be_read(monkeypatch):
    def broken():
        raise RuntimeError("diffusers missing")

    monkeypatch.setattr(offline_image_generator, "get_image_generator", broken)
    assert api.unknown_image_model_message("flux") is None


def test_generate_image_over_mcp_reports_the_refusal_and_what_the_route_queued(route, monkeypatch):
    def via_route(method, path, payload=None, timeout=None):
        resp = route.client.open(path, method=method, json=payload)
        body = resp.get_json()
        if resp.status_code >= 400:
            error = body.get("error")
            raise BackendError("http", error.get("message") if isinstance(error, dict) else str(error),
                               status=resp.status_code, body=body)
        return body["data"]

    monkeypatch.setattr(it, "_http_json", via_route)
    monkeypatch.setattr(it, "_resolve_cast_from_prompt", lambda prompt: [])
    tool = it.ImageGeneratorTool()
    tool.set_context({"transport": "mcp"})

    refused = tool.execute(prompt="a brass key", model="flux", wait_for_result=False)
    assert not refused.success and "Unknown image model 'flux'" in refused.error and "flux-dev" in refused.error

    queued = tool.execute(prompt="a brass key", model="flux-dev", wait_for_result=False)
    assert queued.success and "Model: flux-dev" in queued.output and queued.metadata["model"] == "flux-dev"

    # The route's answer wins over the name that was sent, and its warnings are shown.
    monkeypatch.setattr(it, "_http_json", lambda method, path, payload=None, timeout=None: {
        "batch_id": "ImageBatch_test_2", "parameters": {"model": "sd-xl", "steps": 25},
        "validation": {"warnings": ["guidance corrected to 7.5"]}})
    answered = tool.execute(prompt="a brass key", model="auto", wait_for_result=False)
    assert "Model: sd-xl" in answered.output and "Note: guidance corrected to 7.5" in answered.output
    assert answered.metadata["warnings"] == ["guidance corrected to 7.5"]
