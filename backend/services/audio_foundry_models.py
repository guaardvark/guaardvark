"""Audio Foundry weights: what is on this machine, and the install path.

Generation must never fetch anything: the sidecar runs with the Hugging Face
client offline and refuses a missing file with an Install hint. This module is
the catalog the Audio Studio modal reads, plus the explicit Install flow
(snapshot_download, and for Kokoro the spaCy English model pip-installed into
the plugin venv). Flask owns both so listing and download work when the
sidecar is stopped, and so the downloads run in this process, which is online,
rather than in the offline sidecar.

"Installed" means every file generation reads is present, not one probe file:
a Kokoro cache holding the weights but only some voice packs used to show as
installed while the missing voices were fetched mid-generation.

ACE-Step 1.5 is the one model outside that pattern: its loader reads a plain
checkpoints folder and rewrites code files inside it, so Install puts its weights
in data/models/ace-step-1.5/ rather than the shared cache, and first builds the
Python environment it runs in (plugins/audio_foundry/scripts/setup_music15.sh).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DOWNLOAD_STALL_SECONDS = 180
_HF_XET_ENV = "HF_HUB_DISABLE_XET"
_PIP_TIMEOUT_SECONDS = 600
# Building ACE-Step 1.5's environment downloads torch and the CUDA libraries.
_ENV_BUILD_TIMEOUT_SECONDS = 3600

# Two roots that coincide on a real install but mean different things. The
# voice catalog is tracked source shipped in the same checkout as this module,
# so it is always read from there. PLUGIN_DIR is where the plugin's venv lives
# (untracked, created on this machine at first start); only venv lookups and
# pip use it.
REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_SOURCE_DIR = REPO_ROOT / "plugins" / "audio_foundry"
PLUGIN_DIR = PLUGIN_SOURCE_DIR

# MiniMax Music 3 lives in the video registry (ComfyUI/models). One Install
# click here forwards to that downloader so Audio Studio is self-contained.
MINIMAX_MUSIC3_ID = "minimax-music3-int8"

# Sizes are the sum of the files Install fetches, from the Hugging Face file
# listings read 2026-09-15. ``allow_patterns`` / ``ignore_patterns`` go to
# snapshot_download: the Chatterbox repo is 13.9 GB of language variants of
# which the loader reads five files (3.3 GB); Stable Audio Open ships the same
# weights three times (15.7 GB, 5.3 GB without the root .ckpt/.safetensors).
AUDIO_FOUNDRY_MODELS: List[Dict[str, Any]] = [
    {
        "id": "chatterbox",
        "name": "Chatterbox TTS",
        "description": "Expressive voiceover and reference-clip cloning. 3.3 GB.",
        "group": "voice",
        "hf_repo": "ResembleAI/chatterbox",
        "probe_file": "t3_cfg.safetensors",
        # What ChatterboxBackend.load() requires; the same five Install fetches.
        "required_files": [
            "ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors",
            "tokenizer.json", "conds.pt",
        ],
        "allow_patterns": [
            "ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors",
            "tokenizer.json", "conds.pt",
        ],
        "size_gb": 3.3,
        "gated": False,
    },
    {
        "id": "kokoro",
        "name": "Kokoro-82M",
        "description": (
            "Fast built-in voices (fallback when Chatterbox is off): weights, voice "
            "packs and spaCy's English model. About 375 MB."
        ),
        "group": "voice",
        "hf_repo": "hexgrad/Kokoro-82M",
        "probe_file": "config.json",
        # Required files and the spaCy model come from the plugin's voice list,
        # so a voice added there is part of "installed" and of the Install.
        "voice_catalog": "backends/kokoro_voices.json",
        # The repo snapshot only (weights 327 MB, voice packs 0.5 MB each):
        # progress is measured against the Hugging Face cache. The spaCy model
        # (15 MB installed) goes into the plugin venv afterwards.
        "size_gb": 0.36,
        "gated": False,
    },
    {
        "id": "ace-step",
        "name": "ACE-Step v1 3.5B",
        "description": "Full songs with vocals from a style prompt and lyrics. 8.3 GB.",
        "group": "music",
        "license": "Apache-2.0",
        "hf_repo": "ACE-Step/ACE-Step-v1-3.5B",
        "probe_file": "ace_step_transformer/config.json",
        # What ACEStepPipeline.load_checkpoint reads: its four model folders
        # (config and weights each, plus the umt5 tokenizer), listed in the
        # plugin file the ACE-Step daemon checks too. From the snapshot a full
        # Install left in the cache (revision 82cd0d7b, read 2026-09-30); the
        # other repo files are the README, .gitattributes and a root config
        # the loader never opens. Install stays a full snapshot_download.
        "required_files_catalog": "backends/acestep_files.json",
        "size_gb": 8.3,
        "gated": False,
    },
    {
        "id": "ace-step-1.5",
        "name": "ACE-Step 1.5",
        "description": (
            "Songs and instrumentals at 48 kHz from a 2B turbo model with a 1.7B planner. "
            "Optional: ACE-Step v1 stays the default. Install downloads 9.4 GB of weights "
            "and builds its own Python environment (about 8 GB more, Linux x86_64 only)."
        ),
        "group": "music",
        "license": "MIT",
        # Measured 2026-10-04 on a 16 GB card: 30 s and 240 s renders.
        "vram_note": "Takes the whole GPU while it runs: 14.4 GB at peak, measured on a 16 GB card.",
        "hf_repo": "ACE-Step/Ace-Step1.5",
        "probe_file": "acestep-v15-turbo/config.json",
        # The catalog names the files, the pinned Hugging Face revision, the folder
        # under the repo root the weights go to, and the environment Install builds.
        "required_files_catalog": "backends/acestep15_files.json",
        "local_weights": True,
        "environment_script": "scripts/setup_music15.sh",
        # Sum of the snapshot's files at the pinned revision (read 2026-10-03).
        "size_gb": 9.4,
        "gated": False,
        # Installed only by its own Install button, never by "Install all missing":
        # a stock install keeps v1 and does not pull 9.4 GB plus an 8 GB environment.
        "optional": True,
    },
    {
        "id": "stable-audio-open",
        "name": "Stable Audio Open 1.0",
        "description": "Sound effects, ambience and music beds up to 47 s. 5.3 GB. Gated on Hugging Face.",
        "group": "fx",
        "hf_repo": "stabilityai/stable-audio-open-1.0",
        "probe_file": "model_index.json",
        "ignore_patterns": ["model.ckpt", "model.safetensors", "vae_model.ckpt", "*.png"],
        "size_gb": 5.3,
        "gated": True,
        "terms_url": "https://huggingface.co/stabilityai/stable-audio-open-1.0",
    },
]

_download_lock = threading.Lock()
_download_epoch = 0
_download_state: Dict[str, Any] = {
    "is_downloading": False,
    "current_id": None,
    "progress": 0,
    "status": "idle",
    "speed_mbps": 0,
    "downloaded_gb": 0,
    "total_gb": 0,
    "error": None,
    "delegated": None,
}


def reset_download_state() -> None:
    """Test helper — clear the in-process download lock."""
    global _download_epoch
    with _download_lock:
        _download_epoch = 0
        _download_state.update({
            "is_downloading": False,
            "current_id": None,
            "progress": 0,
            "status": "idle",
            "speed_mbps": 0,
            "downloaded_gb": 0,
            "total_gb": 0,
            "error": None,
            "delegated": None,
            "updated_at": 0,
            "epoch": 0,
        })


def get_entry(model_id: str) -> Optional[Dict[str, Any]]:
    for row in AUDIO_FOUNDRY_MODELS:
        if row["id"] == model_id:
            return row
    if model_id == MINIMAX_MUSIC3_ID:
        return {"id": MINIMAX_MUSIC3_ID, "delegate": "video"}
    return None


def is_hub_cached(repo_id: str, probe_file: str) -> bool:
    from backend.services.local_weights import is_cached
    return is_cached(repo_id, probe_file)


def load_voice_catalog(relpath: str) -> Dict[str, Any]:
    """A JSON list shipped with the plugin (e.g. backends/kokoro_voices.json,
    backends/acestep_files.json)."""
    with (PLUGIN_SOURCE_DIR / relpath).open("r", encoding="utf-8") as fh:
        return json.load(fh)


_voice_consent_module = None


def voice_consent():
    """The plugin's voice-consent rules (plugins/audio_foundry/backends/voice_consent.py).

    Loaded from the checkout by path, like the voice catalog, so the backend
    proxy and the plugin that clones decide consent with the same code. The
    module is standard-library only.
    """
    global _voice_consent_module
    if _voice_consent_module is None:
        import importlib.util
        import sys

        name = "guaardvark_audio_foundry_voice_consent"
        spec = importlib.util.spec_from_file_location(
            name, PLUGIN_SOURCE_DIR / "backends" / "voice_consent.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _voice_consent_module = module
    return _voice_consent_module


def kokoro_catalog() -> Dict[str, Any]:
    entry = next(e for e in AUDIO_FOUNDRY_MODELS if e["id"] == "kokoro")
    return load_voice_catalog(entry["voice_catalog"])


def kokoro_voice_ids() -> List[str]:
    """Voice ids the sidecar accepts for Kokoro, in catalog order."""
    return [v["id"] for g in kokoro_catalog()["groups"] for v in g["voices"]]


def kokoro_voice_groups() -> List[Dict[str, Any]]:
    """The catalog's groups with each voice marked ``installed`` when its voice
    pack is in the local Hugging Face cache: the Kokoro part of the plugin's
    GET /voices, for when the plugin is not running."""
    cat = kokoro_catalog()
    return [
        {
            "label": group["label"],
            "voices": [
                {**voice,
                 "installed": is_hub_cached(cat["hf_repo"], cat["voice_file"].format(voice=voice["id"]))}
                for voice in group["voices"]
            ],
        }
        for group in cat["groups"]
    ]


def kokoro_voice_choices(installed_only: bool = True) -> List[Dict[str, str]]:
    """The Kokoro voices as ``{id, label, group}``, for a caller choosing one.

    With ``installed_only`` only voices whose pack is on this machine are
    listed: Audio Foundry refuses a voice that is not installed rather than
    download it mid-generation.
    """
    cat = kokoro_catalog()
    choices = []
    for group in cat["groups"]:
        for voice in group["voices"]:
            if installed_only and not is_hub_cached(
                    cat["hf_repo"], cat["voice_file"].format(voice=voice["id"])):
                continue
            choices.append({"id": voice["id"], "label": voice["label"], "group": group["label"]})
    return choices


def required_hub_files(entry: Dict[str, Any]) -> List[str]:
    """Every file of ``entry['hf_repo']`` that generation reads."""
    if entry.get("required_files_catalog"):
        return list(load_voice_catalog(entry["required_files_catalog"])["files"])
    if entry.get("voice_catalog"):
        cat = load_voice_catalog(entry["voice_catalog"])
        voices = [v["id"] for g in cat["groups"] for v in g["voices"]]
        return [cat["config_file"], cat["weights_file"]] + [
            cat["voice_file"].format(voice=v) for v in voices
        ]
    return list(entry.get("required_files") or [entry["probe_file"]])


def local_weights_dir(entry: Dict[str, Any]) -> Optional[Path]:
    """The checkpoints folder of a model whose weights live outside the HF cache, else None."""
    if not entry.get("local_weights"):
        return None
    cat = load_voice_catalog(entry["required_files_catalog"])
    return REPO_ROOT / cat["local_dir"] / "checkpoints"


def missing_hub_files(entry: Dict[str, Any]) -> List[str]:
    local = local_weights_dir(entry)
    if local is not None:
        return [f for f in required_hub_files(entry) if not (local / f).is_file()]
    return [f for f in required_hub_files(entry) if not is_hub_cached(entry["hf_repo"], f)]


# setup_music15.sh writes the source commit it built from here as its last step.
_ENV_MARKER = ".acestep15-source"


def missing_environment(entry: Dict[str, Any]) -> List[str]:
    """The model's own Python environment, when it has one and it is not built
    from the pinned source commit."""
    if not entry.get("environment_script"):
        return []
    cat = load_voice_catalog(entry["required_files_catalog"])
    venv = PLUGIN_DIR / cat["environment"]
    try:
        built_from = (venv / _ENV_MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        built_from = None
    if (venv / "bin" / "python").exists() and built_from == cat["source"]["commit"]:
        return []
    return [f"{cat['environment']} (Python environment)"]


def missing_parts(model_id: str) -> List[str]:
    """What generation with ``model_id`` would lack: files, packages, environment."""
    entry = next((e for e in AUDIO_FOUNDRY_MODELS if e["id"] == model_id), None)
    if entry is None:
        return []
    return (missing_environment(entry) + missing_hub_files(entry)
            + [p["package"] for p in missing_venv_packages(entry)])


# The text ACE-Step 1.5's DiT encodes (acestep/constants.py SFT_GEN_PROMPT, _format_lyrics).
# BPM, key and meter sit after the caption, so a long caption pushes them past the limit too.
_ACE15_TEXT_PROMPT = (
    "# Instruction\nFill the audio semantic mask based on the given conditions:\n\n"
    "# Caption\n{caption}\n\n# Metas\n"
    "- bpm: {bpm}\n- timesignature: {meter}\n- keyscale: {key}\n- duration: {seconds} seconds\n"
    "<|endoftext|>\n"
)
_ACE15_LYRIC_PROMPT = "# Languages\n{language}\n\n# Lyric\n{lyrics}<|endoftext|>"


@lru_cache(maxsize=1)
def _ace15_tokenizer(path: str) -> Any:
    from tokenizers import Tokenizer

    return Tokenizer.from_file(path)


def ace15_text_usage(caption: str, lyrics: str = "", bpm: Any = None, keyscale: str = "",
                     timesignature: str = "", duration_s: float = 60.0,
                     vocal_language: str = "") -> Optional[Dict[str, Dict[str, int]]]:
    """Tokens a song's text takes of what ACE-Step 1.5 reads, counted with its own
    tokenizer. The model drops the rest without a word. None before it is installed."""
    cat = json.loads((PLUGIN_SOURCE_DIR / "backends" / "acestep15_files.json").read_text(encoding="utf-8"))
    encoder = cat["text_encoder"]
    path = REPO_ROOT / cat["local_dir"] / "checkpoints" / encoder["tokenizer"]
    if not path.is_file():
        return None
    tokenizer = _ace15_tokenizer(str(path))
    text = _ACE15_TEXT_PROMPT.format(
        caption=caption or "", bpm=bpm or "N/A", meter=(timesignature or "").strip() or "N/A",
        key=(keyscale or "").strip() or "N/A", seconds=int(float(duration_s or 60)),
    )
    sung = _ACE15_LYRIC_PROMPT.format(language=(vocal_language or "").strip() or "unknown", lyrics=lyrics or "")
    return {
        "caption": {"tokens": len(tokenizer.encode(text).ids), "limit": int(encoder["caption_tokens"])},
        "lyrics": {"tokens": len(tokenizer.encode(sung).ids), "limit": int(encoder["lyrics_tokens"])},
    }


def environment_supported(entry: Dict[str, Any]) -> Optional[str]:
    """None when Install can build the entry's environment here, else the reason."""
    if not entry.get("environment_script"):
        return None
    import platform
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        return (f"{entry['name']} runs on Linux x86_64 with an NVIDIA GPU; "
                f"this machine is {platform.system()} {platform.machine()}.")
    return None


