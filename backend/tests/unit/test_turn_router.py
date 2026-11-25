"""The TurnDecision type, route_turn's dry mode and the turn telemetry row.

The route each corpus message gets is checked in test_turn_router_corpus.py.
"""
from __future__ import annotations

import dataclasses
import json
import socket
from unittest import mock

import pytest

from backend.services import decision_telemetry as dt
from backend.services import turn_router as tr
from backend.services.decision_data import SIDE_EFFECTS
from backend.tests.fixtures.turn_router import harness

# The fields the decision-layer design gives a TurnDecision.
TURN_DECISION_FIELDS = (
    "request_id", "surface", "route", "route_args", "route_source", "legacy_route", "candidates",
    "triggers", "flags", "facts", "answers", "side_effect", "router_version",
)


@pytest.fixture
def telemetry_file(tmp_path, monkeypatch):
    path = tmp_path / "decision_telemetry.jsonl"
    monkeypatch.setattr(dt, "TELEMETRY_PATH", str(path))
    monkeypatch.setenv(dt.ENV_SWITCH, "true")
    return path


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- the type ---------------------------------------------------------------

def test_every_route_has_a_side_effect_class():
    assert set(tr.ROUTE_SIDE_EFFECT) == set(tr.Route)
    assert set(tr.ROUTE_SIDE_EFFECT.values()) <= set(SIDE_EFFECTS)
    assert set(tr.DIRECT_TOOL_SIDE_EFFECT.values()) <= set(SIDE_EFFECTS)


def test_trigger_families_name_real_routes():
    for family, routes in tr.TRIGGER_ROUTES.items():
        assert all(isinstance(r, tr.Route) for r in routes), family


def test_turn_decision_has_exactly_the_design_fields_and_is_frozen():
    turn = tr.TurnDecision(request_id="r", surface=tr.Surface.WEB, route=tr.Route.CHAT)
    assert tuple(f.name for f in dataclasses.fields(turn)) == TURN_DECISION_FIELDS
    assert turn.router_version == tr.ROUTER_VERSION and tr.ROUTER_VERSION
    with pytest.raises(dataclasses.FrozenInstanceError):
        turn.route = tr.Route.NEW_IMAGE


def test_unknown_surface_is_api():
    assert tr.surface_of("web") is tr.Surface.WEB
    assert tr.surface_of("carrier pigeon") is tr.Surface.API
    assert tr.surface_of(None) is tr.Surface.API


def test_direct_tool_side_effect_follows_the_tool():
    assert tr.side_effect_for(tr.Route.DIRECT_TOOL, {"tool": "web_search"}) == "web_egress"
    assert tr.side_effect_for(tr.Route.DIRECT_TOOL, {"tool": "generate_image"}) == "gpu_job"
    assert tr.side_effect_for(tr.Route.DIRECT_TOOL, {"tool": "something_new"}) == "shell"


# --- route_turn ---------------------------------------------------------------

def _route(tmp_path, message, *, dry, options=None, env=None):
    row = {"id": "t", "message": message, "options": options or {}, "env": env or {}}
    with harness.environment(row, str(tmp_path), telemetry=True) as e:
        return tr.route_turn(
            message, dict(row["options"]), surface="web", request_id="req-1",
            session_id=harness.SESSION_ID, state=harness.make_state(e),
            screen_drivable=lambda: True, dry=dry,
        )


def test_dry_run_calls_no_model_and_writes_nothing(tmp_path, telemetry_file):
    connects = []
    real_connect = socket.socket.connect

    def counting_connect(self, address):
        connects.append(address)
        return real_connect(self, address)

    with mock.patch.object(socket.socket, "connect", counting_connect), \
            mock.patch("ollama.chat", side_effect=AssertionError("model called"), create=True):
        turn = _route(tmp_path, "draw a cat", dry=True)
    assert turn.route is tr.Route.NEW_IMAGE and turn.legacy_route is tr.Route.NEW_IMAGE
    assert turn.route_source == "rule" and turn.answers == {}
    assert connects == []
    assert not telemetry_file.exists()


def test_a_routed_turn_writes_one_row_without_its_text(tmp_path, telemetry_file):
    message = "remember that my locker code is 4417"
    with mock.patch.object(dt, "llm_debug_on", return_value=False):
        _route(tmp_path, message, dry=False)
    (row,) = _rows(telemetry_file)
    assert row["kind"] == "turn" and row["request_id"] == "req-1" and row["surface"] == "web"
    assert row["route"] == row["legacy_route"] and row["route_source"] == "rule"
    assert row["router_version"] == tr.ROUTER_VERSION
    assert row["facts"]["message_sha256"] == dt.text_sha256(message)
    assert "tools" not in row["facts"]
    assert "4417" not in json.dumps(row)
    assert "message" not in row


def test_llm_debug_keeps_the_text(tmp_path, telemetry_file):
    with mock.patch.object(dt, "llm_debug_on", return_value=True):
        _route(tmp_path, "yes", dry=False, env={"last_assistant_text": "Shall I create the file now?"})
    (row,) = _rows(telemetry_file)
    assert row["route"] == "accepted_offer"
    assert row["message"] == "yes"
    assert row["facts"]["pending_offer"] is True
    assert row["pending_offer_text"] == "Shall I create the file now?"


def test_the_legacy_route_is_always_a_candidate(tmp_path):
    turn = _route(tmp_path, "play devil's advocate on my plan", dry=True)
    assert turn.route is tr.Route.MEDIA_CONTROL
    assert "media" in turn.triggers
    assert turn.route in turn.candidates and turn.candidates[0] is tr.Route.CHAT


def test_a_picture_from_earlier_with_a_change_is_left_to_the_model(tmp_path):
    assert _route(tmp_path, "generate that image please", dry=True).route is tr.Route.NEW_IMAGE
    turn = _route(tmp_path, "generate that image, but make the sky stormy", dry=True)
    assert turn.route is not tr.Route.NEW_IMAGE


def test_flags_follow_the_engine(tmp_path):
    turn = _route(tmp_path, "hi", dry=True)
    assert turn.route is tr.Route.SOCIAL and turn.flags["skip_tools"] is True and turn.flags["use_rag"] is False
    turn = _route(tmp_path, "explain how transformers work, without any tools", dry=True)
    assert turn.route is tr.Route.CHAT and turn.flags["skip_tools"] is True
    assert "declines_tools" in turn.triggers
    turn = _route(tmp_path, "my favourite colour is green", dry=True)
    assert turn.flags["memory_candidate"] is True and "memory" in turn.triggers
    # A problem report is not filed as a fact.
    turn = _route(tmp_path, "my code is throwing an error", dry=True)
    assert turn.flags["memory_candidate"] is False and "memory" not in turn.triggers


def test_direct_turn_records_an_entry_point_shortcut(telemetry_file):
    with mock.patch.object(dt, "llm_debug_on", return_value=False):
        turn = tr.direct_turn("/imagine a red fox", "generate_image", surface="web", request_id="rid")
    assert turn.route is tr.Route.DIRECT_TOOL and turn.side_effect == "gpu_job"
    (row,) = _rows(telemetry_file)
    assert row["route_args"] == {"handler": "entry", "tool": "generate_image"}
    assert "red fox" not in json.dumps(row)
