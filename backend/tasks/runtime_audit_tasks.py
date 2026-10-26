"""Celery tasks for the runtime-liveness layer.

Beat-driven tasks:
  * runtime_audit.flush_hits     — drain the in-memory tracker to symbol_hits
                                    (short cadence; the worker_process_shutdown
                                    signal handles child recycle, this catches
                                    the steady state).
  * runtime_audit.prune_old_hits — delete rows older than the retention window
                                    BUT ONLY where static_reachability is not
                                    TRUE. Reachable-but-cold rows (once-a-month
                                    handlers) are kept on purpose.
  * runtime_audit.reconcile_and_emit (B5, Phase 3) — flush, rebuild the static
                                    map, set static_reachability on each hit,
                                    compute liveness consensus and store those
                                    findings on their own (every later rebuild
                                    merges them back in, so a refresh of the
                                    static map never drops them), then DIFF
                                    findings vs the prior snapshot and dispatch
                                    ONLY genuinely-new HIGH findings whose kind is
                                    in DISPATCHABLE_KINDS. By construction this
                                    EXCLUDES every liveness kind — it is a drift
                                    alarm, never an auto-prune.
"""

import logging
import os
from datetime import datetime, timedelta

from celery import Celery

logger = logging.getLogger(__name__)


def create_runtime_audit_tasks(celery_app: Celery):
    @celery_app.task(name="runtime_audit.flush_hits", ignore_result=True)
    def flush_hits():
        """Drain the process-local execution tracker to the symbol_hits table."""
        try:
            from backend.services.execution_context_tracker import get_tracker
            flushed = get_tracker().flush()
            logger.debug("runtime_audit.flush_hits flushed %s symbols", flushed)
            return {"status": "ok", "flushed": flushed}
        except Exception as e:  # noqa: BLE001 - audit failure must never break beat
            logger.warning("runtime_audit.flush_hits failed (non-fatal): %s", e)
            return {"status": "error", "error": str(e)}

    @celery_app.task(name="runtime_audit.prune_old_hits", ignore_result=True)
    def prune_old_hits():
        """Delete stale symbol_hits older than the retention window.

        Never prunes rows where static_reachability IS TRUE — those are known
        reachable and being cold is expected (rare handlers). Rows that are
        NULL/false for reachability and stale are the audit signal we discard.
        """
        try:
            retention_days = int(os.environ.get("GUAARDVARK_RUNTIME_HITS_RETENTION_DAYS", "90"))
        except (TypeError, ValueError):
            retention_days = 90

        try:
            from backend.models import db, SymbolHit

            cutoff = datetime.now() - timedelta(days=retention_days)
            deleted = (
                db.session.query(SymbolHit)
                .filter(SymbolHit.last_fired_at < cutoff)
                .filter(SymbolHit.static_reachability.isnot(True))
                .delete(synchronize_session=False)
            )
            db.session.commit()
            logger.info(
                "runtime_audit.prune_old_hits deleted %s stale non-reachable rows (>%sd)",
                deleted, retention_days,
            )
            return {"status": "ok", "deleted": deleted, "retention_days": retention_days}
        except Exception as e:  # noqa: BLE001
            logger.warning("runtime_audit.prune_old_hits failed (non-fatal): %s", e)
            try:
                from backend.models import db
                db.session.rollback()
            except Exception:
                pass
            return {"status": "error", "error": str(e)}

    @celery_app.task(name="runtime_audit.reconcile_and_emit", ignore_result=True)
    def reconcile_and_emit():
        """Reconcile runtime hits against the static map, emit liveness consensus
        findings into the snapshot cache, and dispatch ONLY new HIGH dispatchable
        findings (which, by construction, EXCLUDES every liveness kind).

        Steps:
          1. flush() the tracker so the freshest hits are in symbol_hits.
          2. rebuild and store the static map of GUAARDVARK_ROOT.
          3. set SymbolHit.static_reachability from the map's node_meta lifecycle.
          4. liveness.analyze(hits, map) — consensus findings.
          5. store those findings (store.write_liveness) and merge them into the
             snapshot so /api/system-map/findings surfaces them.
          6. diff findings vs the PRIOR cached snapshot by fingerprint(); for NEW
             findings, dispatch ONLY those with kind in DISPATCHABLE_KINDS AND
             severity == HIGH. Liveness kinds are never dispatchable → drift alarm,
             not auto-prune.
        """
        try:
            return _reconcile_and_emit_impl()
        except Exception as e:  # noqa: BLE001 - audit failure must never break beat
            logger.warning("runtime_audit.reconcile_and_emit failed (non-fatal): %s", e)
            try:
                from backend.models import db
                db.session.rollback()
            except Exception:
                pass
            return {"status": "error", "error": str(e)}