def plugin_venv_python() -> Optional[Path]:
    """The Audio Foundry venv interpreter, or None before the plugin's first start."""
    for rel in ("venv/bin/python", "venv/Scripts/python.exe"):
        candidate = PLUGIN_DIR / rel
        if candidate.exists():
            return candidate
    return None


def venv_has_package(package: str) -> Optional[bool]:
    """Whether the plugin venv has ``package`` installed; None when there is no venv.

    Looks for the package's .dist-info, which is what importlib.metadata (and
    so spaCy's is_package, and the sidecar's own check) finds.
    """
    venv = PLUGIN_DIR / "venv"
    if plugin_venv_python() is None:
        return None
    site_dirs = list(venv.glob("lib/python*/site-packages")) + list(venv.glob("Lib/site-packages"))
    name = package.replace("-", "_")
    return any(any(d.glob(f"{name}-*.dist-info")) for d in site_dirs)


def required_venv_packages(entry: Dict[str, Any]) -> List[Dict[str, str]]:
    """Python packages generation needs in the plugin venv: {package, wheel}."""
    if not entry.get("voice_catalog"):
        return []
    g2p = load_voice_catalog(entry["voice_catalog"]).get("english_g2p")
    return [{"package": g2p["package"], "wheel": g2p["wheel"]}] if g2p else []


