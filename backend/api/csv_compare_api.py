# backend/api/csv_compare_api.py   Version 1.0 (CSV compare + generate endpoints)

"""
Module for handling CSV compare and generate endpoints.
"""

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
def parse_csv_from_upload(file: io.IOBase) -> tuple[list[dict[str, str]], list[str]]:
    """
    Parse CSV data from an uploaded file.

    Args:
        file: The uploaded file object.

    Returns:
        A tuple containing the parsed CSV rows and fieldnames.
    """
    content = file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    return rows, reader.fieldnames


@csv_bp.route("/compare", methods=["POST"])
def compare_csv() -> tuple[dict[str, str], int]:
    """
    Compare two CSV files.

    Returns:
        A JSON response containing the comparison results.
    """
    try:
        # Get uploaded CSV files
        file1 = request.files.get("file1")
        file2 = request.files.get("file2")

        # Validate required files
        if not file1 or not file2:
            return jsonify({"error": "Both CSV files are required."}), 400

        # Parse CSV data
        data1, _ = parse_csv_from_upload(file1)
        data2, _ = parse_csv_from_upload(file2)

        # Map key to row in data1
        key_index = {}
        for row in data1:
            if "key" in row:
                key_index[row["key"]] = row

        # Find added, changed, and unchanged rows
        added = []
        changed = []
        for row in data2:
            key = row.get("key")
            if key in key_index:
                if row != key_index[key]:
                    changed.append(row)
            else:
                added.append(row)

        return (
            jsonify(
                {
                    "key": "key",
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
def generate_csv() -> tuple[dict[str, str], int]:
    """
    Generate a CSV file from a JSON payload.

    Returns:
        A JSON response containing the generated CSV file details.
    """
    try:
        # Get JSON payload
        data = request.get_json()
        rows = data.get("rows")
        fields = data.get("columns") or []
        base_name = data.get("name", "merged_output")

        # Validate row data
        if not rows or not isinstance(rows, list):
            return jsonify({"error": "Invalid or empty row data."}), 400

        # Generate CSV file
        filename = f"{secure_filename(base_name)}__v1.csv"
        filepath = os.path.join(OUTPUT_DIR, filename)

        # Write CSV data
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
