# backend/api/infographic_api.py
# REST endpoints for the Flux-schnell infographic generator.
#
#   POST /api/infographic/generate
#       body: {title, scene, footer?, hashtags?, callouts?, style?, aspect?,
#              raw_prompt?, seed?}
#       returns: {prompt_id, filename, image_url, prompt, width, height,
#                 seed, duration_s}
#       Synchronous — blocks until the PNG is ready (~5s on a 4070 Ti SUPER).
#
#   GET  /api/infographic/view?filename=...&subfolder=...
#       Proxies the PNG bytes from ComfyUI's own /view so the frontend
#       can <img src> without CORS hassles.
#
#   GET  /api/infographic/status
#       Returns whether ComfyUI is reachable + which Flux assets are on disk.

from __future__ import annotations

import logging
import threading
import time
import urllib.request
from pathlib import Path
from typing import Dict, List

from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

infographic_bp = Blueprint("infographic_api", __name__, url_prefix="/api/infographic")


def _comfyui_base() -> Path:
    try:
        from backend.config import COMFYUI_DIR  # type: ignore
        return Path(COMFYUI_DIR)
    except Exception:
        return Path(__file__).resolve().parents[2] / "plugins" / "comfyui" / "ComfyUI"


# Single source of truth for the four Flux files. The download URLs are
# the ones we verified work — note ae.safetensors pulls from the
# Comfy-Org Lumina mirror because BFL gated their copy.
INFOGRAPHIC_ASSETS: List[Dict[str, object]] = [
    {
        "id": "flux_unet",
        "name": "FLUX.1-schnell (Q8_0 GGUF)",
        "description": "Diffusion model — the part that actually paints. Q8 is the sweet spot for 16GB VRAM.",
        "rel_path": "models/unet/flux1-schnell-Q8_0.gguf",
        "url": "https://huggingface.co/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q8_0.gguf",
        "size_gb": 12.7,
    },
    {
        "id": "t5xxl",
        "name": "T5-XXL FP8 text encoder",
        "description": "Long-prompt understanding encoder. Required for any Flux workflow.",
        "rel_path": "models/clip/t5/t5xxl_fp8_e4m3fn.safetensors",
        "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors",
        "size_gb": 4.9,
    },
    {
        "id": "clip_l",
        "name": "CLIP-L text encoder",
        "description": "The second of Flux's two text encoders. Small and quick.",
        "rel_path": "models/clip/clip_l.safetensors",
        "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors",
        "size_gb": 0.25,
    },
    {
        "id": "vae",
        "name": "Flux VAE (ae.safetensors)",
        "description": "Decodes Flux's latents into pixels. Same file Lumina ships, so pulled from that mirror.",
        "rel_path": "models/vae/ae.safetensors",
        "url": "https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/resolve/main/split_files/vae/ae.safetensors",
        "size_gb": 0.34,
    },
]


def _asset_status_list() -> List[Dict[str, object]]:
    base = _comfyui_base()
    out = []
    for asset in INFOGRAPHIC_ASSETS:
        full = base / str(asset["rel_path"])
        installed = full.exists() and full.stat().st_size > 1024  # >1KB filters tiny error pages
        size_on_disk_gb = round(full.stat().st_size / 1024**3, 2) if installed else 0.0
        out.append({
            **asset,
            "installed": installed,
            "size_on_disk_gb": size_on_disk_gb,
        })
    return out


def _required_assets_present() -> dict:
    return {a["id"]: a["installed"] for a in _asset_status_list()}


# Per-process download state — only one Flux file at a time so progress
# tracking stays unambiguous. Matches the pattern used by batch-image.
_download_lock = threading.Lock()
_download_state: Dict[str, object] = {
    "is_downloading": False,
    "current_id": None,
    "progress": 0,
    "status": "idle",      # idle | starting | downloading | completed | failed
    "speed_mbps": 0,
    "downloaded_gb": 0,
    "total_gb": 0,
    "error": None,
}