def missing_venv_packages(entry: Dict[str, Any]) -> List[Dict[str, str]]:
    """Required packages the plugin venv lacks.

    Before the plugin's first start there is no venv to check or install into,
    so nothing is reported missing; once it exists, a missing package makes
    the model show as not installed and Install adds it.
    """
    return [p for p in required_venv_packages(entry) if venv_has_package(p["package"]) is False]


def hf_token_present() -> bool:
    from backend.services.user_model_families import hf_token_present as _present
    return _present()


def plugin_snapshot() -> Dict[str, Any]:
    """audio_foundry enable/run state. Never starts the plugin."""
    try:
        from backend.plugins.plugin_base import PluginStatus
        from backend.plugins.plugin_manager import get_plugin_manager

        pm = get_plugin_manager()
        status = pm.get_status("audio_foundry")
        value = status.value if hasattr(status, "value") else str(status)
        running = status == PluginStatus.RUNNING
        enabled = bool(pm.is_effectively_enabled("audio_foundry"))
        return {"running": running, "enabled": enabled, "status": value}
    except Exception as e:
        logger.debug("audio_foundry plugin snapshot failed: %s", e)
        return {"running": False, "enabled": False, "status": "unknown"}


def _minimax_row() -> Dict[str, Any]:
    from backend.api.batch_video_generation_api import (
        _check_model_downloaded,
        _missing_check_files,
        _resolve_download_plan,
    )
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY

    info = VIDEO_MODEL_REGISTRY[MINIMAX_MUSIC3_ID]
    plan = _resolve_download_plan(MINIMAX_MUSIC3_ID)
    ready = all(_check_model_downloaded(eid) for eid in plan)
    size = round(sum(VIDEO_MODEL_REGISTRY[eid]["size_gb"] for eid in plan), 2)
    missing = _missing_check_files(MINIMAX_MUSIC3_ID) if not ready else []
    return {
        "id": MINIMAX_MUSIC3_ID,
        "name": info["name"],
        "description": (
            f"{info['description']} Installs into ComfyUI — same files as Manage Video Models."
        ),
        "group": "music",
        "hf_repo": info["hf_repo"],
        "probe_file": None,
        "size_gb": size,
        "gated": False,
        "terms_url": None,
        "installed": ready,
        "delegate": "video",
        "missing_files": missing,
    }


