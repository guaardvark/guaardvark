# backend/api/meta_api.py
# Version: v6.0
# Remaining endpoints handle rule export/import.

import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile

from flask import Blueprint, current_app, jsonify, request, send_file
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

import backend.config

try:
    from backend.models import (Client, Document, Project, Rule, Task,
                                TrainingDataset, Website, db)
except Exception:
    db = Rule = Project = Client = Website = Document = Task = TrainingDataset = None

meta_bp = Blueprint("meta_api", __name__, url_prefix="/api/meta")
logger = logging.getLogger(__name__)


@meta_bp.route("/rules/export", methods=["GET"])
def export_rules():
    logger.info("API: Received GET /api/meta/rules/export request")
    if not Rule or not db:
        return jsonify({"error": "Rule model or DB not available."}), 503
    try:
        rules = Rule.query.all()
        exported_rules = []
        for rule in rules:
            rule_dict = rule.to_dict()
            rule_dict["target_models_json"] = rule.target_models_json
            rule_dict.pop("project", None)
            rule_dict.pop("target_models", None)
            exported_rules.append(rule_dict)

        logger.info(f"Exporting {len(exported_rules)} rules.")
        return jsonify({"rules": exported_rules}), 200
    except Exception as e:
        logger.error(f"Error exporting rules: {e}", exc_info=True)
        return jsonify({"error": f"Failed to export rules: {str(e)}"}), 500