def _do_download(asset: Dict[str, object]) -> None:
    """Stream the file to the right subdirectory, updating progress as we go."""
    base = _comfyui_base()
    dest = base / str(asset["rel_path"])
    dest.parent.mkdir(parents=True, exist_ok=True)

    tmp = dest.with_suffix(dest.suffix + ".part")
    url = str(asset["url"])
    started = time.time()
    last_progress_tick = started

    try:
        with _download_lock:
            _download_state.update({"status": "downloading"})

        req = urllib.request.Request(url, headers={"User-Agent": "guaardvark-infographic-installer"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or 0) or int(float(asset["size_gb"]) * 1024**3)
            downloaded = 0
            chunk = 1024 * 256  # 256KB
            with open(tmp, "wb") as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    downloaded += len(buf)

                    now = time.time()
                    if now - last_progress_tick > 0.5:  # cap to 2 updates/sec
                        elapsed = max(now - started, 0.1)
                        speed_mbps = (downloaded / (1024 * 1024)) / elapsed
                        with _download_lock:
                            _download_state.update({
                                "progress": min(int((downloaded / max(total, 1)) * 100), 99),
                                "speed_mbps": round(speed_mbps, 1),
                                "downloaded_gb": round(downloaded / 1024**3, 2),
                                "total_gb": round(total / 1024**3, 2),
                            })
                        last_progress_tick = now

        # Sanity check — if the server gave us an HTML error page, the
        # file will be tiny. Don't leave a broken sentinel on disk.
        if tmp.stat().st_size < 1024 * 50:  # 50KB floor
            head = tmp.read_bytes()[:200].decode("utf-8", errors="replace")
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Download came back too small — looks like an error page: {head!r}")

        tmp.rename(dest)
        with _download_lock:
            _download_state.update({
                "status": "completed",
                "progress": 100,
                "downloaded_gb": round(dest.stat().st_size / 1024**3, 2),
            })
        logger.info(f"[INFOGRAPHIC] downloaded {asset['id']} → {dest}")

    except Exception as e:
        logger.exception(f"Infographic asset download failed: {asset['id']}")
        with _download_lock:
            _download_state.update({
                "status": "failed",
                "error": str(e),
            })
        # Clean up partial file so a retry starts fresh
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    finally:
        with _download_lock:
            _download_state["is_downloading"] = False


@infographic_bp.route("/status", methods=["GET"])
def status():
    from backend.services.infographic_generator import get_infographic_generator

    gen = get_infographic_generator()
    assets = _required_assets_present()
    return jsonify({
        "comfyui_reachable": gen.is_available(),
        "comfyui_url": gen.comfy_url,
        "assets": assets,
        "ready": gen.is_available() and all(assets.values()),
    })


@infographic_bp.route("/generate", methods=["POST"])
def generate():
    from backend.services.infographic_generator import (
        ComfyUIUnavailable,
        InfographicSpec,
        get_infographic_generator,
    )

    body = request.get_json(silent=True) or {}

    # Hashtags arrive as a list or as a single comma/space-separated string;
    # normalize both shapes so the frontend can be lazy.
    raw_tags = body.get("hashtags") or []
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.replace(",", " ").split() if t.strip()]

    raw_callouts = body.get("callouts") or []
    if isinstance(raw_callouts, str):
        raw_callouts = [c.strip() for c in raw_callouts.splitlines() if c.strip()]

    spec = InfographicSpec(
        title=str(body.get("title", "")).strip(),
        scene=str(body.get("scene", "")).strip(),
        footer=str(body.get("footer", "")).strip(),
        hashtags=[str(t).lstrip("#") for t in raw_tags],
        callouts=[str(c) for c in raw_callouts],
        style=str(body.get("style", "editorial")),
        aspect=str(body.get("aspect", "16:9")),
        raw_prompt=str(body.get("raw_prompt", "")),
    )

    if not (spec.scene or spec.raw_prompt):
        return jsonify({
            "success": False,
            "error": "Either 'scene' or 'raw_prompt' is required.",
        }), 400

    seed = body.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            seed = None

    gen = get_infographic_generator()
    try:
        result = gen.generate(spec, seed=seed)
    except ComfyUIUnavailable as e:
        return jsonify({"success": False, "error": str(e)}), 503
    except TimeoutError as e:
        return jsonify({"success": False, "error": str(e)}), 504
    except Exception as e:
        logger.exception("Infographic generation failed")
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({"success": True, **result})


@infographic_bp.route("/view", methods=["GET"])
def view():
    """Proxy ComfyUI's /view so the frontend can fetch the PNG without
    cross-origin gymnastics. Streams the bytes through."""
    from backend.services.infographic_generator import get_infographic_generator

    filename = request.args.get("filename", "").strip()
    subfolder = request.args.get("subfolder", "").strip()
    if not filename:
        return jsonify({"error": "filename required"}), 400

    gen = get_infographic_generator()
    try:
        data = gen.fetch_image_bytes(filename, subfolder=subfolder)
    except Exception as e:
        logger.warning(f"view proxy failed for {filename}: {e}")
        return jsonify({"error": "image not available"}), 404

    return Response(data, mimetype="image/png")


# -- Models management ---------------------------------------------------

@infographic_bp.route("/models", methods=["GET"])
def list_models():
    return jsonify({
        "success": True,
        "models": _asset_status_list(),
    })


@infographic_bp.route("/models/download", methods=["POST"])
def download_model():
    body = request.get_json(silent=True) or {}
    asset_id = str(body.get("id", "")).strip()

    asset = next((a for a in INFOGRAPHIC_ASSETS if a["id"] == asset_id), None)
    if not asset:
        return jsonify({"success": False, "error": f"unknown asset id: {asset_id}"}), 400

    with _download_lock:
        if _download_state["is_downloading"]:
            return jsonify({
                "success": False,
                "error": f"already downloading {_download_state['current_id']}",
            }), 409

        _download_state.update({
            "is_downloading": True,
            "current_id": asset_id,
            "progress": 0,
            "status": "starting",
            "speed_mbps": 0,
            "downloaded_gb": 0,
            "total_gb": float(asset["size_gb"]),
            "error": None,
        })

    threading.Thread(
        target=_do_download, args=(asset,), daemon=True,
        name=f"infographic-dl-{asset_id}",
    ).start()

    return jsonify({"success": True, "status": "started", "id": asset_id})


@infographic_bp.route("/models/download-status", methods=["GET"])
def download_status():
    with _download_lock:
        return jsonify({"success": True, **_download_state})