def _hub_row(entry: Dict[str, Any]) -> Dict[str, Any]:
    missing = (missing_environment(entry) + missing_hub_files(entry)
               + [p["package"] for p in missing_venv_packages(entry)])
    installed = not missing
    return {
        "id": entry["id"],
        "name": entry["name"],
        "description": entry["description"],
        "group": entry["group"],
        "hf_repo": entry["hf_repo"],
        "probe_file": entry["probe_file"],
        "size_gb": entry["size_gb"],
        "gated": bool(entry.get("gated")),
        "terms_url": entry.get("terms_url"),
        "license": entry.get("license"),
        "vram_note": entry.get("vram_note"),
        "optional": bool(entry.get("optional")),
        "installed": installed,
        "delegate": None,
        "missing_files": missing,
    }


def list_models() -> Dict[str, Any]:
    models = [_hub_row(e) for e in AUDIO_FOUNDRY_MODELS]
    try:
        models.append(_minimax_row())
    except Exception as e:
        logger.warning("MiniMax Music 3 row failed: %s", e)
    return {"plugin": plugin_snapshot(), "models": models}


def download_status() -> Dict[str, Any]:
    with _download_lock:
        st = dict(_download_state)
    if st.get("delegated") == "video":
        return _video_status_as_audio(st)
    return {"success": True, **{k: v for k, v in st.items() if k != "delegated"}}


