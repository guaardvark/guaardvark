"""Cast Library — CRUD over Subjects. Reusable across Productions."""
import json
import logging
import os
import re
import struct
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, request, jsonify, send_file
from werkzeug.utils import secure_filename

from backend.config import STORAGE_DIR
from backend.celery_dispatch import TaskNotStarted, mark_progress_not_started
from backend.models import db, Subject, SubjectSample
from backend.services.cast_lora_selection import (
    CastLoraRefusal,
    record_subject_lora,
    set_default_lora,
)
from backend.services.media_model_registry import (
    ZIMAGE_TURBO,
    FLUX_DEV,
    get_profile,
    write_lora_sidecar,
)

bp = Blueprint("cast_library_api", __name__, url_prefix="/api/cast-library")
log = logging.getLogger(__name__)

VALID_KINDS = {"character", "environment", "prop"}

# Repo root (…/backend/api/cast_library_api.py → parents[2]). Ref paths are stored
# relative to the project root; resolving against this instead of the process cwd
# makes preview serving robust no matter where/how the backend was launched.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref_path(p: str) -> str | None:
    """Return an on-disk absolute path for a stored ref image, or None.
    Tries the value as-is (abs or cwd-relative), then relative to the repo root,
    then relative to the data/STORAGE_DIR — covers training-dir refs and cast_refs
    uploads. The resolved path MUST live under one of those roots: ref_image_paths
    can be set via the create-API body, so this is the guard against a crafted
    '../../etc/passwd' reaching send_file."""
    if not p:
        return None
    roots = [_PROJECT_ROOT]
    try:
        storage = current_app.config.get("STORAGE_DIR")  # registered key (NOT "DATA_DIR")
        if storage:
            roots.append(Path(storage).resolve())
    except RuntimeError:
        pass  # outside app context (shouldn't happen on a request, but be safe)
    candidates = [p] + [str(r / p) for r in roots]
    for c in candidates:
        try:
            if not os.path.isfile(c):
                continue
            # Always hand send_file an ABSOLUTE path — Flask raises on a relative one.
            ap = os.path.abspath(c)
            # Containment: the file must sit inside an allowed root, else skip it.
            if any(os.path.commonpath([ap, str(r)]) == str(r) for r in roots):
                return ap
        except (OSError, ValueError):
            continue
    return None

# Standard image formats — anything else is rejected at upload time.
_ALLOWED_REF_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_MAX_REF_BYTES = 25 * 1024 * 1024  # 25 MB per image — generous, but caps runaway uploads.

# NOTE: there is intentionally NO "generated-frame" provenance guard here. An earlier
# version rejected uploads whose filenames looked like the system's own outputs
# (storyboard/i2v/sample frames) to avoid a model-collapse feedback loop. Removed at
# Operator call (2026-06-25): characters here are often AI-generated to begin with, so
# "real photo vs generated" is a false distinction — generated-but-on-model frames are
# legitimate training material. Curating a coherent reference pool is the user's job;
# the system should not second-guess which images belong to a character.


