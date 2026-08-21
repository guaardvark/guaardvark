"""B-roll generation for training videos, via the Guaardvark HTTP API.

Stills come from `/api/batch-image`; optional motion from `/api/batch-video`.
Results are cached by prompt so re-running a production reuses approved frames
instead of paying for them again.

Prompt discipline — this is a safety constraint, not a style preference:
generated imagery is establishing and atmospheric ONLY. Every specification a
trainee acts on belongs on a spec card and in the narration. Prompts must
describe scene, material and light, never a countable technical detail (nail
counts, seam geometry, fastener spacing), because the model will render those
wrong and a trainee could read the frame as instruction.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import requests

from config import (API, CACHE_ROOT, FOUNDRY, HEIGHT, IMAGE_H, IMAGE_MODEL,
                    IMAGE_W, VIDEO_FPS, VIDEO_FRAMES, VIDEO_MODEL, WIDTH)

CACHE_DIR = CACHE_ROOT
INDEX_FILE = CACHE_DIR / "index.json"

# Appended to every still prompt: holds the series look together and keeps
# rendered text out of the frame (all text is composited).
STYLE_SUFFIX = (
    "documentary photograph, natural daylight, realistic materials, "
    "shallow depth of field, no text, no watermark"
)

NEGATIVE = "text, watermark, logo, caption, diagram, illustration, cartoon"

# Excluded unless a shot asks for a figure. A generated worker cannot be
# trusted to wear the right protection or hold the right stance, and in a
# safety module a wrong one reads as instruction. Only binds on models using
# classifier-free guidance — the distilled turbo models run at CFG 0 and
# ignore it, so emptiness also has to be stated positively in the prompt.
NO_PEOPLE = "person, people, portrait, face, headshot"


def negative_for(people: bool) -> str:
    """Negative prompt for a shot that does or does not want a figure."""
    return NEGATIVE if people else f"{NEGATIVE}, {NO_PEOPLE}"


def _key(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def _load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text())
    return {}


def _save_index(index: dict) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, indent=2, sort_keys=True))


def full_prompt(prompt: str) -> str:
    return f"{prompt.rstrip().rstrip(',')}, {STYLE_SUFFIX}"


def dispatch_stills(prompts: list[str], variants: int = 2,
                    negative: str | None = None) -> str:
    """Queue one batch covering `prompts`; returns the batch id."""
    expanded = [full_prompt(p) for p in prompts for _ in range(variants)]
    r = requests.post(f"{API}/api/batch-image/generate/prompts", json={
        "prompts": expanded,
        "model": IMAGE_MODEL,
        "width": IMAGE_W,
        "height": IMAGE_H,
        "negative_prompt": negative or negative_for(False),
        "style": "realistic",
    }, timeout=60)
    r.raise_for_status()
    return r.json()["data"]["batch_id"]


def batch_status(batch_id: str) -> dict:
    r = requests.get(f"{API}/api/batch-image/status/{batch_id}",
                     params={"include_results": "true"}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]


_TERMINAL_OK = {"completed", "complete", "done", "finished"}
_TERMINAL_BAD = {"error", "failed", "cancelled", "canceled"}


def wait_for_batch(batch_id: str, timeout_s: int = 3600) -> list[Path]:
    """Block until the batch settles; returns the generated image paths in order."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        data = batch_status(batch_id)
        state = (data.get("status") or "").lower()
        done = data.get("completed_images", 0)
        total = data.get("total_images", 0)
        print(f"  batch {batch_id}: {state} {done}/{total}", flush=True)
        if state in _TERMINAL_OK:
            return _collect(data)
        if state in _TERMINAL_BAD:
            raise RuntimeError(
                f"batch {batch_id} ended '{state}': "
                f"{data.get('error') or 'no detail returned'}")
        time.sleep(10)
    raise RuntimeError(f"batch {batch_id} did not finish within {timeout_s}s")


def release_voice_vram() -> None:
    """Unload the voice model before an image batch.

    Chatterbox and the image models do not co-reside on a single consumer
    card; a batch that starts while the narrator is loaded fails its VRAM
    headroom check outright.
    """
    try:
        requests.post(f"{FOUNDRY}/evict/voice", timeout=60)
    except Exception as e:
        print(f"  could not evict the voice model ({e}) — continuing")