@meta_bp.route("/rules/import", methods=["POST"])
def import_rules():
    logger.info("API: Received POST /api/meta/rules/import request")
    if not Rule or not db or not Project:
        return jsonify({"error": "Rule/Project model or DB not available."}), 503

    if (
        not request.is_json
        or "rules" not in request.json
        or not isinstance(request.json["rules"], list)
    ):
        return (
            jsonify(
                {"error": "Invalid request format. Expected JSON with a 'rules' list."}
            ),
            400,
        )

    imported_rules_data = request.json["rules"]
    created_count, updated_count, skipped_count = 0, 0, 0
    error_details = []

    VALID_LEVELS = [
        "SYSTEM",
        "PROJECT",
        "CLIENT",
        "USER_GLOBAL",
        "USER_SPECIFIC",
        "PROMPT",
        "LEARNED",
    ]
    VALID_TYPES = [
        "PROMPT_TEMPLATE",
        "QA_TEMPLATE",
        "COMMAND_RULE",
        "FILTER_RULE",
        "FORMATTING_RULE",
        "SYSTEM_PROMPT",
        "OTHER",
    ]

    for rule_data in imported_rules_data:
        try:
            rule_data.pop("id", None)
            rule_data.pop("created_at", None)
            rule_data.pop("updated_at", None)
            rule_data.pop("project", None)

            if (
                rule_data.get("level") not in VALID_LEVELS
                or rule_data.get("type") not in VALID_TYPES
            ):
                error_details.append(
                    f"Rule '{rule_data.get('name', 'Unknown')}' has invalid level/type. Skipping."
                )
                skipped_count += 1
                continue

            if "target_models_json" in rule_data and isinstance(
                rule_data["target_models_json"], str
            ):
                try:
                    rule_data["target_models"] = json.loads(
                        rule_data["target_models_json"]
                    )
                except json.JSONDecodeError:
                    rule_data["target_models"] = ["__ALL__"]
            elif "target_models" not in rule_data or not isinstance(
                rule_data["target_models"], list
            ):
                rule_data["target_models"] = ["__ALL__"]

            if "target_models" in rule_data and isinstance(
                rule_data["target_models"], list
            ):
                rule_data["target_models_json"] = json.dumps(rule_data["target_models"])

            project_id_val = rule_data.get("project_id")
            if (
                project_id_val
                and not db.session.query(Project.id)
                .filter_by(id=project_id_val)
                .scalar()
            ):
                rule_data["project_id"] = None

            existing_rule = None
            if rule_data.get("command_label"):
                existing_rule = Rule.query.filter_by(
                    command_label=str(rule_data["command_label"]).strip()
                ).first()
            if not existing_rule:
                existing_rule = Rule.query.filter_by(
                    name=rule_data.get("name"),
                    level=rule_data.get("level"),
                    type=rule_data.get("type"),
                ).first()

            if existing_rule:
                for key, value in rule_data.items():
                    if hasattr(existing_rule, key):
                        setattr(existing_rule, key, value)
                updated_count += 1
            else:
                rule_data.pop("id", None)
                rule_data.pop("project", None)
                if (
                    "target_models_json" in rule_data
                    and "target_models" not in rule_data
                ):
                    try:
                        rule_data["target_models"] = json.loads(
                            rule_data["target_models_json"]
                        )
                    except Exception:
                        rule_data["target_models"] = ["__ALL__"]
                elif "target_models" not in rule_data:
                    rule_data["target_models"] = ["__ALL__"]
                new_rule = Rule(
                    **{
                        k: v
                        for k, v in rule_data.items()
                        if k != "target_models_json" or "target_models" not in rule_data
                    }
                )
                db.session.add(new_rule)
                created_count += 1
        except IntegrityError as ie:
            db.session.rollback()
            error_details.append(
                f"Integrity error for rule '{rule_data.get('name', 'Unknown')}': {str(ie.orig)}"
            )
            skipped_count += 1
        except Exception as e:
            db.session.rollback()
            error_details.append(
                f"Error processing rule '{rule_data.get('name', 'Unknown')}': {str(e)}"
            )
            skipped_count += 1
    try:
        db.session.commit()
        msg = f"Rules import finished. Created: {created_count}, Updated: {updated_count}, Skipped/Errors: {skipped_count}."
        logger.info(msg)
        if error_details:
            return (
                jsonify(
                    {
                        "message": msg,
                        "created": created_count,
                        "updated": updated_count,
                        "skipped": skipped_count,
                        "errors": error_details,
                    }
                ),
                207,
            )
        return (
            jsonify(
                {
                    "message": msg,
                    "created": created_count,
                    "updated": updated_count,
                    "skipped": skipped_count,
                }
            ),
            200,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(
            f"Critical error during final commit of rule import: {e}", exc_info=True
        )
        return (
            jsonify(
                {
                    "error": "Failed to commit imported rules to database.",
                    "details": str(e),
                }
            ),
            500,
        )


@meta_bp.route("/backup/export", methods=["POST"])
def export_backup():
    """Export selected entities to a ZIP archive.

    Request JSON should include ``entities`` list and optional ``include_files`` boolean.
    """
    logger.info("API: Received POST /api/meta/backup/export request")
    if not db:
        return jsonify({"error": "Database unavailable"}), 500

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    payload = request.get_json() or {}
    entities = payload.get("entities") or []
    include_files = bool(payload.get("include_files"))

    export_data: dict[str, list] = {}
    files: list[tuple[str, str]] = []
    upload_root = current_app.config.get("UPLOAD_FOLDER")

    def _handle_file(path: str, prefix: str, obj_id: int) -> str:
        if not include_files or not path:
            return path
        abs_path = path if os.path.isabs(path) else os.path.join(upload_root, path)
        if os.path.isfile(abs_path):
            safe_name = secure_filename(os.path.basename(path))
            dest_name = f"{prefix}_{obj_id}_{safe_name}"
            files.append((abs_path, dest_name))
            return f"files/{dest_name}"
        return path

    try:
        if "clients" in entities and Client:
            items = []
            for c in Client.query.all():
                d = c.to_dict()
                if d.get("logo_path"):
                    d["logo_path"] = _handle_file(d["logo_path"], "client", c.id)
                items.append(d)
            export_data["clients"] = items

        if "projects" in entities and Project:
            export_data["projects"] = [p.to_dict() for p in Project.query.all()]

        if "websites" in entities and Website:
            export_data["websites"] = [w.to_dict() for w in Website.query.all()]

        if "documents" in entities and Document:
            items = []
            for d in Document.query.all():
                data = d.to_dict()
                data["path"] = _handle_file(data["path"], "document", d.id)
                items.append(data)
            export_data["documents"] = items

        if "tasks" in entities and Task:
            export_data["tasks"] = [t.to_dict() for t in Task.query.all()]

        if "training_datasets" in entities and TrainingDataset:
            items = []
            for ds in TrainingDataset.query.all():
                data = ds.to_dict()
                if data.get("path"):
                    data["path"] = _handle_file(data["path"], "dataset", ds.id)
                items.append(data)
            export_data["training_datasets"] = items

        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, "backup.json")
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2)

            files_dir = os.path.join(tmpdir, "files")
            os.makedirs(files_dir, exist_ok=True)
            for src, dest_name in files:
                shutil.copy2(src, os.path.join(files_dir, dest_name))

            zip_path = os.path.join(tmpdir, "backup.zip")
            with zipfile.ZipFile(zip_path, "w") as zipf:
                zipf.write(data_path, "backup.json")
                for _, dest_name in files:
                    zipf.write(os.path.join(files_dir, dest_name), f"files/{dest_name}")

            return send_file(zip_path, as_attachment=True, download_name="backup.zip")

    except FileNotFoundError as e:
        logger.error(f"File not found during backup export: {e}")
        return jsonify({"error": "Required files not found for backup"}), 404
    except PermissionError as e:
        logger.error(f"Permission error during backup export: {e}")
        return jsonify({"error": "Permission denied during backup creation"}), 403
    except OSError as e:
        logger.error(f"OS error during backup export: {e}")
        return jsonify({"error": "System error during backup creation"}), 500
    except Exception as e:
        logger.error(f"Unexpected error during backup export: {e}", exc_info=True)
        return jsonify({"error": "Internal error during backup creation"}), 500