def _cast_ref_dir(subject_id: int) -> Path:
    """Where reference images for a Subject live on disk. Created lazily."""
    base = Path(current_app.config.get("STORAGE_DIR") or "data")  # registered key (NOT "DATA_DIR")
    target = base / "cast_refs" / str(subject_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _serialize(s: Subject) -> dict:
    try:
        from backend.services.media_model_registry import (
            get_profile,
            subject_base_model_id,
        )
        base_id = subject_base_model_id(s)
        base_prof = get_profile(base_id) or {}
    except Exception:
        base_id = "zimage-turbo"
        base_prof = {}
    return {
        "id": s.id, "kind": s.kind, "name": s.name,
        "description": s.description,
        "ref_image_paths": s.ref_image_paths or [],
        "trigger_word": s.trigger_word,
        "voice_id": s.voice_id,
        "lora_path": s.lora_path,
        "lora_version": s.lora_version,
        # Every LoRA this member holds, one row per import/train per base; the
        # Versions tab groups these by base. Newest version of each base first.
        "lora_versions": sorted(
            (r.to_dict() for r in (s.lora_versions or [])),
            key=lambda r: (r["base_model_id"], -r["version"]),
        ),
        "training_status": s.training_status,
        "training_error": getattr(s, 'training_error', None),
        "current_training_job_id": getattr(s, 'current_training_job_id', None),
        "last_trained_image_paths": getattr(s, 'last_trained_image_paths', None) or [],
        "last_trained_at": getattr(s, 'last_trained_at', None).isoformat() if getattr(s, 'last_trained_at', None) else None,
        "bible": getattr(s, 'bible', None),
        "training_settings_json": getattr(s, "training_settings_json", None) or {},
        # Media model registry — LoRA train/inference base (Z-Image default; SDXL legacy)
        "base_model_id": base_id,
        "base_model_name": base_prof.get("name"),
        "lora_format": base_prof.get("lora_format"),
        "train_ready": bool(base_prof.get("train_ready")),
        "train_status_note": base_prof.get("train_status_note"),
        "caption_coverage": _caption_coverage_for(s),
        "smoke_identity": ((s.training_settings_json or {}).get("smoke_identity")
                           if getattr(s, "training_settings_json", None) else None),
        "bible_vision_grounded": bool(
            (getattr(s, "training_settings_json", None) or {}).get("bible_vision_grounded")
        ),
        "bible_manual_override": bool(
            (getattr(s, "training_settings_json", None) or {}).get("bible_manual_override")
        ),
        "bible_identity_marks": (
            (getattr(s, "training_settings_json", None) or {}).get("bible_identity_marks") or ""
        ),
        "class_token": (
            (getattr(s, "training_settings_json", None) or {}).get("class_token") or None
        ),
    }


def _identity_marks_for_captions(s: Subject) -> str:
    """Short vision marks for captions — never dump a long invented bible."""
    cfg = getattr(s, "training_settings_json", None) or {}
    marks = (cfg.get("bible_identity_marks") or "").strip()
    if marks:
        return marks[:200]
    # If vision-grounded bible exists, take a short prefix of tag-like content
    if cfg.get("bible_vision_grounded") and cfg.get("bible_vision_tags"):
        return ", ".join(cfg["bible_vision_tags"][:12])[:200]
    return ""


def _caption_coverage_for(s: Subject) -> dict:
    try:
        from backend.services.lora_pretrain_gate import caption_coverage_stats
        stats = caption_coverage_stats(s)
        return {
            "images": stats.get("images", 0),
            "rich_captions": stats.get("rich_captions", 0),
            "bare_captions": stats.get("bare_captions", 0),
        }
    except Exception:
        return {"images": 0, "rich_captions": 0, "bare_captions": 0}


@bp.get("")
def list_subjects():
    subjects = Subject.query.order_by(Subject.created_at.desc()).all()
    return jsonify({"subjects": [_serialize(s) for s in subjects]})


def _maybe_backfill_promotion(s: Subject) -> Subject:
    """Graduate samples already recorded in last_trained_image_paths (pre-feature rows).

    Idempotent. Called from read paths so older trained subjects pick up the new
    Generate-vs-Training Data contract without requiring a retrain.
    """
    if s is None or s.training_status != "trained" or not (s.last_trained_image_paths or []):
        return s
    try:
        pending = (
            SubjectSample.query
            .filter_by(subject_id=s.id, approved=True, status="done")
            .filter(SubjectSample.promoted_to_training.is_(False))
            .count()
        )
        if not pending:
            return s
        from backend.services.sample_promotion import promote_samples_after_train
        promo = promote_samples_after_train(s, s.last_trained_image_paths or [])
        if promo.get("promoted") or promo.get("paths_added"):
            db.session.commit()
            return db.session.get(Subject, s.id) or s
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
    return s


@bp.get("/subjects/<int:subject_id>")
def get_subject(subject_id: int):
    """Efficient single-subject fetch (replaces the previous client-side list+find
    pattern in productionService.getCastSubject for the detail page).

    Supports ?include=samples to return the full ordered SubjectSample list
    inline, reducing roundtrips for CastMemberPage loads and polling.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    s = _maybe_backfill_promotion(s)

    data = {"subject": _serialize(s)}

    # Optional eager samples for the Cast detail page (Generate + Training tabs).
    if request.args.get("include") == "samples":
        samples = (
            SubjectSample.query
            .filter_by(subject_id=subject_id)
            .order_by(SubjectSample.index)
            .all()
        )
        data["samples"] = [r.to_dict() for r in samples]

    return jsonify(data)


@bp.post("/subjects")
def create_subject():
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    name = body.get("name")
    if kind not in VALID_KINDS:
        return jsonify({"error": f"kind must be one of {sorted(VALID_KINDS)}"}), 400
    if not name:
        return jsonify({"error": "name is required"}), 400
    s = Subject(
        kind=kind, name=name,
        description=body.get("description") or "",
        ref_image_paths=body.get("ref_image_paths") or [],
        trigger_word=(body.get("trigger_word") or "").strip() or None,
        voice_id=(body.get("voice_id") or "").strip() or None,
    )
    db.session.add(s); db.session.commit()
    return jsonify(_serialize(s)), 201


@bp.patch("/subjects/<int:subject_id>")
def update_subject(subject_id):
    """Update editable Subject fields. The cast UI uses this to assign a
    character's voice (voice_id) and LoRA trigger word after creation — without
    it, voice_id could only ever be set by the Casting Director's auto-pick."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    if "name" in body and body["name"]:
        s.name = body["name"]
    if "description" in body:
        s.description = body["description"] or ""
    # Empty string clears the field (sets NULL); absent key leaves it untouched.
    if "voice_id" in body:
        s.voice_id = (body["voice_id"] or "").strip() or None
    if "trigger_word" in body:
        s.trigger_word = (body["trigger_word"] or "").strip() or None
    if "bible" in body:
        # Manual Overview edit: mark as operator override so UI warns to re-sync.
        new_bible = (body["bible"] or "").strip() or None
        old = (s.bible or "").strip() or None
        s.bible = new_bible
        if new_bible != old:
            cfg = dict(s.training_settings_json or {})
            cfg["bible_vision_grounded"] = False
            cfg["bible_manual_override"] = True
            s.training_settings_json = cfg
    if "training_settings" in body:
        # Merge, not replace: the same JSON holds the identity flags and the
        # smoke score (see merge_training_settings).
        from backend.services.lora_training_settings import merge_training_settings
        s.training_settings_json = merge_training_settings(
            s.training_settings_json, body["training_settings"],
        )
    db.session.commit()
    return jsonify(_serialize(s))


@bp.get("/subjects/<int:subject_id>/preview")
def subject_preview(subject_id):
    """Serve a thumbnail for a Subject — its first existing reference image.
    Used by the character picker UI. Falls back to 404 if no image is on disk."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    for p in (s.ref_image_paths or []):
        resolved = _resolve_ref_path(p)
        if resolved:
            return send_file(resolved, max_age=3600)
    return jsonify({"error": "no_preview"}), 404


@bp.get("/subjects/<int:subject_id>/refs/<int:index>/image")
def ref_image(subject_id: int, index: int):
    """Serve the Nth reference image for a Subject (by position in ref_image_paths).
    The preview route only serves the first; this lets the UI render EVERY ref as a
    thumbnail. Resolved through the same containment guard as preview."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    paths = s.ref_image_paths or []
    if index < 0 or index >= len(paths):
        return jsonify({"error": "not_found"}), 404
    resolved = _resolve_ref_path(paths[index])
    if not resolved:
        return jsonify({"error": "no_image"}), 404
    return send_file(resolved, max_age=3600)


@bp.delete("/subjects/<int:subject_id>/refs/<int:index>")
def delete_subject_ref(subject_id: int, index: int):
    """Remove the Nth reference image from a Subject — both the entry in
    ref_image_paths and the file on disk. The Training Data tab calls this so a
    user can prune a bad/over-large reference set (e.g. dropped 57, wants fewer)
    without nuking the whole character. Index is the position in ref_image_paths,
    matching the GET …/refs/<index>/image the thumbnails render from."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    paths = list(s.ref_image_paths or [])
    if index < 0 or index >= len(paths):
        return jsonify({"error": "index_out_of_range"}), 404

    removed = paths.pop(index)
    # Reassign (not in-place mutate) so SQLAlchemy flags the JSON column dirty.
    s.ref_image_paths = paths
    db.session.commit()

    # Best-effort disk cleanup — only delete files that resolve INSIDE an allowed
    # root (same containment guard as serving), so a crafted stored path can't
    # make us unlink something outside cast_refs/.
    try:
        resolved = _resolve_ref_path(removed)
        if resolved and os.path.isfile(resolved):
            os.remove(resolved)
    except OSError as e:
        log.warning("delete_subject_ref: could not unlink %s: %s", removed, e)

    return jsonify(_serialize(s)), 200


@bp.delete("/subjects/<int:subject_id>")
def delete_subject(subject_id):
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    # Cancel the associated unified progress job (if any) so it disappears from
    # the global queue/Activity. This is key for "if a character is deleted,
    # then any jobs should cancel and be purged (training jobs)".
    if s.current_training_job_id:
        try:
            from backend.utils.unified_progress_system import get_unified_progress
            prog = get_unified_progress()
            prog.cancel_process(s.current_training_job_id, "Subject deleted")
        except Exception:
            log.warning("could not mark training job %s cancelled", s.current_training_job_id, exc_info=True)

    # Mark training as purged if it was running.
    if s.training_status == "training":
        s.training_status = "failed"
        s.training_error = "Subject deleted — training job cancelled/purged."
    s.current_training_job_id = None
    db.session.commit()

    db.session.delete(s)
    db.session.commit()
    return "", 204


@bp.post("/subjects/<int:subject_id>/upload-refs")
def upload_subject_refs(subject_id):
    """Drag-and-drop receiver for reference images. Accepts one or more
    multipart files under the ``files`` field, saves them under
    ``data/cast_refs/<subject_id>/``, appends the resolved paths onto
    ``Subject.ref_image_paths``, and returns the updated subject.

    The user-facing flow expects no path-typing — the frontend drops images
    here and the server owns persistence.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "subject not found"}), 404

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files (expected multipart field 'files')"}), 400

    target_dir = _cast_ref_dir(subject_id)
    saved_paths: list[str] = []
    skipped: list[dict] = []

    for f in files:
        if not f or not f.filename:
            continue
        safe_name = secure_filename(f.filename) or ""
        ext = Path(safe_name).suffix.lower()
        if ext not in _ALLOWED_REF_EXTS:
            skipped.append({"name": f.filename, "reason": f"unsupported extension {ext!r}"})
            continue

        # Resolve collisions by appending -1, -2, … so multiple uploads with
        # the same source filename don't clobber each other.
        stem = Path(safe_name).stem or "ref"
        candidate = target_dir / f"{stem}{ext}"
        n = 1
        while candidate.exists():
            candidate = target_dir / f"{stem}-{n}{ext}"
            n += 1

        # Stream-write with a per-file size cap so a malicious / runaway
        # upload can't fill disk. Anything that goes wrong inside the loop
        # (network drop, disk full, write error) must clean up the partial
        # file — otherwise we leave half-written turds in cast_refs/.
        written = 0
        oversized = False
        write_error = None
        try:
            with open(candidate, "wb") as out:
                while True:
                    chunk = f.stream.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_REF_BYTES:
                        oversized = True
                        break
                    out.write(chunk)
        except OSError as e:
            write_error = e
        if oversized or write_error is not None:
            candidate.unlink(missing_ok=True)
            reason = "too large" if oversized else f"write failed: {write_error}"
            skipped.append({"name": f.filename, "reason": reason})
            continue
        saved_paths.append(str(candidate))

    s.ref_image_paths = list(s.ref_image_paths or []) + saved_paths
    db.session.commit()

    # VLM caption new uploads so train is not stuck with bare "a photo of TOKEN".
    caption_summary = {}
    if saved_paths:
        try:
            from backend.services.character_captioner import ensure_subject_image_captions
            from backend.services.character_identity_prompt import resolve_class_token
            token = (s.trigger_word or "").strip() or s.name
            marks = _identity_marks_for_captions(s)
            caption_summary = ensure_subject_image_captions(
                saved_paths,
                trigger=token,
                identity_marks=marks,
                class_token=resolve_class_token(s),
                kind=s.kind or "character",
            )
        except Exception as e:
            caption_summary = {"error": str(e)[:200]}

    # Auto-sync identity when we have enough refs and are not yet vision-grounded.
    identity_sync = {}
    refs_now = list(s.ref_image_paths or [])
    if len(refs_now) >= 3:
        try:
            from backend.services.cast_identity_manager import (
                ensure_vision_identity,
                subject_is_vision_grounded,
            )
            if not subject_is_vision_grounded(s):
                identity_sync = ensure_vision_identity(s)
                db.session.refresh(s)
            else:
                # Already grounded: still refresh captions for new uploads only
                # (ensure_subject_image_captions above). Full vision re-scan is
                # operator-driven via Sync identity from photos.
                identity_sync = {"ok": True, "synced": False, "skipped": "already_grounded"}
        except Exception as e:  # noqa: BLE001
            identity_sync = {"ok": False, "error": str(e)[:200]}

    return jsonify({
        "subject": _serialize(s),
        "saved": saved_paths,
        "skipped": skipped,
        "captions": caption_summary,
        "identity_sync": identity_sync,
    })