def stop_voice_service() -> None:
    """Stop audio_foundry outright to reclaim its CUDA context.

    Evicting a backend frees its weights but not the process's context, which
    is several hundred megabytes — enough to keep a large image model from
    clearing its headroom check. narration.ensure_narrator_ready restarts the
    service when the narration pass needs it.
    """
    try:
        requests.post(f"{API}/api/plugins/audio_foundry/stop", timeout=120)
        print("  stopped audio_foundry to free its CUDA context")
        time.sleep(3)
    except Exception as e:
        print(f"  could not stop audio_foundry ({e}) — continuing")


def _collect(data: dict) -> list[Path]:
    paths = [Path(r["image_path"]) for r in data.get("results") or []
             if r.get("success") and r.get("image_path")]
    paths = [p for p in paths if p.exists()]
    if paths:
        return paths
    out_dir = data.get("output_dir")
    if out_dir and Path(out_dir).exists():
        root = Path(out_dir)
        return sorted(root.glob("images/*.png")) or sorted(root.glob("*.png"))
    return []


# The service reports a VRAM shortfall as a batch error and invites a retry.
# The narrator's CUDA context survives eviction, so a shortfall of a few
# hundred megabytes clears on its own once the allocator settles.
_HEADROOM_HINTS = ("headroom", "free vram", "gpu short", "try again",
                   "out of memory", "oom", "allocat")
HEADROOM_RETRIES = 4
HEADROOM_WAIT_S = 45


def _free_vram(attempt: int) -> None:
    """Escalate VRAM recovery between retries.

    Eviction drops the voice weights; the process's CUDA context survives it and
    has to be stopped outright to release the last few hundred megabytes.
    """
    if attempt == 1:
        release_voice_vram()
    else:
        stop_voice_service()


def _dispatch_with_retry(prompts: list[str], variants: int,
                         negative: str | None = None) -> list[Path]:
    for attempt in range(1, HEADROOM_RETRIES + 1):
        batch_id = dispatch_stills(prompts, variants=variants,
                                   negative=negative)
        try:
            return wait_for_batch(batch_id)
        except RuntimeError as e:
            transient = any(h in str(e).lower() for h in _HEADROOM_HINTS)
            if not transient or attempt == HEADROOM_RETRIES:
                raise
            print(f"  VRAM not free yet (attempt {attempt}/{HEADROOM_RETRIES})"
                  f" — retrying in {HEADROOM_WAIT_S}s")
            _free_vram(attempt)
            time.sleep(HEADROOM_WAIT_S)
    raise RuntimeError("unreachable")


def _generate_group(missing: list[str], variants: int, negative: str,
                    index: dict) -> None:
    """Generate and cache one group of prompts sharing a negative prompt."""
    produced = _dispatch_with_retry(missing, variants, negative=negative)
    if len(produced) < len(missing) * variants:
        raise RuntimeError(
            f"batch returned {len(produced)} images for {len(missing)} "
            f"prompt(s) x{variants} variants — check the image service log")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for i, prompt in enumerate(missing):
        chunk = produced[i * variants:(i + 1) * variants]
        kept = []
        for j, src in enumerate(chunk):
            dst = CACHE_DIR / f"{_key(prompt)}_{j}{src.suffix}"
            dst.write_bytes(src.read_bytes())
            kept.append(str(dst))
        index[_key(prompt)] = kept


def stills_for(prompts: list[str], variants: int = 2,
               people: set[str] | None = None) -> dict[str, list[Path]]:
    """Return cached-or-generated stills keyed by prompt.

    Only prompts absent from the cache are dispatched, so an approved look
    survives re-runs and script edits.

    `people` names the prompts that deliberately want a figure in frame. They
    are dispatched as their own batch, because the rest are generated with
    people excluded and a batch carries a single negative prompt.
    """
    people = people or set()
    index = _load_index()
    missing = [p for p in prompts
               if not (index.get(_key(p))
                       and all(Path(f).exists() for f in index[_key(p)]))]
    if missing:
        print(f"generating {len(missing)} new still prompt(s) "
              f"x{variants} variants…")
        release_voice_vram()
        for wants_people in (False, True):
            group = [p for p in missing if (p in people) is wants_people]
            if group:
                _generate_group(group, variants,
                                negative_for(wants_people), index)
        _save_index(index)

    return {p: [Path(f) for f in index[_key(p)]] for p in prompts}


