# backend/api/csv_compare_api.py   Version 1.0 (CSV compare + generate endpoints)

import csv
import io
import json
import os

from flask import Blueprint, jsonify, request, send_file
from werkzeug.utils import secure_filename

from backend.config import OUTPUT_DIR
from backend.tools.file_processor import write_csv

csv_bp = Blueprint("csv_api", __name__, url_prefix="/api/csv")

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)


# --- In-memory CSV diff logic ---
def parse_csv_from_upload(file):
    content = file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    return rows, reader.fieldnames


@csv_bp.route("/compare", methods=["POST"])
def compare_csv():
    try:
        file1 = request.files.get("file1")
        file2 = request.files.get("file2")
        merge_key = request.form.get("key") or "id"

        if not file1 or not file2:
            return jsonify({"error": "Both CSV files are required."}), 400

        data1, _ = parse_csv_from_upload(file1)
        data2, _ = parse_csv_from_upload(file2)

        key_index = {}  # map of key -> row from data1
        for row in data1:
            if merge_key in row:
                key_index[row[merge_key]] = row

        added = []
        changed = []
        for row in data2:
            key = row.get(merge_key)
            if key in key_index:
                if row != key_index[key]:
                    changed.append(row)
            else:
                added.append(row)

        return (
            jsonify(
                {
                    "key": merge_key,
                    "added": added,
                    "changed": changed,
                    "count_added": len(added),
                    "count_changed": len(changed),
                }
            ),
            200,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@csv_bp.route("/generate", methods=["POST"])
def generate_csv():
    try:
        data = request.get_json()
        rows = data.get("rows")
        fields = data.get("columns") or []
        base_name = data.get("name", "merged_output")

        if not rows or not isinstance(rows, list):
            return jsonify({"error": "Invalid or empty row data."}), 400

        filename = f"{secure_filename(base_name)}__v1.csv"
        filepath = os.path.join(OUTPUT_DIR, filename)

        fieldnames = fields or list(rows[0].keys())
        write_csv(rows, filepath)

        return (
            jsonify(
                {"message": "CSV generated.", "filename": filename, "path": filepath}
            ),
            200,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