# ── Character Generator endpoints ─────────────────────────────────────────────
#
# POST  /cast-library/subjects/<id>/plan          — run Casting Director LLM
#                                                   synchronously; persist bible +
#                                                   SubjectSample rows; return them.
# POST  /cast-library/subjects/<id>/generate      — dispatch character.generate_samples
#                                                   (FLUX loop, async Celery task).
# GET   /cast-library/subjects/<id>/samples       — list SubjectSamples.
# POST  /cast-library/subjects/<id>/samples/<sid>/regenerate
#                                                 — dispatch character.regen_sample.
# POST  /cast-library/subjects/<id>/samples/approve
#                                                 — bulk-approve a set of samples.


@bp.post("/subjects/<int:subject_id>/bible/from-refs")
def rebuild_bible_from_refs(subject_id: int):
    """Sync identity from photos (Cast Identity Manager).

    Vision-rescan refs → rewrite bible/marks → refresh captions → recompose
    sheet sample prompts. Train LoRA does not rewrite the bible.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    if not (s.ref_image_paths or []):
        return jsonify({"error": "no_refs", "message": "Upload reference photos first."}), 400

    body = request.get_json(silent=True) or {}
    from backend.services.cast_identity_manager import sync_identity_from_refs
    result = sync_identity_from_refs(
        subject_id,
        refresh_captions=bool(body.get("refresh_captions", True)),
        refresh_sample_prompts=bool(body.get("refresh_sample_prompts", True)),
    )
    if not result.get("ok"):
        status = 503 if result.get("error") == "ollama_unavailable" else 502
        if result.get("error") == "no_refs":
            status = 400
        return jsonify(result), status
    db.session.refresh(s)
    return jsonify({
        "subject": _serialize(s),
        **{k: result.get(k) for k in (
            "bible", "trigger_word", "tags", "marks", "class_token",
            "sources_used", "captions_refreshed", "samples_updated", "vision_grounded",
        )},
    })


@bp.post("/subjects/<int:subject_id>/plan")
def plan_character(subject_id: int):
    """Run the Casting Director synchronously to produce bible + shot plan.

    When reference photos exist, keeps a vision-grounded bible (or builds one)
    instead of inventing appearance from text. Images are NOT generated here.

    Request body (JSON, all optional):
      n            int   — number of reference shots to plan (default 32).
      trigger_word str   — force a specific trigger token.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    body = request.get_json(silent=True) or {}
    n = int(body.get("n", 32))
    trigger_word = (body.get("trigger_word") or "").strip() or None

    from backend.services.plugin_bridge import PluginUnavailable, ensure_plugins_for_stage
    try:
        ensure_plugins_for_stage("cast", "planning", job_critical=True)
    except PluginUnavailable as e:
        return jsonify({
            "error": f"Ollama/ComfyUI could not be started for Cast: {e}",
        }), 503

    refs = list(s.ref_image_paths or [])
    if refs:
        from backend.services.cast_identity_manager import ensure_vision_identity
        sync = ensure_vision_identity(s)
        if not sync.get("ok"):
            status = 503 if sync.get("error") == "ollama_unavailable" else 502
            return jsonify({
                "error": sync.get("error") or "identity_sync_failed",
                "message": sync.get("message") or "Vision identity sync failed before plan.",
                **{k: sync.get(k) for k in ("tags", "sources_used") if sync.get(k)},
            }), status
        db.session.refresh(s)

    from backend.services.character_generator_service import generate_character_sheet
    from backend.services.character_identity_prompt import (
        resolve_class_token,
        short_marks_from_subject,
    )
    plan = generate_character_sheet(
        name=s.name,
        kind=s.kind,
        description=s.description or "",
        n=n,
        trigger_word=trigger_word or s.trigger_word or None,
        existing_bible=s.bible or None,
        ref_image_paths=refs,
        prefer_vision_bible=bool(refs),
        include_bible_in_prompts=not bool(s.lora_path),
        # Refs present: never invent a look from name/description.
        invent_bible=not bool(refs),
        class_token=resolve_class_token(s),
        identity_marks=short_marks_from_subject(s),
    )

    if plan.get("error"):
        return jsonify({"error": plan["error"]}), 502

    bible = plan["bible"]
    trigger = plan["trigger_word"]
    shots = plan["shots"]

    # Persist bible + (new) trigger on the Subject.
    s.bible = bible
    if trigger:
        s.trigger_word = trigger
    if plan.get("vision_grounded"):
        cfg = dict(s.training_settings_json or {})
        cfg["bible_vision_grounded"] = True
        if plan.get("tags"):
            cfg["bible_vision_tags"] = plan["tags"][:32]
            cfg["bible_identity_marks"] = ", ".join(plan["tags"][:12])[:200]
        s.training_settings_json = cfg
    db.session.flush()

    # Idempotent: wipe the Generate Character sheet. Keep promoted samples —
    # they already graduated into Training Data (ref_image_paths) and must not
    # be deleted by a re-plan of the generation workspace. Index new shots
    # above any kept promoted rows so sample_<n>.png paths never collide.
    SubjectSample.query.filter_by(
        subject_id=subject_id, promoted_to_training=False,
    ).delete()
    db.session.flush()
    kept = SubjectSample.query.filter_by(subject_id=subject_id).all()
    base_index = (max((s.index for s in kept), default=-1)) + 1

    rows: list[SubjectSample] = []
    for shot in shots:
        row = SubjectSample(
            subject_id=subject_id,
            index=base_index + shot["index"],
            angle=shot.get("angle") or "",
            framing=shot.get("framing") or "",
            expression=shot.get("expression") or "",
            lighting=shot.get("lighting") or "",
            scene=shot.get("scene") or "",
            image_prompt=shot.get("image_prompt") or "",
            placeholder=bool(shot.get("placeholder", False)),
            status="pending",
            approved=False,
        )
        db.session.add(row)
        rows.append(row)

    db.session.commit()

    return jsonify({
        "subject": _serialize(s),
        "bible": bible,
        "trigger_word": trigger,
        "samples": [r.to_dict() for r in rows],
    }), 201