def i2v_payload(still: Path, prompt: str) -> dict:
    """Build the /api/batch-video/generate/image body for one start frame."""
    return {
        # The endpoint takes paths the backend can already read, not an upload.
        "image_paths": [str(still)],
        "prompt": full_prompt(prompt),
        "negative_prompt": NEGATIVE,
        "model": VIDEO_MODEL,
        "duration_frames": VIDEO_FRAMES,
        "fps": VIDEO_FPS,
        # Oversize frames are clamped to the model's pixel budget and snapped to
        # its alignment server-side.
        "width": WIDTH,
        "height": HEIGHT,
        # The server-side enhancer rewrites prompts toward cinematic detail,
        # which is exactly what the prompt discipline above excludes.
        "enhance_prompt": False,
    }


def _dispatch_i2v(still: Path, prompt: str) -> str:
    """Queue one image-to-video batch; returns the batch id."""
    r = requests.post(f"{API}/api/batch-video/generate/image",
                      json=i2v_payload(still, prompt), timeout=120)
    if not r.ok:
        # Carries the model preflight message, which names a missing checkpoint
        # outright.
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["data"]["batch_id"]


def _rendered_clip(data: dict) -> Path | None:
    """Locate the rendered clip in a settled batch.

    The batch reports `frame_paths` relative to the batch's `output_dir`; the
    singular keys only appear on some generators, so both are tried.
    """
    root = Path(data.get("output_dir") or ".")
    for result in data.get("results") or []:
        candidates = list(result.get("frame_paths") or [])
        candidates += [result[k] for k in ("video_path", "output_path")
                       if result.get(k)]
        for candidate in candidates:
            path = Path(candidate)
            if not path.is_absolute():
                path = root / path
            if path.suffix.lower() == ".mp4" and path.exists():
                return path
    return None


def _collect_i2v(batch_id: str, dest: Path, timeout_s: int = 3600) -> Path | None:
    """Block until the batch settles; returns the copied clip, or None."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        s = requests.get(f"{API}/api/batch-video/status/{batch_id}", timeout=30)
        data = s.json().get("data", {})
        state = (data.get("status") or "").lower()
        if state in _TERMINAL_OK:
            clip = _rendered_clip(data)
            if clip:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(clip.read_bytes())
                return dest
            return None
        if state in _TERMINAL_BAD:
            raise RuntimeError(
                f"batch {batch_id} ended '{state}': "
                f"{data.get('error') or 'no detail returned'}")
        time.sleep(10)
    raise RuntimeError(f"batch {batch_id} did not finish within {timeout_s}s")


def animate(still: Path, prompt: str, dest: Path) -> Path:
    """Render motion from a still via image-to-video. Falls back to the still.

    Motion is an enhancement: a failed I2V render must never block a production,
    because `assemble.py` gives every still a slow push anyway.

    The I2V models are the largest thing the engine loads — the Wan 2.2 MoE
    holds two experts — so the narrator is evicted first and a headroom failure
    escalates the same way an image batch does.
    """
    release_voice_vram()
    for attempt in range(1, HEADROOM_RETRIES + 1):
        try:
            clip = _collect_i2v(_dispatch_i2v(still, prompt), dest)
            if clip:
                return clip
            print(f"  i2v produced nothing for {still.name} — using the still")
            return still
        except Exception as e:
            transient = any(h in str(e).lower() for h in _HEADROOM_HINTS)
            if not transient or attempt == HEADROOM_RETRIES:
                print(f"  i2v failed ({e}) — using the still")
                return still
            print(f"  VRAM not free yet (attempt {attempt}/{HEADROOM_RETRIES})"
                  f" — retrying in {HEADROOM_WAIT_S}s")
            _free_vram(attempt)
            time.sleep(HEADROOM_WAIT_S)
    return still
