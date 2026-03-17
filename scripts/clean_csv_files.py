#!/usr/bin/env python3
"""
Clean CSV Files Script
Applies validators to fix category, tags, title, and excerpt fields in existing CSV files.
"""

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.utils.category_validator import validate_and_fix_category
from backend.utils.tags_normalizer import normalize_tags


def clean_text_field(text: str) -> str:
    if not text:
        return text
    text = text.replace('"""', "").replace("'''", "")
    text = text.replace('\\"', "").replace("\\'", "")
    text = text.strip('"').strip("'").strip()
    return text


def clean_csv_file(input_path: str, output_path: str = None) -> dict:
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_cleaned{ext}"

    rows_processed = 0
    rows_fixed = 0

    with open(input_path, "r", encoding="utf-8") as infile:
        reader = csv.DictReader(infile)
        fieldnames = reader.fieldnames

        if not fieldnames:
            return {"success": False, "error": "No headers found in CSV"}

        rows = list(reader)

    with open(output_path, "w", newline="", encoding="utf-8") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()

        for row in rows:
            rows_processed += 1
            original_row = dict(row)

            title_key = next((k for k in row.keys() if k.lower() == "title"), None)
            excerpt_key = next((k for k in row.keys() if k.lower() == "excerpt"), None)
            category_key = next(
                (k for k in row.keys() if k.lower() == "category"), None
            )
            tags_key = next((k for k in row.keys() if k.lower() == "tags"), None)

            if title_key and row.get(title_key):
                row[title_key] = clean_text_field(row[title_key])

            if excerpt_key and row.get(excerpt_key):
                row[excerpt_key] = clean_text_field(row[excerpt_key])

            if category_key and row.get(category_key):
                topic = row.get(title_key, "") if title_key else ""
                row[category_key] = validate_and_fix_category(
                    category=row[category_key], topic=topic
                )

            if tags_key and row.get(tags_key):
                topic = row.get(title_key, "") if title_key else ""
                row[tags_key] = normalize_tags(
                    tags=row[tags_key], topic=topic, min_count=3, max_count=12
                )

            if row != original_row:
                rows_fixed += 1

            writer.writerow(row)

    return {
        "success": True,
        "input_file": input_path,
        "output_file": output_path,
        "rows_processed": rows_processed,
        "rows_fixed": rows_fixed,
    }


def clean_directory(input_dir: str, output_dir: str = None):
    if output_dir is None:
        output_dir = input_dir

    os.makedirs(output_dir, exist_ok=True)

    results = []
    csv_files = [f for f in os.listdir(input_dir) if f.endswith(".csv")]

    print(f"Found {len(csv_files)} CSV files to process")

    for filename in csv_files:
        input_path = os.path.join(input_dir, filename)

        if output_dir == input_dir:
            base, ext = os.path.splitext(filename)
            output_filename = f"{base}_cleaned{ext}"
        else:
            output_filename = filename

        output_path = os.path.join(output_dir, output_filename)

        print(f"Processing: {filename}")
        result = clean_csv_file(input_path, output_path)
        results.append(result)

        if result["success"]:
            print(f"  -> {result['rows_processed']} rows, {result['rows_fixed']} fixed")
        else:
            print(f"  -> ERROR: {result.get('error', 'Unknown error')}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Clean CSV files by applying field validators"
    )
    parser.add_argument("input", help="Input CSV file or directory")
    parser.add_argument(
        "-o",
        "--output",
        help="Output file or directory (default: adds _cleaned suffix)",
    )
    parser.add_argument(
        "--in-place", action="store_true", help="Overwrite original files"
    )

    args = parser.parse_args()

    if os.path.isfile(args.input):
        output = args.input if args.in_place else args.output
        result = clean_csv_file(args.input, output)
        if result["success"]:
            print(
                f"Cleaned {result['rows_processed']} rows ({result['rows_fixed']} fixed)"
            )
            print(f"Output: {result['output_file']}")
        else:
            print(f"Error: {result['error']}")
            sys.exit(1)
    elif os.path.isdir(args.input):
        output_dir = args.input if args.in_place else args.output
        results = clean_directory(args.input, output_dir)
        total_processed = sum(
            r.get("rows_processed", 0) for r in results if r["success"]
        )
        total_fixed = sum(r.get("rows_fixed", 0) for r in results if r["success"])
        print(f"\nTotal: {total_processed} rows processed, {total_fixed} fixed")
    else:
        print(f"Error: {args.input} not found")
        sys.exit(1)