@bp.post("/subjects/<int:subject_id>/generate")
def dispatch_generate_samples(subject_id: int):
    """Dispatch the character.generate_samples Celery task.

    The task re-runs generate_character_sheet, persists a fresh plan, then
    runs the FLUX image loop under gpu_exclusive.  Call /plan first if you
    want to inspect (and possibly edit) the shot plan before committing GPU
    time, or call this endpoint directly to do both in one task.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    from backend.celery_app import celery
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType

    body = request.get_json(silent=True) or {}
    use_lora = bool(body.get("use_trained_lora", False))
    # APPEND by default: a new batch stacks onto the user's curated (approved)
    # samples instead of wiping them. Pass append=false to start a clean slate.
    append = bool(body.get("append", True))

    progress = get_unified_progress()
    job_id = progress.create_process(
        ProcessType.IMAGE_GENERATION,
        f"Character reference sheet generation for subject {subject_id}",
        additional_data={"subject_id": subject_id, "operation": "generate_samples", "kind": "cast_character_gen", "use_trained_lora": use_lora, "append": append},
    )
    try:
        task = celery.send_task("character.generate_samples", args=[subject_id, job_id, use_lora, append])
    except TaskNotStarted as e:
        mark_progress_not_started(job_id, e)
        raise
    # Persist celery id so /generate/cancel can revoke the worker without inspect.
    try:
        progress.update_process(
            job_id, 1, "Queued for generation",
            additional_data={"celery_task_id": task.id},
        )
    except Exception:
        pass
    return jsonify({"task_id": task.id, "job_id": job_id, "subject_id": subject_id, "use_trained_lora": use_lora, "append": append}), 202


@bp.post("/subjects/<int:subject_id>/generate/cancel")
def cancel_generate_samples(subject_id: int):
    """Cancel an in-flight character sample generation (or single-sample regen).

    Stops the batch cooperatively (marks pending/generating samples cancelled),
    revokes the Celery task, cancels the unified-progress job, and interrupts
    ComfyUI so the current sampler does not keep running after Cancel.
    """
    from backend.services.character_generation_cancel import cancel_character_generation

    result = cancel_character_generation(subject_id)
    if not result.get("cancelled"):
        reason = result.get("reason", "unknown")
        if reason == "not_found":
            return jsonify(result), 404
        if reason == "not_generating":
            return jsonify(result), 409
        return jsonify(result), 400
    return jsonify(result), 200


@bp.get("/subjects/<int:subject_id>/samples")
def list_samples(subject_id: int):
    """Return all SubjectSamples for a Subject, ordered by index."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    _maybe_backfill_promotion(s)

    samples = (
        SubjectSample.query
        .filter_by(subject_id=subject_id)
        .order_by(SubjectSample.index)
        .all()
    )
    return jsonify({
        "subject_id": subject_id,
        "samples": [r.to_dict() for r in samples],
    })