def _video_status_as_audio(fallback: Dict[str, Any]) -> Dict[str, Any]:
    from backend.api import batch_video_generation_api as video_api

    with video_api._video_model_download_lock:
        vs = dict(video_api._video_model_download_status)
    return {
        "success": True,
        "is_downloading": bool(vs.get("is_downloading")),
        "current_id": vs.get("current_model") or fallback.get("current_id"),
        "progress": vs.get("progress") or 0,
        "status": vs.get("status") or "idle",
        "speed_mbps": vs.get("speed_mbps") or 0,
        "downloaded_gb": vs.get("downloaded_gb") or 0,
        "total_gb": vs.get("total_gb") or fallback.get("total_gb") or 0,
        "error": vs.get("error"),
    }


def _hf_repo_cache_dir(repo_id: str) -> Path:
    from backend.services.local_weights import hf_repo_cache_dir
    return hf_repo_cache_dir(repo_id)


def _dir_bytes(d: Path) -> int:
    total = 0
    if not d.exists():
        return 0
    # snapshots/<rev>/ holds symlinks into blobs/; count each blob once.
    for f in d.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def start_download(model_id: str) -> tuple:
    """Start an Install. Returns (payload_dict, http_status)."""
    global _download_epoch

    entry = get_entry(model_id)
    if not entry:
        return {"success": False, "error": f"unknown model id: {model_id}"}, 400

    with _download_lock:
        st = _download_state
        if st.get("is_downloading"):
            return {
                "success": False,
                "error": f"already downloading {st.get('current_id')}",
            }, 409

    if entry.get("delegate") == "video" or model_id == MINIMAX_MUSIC3_ID:
        return _start_minimax_download()

    hub_entry = next(e for e in AUDIO_FOUNDRY_MODELS if e["id"] == model_id)
    if hub_entry.get("gated") and not hf_token_present():
        terms = hub_entry.get("terms_url") or f"https://huggingface.co/{hub_entry['hf_repo']}"
        return {
            "success": False,
            "error": (
                f"{hub_entry['name']} is gated on Hugging Face. "
                f"1) Accept terms at {terms}. "
                "2) Set HF_TOKEN in .env and restart the backend."
            ),
        }, 400

    if not missing_parts(model_id):
        return {
            "success": True,
            "already_installed": True,
            "id": model_id,
        }, 200

    unsupported = environment_supported(hub_entry)
    if unsupported:
        return {"success": False, "error": unsupported}, 400

    with _download_lock:
        _download_epoch += 1
        epoch = _download_epoch
        _download_state.update({
            "is_downloading": True,
            "current_id": model_id,
            "progress": 0,
            "status": "starting",
            "speed_mbps": 0,
            "downloaded_gb": 0,
            "total_gb": float(hub_entry["size_gb"]),
            "error": None,
            "delegated": None,
            "updated_at": time.time(),
            "epoch": epoch,
        })

    threading.Thread(
        target=_run_install,
        args=(hub_entry, epoch),
        daemon=True,
        name=f"audio-foundry-dl-{model_id}",
    ).start()
    return {"success": True, "status": "started", "id": model_id}, 200


