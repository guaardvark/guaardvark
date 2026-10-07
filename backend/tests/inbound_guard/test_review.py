"""The review list: when items were flagged, what approving each one does, batches, and paging.

The database is in-memory sqlite and the git ledger a scratch file, so nothing
here reads or writes this clone's own guard state.
"""
import json
import os
import subprocess
from datetime import datetime, timezone

import pytest
from flask import Flask

from backend.models import InboundBaseline, InboundScan, PendingFix, db
from backend.services import inbound_guard_service as guard
from backend.utils.clock import iso_epoch, iso_local, iso_text, iso_utc
from scripts.inbound_guard import ledger


@pytest.fixture
def ledger_file(tmp_path, monkeypatch):
    path = tmp_path / "inbound-guard" / "ledger.jsonl"
    monkeypatch.setattr(guard, "_git_ledger_path", lambda: path)
    guard._ledger_cache.clear()
    return path


@pytest.fixture
def app(ledger_file, monkeypatch):
    from backend.api.inbound_guard_api import inbound_guard_bp

    monkeypatch.setattr(guard, "get_mode", lambda: "observe")
    monkeypatch.setattr(guard, "git_hooks_status", lambda: {"installed": True, "mode": "observe"})
    emitted = []
    monkeypatch.setattr(guard, "_emit", emitted.append)
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(inbound_guard_bp)
    app.emitted = emitted
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _row(**kw):
    values = dict(source="self_code", subject="edit x.py", mode="observe", verdict="hold", top_severity="medium",
                  digest="d" * 20, findings=json.dumps([{"rule": "exec.shell-true", "pack": "execution",
                                                         "severity": "medium", "path": "x.py", "line": 3,
                                                         "excerpt": "a quoted line", "why": "w",
                                                         "provider": "core"}]),
                  providers="[]", errors="[]", files=1, added_lines=1, status="open")
    values.update(kw)
    row = InboundScan(**values)
    db.session.add(row)
    db.session.commit()
    return row


def _verdict_record(digest, *, source="git-merge", verdict="hold", mode="observe", subject=None, **extra):
    return {"kind": "verdict", "digest": digest, "source": source, "verdict": verdict, "mode": mode,
            "subject": subject or f"merge {digest[:6]} into main", "findings": [], "providers": [],
            "top_severity": "medium", "files": 1, "added_lines": 1, **extra}


# -- times ----------------------------------------------------------------------------------------

def test_every_time_carries_an_explicit_offset():
    assert iso_utc(datetime(2026, 10, 7, 6, 30, 43, 13431)) == "2026-10-07T06:30:43.013Z"
    assert iso_local(datetime(2026, 10, 7, 16, 0, 0))[-6] in "+-"
    assert iso_epoch(0.0) == "1970-01-01T00:00:00.000Z"
    assert iso_text("2026-10-07T16:00:39-0400") == "2026-10-07T16:00:39-04:00"
    assert iso_text("2026-10-07T20:00:39Z") == "2026-10-07T20:00:39.000Z"
    assert iso_text("2026-10-07T16:00:39")[-6] in "+-"
    assert iso_text("not a time") is None and iso_text(None) is None


def test_stored_utc_reads_back_as_the_same_instant(app):
    row = _row(created_at=datetime(2026, 10, 7, 6, 30, 43), decided_at=datetime(2026, 10, 7, 9, 0, 0))
    data = row.to_dict()
    assert data["created_at"] == "2026-10-07T06:30:43.000Z"
    flagged = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
    assert flagged == datetime(2026, 10, 7, 6, 30, 43, tzinfo=timezone.utc)
    decided = datetime.fromisoformat(data["decided_at"])
    assert decided.utcoffset() is not None
    assert decided.replace(tzinfo=None) == datetime(2026, 10, 7, 9, 0, 0)