@bp.get("/subjects/<int:subject_id>/samples/<int:sample_id>/image")
def sample_image(subject_id: int, sample_id: int):
    """Serve a generated SubjectSample's PNG. image_path is an absolute disk path
    (not web-accessible), so resolve it through the same containment guard the
    preview route uses and send_file it. 404 until generation writes the image."""
    row = db.session.get(SubjectSample, sample_id)
    if row is None or row.subject_id != subject_id:
        return jsonify({"error": "not_found"}), 404
    resolved = _resolve_ref_path(row.image_path) if row.image_path else None
    if not resolved:
        return jsonify({"error": "no_image"}), 404
    return send_file(resolved, max_age=3600)


@bp.post("/subjects/<int:subject_id>/train")
def dispatch_train(subject_id: int):
    """Dispatch a LoRA training run for this subject directly from the Cast Studio
    (the production casting flow is the only other trigger). The lora_trainer.train_lora
    Celery task is subject-centric (args=[subject_id]) — no Production context needed."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    if s.training_status == "training":
        return jsonify({"error": "already_training", "subject_id": subject_id}), 409

    body = request.get_json(silent=True) or {}
    if body.get("training_settings"):
        # Merge, not replace: the same JSON holds bible_vision_grounded (without
        # it every run re-syncs identity from the photos) and the smoke score.
        from backend.services.lora_training_settings import merge_training_settings
        s.training_settings_json = merge_training_settings(
            s.training_settings_json, body["training_settings"],
        )

    # Gate on media model registry: Z-Image/FLUX train backends land next;
    # only train_ready profiles (currently sdxl-legacy PEFT) may dispatch.
    from backend.services.lora_training_settings import settings_for_subject
    from backend.services.media_model_registry import (
        assert_train_ready, get_profile, train_base_missing_message,
    )
    train_cfg = settings_for_subject(s)
    base_id = train_cfg.get("base_model_id")
    try:
        missing = train_base_missing_message(assert_train_ready(base_id))
        if missing:
            prof = get_profile(base_id) or {}
            return jsonify({
                "error": "train_base_not_installed",
                "message": missing,
                "base_model_id": base_id,
                "base_model_name": prof.get("name"),
            }), 400
    except ValueError as e:
        prof = get_profile(base_id) or {}
        return jsonify({
            "error": "train_base_not_ready",
            "message": str(e),
            "base_model_id": base_id,
            "base_model_name": prof.get("name"),
            "train_ready": False,
            "train_status_note": prof.get("train_status_note"),
        }), 400

    # Persist resolved base onto the subject so serialize/UI stay honest.
    merged = dict(s.training_settings_json or {})
    merged.update(train_cfg)
    s.training_settings_json = merged
    # Into the transaction now: the refresh after the identity check below
    # reloads the row, and would otherwise discard these settings whenever no
    # sync ran (and so nothing committed them).
    db.session.flush()

    # Vision-ground identity before captions/train when refs exist and ungrounded.
    refs = list(s.ref_image_paths or [])
    if refs:
        from backend.services.cast_identity_manager import ensure_vision_identity
        sync = ensure_vision_identity(s)
        if not sync.get("ok"):
            status = 503 if sync.get("error") == "ollama_unavailable" else 502
            return jsonify({
                "error": sync.get("error") or "identity_sync_failed",
                "message": sync.get("message")
                or "Vision identity sync failed — fix Ollama/refs before training.",
            }), status
        db.session.refresh(s)

    # Caption any refs still missing rich .txt sidecars before the Celery train task.
    try:
        from backend.services.character_captioner import ensure_subject_image_captions
        from backend.models import SubjectSample
        train_images = list(s.ref_image_paths or [])
        for smp in SubjectSample.query.filter_by(
            subject_id=s.id, approved=True, status="done"
        ).all():
            if smp.image_path and smp.image_path not in train_images:
                train_images.append(smp.image_path)
        from backend.services.character_identity_prompt import resolve_class_token
        token = (s.trigger_word or "").strip() or s.name
        marks = _identity_marks_for_captions(s)
        ensure_subject_image_captions(
            train_images,
            trigger=token,
            identity_marks=marks,
            class_token=resolve_class_token(s),
            kind=s.kind or "character",
        )
    except Exception:
        pass

    s.training_status = "training"
    s.training_error = None  # clear any prior failure reason before the new run
    db.session.commit()

    from backend.services.lora_train_dispatch import dispatch_lora_train

    result = dispatch_lora_train(subject_id)
    return jsonify({**result, "subject_id": subject_id, "base_model_id": base_id}), 202


@bp.post("/subjects/<int:subject_id>/train/cancel")
def cancel_train(subject_id: int):
    """Cancel an in-flight LoRA training run for this cast subject."""
    from backend.services.lora_train_dispatch import cancel_lora_train

    result = cancel_lora_train(subject_id)
    if not result.get("cancelled"):
        reason = result.get("reason", "unknown")
        if reason == "not_found":
            return jsonify(result), 404
        if reason == "not_training":
            return jsonify(result), 409
        return jsonify(result), 400
    return jsonify(result), 200


@bp.delete("/subjects/<int:subject_id>/samples/<int:sample_id>")
def delete_sample(subject_id: int, sample_id: int):
    """Delete one generated SubjectSample — the UI '✕' on an unwanted generation.
    Leaves the PNG on disk (harmless, in outputs); just drops the row so it
    vanishes from the sheet and never reaches training."""
    row = db.session.get(SubjectSample, sample_id)
    if row is None or row.subject_id != subject_id:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(row)
    db.session.commit()
    return "", 204


@bp.post("/subjects/<int:subject_id>/samples/<int:sample_id>/regenerate")
def dispatch_regen_sample(subject_id: int, sample_id: int):
    """Dispatch character.regen_sample for a single SubjectSample.

    Request body (JSON, all optional):
      prompt_override  str  — use this prompt instead of the stored image_prompt.
      seed             int  — fixed seed for reproducibility; omit for random.
    """
    # Validate ownership — the sample must belong to this subject.
    row = db.session.get(SubjectSample, sample_id)
    if row is None or row.subject_id != subject_id:
        return jsonify({"error": "not_found"}), 404

    body = request.get_json(silent=True) or {}
    prompt_override = (body.get("prompt_override") or "").strip() or None
    seed = body.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            return jsonify({"error": "seed must be an integer"}), 400

    from backend.celery_app import celery
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType

    progress = get_unified_progress()
    job_id = progress.create_process(
        ProcessType.IMAGE_GENERATION,
        f"Regen sample {sample_id} for cast subject {subject_id}",
        additional_data={"subject_id": subject_id, "sample_id": sample_id, "operation": "regen_sample", "kind": "cast_character_gen"},
    )
    try:
        task = celery.send_task(
            "character.regen_sample",
            args=[sample_id, prompt_override, seed, job_id],
        )
    except TaskNotStarted as e:
        mark_progress_not_started(job_id, e)
        raise
    try:
        progress.update_process(
            job_id, 1, "Queued for regen",
            additional_data={"celery_task_id": task.id},
        )
    except Exception:
        pass
    return jsonify({"task_id": task.id, "job_id": job_id, "sample_id": sample_id}), 202


@bp.post("/subjects/<int:subject_id>/samples/approve")
def approve_samples(subject_id: int):
    """Bulk-approve (or un-approve) a set of SubjectSamples.

    Request body (JSON):
      sample_ids   list[int]   — IDs to act on.
      approved     bool        — True to approve, False to un-approve (default True).

    Only samples that belong to this subject are modified; unknown IDs are
    silently ignored (idempotent — the frontend can fire this on every toggle).
    Returns the updated sample list.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    body = request.get_json(silent=True) or {}
    sample_ids = body.get("sample_ids")
    if not isinstance(sample_ids, list):
        return jsonify({"error": "sample_ids must be a list of integers"}), 400

    approved_flag = bool(body.get("approved", True))

    rows = (
        SubjectSample.query
        .filter(SubjectSample.subject_id == subject_id)
        .filter(SubjectSample.id.in_(sample_ids))
        .all()
    )
    for row in rows:
        row.approved = approved_flag
    db.session.commit()

    all_samples = (
        SubjectSample.query
        .filter_by(subject_id=subject_id)
        .order_by(SubjectSample.index)
        .all()
    )
    return jsonify({
        "subject_id": subject_id,
        "updated": len(rows),
        "samples": [r.to_dict() for r in all_samples],
    })

