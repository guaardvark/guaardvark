"""Settings → Agents → Inbound guard: the mode, the verdicts, and decisions on held changes.

Every mutating route here is under /api/settings/inbound_guard, which auth_guard
keeps closed to other hosts: switching the guard off or approving a held change
must come from this machine or carry the API key.
"""
import logging

from flask import Blueprint, request

from backend.services import inbound_guard_service as guard
from backend.utils.response_utils import error_response, success_response

logger = logging.getLogger(__name__)

inbound_guard_bp = Blueprint("inbound_guard", __name__, url_prefix="/api/settings/inbound_guard")


@inbound_guard_bp.route("", methods=["GET"])
def get_state():
    from backend.models import InboundScan, db

    open_count = 0
    open_by = None
    try:
        open_count = db.session.query(InboundScan.id).filter_by(status="open").count()
        open_by = guard.open_summary()
    except Exception as exc:
        logger.warning("inbound guard: could not count open verdicts: %s", exc)
    from backend.services import inbound_guard_watch

    return success_response({
        "mode": guard.get_mode(),
        "modes": list(guard.MODES),
        "open": open_count,
        "open_by": open_by,
        "providers": guard.registered(),
        "git": guard.git_hooks_status(),
        "sweep": inbound_guard_watch.last_summary(),
    })


@inbound_guard_bp.route("", methods=["POST"])
def set_state():
    data = request.get_json(silent=True) or {}
    try:
        mode = guard.set_mode(str(data.get("mode", "")))
    except ValueError as exc:
        return error_response(str(exc), 400)
    return success_response({"mode": mode})


MAX_PAGE = 500
MAX_BATCH = 500


