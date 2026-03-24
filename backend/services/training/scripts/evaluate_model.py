#!/usr/bin/env python3
"""
Evaluate a vision model's servo accuracy against the held-out test set.

Metrics:
- Mean pixel error (coordinate estimation)
- On-target accuracy (classification)

Usage:
    python3 evaluate_model.py --model qwen3-vl:2b-instruct --data data/training/datasets/servo_eval.jsonl
    python3 evaluate_model.py --model gvk-eye-v1 --data data/training/datasets/servo_eval.jsonl
"""

import argparse
import json
import math
import os
from pathlib import Path

import requests
from PIL import Image

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def evaluate(model: str, data_path: str, ollama_url: str = OLLAMA_URL):
    import base64
    from io import BytesIO

    records = []
    with open(data_path) as f:
        for line in f:
            records.append(json.loads(line))

    coord_errors = []
    on_target_correct = 0
    on_target_total = 0
    total = len(records)

    for i, record in enumerate(records):
        img_path = record.get("image", "")
        conversations = record.get("conversations", [])
        if not conversations or not img_path:
            continue

        user_msg = conversations[0]["content"]
        expected = conversations[1]["content"]

        try:
            img = Image.open(img_path)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=70)
            img_b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception:
            continue

        try:
            resp = requests.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": user_msg, "images": [img_b64]}],
                    "stream": False,
                    "options": {"num_predict": 128, "temperature": 0.1},
                },
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            output = resp.json().get("message", {}).get("content", "").strip()
        except Exception:
            continue

        try:
            expected_data = json.loads(expected)
            start = output.find("{")
            end = output.rfind("}") + 1
            if start >= 0 and end > start:
                actual_data = json.loads(output[start:end])
            else:
                continue

            if "x" in expected_data and "x" in actual_data:
                dx = actual_data["x"] - expected_data["x"]
                dy = actual_data["y"] - expected_data["y"]
                error = math.sqrt(dx * dx + dy * dy)
                coord_errors.append(error)

            if "on_target" in expected_data:
                on_target_total += 1
                if actual_data.get("on_target") == expected_data["on_target"]:
                    on_target_correct += 1

        except (json.JSONDecodeError, KeyError):
            continue

        if (i + 1) % 10 == 0:
            print(f"  Evaluated {i+1}/{total}...")

    print(f"\n=== Evaluation: {model} ===")
    print(f"Total records: {total}")
    if coord_errors:
        mean_err = sum(coord_errors) / len(coord_errors)
        median_err = sorted(coord_errors)[len(coord_errors) // 2]
        print(f"Coordinate estimation: {len(coord_errors)} samples")
        print(f"  Mean pixel error: {mean_err:.1f}px")
        print(f"  Median pixel error: {median_err:.1f}px")
    if on_target_total:
        acc = on_target_correct / on_target_total * 100
        print(f"On-target accuracy: {acc:.1f}% ({on_target_correct}/{on_target_total})")

    return {
        "model": model,
        "mean_pixel_error": sum(coord_errors) / len(coord_errors) if coord_errors else None,
        "on_target_accuracy": on_target_correct / on_target_total if on_target_total else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Ollama model name")
    parser.add_argument("--data", required=True, help="Path to eval JSONL")
    args = parser.parse_args()
    evaluate(args.model, args.data)


if __name__ == "__main__":
    main()