def test_new_ledger_records_carry_an_offset_with_a_colon(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append(path, {"kind": "verdict", "digest": "a" * 20})
    ts = json.loads(path.read_text())["ts"]
    assert ts[-3] == ":" and ts[-6] in "+-"
    assert datetime.fromisoformat(ts).utcoffset() is not None


# -- what approving does ----------------------------------------------------------------------------

@pytest.mark.parametrize("kw,fix_status,effect", [
    ({"source": "watch"}, None, "accept"),
    ({"source": "posture", "mode": "enforce"}, None, "record"),
    ({"source": "watch-audit"}, None, "record"),
    ({"pending_fix_id": 4}, "proposed", "apply"),
    ({"pending_fix_id": 4, "mode": "enforce"}, "applied", "record"),
    ({"payload": '{"kind": "write_file"}'}, None, "record"),
    ({"payload": '{"kind": "write_file"}', "mode": "enforce"}, None, "apply"),
    ({"mode": "enforce"}, None, "allow_again"),
    ({}, None, "record"),
])
def test_scan_effect(app, kw, fix_status, effect):
    row = _row(**kw)
    assert guard.scan_effect(row, fix_status) == effect
    assert guard.APPROVE_EFFECTS[effect]


@pytest.mark.parametrize("records,effect", [
    ([{"source": "git-merge", "mode": "observe"}], "record"),
    ([{"source": "git-merge", "mode": "enforce"}], "merge_hook"),
    ([{"source": "git-merge", "mode": "enforce", "approved": True}], "record"),
    ([{"source": "fetch", "mode": "enforce"}], "record"),
    ([{"source": "cli", "mode": "observe"}], "allow_again"),
    ([{"source": "cli", "mode": "observe"}, {"source": "git-merge", "mode": "observe"}], "record"),
    ([{"source": "self_code", "mode": "enforce"}], "allow_again"),
])
def test_git_effect(records, effect):
    assert guard.git_effect(records) == effect


def test_described_rows_say_what_approving_does_and_when(app):
    watched = _row(source="watch", subject="out-of-band change: x.py", created_at=datetime(2026, 10, 7, 1, 0, 0))
    db.session.add(InboundBaseline(path="x.py", sha256="0" * 64, mtime=1700000000.5, status="held",
                                   accepted="[]", scan_id=watched.id))
    db.session.commit()
    observed = _row(providers='["ext.scan"]', findings=json.dumps([
        {"rule": "r1", "pack": "p", "severity": "low", "path": "x", "line": None, "excerpt": "", "why": "w",
         "provider": "ext.scan"},
        {"rule": "r2", "pack": "p", "severity": "low", "path": "x", "line": None, "excerpt": "", "why": "w"},
    ]))
    by_id = {d["id"]: d for d in guard.describe_scans([watched, observed])}

    w = by_id[watched.id]
    assert w["what_approve_does"] == "accept the file as it is" and w["landed"] is True
    assert w["flagged_at"] == "2026-10-07T01:00:00.000Z"
    assert w["file_path"] == "x.py" and w["file_mtime"] == iso_epoch(1700000000.5)

    o = by_id[observed.id]
    assert o["what_approve_does"] == "record an approval only (the change already landed)"
    assert o["mode_at_record"] == "observe" and o["landed"] is True
    assert [f["origin"] for f in o["findings"]] == ["extension", "unknown"]
    assert o["scanners_run"] == [{"name": "core rules", "by": "Guaardvark"},
                                 {"name": "ext.scan", "by": "extension ext"}]


def test_findings_name_the_scanner_that_produced_them():
    from backend.tests.inbound_guard.test_service import _case

    case = _case("shell through subprocess")
    change = guard.change_for_file(case["path"], None, case["new"])

    def extension(changes, ctx):
        return [guard.engine().Finding("ext.rule", "ext", "low", case["path"], None, "", "why", provider="core")]

    verdict = guard.engine().scan([change], source="self_code", subject="x", mode="observe",
                                  scanners=[("ext.check", extension)])
    by_rule = {f.rule: f.to_dict() for f in verdict.findings}
    assert by_rule["ext.rule"]["provider"] == "ext.check"
    assert all(d["provider"] == "core" for rule, d in by_rule.items() if rule != "ext.rule")


# -- approving -----------------------------------------------------------------------------------------

def test_an_observed_file_write_is_not_written_again(app, monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "_land_payload", lambda payload: calls.append(payload) or [payload["path"]])
    observed = _row(payload=json.dumps({"kind": "write_file", "path": "x.py", "content": "old"}))
    enforced = _row(mode="enforce", digest="e" * 20,
                    payload=json.dumps({"kind": "write_file", "path": "y.py", "content": "new"}))
    assert guard.approve_and_land(observed.id, by="t")["landed_paths"] == []
    assert calls == []
    assert guard.approve_and_land(enforced.id, by="t")["landed_paths"] == ["y.py"]
    assert [c["path"] for c in calls] == ["y.py"]


def test_batch_approve_skips_blocks_and_reports_each_item(app, ledger_file):
    held = _row()
    blocked = _row(verdict="block", digest="b" * 20)
    ledger.append(ledger_file, _verdict_record("1" * 20))
    ledger.append(ledger_file, _verdict_record("2" * 20, verdict="block"))
    res = app.test_client().post("/api/settings/inbound_guard/scans/approve-batch", json={
        "ids": [held.id, blocked.id, 999], "git_digests": ["1" * 20, "2" * 20, "3" * 20], "note": "read"})
    body = res.get_json()["data"]
    outcome = {(r["kind"], r.get("id") or r.get("digest")): r["outcome"] for r in body["results"]}
    assert outcome == {("scan", held.id): "approved", ("scan", blocked.id): "skipped", ("scan", 999): "failed",
                       ("git", "1" * 20): "approved", ("git", "2" * 20): "skipped", ("git", "3" * 20): "failed"}
    assert (body["approved"], body["skipped"], body["failed"]) == (2, 2, 2)
    assert db.session.get(InboundScan, blocked.id).status == "open"
    assert ledger.approval_for(ledger_file, "1" * 20) and not ledger.approval_for(ledger_file, "2" * 20)


def test_batch_approve_takes_blocks_only_when_asked(app, ledger_file):
    blocked = _row(verdict="block", digest="b" * 20)
    ledger.append(ledger_file, _verdict_record("2" * 20, verdict="block"))
    res = app.test_client().post("/api/settings/inbound_guard/scans/approve-batch", json={
        "ids": [blocked.id], "git_digests": ["2" * 20], "include_blocks": True})
    assert res.get_json()["data"]["approved"] == 2
    assert db.session.get(InboundScan, blocked.id).status == "approved"
    assert ledger.approval_for(ledger_file, "2" * 20)["verdict"] == "block"


def test_batch_reject_leaves_git_records_alone(app, ledger_file):
    held = _row()
    res = app.test_client().post("/api/settings/inbound_guard/scans/reject-batch",
                                 json={"ids": [held.id], "git_digests": ["1" * 20]})
    body = res.get_json()["data"]
    assert (body["rejected"], body["skipped"]) == (1, 1)
    assert db.session.get(InboundScan, held.id).status == "rejected"


def test_batch_rejects_bad_input(app):
    client = app.test_client()
    assert client.post("/api/settings/inbound_guard/scans/approve-batch", json={"ids": "1"}).status_code == 400
    assert client.post("/api/settings/inbound_guard/scans/approve-batch",
                       json={"git_digests": ["--output=x"]}).status_code == 400


def test_web_git_approve_refuses_a_block_like_the_cli(app, ledger_file):
    ledger.append(ledger_file, _verdict_record("2" * 20, verdict="block"))
    client = app.test_client()
    refused = client.post(f"/api/settings/inbound_guard/git/{'2' * 20}/approve", json={"note": "x"})
    assert refused.status_code == 409 and ledger.approval_for(ledger_file, "2" * 20) is None
    unknown = client.post(f"/api/settings/inbound_guard/git/{'9' * 20}/approve", json={"note": "x"})
    assert unknown.status_code == 404
    taken = client.post(f"/api/settings/inbound_guard/git/{'2' * 20}/approve",
                        json={"note": "x", "override_block": True})
    assert taken.status_code == 200
    assert ledger.approval_for(ledger_file, "2" * 20)["note"] == "x"


# -- the git list ------------------------------------------------------------------------------------

def test_git_items_page_the_whole_ledger_and_fold_by_digest(app, ledger_file):
    for n in range(120):
        ledger.append(ledger_file, _verdict_record(f"{n:020d}", ts=f"2026-10-01T10:{n // 60:02d}:{n % 60:02d}-0400"))
    ledger.append(ledger_file, {"kind": "approval", "digest": f"{0:020d}", "by": "t", "note": ""})
    ledger.append(ledger_file, _verdict_record(f"{5:020d}", source="branch-update",
                                               subject="main 1111111aa..2222222bb", ref="refs/heads/main"))
    for _ in range(60):
        ledger.append(ledger_file, _verdict_record("f" * 20, verdict="allow"))

    page, total = guard.git_items(0, 50, with_commits=False)
    assert total == 119  # one approved, the allow verdicts never listed
    assert page[0]["digest"] == f"{5:020d}"  # newest record first
    assert page[0]["seen"] == 2 and page[0]["sources"] == ["git-merge", "branch-update"]
    assert (page[0]["old_sha"], page[0]["new_sha"]) == ("1111111aa", "2222222bb")
    assert page[0]["flagged_at"] == "2026-10-01T10:00:05-04:00"
    assert page[1]["digest"] == f"{119:020d}"
    rest, _ = guard.git_items(100, 50, with_commits=False)
    assert len(rest) == 19 and f"{0:020d}" not in {i["digest"] for i in page + rest}

    res = app.test_client().get("/api/settings/inbound_guard/scans?git_limit=10&git_offset=110")
    body = res.get_json()["data"]
    assert body["git_total"] == 119 and len(body["git"]) == 9


def test_commit_dates_for_a_range(tmp_path):
    def run(*args, date="2026-10-01T09:00:00+02:00"):
        env = dict(os.environ, GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date)
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True,
                              env=env).stdout.strip()

    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    run("config", "commit.gpgsign", "false")
    run("commit", "-q", "--allow-empty", "-m", "base", "--no-verify")
    old = run("rev-parse", "HEAD")
    for day in ("02", "03"):
        run("commit", "-q", "--allow-empty", "-m", day, "--no-verify", date=f"2026-10-{day}T09:00:00+02:00")
    new = run("rev-parse", "HEAD")
    dates = guard.commit_dates(old, new, repo=tmp_path)
    assert dates["count"] == 2
    assert dates["oldest_at"] == "2026-10-02T09:00:00+02:00" and dates["newest_at"] == "2026-10-03T09:00:00+02:00"
    assert guard.commit_dates("--output=x", new, repo=tmp_path) is None
    assert guard.commit_dates("0" * 40, new, repo=tmp_path) is None


def test_open_summary_counts_by_kind(app):
    _row()
    _row(source="watch")
    _row(mode="enforce", payload='{"kind": "write_file"}')
    _row(source="watch-audit", verdict="block")
    fix = PendingFix(file_path="x.py", proposed_diff="d", status="proposed")
    db.session.add(fix)
    db.session.commit()
    _row(pending_fix_id=fix.id)
    assert guard.open_summary() == {"waiting": 2, "landed": 2, "on_disk": 1, "blocked": 1}
