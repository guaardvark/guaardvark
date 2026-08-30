"""Autoresearch 2.0 tests: the active-config layer, honest eval scoring,
and the research-run engine (Phases A+B of the 2026-08-10 rebuild)."""
import hashlib
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

try:
    from flask import Flask
    from backend.models import db, ResearchConfig, ResearchRun, EvalPair, Setting
    from backend.utils import experiment_context as ec
    from backend.services.rag_autoresearch_service import RAGAutoresearchService
    from backend.services.rag_eval_harness import RAGEvalHarness, LLMUnavailableError
    from backend.services.research_run_service import ResearchRunService
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        ec.invalidate_active_params_cache()
        yield app
        ec.clear_experiment_config()
        ec.invalidate_active_params_cache()
        db.session.remove()
        db.drop_all()


class TestActiveParamsLayer:
    def test_empty_overlay_without_promotion_or_experiment(self, app):
        with app.app_context():
            assert ec.get_active_rag_params() == {}

    def test_promoted_config_feeds_overlay(self, app):
        with app.app_context():
            db.session.add(ResearchConfig(
                params={"top_k": 8, "hybrid_search_alpha": 0.7},
                is_active=True, status="promoted",
            ))
            db.session.commit()
            ec.invalidate_active_params_cache()
            overlay = ec.get_active_rag_params()
            assert overlay["top_k"] == 8
            assert overlay["hybrid_search_alpha"] == 0.7

    def test_experiment_override_beats_promoted(self, app):
        with app.app_context():
            db.session.add(ResearchConfig(params={"top_k": 8}, is_active=True))
            db.session.commit()
            ec.invalidate_active_params_cache()
            ec.set_experiment_config({"top_k": 2})
            assert ec.get_active_rag_params()["top_k"] == 2
            ec.clear_experiment_config()
            assert ec.get_active_rag_params()["top_k"] == 8

    def test_hostile_values_are_clamped(self, app):
        with app.app_context():
            db.session.add(ResearchConfig(
                params={"top_k": 500, "hybrid_search_alpha": 9.0,
                        "dedup_threshold": "garbage"},
                is_active=True,
            ))
            db.session.commit()
            ec.invalidate_active_params_cache()
            overlay = ec.get_active_rag_params()
            assert overlay["top_k"] == 20          # clamped to max
            assert overlay["hybrid_search_alpha"] == 1.0
            assert "dedup_threshold" not in overlay  # non-numeric dropped

    def test_cache_invalidation_applies_immediately(self, app):
        with app.app_context():
            row = ResearchConfig(params={"top_k": 8}, is_active=True)
            db.session.add(row)
            db.session.commit()
            ec.invalidate_active_params_cache()
            assert ec.get_active_rag_params()["top_k"] == 8
            row.is_active = False
            db.session.commit()
            # cached until invalidated
            assert ec.get_active_rag_params()["top_k"] == 8
            ec.invalidate_active_params_cache()
            assert ec.get_active_rag_params() == {}