# Lazily-bound module-level handles so tests can monkeypatch them without
# importing the live app at module-load time. _ensure_reconcile_deps() fills any
# that are still None on first real use.
build_map = None
dispatch_finding = None


def _ensure_reconcile_deps():
    """Bind heavy deps to module globals on first use (keeps import cheap, lets
    tests monkeypatch `build_map` / `dispatch_finding` on this module)."""
    global build_map, dispatch_finding
    if build_map is None:
        from backend.services.system_mapper.store import build as _build
        build_map = _build
    if dispatch_finding is None:
        from backend.services.system_mapper.actions import dispatch_finding as _df
        dispatch_finding = _df


def _reconcile_and_emit_impl():
    from pathlib import Path

    from backend.config import GUAARDVARK_ROOT
    from backend.models import db, SymbolHit
    from backend.services.execution_context_tracker import get_tracker
    from backend.services.system_mapper import liveness as liveness_mod
    from backend.services.system_mapper import store
    from backend.services.system_mapper.actions import DISPATCHABLE_KINDS

    _ensure_reconcile_deps()

    # 1. Flush the tracker so the snapshot reflects the latest runtime hits.
    try:
        get_tracker().flush()
    except Exception:
        logger.debug("reconcile_and_emit: flush failed (non-fatal)", exc_info=True)

    root = Path(GUAARDVARK_ROOT).resolve()

    # 2. The PRIOR snapshot, for the diff, then a fresh static map (stored).
    prior, _age = store.read_snapshot(root)
    prior_ids = {f.get("id") for f in (prior or {}).get("findings", []) if f.get("id")}
    payload = build_map(root)
    payload.pop("_cache", None)

    # node_meta -> reachable modules (active/auto-loaded or imported).
    reachable_modules = set(liveness_mod._static_modules(payload))

    # 3. Set static_reachability on each SymbolHit from the map.
    hits = SymbolHit.query.all()
    for h in hits:
        h.static_reachability = bool(h.module and h.module in reachable_modules)
    db.session.commit()

    # 4. Liveness consensus findings (advisory; never dispatchable).
    liveness_result = liveness_mod.analyze(hits, payload)
    liveness_findings = [f.to_dict() for f in liveness_result["findings"]]

    # 5. Store them on their own, then in the snapshot. Both writes are atomic,
    #    and every later rebuild merges the stored liveness back in.
    try:
        store.write_liveness(root, liveness_findings, liveness_result["stats"])
        store.merge_liveness(root, payload)
        store.write_json_atomic(store.snapshot_path(root), payload)
    except Exception as e:
        logger.warning("reconcile_and_emit: storing liveness failed: %s", e)

    # 6. Diff vs prior by fingerprint; dispatch ONLY new HIGH dispatchable.
    #    Liveness kinds are excluded from DISPATCHABLE_KINDS by construction, so
    #    they can NEVER be dispatched here — this is a drift alarm, not a pruner.
    #    dispatch_finding itself skips a finding whose earlier fix is still open.
    dispatched = []
    for f in payload["findings"]:
        fid = f.get("id")
        if fid in prior_ids:
            continue  # not new
        if f.get("kind") not in DISPATCHABLE_KINDS:
            continue  # excludes all liveness kinds + advisory kinds
        if f.get("severity") != "high":
            continue
        try:
            result = dispatch_finding(f, priority="high")
            if not (isinstance(result, dict) and result.get("skipped")):
                dispatched.append(fid)
        except Exception as e:
            logger.warning("reconcile_and_emit: dispatch of %s failed: %s", fid, e)

    logger.info(
        "runtime_audit.reconcile_and_emit: %s hits reconciled, %s liveness findings, "
        "%s new HIGH dispatchable dispatched",
        len(hits), len(liveness_findings), len(dispatched),
    )
    return {
        "status": "ok",
        "hits": len(hits),
        "liveness_findings": len(liveness_findings),
        "dispatched": dispatched,
    }


def schedule_runtime_audit_tasks(celery_app: Celery):
    """Merge runtime-audit beat entries into the existing beat schedule."""
    # .update() (mutation) per infra HIGH (fragile {**} on import/Celery conf).
    celery_app.conf.beat_schedule.update({
        "runtime-audit-flush-hits": {
            "task": "runtime_audit.flush_hits",
            "schedule": 300.0,  # every 5 minutes
            "options": {"queue": "default"},
        },
        "runtime-audit-prune-old-hits": {
            "task": "runtime_audit.prune_old_hits",
            "schedule": 86400.0,  # daily
            "options": {"queue": "default"},
        },
        "runtime-audit-reconcile-and-emit": {
            "task": "runtime_audit.reconcile_and_emit",
            "schedule": 43200.0,  # every 12 hours
            "options": {"queue": "default"},
        },
    })