def _start_minimax_download() -> tuple:
    from backend.api.batch_video_generation_api import start_video_model_download

    resp, status = start_video_model_download(MINIMAX_MUSIC3_ID)
    body = resp.get_json() if hasattr(resp, "get_json") else {}
    if status == 409:
        err = (body.get("error") or {}).get("message") or body.get("error") or "already downloading"
        return {"success": False, "error": err}, 409
    if status != 200 or not body.get("success"):
        err = (body.get("error") or {}).get("message") or body.get("error") or "download failed"
        return {"success": False, "error": err}, status or 500

    data = body.get("data") or {}
    message = (body.get("message") or data.get("message") or "").lower()
    if data.get("already_installed") or "already installed" in message:
        return {"success": True, "already_installed": True, "id": MINIMAX_MUSIC3_ID}, 200

    with _download_lock:
        _download_state.update({
            "is_downloading": True,
            "current_id": MINIMAX_MUSIC3_ID,
            "progress": 0,
            "status": "starting",
            "speed_mbps": 0,
            "downloaded_gb": 0,
            "total_gb": float((_minimax_row() or {}).get("size_gb") or 11.09),
            "error": None,
            "delegated": "video",
            "updated_at": time.time(),
        })
    return {"success": True, "status": "started", "id": MINIMAX_MUSIC3_ID}, 200