# ── Import LoRA ──────────────────────────────────────────────────────────
# Accepts a LoRA checkpoint trained OUTSIDE Guaardvark (Ostris AI-Toolkit,
# kohya, or diffusers/PEFT) and attaches it to a Subject, so Cast selector,
# Film Crew casting and keyframe rendering pick it up like a native-trained
# LoRA. Validated against the two families the product currently supports:
# Z-Image Turbo (diffusers/PEFT: layers.<n>...lora_A/lora_B) and FLUX.1 Dev
# (kohya: double_blocks/single_blocks...lora_down/lora_up[+alpha]).
#
# Issue: https://github.com/guaardvark/guaardvark/issues/245
# Author: Caroline Zolet (Carol-zolet)

_IMPORT_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB — LoRA checkpoints, not images
# Matches the trainer's own layout. A relative path here resolves against
# the process cwd, not STORAGE_DIR — the Z-Image renderer then skips a
# missing file with only a log line instead of failing loudly.
_LORA_SUBDIR = Path("training") / "loras"

# Z-Image (diffusers/PEFT) module path, after stripping a known prefix.
_ZIMAGE_MODULE_RE = re.compile(
    r"^(?:layers|context_refiner|noise_refiner)\.\d+\."
    r"(?:attention\.to_(?:q|k|v|out\.0)|feed_forward\.w[123]|adaLN_modulation\.0)"
)
_ZIMAGE_PREFIXES = ("diffusion_model.", "transformer.")
_ZIMAGE_SUFFIXES = (".lora_A.weight", ".lora_B.weight", ".lora_down.weight", ".lora_up.weight", ".alpha")

