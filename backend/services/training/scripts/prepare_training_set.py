#!/usr/bin/env python3
"""
Convert raw servo interaction logs into QLoRA training format.

Reads: data/training/servo_logs/*.jsonl
Writes: data/training/datasets/servo_train.jsonl, servo_eval.jsonl

Three training tasks generated per interaction:
1. Coordinate estimation (success=true only)
2. Correction prediction (all records)
3. On-target classification (all records)
"""

import json
import os
import random
from pathlib import Path

GUAARDVARK_ROOT = Path(os.environ.get("GUAARDVARK_ROOT", "."))
SERVO_LOGS = GUAARDVARK_ROOT / "data" / "training" / "servo_logs"
DATASETS_DIR = GUAARDVARK_ROOT / "data" / "training" / "datasets"

SCREEN_W, SCREEN_H = 1024, 1024


def load_servo_logs():
    records = []
    for log_file in sorted(SERVO_LOGS.glob("*.jsonl")):
        with open(log_file) as f:
            for line in f:
                records.append(json.loads(line))
    return records


def generate_coordinate_examples(records):
    examples = []
    for r in records:
        if not r.get("success"):
            continue
        examples.append({
            "image": r["screenshot_path"],
            "conversations": [
                {"role": "user", "content": (
                    f"Screen is {SCREEN_W}x{SCREEN_H}. "
                    f"Where is the {r['target_description']}? "
                    f"Respond with ONLY: {{\"x\": N, \"y\": N}}"
                )},
                {"role": "assistant", "content": json.dumps({
                    "x": r["target_actual"][0], "y": r["target_actual"][1]
                })},
            ]
        })
    return examples


def generate_correction_examples(records):
    examples = []
    for r in records:
        for corr in r.get("corrections", []):
            examples.append({
                "image": r["screenshot_path"],
                "conversations": [
                    {"role": "user", "content": (
                        f"The crosshair is at ({r['crosshair_pos'][0]}, {r['crosshair_pos'][1]}). "
                        f"How far is it from the {r['target_description']}? "
                        f"Respond with ONLY: {{\"on_target\": false, \"direction\": \"...\", \"distance\": \"...\"}}"
                    )},
                    {"role": "assistant", "content": json.dumps({
                        "on_target": False,
                        "direction": corr["direction"],
                        "distance": corr["distance"],
                    })},
                ]
            })
    return examples


def generate_on_target_examples(records):
    examples = []
    for r in records:
        if r.get("success"):
            examples.append({
                "image": r["screenshot_path"],
                "conversations": [
                    {"role": "user", "content": (
                        f"Is the crosshair directly on the {r['target_description']}? "
                        f"Respond with ONLY: {{\"on_target\": true}} or {{\"on_target\": false, ...}}"
                    )},
                    {"role": "assistant", "content": json.dumps({"on_target": True})},
                ]
            })
    return examples


def main():
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    records = load_servo_logs()
    print(f"Loaded {len(records)} servo interaction records")

    examples = []
    examples.extend(generate_coordinate_examples(records))
    examples.extend(generate_correction_examples(records))
    examples.extend(generate_on_target_examples(records))

    random.shuffle(examples)
    split = int(len(examples) * 0.8)
    train = examples[:split]
    eval_set = examples[split:]

    train_path = DATASETS_DIR / "servo_train.jsonl"
    eval_path = DATASETS_DIR / "servo_eval.jsonl"

    for path, data in [(train_path, train), (eval_path, eval_set)]:
        with open(path, "w") as f:
            for ex in data:
                f.write(json.dumps(ex) + "\n")

    print(f"Train: {len(train)} examples -> {train_path}")
    print(f"Eval: {len(eval_set)} examples -> {eval_path}")


if __name__ == "__main__":
    main()