class VenvInstallFailed(RuntimeError):
    """pip could not add a package to the plugin venv (not a Hugging Face error)."""


class EnvironmentBuildFailed(VenvInstallFailed):
    """A model's own Python environment could not be built."""


def build_model_environment(entry: Dict[str, Any]) -> None:
    """Run the entry's environment script; its output goes to logs/<id>_install.log."""
    script = PLUGIN_SOURCE_DIR / entry["environment_script"]
    log_path = REPO_ROOT / "logs" / f"{entry['id']}_install.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # The backend's own interpreter settings must not leak into the build.
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    try:
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.run(
                ["bash", str(script)], stdout=log, stderr=subprocess.STDOUT,
                cwd=str(REPO_ROOT), env=env, timeout=_ENV_BUILD_TIMEOUT_SECONDS,
            )
    except subprocess.TimeoutExpired as e:
        raise EnvironmentBuildFailed(
            f"Building the {entry['name']} environment took over "
            f"{_ENV_BUILD_TIMEOUT_SECONDS // 60} minutes and was stopped. See {log_path}."
        ) from e
    except OSError as e:
        raise EnvironmentBuildFailed(f"Could not run {script.name}: {e}") from e
    if proc.returncode != 0:
        lines = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        reason = next((ln.split("FAILED:", 1)[1].strip() for ln in reversed(lines) if "FAILED:" in ln),
                      lines[-1] if lines else f"exit {proc.returncode}")
        raise EnvironmentBuildFailed(
            f"Building the {entry['name']} environment failed: {reason}. Full log: {log_path}"
        )
    if missing_environment(entry):
        raise EnvironmentBuildFailed(
            f"{script.name} finished but the {entry['name']} environment is not complete. See {log_path}."
        )


def pip_install_into_plugin_venv(wheel: str) -> None:
    """``pip install --no-deps <wheel>`` with the plugin venv's interpreter.

    --no-deps: the wheel is a data package, and a resolver pass here could
    move torch or diffusers off the versions start.sh settled on. The wheel
    URL carries its sha256, which pip checks.
    """
    python = plugin_venv_python()
    if python is None:
        raise VenvInstallFailed(
            "Audio Foundry has no venv yet; start the plugin once, then Install again"
        )
    try:
        proc = subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", "--disable-pip-version-check", wheel],
            capture_output=True, text=True, timeout=_PIP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise VenvInstallFailed(f"pip could not run in the Audio Foundry venv: {e}") from e
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise VenvInstallFailed(f"pip could not install {wheel.split('#', 1)[0]}: {tail}")