# FLUX module path — kohya form keeps the lora_unet_ prefix baked into the
# name (no dot after it), the dotted form doesn't.
_FLUX_KOHYA_RE = re.compile(r"^lora_unet_(?:double|single)_blocks_\d+_")
_FLUX_DOTTED_RE = re.compile(r"^diffusion_model\.(?:double|single)_blocks\.\d+\.")

# SDXL — explicitly rejected even though it also uses a lora_unet_ prefix,
# because its block names never match the FLUX pattern above.
_SDXL_RE = re.compile(r"^lora_unet_(?:down|up|input|output|middle)_blocks|^lora_te")

# Text-encoder keys ("lora_te1_"/"lora_te2_") appear both on true SDXL LoRAs
# and on a FLUX LoRA that was also trained on its text encoder. Used only to
# give the latter a more accurate rejection message — see
# _flux_text_encoder_hint below. Not part of the accept/reject decision.
_TEXT_ENCODER_RE = re.compile(r"^lora_te\d*_")


def _read_safetensors_header(fileobj) -> dict:
    """Read only the JSON header of a .safetensors file — no tensor data.
    Format: 8-byte little-endian uint64 header length, then that many bytes
    of JSON. Never touches the tensor bytes that follow."""
    fileobj.seek(0)
    length_bytes = fileobj.read(8)
    if len(length_bytes) != 8:
        raise ValueError("file too small to be a valid safetensors checkpoint")
    (header_len,) = struct.unpack("<Q", length_bytes)
    if header_len <= 0 or header_len > 50 * 1024 * 1024:
        raise ValueError(f"implausible header length ({header_len} bytes) — not a LoRA checkpoint")
    header_bytes = fileobj.read(header_len)
    if len(header_bytes) != header_len:
        raise ValueError("truncated safetensors header")
    try:
        return json.loads(header_bytes)
    except json.JSONDecodeError as e:
        raise ValueError(f"header is not valid JSON: {e}") from e


def _detect_lora_family(keys: list[str]) -> str | None:
    """Return 'zimage-turbo', 'flux-dev', or None if the key layout matches
    neither supported family (SDXL included).

    Validated 2026-09-27 against real Ostris AI-Toolkit checkpoints:
      - Z-Image Turbo: 480/480 keys matched (diffusers/PEFT layout)
      - FLUX.1 Dev:    912/912 keys matched (kohya layout)
    and independently confirmed by the maintainer running the same key
    names through diffusers' load_lora_weights (Z-Image) and ComfyUI's
    LoraLoaderModelOnly (FLUX) — both load every key with no warnings.

    Known limitation: a FLUX LoRA that also trains the text encoder carries
    lora_te1_* keys, which _SDXL_RE matches (it starts with "lora_te"). Today
    that turns the whole file away as unrecognised/SDXL rather than accepting
    it as FLUX with an ignored text-encoder component — see
    test_import_rejects_flux_with_text_encoder_keys for the pinned behaviour.
    """
    tensor_keys = [k for k in keys if k != "__metadata__"]
    if not tensor_keys:
        return None

    zimage_hits = 0
    flux_hits = 0
    sdxl_hits = 0

    for key in tensor_keys:
        if _SDXL_RE.match(key):
            sdxl_hits += 1
            continue

        stripped = key
        for prefix in _ZIMAGE_PREFIXES:
            if key.startswith(prefix):
                stripped = key[len(prefix):]
                break
        if key.endswith(_ZIMAGE_SUFFIXES) and _ZIMAGE_MODULE_RE.search(stripped):
            zimage_hits += 1
            continue

        if _FLUX_KOHYA_RE.match(key) or _FLUX_DOTTED_RE.match(key):
            flux_hits += 1
            continue

    total = len(tensor_keys)
    if sdxl_hits > total * 0.5:
        return None
    if zimage_hits >= total * 0.9:
        return ZIMAGE_TURBO
    if flux_hits >= total * 0.9:
        return FLUX_DEV
    return None


def _flux_text_encoder_hint(keys: list[str]) -> bool:
    """True if a rejected file looks like a FLUX LoRA that also trained its
    text encoder, rather than an actual SDXL checkpoint: its non-text-encoder
    keys overwhelmingly match the FLUX U-Net block pattern, and only the
    lora_te*_ keys triggered the SDXL rejection. Used solely to pick a more
    accurate error message; it does not change what gets accepted."""
    tensor_keys = [k for k in keys if k != "__metadata__"]
    te_keys = [k for k in tensor_keys if _TEXT_ENCODER_RE.match(k)]
    if not te_keys:
        return False
    other_keys = [k for k in tensor_keys if k not in te_keys]
    if not other_keys:
        return False
    flux_hits = sum(
        1 for k in other_keys
        if _FLUX_KOHYA_RE.match(k) or _FLUX_DOTTED_RE.match(k)
    )
    return flux_hits >= len(other_keys) * 0.9