class TestHonestEval:
    def test_llm_unavailable_raises_not_floor(self, app):
        with app.app_context():
            harness = RAGEvalHarness()
            with patch.object(harness, "_get_llm", return_value=None):
                with pytest.raises(LLMUnavailableError):
                    harness._call_llm("prompt", role="judge")

    def test_llm_unavailable_mid_eval_becomes_crash(self, app):
        with app.app_context():
            svc = RAGAutoresearchService()
            with patch.object(svc.agent, "propose_experiment",
                              return_value={"parameter": "top_k", "new_value": 9,
                                            "hypothesis": "t", "source": "llm"}), \
                 patch.object(svc.eval_harness, "run_full_eval",
                              side_effect=LLMUnavailableError("ollama down")), \
                 patch.object(svc, "_load_config", return_value={
                     "params": {}, "baseline_score": 3.0, "phase": 1,
                     "phase_plateau_count": 0}), \
                 patch.object(svc, "_save_config"), \
                 patch.object(svc, "_log_experiment"):
                result = svc.run_single_experiment()
            assert result["status"] == "crash"

    def test_chunk_hash_alignment_produces_hits(self, app):
        """A retrieved chunk whose text matches a stored chunk hash scores a hit
        — the legacy whole-document hash could never match anything."""
        with app.app_context():
            harness = RAGEvalHarness()
            chunk = "The mitochondria is the powerhouse of the cell."
            pair = {
                "source_chunk_hashes": [hashlib.sha256(chunk.encode()).hexdigest()],
                "source_doc_id": 1,
            }
            results = [{"text": "unrelated"}, {"text": chunk}]
            metrics = harness._score_retrieval(pair, results)
            assert metrics["hit_rate_at_k"] == 1.0
            assert metrics["mrr"] > 0

    def test_inactive_pairs_are_excluded(self, app):
        with app.app_context():
            db.session.add(EvalPair(question="q1", expected_answer="a1", is_active=True))
            db.session.add(EvalPair(question="q2", expected_answer="a2", is_active=False))
            db.session.commit()
            harness = RAGEvalHarness()
            pairs = harness._get_active_eval_pairs()
            assert [p["question"] for p in pairs] == ["q1"]

    def test_judge_parse_failure_is_labeled(self, app):
        with app.app_context():
            harness = RAGEvalHarness()
            with patch.object(harness, "_call_llm", return_value="not json"):
                score = harness.score_response("q", "a", "r", [])
            assert score["composite"] is None
            assert score.get("judge_parse_failed") is True


