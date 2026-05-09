"""Mock LoRA trainer — the v1 implementation.

Real training arrives in v1.1: an isolated torch venv (`venv-torch/`) with
diffusers + peft, driven via subprocess like ACE-Step in audio_foundry. This
mock fakes the same outputs in <2s so the casting flow is testable on any
machine without a GPU.

Output contract matches whatever the real trainer will produce: a single
.safetensors file under output_dir, plus a small JSON metadata sidecar.
"""
from __future__ import annotations
import json
import time
from pathlib import Path


# 8 bytes that pass as a "valid" safetensors header for filesystem-existence
# checks. Anyone who actually loads this in diffusers will get a parse error,
# which is exactly the right failure mode — mock weights should fail loud.
_MOCK_HEADER = b"\x10\x00\x00\x00\x00\x00\x00\x00{\"__metadata__\":{}}"


def train_subject_lora(
    *,
    subject_id: int,
    subject_name: str,
    ref_image_paths: list[str],
    output_dir: str,
    sleep_s: float = 1.0,
) -> dict:
    """Pretend to train a LoRA. Sleep, write a stub file, return metadata.

    Returns a dict with keys: status ("ok"|"failed"), lora_path, lora_version,
    error (only on failed). On success, lora_path points at a real file on
    disk; on failure, no file is written.
    """
    if not ref_image_paths:
        return {"status": "failed", "error": "no reference images provided"}

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c if c.isalnum() else "_" for c in subject_name) or "subject"
    out_path = target_dir / f"{safe_name}_v1.safetensors"

    time.sleep(sleep_s)
    out_path.write_bytes(_MOCK_HEADER)

    sidecar = out_path.with_suffix(".json")
    sidecar.write_text(json.dumps({
        "subject_id": subject_id,
        "subject_name": subject_name,
        "ref_count": len(ref_image_paths),
        "mock": True,
    }))

    return {"status": "ok", "lora_path": str(out_path), "lora_version": 1}