@bp.post("/subjects/<int:subject_id>/import-lora")
def import_subject_lora(subject_id):
    """Attach an externally-trained LoRA (.safetensors) to a Subject.

    Multipart fields:
      lora_file      — required, the .safetensors checkpoint
      base_model_id  — required, 'zimage-turbo' or 'flux-dev'
      trigger_word   — required, the token the LoRA was trained on
    """
    request.max_content_length = _IMPORT_MAX_BYTES

    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "subject not found"}), 404
    if s.training_status == "training":
        return jsonify({"error": "already_training", "subject_id": subject_id}), 409

    f = request.files.get("lora_file")
    if not f or not f.filename:
        return jsonify({"error": "no file (expected multipart field 'lora_file')"}), 400

    base_model_id = (request.form.get("base_model_id") or "").strip()
    trigger_word = (request.form.get("trigger_word") or "").strip()
    if not trigger_word:
        return jsonify({"error": "trigger_word is required"}), 400

    profile = get_profile(base_model_id)
    if not profile or profile["id"] not in (ZIMAGE_TURBO, FLUX_DEV):
        return jsonify({
            "error": f"unsupported base_model_id {base_model_id!r}; "
                     f"expected one of: {ZIMAGE_TURBO!r}, {FLUX_DEV!r}"
        }), 400
    base_model_id = profile["id"]

    ext = Path(secure_filename(f.filename) or "").suffix.lower()
    if ext != ".safetensors":
        return jsonify({"error": f"expected a .safetensors file, got {ext!r}"}), 400

    target_dir = Path(STORAGE_DIR) / _LORA_SUBDIR
    target_dir.mkdir(parents=True, exist_ok=True)
    # .partial (not .safetensors) so ComfyUI's recursive LoRA scan never lists a
    # half-written upload, and mkstemp's own uniqueness rules out two uploads to
    # the same member in one worker process colliding on a name.
    tmp_fd, _tmp_name = tempfile.mkstemp(dir=target_dir, suffix=".partial")
    os.close(tmp_fd)
    tmp_path = Path(_tmp_name)
    # mkstemp creates the file owner-only (0600) and the rename below keeps that
    # mode. ComfyUI loads Cast LoRAs from this folder and can run as another
    # user (Docker), so the stored LoRA is made readable like a trained one.
    os.chmod(tmp_path, 0o644)

    written = 0
    oversized = False
    write_error = None
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = f.stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > _IMPORT_MAX_BYTES:
                    oversized = True
                    break
                out.write(chunk)
    except OSError as e:
        write_error = e

    if oversized or write_error is not None:
        tmp_path.unlink(missing_ok=True)
        reason = f"file exceeds {_IMPORT_MAX_BYTES // (1024**3)}GB limit" if oversized else f"write failed: {write_error}"
        return jsonify({"error": reason}), 400

    try:
        with open(tmp_path, "rb") as fh:
            header = _read_safetensors_header(fh)
    except ValueError as e:
        tmp_path.unlink(missing_ok=True)
        return jsonify({"error": f"invalid safetensors file: {e}"}), 400

    detected_family = _detect_lora_family(list(header.keys()))
    if detected_family is None:
        tmp_path.unlink(missing_ok=True)
        if _flux_text_encoder_hint(list(header.keys())):
            return jsonify({
                "error": "this looks like a FLUX LoRA that also trained its text "
                         "encoder; only the U-Net/transformer weights are supported "
                         "for import right now, the text-encoder part isn't yet"
            }), 400
        return jsonify({
            "error": "key layout does not match any supported LoRA family "
                     "(zimage-turbo or flux-dev); SDXL LoRAs are not supported"
        }), 400
    if detected_family != base_model_id:
        tmp_path.unlink(missing_ok=True)
        return jsonify({
            "error": f"selected base_model_id {base_model_id!r} does not match "
                     f"the detected key layout ({detected_family!r})"
        }), 400

    # A running counter, not the per-base version record_subject_lora keeps below
    # (SubjectLora.version) — just enough to keep filenames unique across every
    # import this member ever makes, whichever base each one targets.
    next_import_seq = (s.lora_version or 0) + 1
    final_path = target_dir / f"subject_{subject_id}_imported_v{next_import_seq}.safetensors"
    tmp_path.replace(final_path)

    write_lora_sidecar(
        final_path,
        subject_id=s.id,
        subject_name=s.name,
        trigger_word=trigger_word,
        base_model_id=base_model_id,
        ref_count=0,
        mock=False,
        extra={"imported": True, "train_backend": None},
    )

    resolved_path = str(final_path.resolve())
    # record_subject_lora adds the next version for this base and, per the
    # import rule (see its docstring), makes it the member's default only when
    # the member has none yet or the default is already on this same base —
    # otherwise this base's new row is added and the current default is left
    # alone (see the maintainer's phase 2 split on issue #245).
    record_subject_lora(
        s, base_model_id, resolved_path, source="imported", trigger_word=trigger_word,
    )
    s.lora_version = next_import_seq
    s.training_status = "trained"
    became_default = s.lora_path == resolved_path
    if became_default:
        # Member-level fields are the default LoRA's own — only touch them when
        # this import is the one that became the default.
        s.trigger_word = trigger_word
        s.training_settings_json = dict(
            s.training_settings_json or {}, base_model_id=base_model_id, imported=True,
        )
    db.session.commit()

    return jsonify({"subject": _serialize(s)})


@bp.post("/subjects/<int:subject_id>/loras/<base_model_id>/make-default")
def make_default_lora(subject_id, base_model_id):
    """Make the member's current LoRA for ``base_model_id`` its default.

    Used by the Versions tab's "Make default" button (one per base the member
    holds a LoRA for) — see the maintainer's phase 2 split on issue #245.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "subject not found"}), 404
    try:
        set_default_lora(s, base_model_id)
    except CastLoraRefusal as e:
        return jsonify({"error": str(e)}), 400
    db.session.commit()
    return jsonify({"subject": _serialize(s)})


# ── The identity check's image encoder (Settings > Generation) ───────────────

@bp.get("/identity-check")
def identity_check_status():
    """The encoder the identity check reads: what it is, whether it is installed,
    which check the post-train smoke still gets, and any install in progress."""
    from backend.services import identity_encoder
    return jsonify(identity_encoder.status()), 200


@bp.post("/identity-check/install")
def identity_check_install():
    """Download the encoder. Its Install button is the only path that fetches it."""
    from backend.services import identity_encoder
    try:
        return jsonify(identity_encoder.start_install()), 202
    except identity_encoder.Refused as e:
        return jsonify({**identity_encoder.status(), "error": str(e)}), e.status


@bp.post("/identity-check/remove")
def identity_check_remove():
    """Delete the encoder's files; the checks fall back to what ran before it."""
    from backend.services import identity_encoder
    try:
        return jsonify(identity_encoder.remove()), 200
    except identity_encoder.Refused as e:
        return jsonify({**identity_encoder.status(), "error": str(e)}), e.status