def _int_arg(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


@inbound_guard_bp.route("/scans", methods=["GET"])
def list_scans():
    """Verdicts from the database and changes the git side recorded, each a page with its total.

    ``limit``/``offset`` page the database rows; ``git_limit``/``git_offset`` page
    the git items (default: the same limit, from 0), read from the whole ledger.
    ``git=0`` leaves the git side out, for cheap polling.
    """
    from backend.models import InboundScan, db

    status = request.args.get("status", "open")
    limit = _int_arg("limit", 50, 1, MAX_PAGE)
    offset = _int_arg("offset", 0, 0, 10 ** 9)
    query = db.session.query(InboundScan)
    if status != "all":
        query = query.filter_by(status=status)
    total = query.count()
    rows = query.order_by(InboundScan.created_at.desc(), InboundScan.id.desc()).offset(offset).limit(limit).all()
    body = {"scans": guard.describe_scans(rows), "scans_total": total, "mode": guard.get_mode(),
            "git": [], "git_total": 0}
    if request.args.get("git", "1") != "0":
        hooks_mode = guard.git_hooks_status().get("mode", "off")
        page, git_total = guard.git_items(
            _int_arg("git_offset", 0, 0, 10 ** 9), _int_arg("git_limit", limit, 1, MAX_PAGE),
            include_approved=status == "all", hooks_mode=hooks_mode,
        )
        body.update(git=page, git_total=git_total, git_mode=hooks_mode)
    return success_response(body)


@inbound_guard_bp.route("/scans/<int:scan_id>", methods=["GET"])
def get_scan(scan_id):
    """One verdict with what a person needs to judge it: the diff it would apply."""
    from backend.models import InboundScan, PendingFix, db
    from backend.utils.clock import iso_local

    row = db.session.get(InboundScan, scan_id)
    if row is None:
        return error_response("No such inbound verdict", 404)
    data = {**guard.describe_scans([row])[0], "payload": row.to_dict(include_payload=True)["payload"]}
    if row.pending_fix_id:
        fix = db.session.get(PendingFix, row.pending_fix_id)
        if fix is not None:
            data["fix"] = {"id": fix.id, "status": fix.status, "file_path": fix.display_path(),
                           "diff": fix.proposed_diff, "description": fix.fix_description,
                           "created_at": iso_local(fix.created_at)}
    payload = data.get("payload") or {}
    if payload.get("kind") == "write_file":
        from backend.services.guarded_code_service import build_unified_diff

        target = guard.REPO_ROOT / payload["path"]
        current = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        data["diff"] = build_unified_diff(payload["path"], current, payload.get("content", ""))
        data["payload"] = {k: v for k, v in payload.items() if k != "content"}
    elif payload.get("kind") == "delete_file":
        data["diff"] = f"delete {payload['path']}"
    elif payload.get("kind") == "rename_file":
        data["diff"] = f"rename {payload['path']} -> {payload['new_path']}"
    return success_response(data)


def _act(fn, scan_id, **kwargs):
    from backend.services.guarded_code_service import GuardedCodeError

    try:
        return success_response(fn(scan_id, **kwargs))
    except LookupError as exc:
        return error_response(str(exc), 404)
    except ValueError as exc:
        return error_response(str(exc), 409)
    except GuardedCodeError as exc:
        return error_response(str(exc), exc.status_code, exc.code)


@inbound_guard_bp.route("/scans/<int:scan_id>/approve", methods=["POST"])
def approve_scan(scan_id):
    """Approve a held change and land it; nothing changes if it cannot land."""
    data = request.get_json(silent=True) or {}
    return _act(guard.approve_and_land, scan_id, by=str(data.get("by") or "operator"),
                note=str(data.get("note") or ""), override_block=bool(data.get("override_block")))


@inbound_guard_bp.route("/scans/<int:scan_id>/reject", methods=["POST"])
def reject_scan(scan_id):
    data = request.get_json(silent=True) or {}
    return _act(guard.reject_held, scan_id, by=str(data.get("by") or "operator"), note=str(data.get("note") or ""))


@inbound_guard_bp.route("/scans/<int:scan_id>/decide", methods=["POST"])
def decide_scan(scan_id):
    from backend.services.guarded_code_service import GuardedCodeError

    data = request.get_json(silent=True) or {}
    try:
        result = guard.decide(
            scan_id,
            str(data.get("decision", "")),
            by=str(data.get("by") or "operator"),
            note=str(data.get("note") or ""),
            override_block=bool(data.get("override_block")),
        )
    except LookupError as exc:
        return error_response(str(exc), 404)
    except ValueError as exc:
        return error_response(str(exc), 400)
    except GuardedCodeError as exc:
        return error_response(str(exc), exc.status_code, exc.code)
    return success_response(result)


def _is_digest(value) -> bool:
    return isinstance(value, str) and value.isalnum() and 0 < len(value) <= 40


@inbound_guard_bp.route("/git/<digest>/approve", methods=["POST"])
def approve_git(digest):
    """Record an approval for a git verdict. A block needs ``override_block``, as with check_inbound.py approve."""
    data = request.get_json(silent=True) or {}
    if not _is_digest(digest):
        return error_response("Not a verdict digest", 400)
    try:
        entry = guard.approve_git(digest, str(data.get("by") or "operator"), str(data.get("note") or ""),
                                  override_block=bool(data.get("override_block")))
    except LookupError as exc:
        return error_response(str(exc), 404)
    except ValueError as exc:
        return error_response(str(exc), 409)
    return success_response({"digest": digest, "approved": True, "verdict": entry.get("verdict"),
                             "effect": guard.git_effect_for(digest)})


def _batch_body():
    """((body, ids, digests), None) from a batch request, or (None, an error response)."""
    data = request.get_json(silent=True) or {}
    ids, digests = data.get("ids") or [], data.get("git_digests") or []
    if not isinstance(ids, list) or not isinstance(digests, list):
        return None, error_response("ids and git_digests must be lists", 400)
    if len(ids) + len(digests) > MAX_BATCH:
        return None, error_response(f"At most {MAX_BATCH} items in one batch", 400)
    if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        return None, error_response("ids must be whole numbers", 400)
    if not all(_is_digest(d) for d in digests):
        return None, error_response("Not a verdict digest", 400)
    return (data, list(dict.fromkeys(ids)), list(dict.fromkeys(digests))), None


def _batch_counts(results):
    counts = {"approved": 0, "rejected": 0, "skipped": 0, "failed": 0}
    for item in results:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    return counts


@inbound_guard_bp.route("/scans/approve-batch", methods=["POST"])
def approve_batch():
    """Approve several items, one at a time; blocks are skipped unless ``include_blocks`` is true."""
    parsed, error = _batch_body()
    if error:
        return error
    data, ids, digests = parsed
    results = guard.approve_batch(ids, digests, by=str(data.get("by") or "operator"),
                                  note=str(data.get("note") or ""), include_blocks=data.get("include_blocks") is True)
    return success_response({"results": results, **_batch_counts(results)})


@inbound_guard_bp.route("/scans/reject-batch", methods=["POST"])
def reject_batch():
    """Reject several database items, one at a time; git records come back skipped."""
    parsed, error = _batch_body()
    if error:
        return error
    data, ids, digests = parsed
    results = guard.reject_batch(ids, digests, by=str(data.get("by") or "operator"), note=str(data.get("note") or ""))
    return success_response({"results": results, **_batch_counts(results)})


@inbound_guard_bp.route("/sweep", methods=["POST"])
def sweep_now():
    """Read every watched file that changed since the last sweep, now."""
    from backend.services import inbound_guard_watch

    if not guard.is_on():
        return error_response("The inbound guard is off; turn it on to sweep.", 409)
    return success_response(inbound_guard_watch.sweep())