class TestResearchRunEngine:
    def _mk_service(self):
        return ResearchRunService()

    def test_ollama_down_is_failed_precondition(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-1", mode="rag_tuning",
                              wall_clock_budget_s=60)
            db.session.add(run)
            db.session.commit()
            auto_svc = MagicMock()
            with patch("requests.get", side_effect=ConnectionError("refused")), \
                 patch("backend.services.rag_autoresearch_service.get_autoresearch_service",
                       return_value=auto_svc):
                svc_run.execute_run(run.id)
            db.session.refresh(run)
            assert run.status == "failed_precondition"
            assert "ollama" in (run.halt_reason or "").lower()
            assert "DID NOT RUN" in (run.report_md or "")

    def test_kill_flag_halts_run(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-2", mode="rag_tuning",
                              wall_clock_budget_s=3600)
            db.session.add(run)
            db.session.add(Setting(key="autoresearch_kill", value="true"))
            db.session.commit()
            auto_svc = MagicMock()
            auto_svc._load_config.return_value = {
                "params": {}, "baseline_score": 3.0, "phase": 1,
                "phase_plateau_count": 0,
            }
            with patch.object(svc_run, "_check_preconditions", return_value=(True, "")), \
                 patch("backend.services.rag_autoresearch_service.get_autoresearch_service",
                       return_value=auto_svc):
                svc_run.execute_run(run.id)
            db.session.refresh(run)
            assert run.status == "killed"
            assert run.halt_reason == "killed"
            auto_svc.run_single_experiment.assert_not_called()

    def test_budget_exhaustion_completes_with_report(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-3", mode="rag_tuning",
                              wall_clock_budget_s=0)  # instantly exhausted
            db.session.add(run)
            db.session.commit()
            auto_svc = MagicMock()
            auto_svc._load_config.return_value = {
                "params": {}, "baseline_score": 3.0, "phase": 1,
                "phase_plateau_count": 0,
            }
            auto_svc.eval_harness = MagicMock()
            with patch.object(svc_run, "_check_preconditions", return_value=(True, "")), \
                 patch.object(svc_run, "_confirm_and_activate",
                              return_value="no candidate configs produced"), \
                 patch("backend.services.rag_autoresearch_service.get_autoresearch_service",
                       return_value=auto_svc):
                svc_run.execute_run(run.id)
            db.session.refresh(run)
            assert run.status == "completed"
            assert run.halt_reason == "budget_exhausted"
            assert "Headline" in run.report_md

    def test_confirmation_rejects_weak_candidate(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-4", mode="rag_tuning")
            db.session.add(run)
            active = ResearchConfig(params={"top_k": 5}, is_active=True,
                                    status="promoted", composite_score=3.0)
            cand = ResearchConfig(params={"top_k": 9}, is_active=False,
                                  status="candidate", composite_score=3.2)
            db.session.add_all([active, cand])
            db.session.commit()
            auto_svc = MagicMock()
            # candidate barely better than active — below CONFIRMATION_MIN_DELTA
            auto_svc.eval_harness.run_full_eval.side_effect = [
                {"composite_score": 3.01}, {"composite_score": 3.0},
            ]
            note = svc_run._confirm_and_activate(auto_svc, run)
            assert "NOT confirmed" in note
            db.session.refresh(cand)
            db.session.refresh(active)
            assert cand.status == "rejected"
            assert active.is_active is True

    def test_confirmation_activates_clear_winner(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-5", mode="rag_tuning")
            db.session.add(run)
            active = ResearchConfig(params={"top_k": 5}, is_active=True,
                                    status="promoted", composite_score=3.0)
            cand = ResearchConfig(params={"top_k": 9}, is_active=False,
                                  status="candidate", composite_score=3.8)
            db.session.add_all([active, cand])
            db.session.commit()
            auto_svc = MagicMock()
            auto_svc.eval_harness.run_full_eval.side_effect = [
                {"composite_score": 3.8}, {"composite_score": 3.0},
            ]
            note = svc_run._confirm_and_activate(auto_svc, run)
            assert "CONFIRMED" in note
            db.session.refresh(cand)
            db.session.refresh(active)
            assert cand.is_active is True and cand.status == "promoted"
            assert active.is_active is False and active.status == "superseded"
            assert run.promotions == [cand.id]

    def test_report_flags_single_model_judging(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-6", mode="rag_tuning",
                              baseline_score=3.0, best_score=3.1,
                              halt_reason="budget_exhausted")
            ledger = [
                {"parameter": "top_k", "old_value": "5", "new_value": "8",
                 "delta": 0.1, "status": "keep", "proposal_source": "llm",
                 "proposer_model": "gemma4", "judge_model": "gemma4",
                 "composite_score": 3.1},
            ]
            report = svc_run._write_report(run, ledger)
            assert "single-model judging" in report
            assert "100% LLM" in report

    def test_confirmation_ignores_foreign_candidates(self, app):
        with app.app_context():
            svc_run = self._mk_service()
            run = ResearchRun(run_tag="t-scope", mode="rag_tuning")
            db.session.add(run)
            ours = ResearchConfig(
                params={"top_k": 9}, is_active=False,
                status="candidate", composite_score=3.8, source="local",
            )
            foreign = ResearchConfig(
                params={"top_k": 12}, is_active=False,
                status="candidate", composite_score=4.9,
                source="family_broadcast",
            )
            db.session.add_all([ours, foreign])
            db.session.commit()
            auto_svc = MagicMock()
            auto_svc.eval_harness.run_full_eval.side_effect = [
                {"composite_score": 3.8}, {"composite_score": 3.0},
            ]
            active = ResearchConfig(
                params={"top_k": 5}, is_active=True,
                status="promoted", composite_score=3.0,
            )
            db.session.add(active)
            db.session.commit()
            note = svc_run._confirm_and_activate(
                auto_svc, run, candidate_ids=[ours.id],
            )
            assert "CONFIRMED" in note
            db.session.refresh(ours)
            db.session.refresh(foreign)
            assert ours.is_active is True
            assert foreign.is_active is False
            assert foreign.status == "candidate"

    def test_stale_running_row_recovered_on_kickoff(self, app):
        with app.app_context():
            stale = ResearchRun(
                run_tag="old-dead", mode="rag_tuning", status="running",
                started_at=datetime.utcnow() - timedelta(hours=3),
            )
            db.session.add(stale)
            db.session.commit()
            svc_run = self._mk_service()
            with patch.object(svc_run, "_celery_has_live_execute_run",
                              return_value=False), \
                 patch.object(svc_run, "_enqueue_execute_run"):
                result = svc_run.kickoff(budget_hours=1, trigger="manual")
            db.session.refresh(stale)
            assert stale.status == "halted"
            assert stale.halt_reason == "worker_crashed"
            assert result["status"] == "started"
            assert result["run"]["run_tag"] != "old-dead"

    def test_status_running_when_research_run_active(self, app):
        with app.app_context():
            db.session.add(ResearchRun(
                run_tag="t-status", mode="rag_tuning", status="running",
                started_at=datetime.utcnow(), wall_clock_budget_s=3600,
            ))
            db.session.commit()
            svc = RAGAutoresearchService()
            st = svc.get_status()
            assert st["running"] is True
            assert st["active_run"]["run_tag"] == "t-status"
            assert st["active_run"]["budget_remaining_s"] is not None


class TestDirector:
    def test_allocate_plateaued_majority_code(self):
        split = ResearchRunService()._allocate(
            {"code_allowed": True, "rag_plateaued": True}, 1000)
        assert split["code_s"] >= 500
        assert split["code_s"] >= split["rag_s"]
        assert split["code_skip"] is None

    def test_allocate_skips_code_when_not_allowed(self):
        split = ResearchRunService()._allocate(
            {"code_allowed": False, "code_skip_reason": "codebase_locked",
             "rag_plateaued": True}, 1000)
        assert split["code_s"] == 0
        assert split["rag_s"] == 1000
        assert "codebase_locked" in split["code_skip"]

    def test_kickoff_default_mode_is_unified(self):
        import inspect
        assert inspect.signature(ResearchRunService.kickoff).parameters["mode"].default == "unified"

    def test_beat_kicks_unified(self):
        import inspect
        from backend.tasks import rag_autoresearch_tasks as tasks
        src = inspect.getsource(tasks.create_autoresearch_tasks)
        assert 'mode="unified"' in src

    def test_unified_skips_code_when_swarm_down(self, app):
        with app.app_context():
            run = ResearchRun(
                run_tag="t-unified-skip", mode="unified",
                wall_clock_budget_s=0, status="pending",
            )
            db.session.add(run)
            db.session.commit()
            svc_run = ResearchRunService()
            with patch.object(svc_run, "_check_preconditions", return_value=(True, "")), \
                 patch.object(svc_run, "_diagnose", return_value={
                     "code_allowed": False, "code_skip_reason": "swarm_unreachable",
                     "rag_plateaued": False, "tests_red": False,
                 }), \
                 patch.object(svc_run, "_run_code_slice") as mock_code, \
                 patch.object(svc_run, "_confirm_and_activate",
                              return_value="no candidate configs produced"), \
                 patch("backend.services.rag_autoresearch_service.get_autoresearch_service"):
                svc_run.execute_run(run.id)
            mock_code.assert_not_called()
            db.session.refresh(run)
            assert run.status == "completed"
            assert "code half skipped" in (run.report_md or "")
            assert "swarm_unreachable" in (run.report_md or "")

    def test_code_keep_rejected_when_rag_drops(self, app):
        from backend.api.rag_autoresearch_api import autoresearch_bp
        if "autoresearch" not in app.blueprints:
            app.register_blueprint(autoresearch_bp)
        with app.test_client() as client:
            res = client.post("/api/autoresearch/experiments", json={
                "parameter": "chunker",
                "new_value": "smarter dedup",
                "status": "keep",
                "source": "code_arm",
                "composite_score": 2.0,
                "baseline_score": 3.0,
                "pytest_passed": True,
                "run_tag": "t-pne",
            })
            assert res.status_code == 201
            body = res.get_json()
            assert body["recorded_status"] == "discard"

    def test_snapshot_pytest_does_not_dispatch_fixes(self, app):
        with app.app_context():
            from backend.services.self_improvement_service import (
                get_self_improvement_service,
            )
            si = get_self_improvement_service()
            si._running = False
            with patch("backend.services.self_improvement_service._is_codebase_locked",
                       return_value=False), \
                 patch("backend.services.self_improvement_service._is_self_improvement_enabled",
                       return_value=True), \
                 patch("backend.services.self_improvement_service.subprocess.run") as mock_run, \
                 patch.object(si, "_attempt_fix") as mock_fix, \
                 patch.object(si, "run_self_check") as mock_check:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                out = si.snapshot_pytest()
            mock_fix.assert_not_called()
            mock_check.assert_not_called()
            assert out.get("ok") is True
            assert out.get("red") is False