def _run_install(entry: Dict[str, Any], epoch: int) -> None:
    """Install thread: the model's own environment when it has one, the repo
    snapshot when files are missing, then venv packages."""
    os.environ.setdefault(_HF_XET_ENV, "1")
    repo_id = entry["hf_repo"]
    local = local_weights_dir(entry)
    dest = local if local is not None else _hf_repo_cache_dir(repo_id)
    dest.mkdir(parents=True, exist_ok=True)
    baseline = _dir_bytes(dest)
    total_bytes = int(float(entry["size_gb"]) * 1024**3) or 1
    started = time.time()
    stalled = threading.Event()
    last_repo = repo_id

    def _is_current() -> bool:
        return _download_state.get("epoch") == epoch

    def _update(**kw) -> bool:
        with _download_lock:
            if not _is_current():
                return False
            _download_state.update(kw)
            _download_state["updated_at"] = time.time()
            return True

    stop_monitor = threading.Event()

    def _monitor() -> None:
        last_bytes = -1
        last_change = time.time()
        while not stop_monitor.is_set():
            try:
                downloaded = max(0, _dir_bytes(dest) - baseline)
                now = time.time()
                if downloaded != last_bytes:
                    last_bytes = downloaded
                    last_change = now
                elif (now - last_change) > _DOWNLOAD_STALL_SECONDS:
                    stalled.set()
                    # is_downloading stays True until the worker exits (finally
                    # below), so a retry cannot start a second snapshot_download
                    # into the same cache directory.
                    _update(
                        status="failed",
                        progress=0,
                        error=(
                            f"Download stalled — no progress for {_DOWNLOAD_STALL_SECONDS}s. "
                            "Check your network and click Install to retry."
                        ),
                    )
                    stop_monitor.set()
                    break
                elapsed = max(now - started, 0.1)
                speed = (downloaded / (1024 * 1024)) / elapsed
                pct = min(int((downloaded / max(total_bytes, 1)) * 100), 99)
                _update(
                    status="downloading",
                    progress=pct,
                    speed_mbps=round(speed, 1),
                    downloaded_gb=round(downloaded / 1024**3, 2),
                )
            except Exception:
                pass
            stop_monitor.wait(1.0)

    monitor = threading.Thread(target=_monitor, daemon=True)

    def _stop_monitor() -> None:
        stop_monitor.set()
        if monitor.ident is not None:
            monitor.join(timeout=2)

    try:
        if missing_environment(entry):
            # Before the monitor starts: the build writes nothing under dest,
            # which the monitor would report as a stall.
            _update(status="installing")
            build_model_environment(entry)
        _update(status="downloading")
        started = time.time()
        monitor.start()
        if missing_hub_files(entry):
            from huggingface_hub import snapshot_download

            pinned: Dict[str, Any] = {}
            if local is not None:
                cat = load_voice_catalog(entry["required_files_catalog"])
                pinned = {"revision": cat.get("hf_revision"), "local_dir": str(local)}
            snapshot_download(
                repo_id=repo_id,
                allow_patterns=entry.get("allow_patterns"),
                ignore_patterns=entry.get("ignore_patterns"),
                **pinned,
            )
        still_missing = missing_hub_files(entry)
        if still_missing:
            raise RuntimeError(
                f"Download of {entry['id']} finished but {still_missing[0]} is still missing"
            )
        if stalled.is_set():
            return
        # The monitor watches the download folder only. Stop it here so a quiet
        # pip run is not reported as a stall, and so a late progress tick cannot
        # overwrite the final "completed".
        _stop_monitor()
        for pkg in missing_venv_packages(entry):
            _update(status="downloading", progress=99)
            pip_install_into_plugin_venv(pkg["wheel"])
        leftover = missing_venv_packages(entry)
        if leftover:
            raise VenvInstallFailed(
                f"pip finished but {leftover[0]['package']} is still not in the Audio Foundry venv"
            )
        _update(
            progress=100,
            downloaded_gb=float(entry["size_gb"]),
            status="completed",
            is_downloading=False,
        )
        logger.info("Audio Foundry model downloaded: %s", entry["id"])
    except Exception as e:
        logger.error("Audio Foundry download failed: %s", e, exc_info=True)
        if not stalled.is_set():
            from backend.services.video_model_registry import classify_hf_download_error
            _update(
                status="failed",
                error=(str(e) if isinstance(e, VenvInstallFailed)
                       else classify_hf_download_error(e, repo_id=last_repo)),
                progress=0,
                is_downloading=False,
            )
    finally:
        _stop_monitor()
        with _download_lock:
            if _download_state.get("epoch") == epoch and _download_state.get("is_downloading"):
                _download_state["is_downloading"] = False
                _download_state["updated_at"] = time.time()
