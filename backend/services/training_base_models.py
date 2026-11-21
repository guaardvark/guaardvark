"""Base models the Training page can fine-tune: a declared list, what is on
this machine, and the Download behind a click.

Training never fetches weights. The trainer runs offline and loads the
snapshot this module finds with snapshot_download(local_files_only=True).
Weights arrive only from Settings > Training libraries > Base models, where
a person clicks Download; that request carries a one-use plan token from the
modal's status read (the same tokens the library Install uses), and the
download sends no Hugging Face token (token=False), so a token in .env is
never sent with it.

Each entry declares what the rest of the product enforces: the limits a job
may ask for, the VRAM a run is budgeted, the chat markers the trainer masks
the loss with, and the Ollama template an export is registered with.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Qwen2.5's ChatML, written in Ollama's template language: the default system
# line is the one Qwen2.5's own chat template adds when a chat has none, so an
# exported model is prompted the way it was trained.
QWEN25_OLLAMA_TEMPLATE = """{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ else }}<|im_start|>system
You are Qwen, created by Alibaba Cloud. You are a helpful assistant.<|im_end|>
{{ end }}
{{- range $i, $_ := .Messages }}
{{- $last := eq (len (slice $.Messages $i)) 1 }}
{{- if eq .Role "user" }}<|im_start|>user
{{ .Content }}<|im_end|>
{{ else if eq .Role "assistant" }}<|im_start|>assistant
{{ .Content }}{{ if not $last }}<|im_end|>
{{ end }}
{{- end }}
{{- if and (ne .Role "assistant") $last }}<|im_start|>assistant
{{ end }}
{{- end }}"""

QWEN25_MARKERS = {"instruction": "<|im_start|>user\n", "response": "<|im_start|>assistant\n"}

# Gemma 4's turns, in Ollama's template language: "<|turn>role" ... "<turn|>",
# with the assistant's role named "model" and no default system line.
GEMMA4_OLLAMA_TEMPLATE = """{{- if .System }}<|turn>system
{{ .System }}<turn|>
{{ end }}
{{- range $i, $_ := .Messages }}
{{- $last := eq (len (slice $.Messages $i)) 1 }}
{{- if eq .Role "user" }}<|turn>user
{{ .Content }}<turn|>
{{ else if eq .Role "assistant" }}<|turn>model
{{ .Content }}{{ if not $last }}<turn|>
{{ end }}
{{- end }}
{{- if and (ne .Role "assistant") $last }}<|turn>model
{{ end }}
{{- end }}"""

GEMMA4_MARKERS = {"instruction": "<|turn>user\n", "response": "<|turn>model\n"}

# revision: the branch or commit a Download fetches. The commit it fetched is
# recorded on this machine (see installed_revision) and training loads exactly
# that commit.
# size_gb: the bf16 weights plus tokenizer files, approximate; the download's
# progress is measured against it.
# vram_mb: what a run is budgeted on the GPU at max_seq_length and
# max_batch_size with LoRA rank 16; vram_measured says how it was arrived at.
# export: "verified" once Export to Ollama has been run live for the model;
# until then the Training page does not offer it. export_measured says how.
# trainable: False until a training run of the model has been measured on a
# GPU; it can be downloaded, but no job is created for it. Left out means True.
BASE_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "Qwen/Qwen2.5-0.5B-Instruct",
        "name": "Qwen2.5 0.5B Instruct",
        "description": "The smallest; quick to train, good for trying a dataset out.",
        "repo": "Qwen/Qwen2.5-0.5B-Instruct",
        "revision": "main",
        "size_gb": 1.0,
        "license": "Apache-2.0",
        "architecture": "qwen2",
        "max_seq_length": 2048,
        "max_batch_size": 4,
        "vram_mb": 4000,
        "vram_measured": "estimate, not yet measured",
        "load_in_4bit": False,
        "response_markers": QWEN25_MARKERS,
        "vision": False,
        "export": "unverified",
        "ollama_tags": ("qwen2.5:0.5b", "qwen2.5:0.5b-instruct"),
        "ollama_template": QWEN25_OLLAMA_TEMPLATE,
        "ollama_stop": ("<|im_end|>",),
    },
    {
        "id": "Qwen/Qwen2.5-1.5B-Instruct",
        "name": "Qwen2.5 1.5B Instruct",
        "description": "A capable small chat model; the usual choice.",
        "repo": "Qwen/Qwen2.5-1.5B-Instruct",
        "revision": "main",
        "size_gb": 3.1,
        "license": "Apache-2.0",
        "architecture": "qwen2",
        "max_seq_length": 2048,
        "max_batch_size": 4,
        "vram_mb": 8000,
        "vram_measured": (
            "peak 3355 MB measured 2026-10-07 on a 16 GB NVIDIA card: bf16 LoRA r16, "
            "batch 2, seq 1024, 108 short chat rows. 8000 stays the cap for batch 4 at "
            "seq 2048 with long rows, which has not been measured."
        ),
        "load_in_4bit": False,
        "response_markers": QWEN25_MARKERS,
        "vision": False,
        "export": "verified",
        "export_measured": (
            "2026-10-16, Ollama 0.40.1 on Linux: an adapter trained here merged, converted to "
            "GGUF at Q8_0 and F16 and registered by the export and import tasks; both Ollama "
            "models answered in the Qwen2.5 chat template "
            "(training_gguf_converter.ARCHITECTURES)."
        ),
        "ollama_tags": ("qwen2.5:1.5b", "qwen2.5:1.5b-instruct"),
        "ollama_template": QWEN25_OLLAMA_TEMPLATE,
        "ollama_stop": ("<|im_end|>",),
    },
    {
        "id": "google/gemma-4-E4B-it",
        "name": "Gemma 4 E4B Instruct",
        "description": ("Google's Gemma 4 E4B. Training adapts its language layers; "
                        "its image and audio parts are left as they are."),
        "repo": "google/gemma-4-E4B-it",
        "revision": "ee0ef6023621cff504d758262d4e04895a5af4a2",
        "size_gb": 16.0,
        "license": "Apache-2.0",
        "architecture": "gemma4",
        "max_seq_length": 2048,
        "max_batch_size": 4,
        "vram_mb": 14000,
        "vram_measured": ("not measured: 14000 is a placeholder that lets a 16 GB card download "
                          "it. A measured run sets vram_mb and load_in_4bit before training is "
                          "offered."),
        "load_in_4bit": False,
        "trainable": False,
        "response_markers": GEMMA4_MARKERS,
        "vision": False,
        "export": "unverified",
        "ollama_tags": ("gemma4:e4b",),
        "ollama_template": GEMMA4_OLLAMA_TEMPLATE,
        "ollama_stop": ("<turn|>",),
    },
)

# GPU memory a run leaves for the CUDA context and the desktop. Not measured.
FIT_HEADROOM_MB = 1024

NEEDS_CLICK = ("Base models download and are removed only from the buttons in "
               "Settings > Training libraries. Open it there and click the button.")

_DOWNLOAD_STALL_SECONDS = 180
_RECORD_NAME = "base_models.json"


class Refused(Exception):
    """A request the routes answer with an error, never with a download."""

    def __init__(self, message: str, status: int = 400, code: str = "REFUSED"):
        super().__init__(message)
        self.status = status
        self.code = code


class BaseModelUnavailable(RuntimeError):
    """A job's base model cannot be trained on here; the message says why."""


# ---- the declared list ------------------------------------------------------------

def get(model_id: Any) -> dict[str, Any] | None:
    return next((e for e in BASE_MODELS if e["id"] == model_id), None)


def by_ollama_tag(tag: str) -> dict[str, Any] | None:
    tag = (tag or "").strip().lower()
    return next((e for e in BASE_MODELS if tag in e["ollama_tags"]), None)


def export_verified(model_id: Any) -> bool:
    entry = get(model_id)
    return bool(entry) and entry.get("export") == "verified"


def trainable(entry: dict[str, Any]) -> bool:
    return entry.get("trainable", True)


# ---- what is on this machine ------------------------------------------------------

def _record_path() -> Path:
    from backend.config import STORAGE_DIR
    return Path(STORAGE_DIR) / "training" / _RECORD_NAME


def _read_record() -> dict[str, Any]:
    try:
        return json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_record(record: dict[str, Any]) -> None:
    path = _record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")


def installed_revision(entry: dict[str, Any]) -> str | None:
    """The commit a Download on this machine fetched, or None."""
    return (_read_record().get(entry["id"]) or {}).get("revision")


def _missing_files(folder: Path) -> list[str]:
    """Files a training run reads that the snapshot lacks, or holds cut short."""
    from backend.utils.model_file_integrity import file_complete
    missing = [name for name in ("config.json", "tokenizer_config.json") if not (folder / name).is_file()]
    index = folder / "model.safetensors.index.json"
    if index.is_file():
        try:
            shards = set(json.loads(index.read_text(encoding="utf-8"))["weight_map"].values())
        except (OSError, ValueError, KeyError, AttributeError):
            return missing + [index.name]
        missing += sorted(s for s in shards if not file_complete(folder / s))
    elif not file_complete(folder / "model.safetensors"):
        missing.append("model.safetensors")
    return missing


def local_snapshot(entry: dict[str, Any]) -> str | None:
    """The complete snapshot folder on this machine, or None. Never touches
    the network."""
    from huggingface_hub import snapshot_download

    revision = installed_revision(entry) or entry["revision"]
    try:
        path = snapshot_download(repo_id=entry["repo"], revision=revision,
                                 local_files_only=True, token=False)
    except Exception:
        return None
    return None if _missing_files(Path(path)) else str(path)


def _hardware() -> dict[str, Any]:
    from backend.services import training_libraries
    return training_libraries.hardware_fit()


def fit(entry: dict[str, Any], hardware: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Whether a run of this model fits this machine's GPU, and why."""
    hardware = _hardware() if hardware is None else hardware
    if not hardware.get("practical"):
        return False, hardware.get("reason") or "Training is not practical on this machine."
    total = hardware.get("vram_mb") or 0
    need = entry["vram_mb"] + FIT_HEADROOM_MB
    if need > total:
        return False, (f"needs about {need / 1024:.1f} GB of GPU memory; this GPU has "
                       f"{total / 1024:.1f} GB")
    return True, f"fits this GPU ({total / 1024:.0f} GB)"


def model_status(entry: dict[str, Any], hardware: dict[str, Any]) -> dict[str, Any]:
    fits, fit_reason = fit(entry, hardware)
    snapshot = local_snapshot(entry)
    return {
        "id": entry["id"], "name": entry["name"], "description": entry["description"],
        "repo": entry["repo"], "size_gb": entry["size_gb"], "license": entry["license"],
        "architecture": entry["architecture"], "max_seq_length": entry["max_seq_length"],
        "max_batch_size": entry["max_batch_size"], "vram_mb": entry["vram_mb"],
        "vram_measured": entry["vram_measured"], "vision": entry["vision"],
        "export_verified": entry.get("export") == "verified",
        "trainable": trainable(entry),
        "installed": snapshot is not None,
        "revision": installed_revision(entry) if snapshot else None,
        "fits": fits, "fit_reason": fit_reason,
        "ollama_tags": list(entry["ollama_tags"]),
    }


def status(*, with_plan_token: bool = False) -> dict[str, Any]:
    hardware = _hardware()
    result = {
        "models": [model_status(e, hardware) for e in BASE_MODELS],
        "hardware": hardware,
        "download": _download_snapshot(),
    }
    if with_plan_token:
        from backend.services import training_libraries
        result["plan_token"] = training_libraries.issue_plan_token()
    return result


# ---- jobs --------------------------------------------------------------------------

def refusal_for_job(model_id: Any, *, vision: bool = False) -> str | None:
    """Why a job cannot train on this base model here, or None."""
    names = ", ".join(e["id"] for e in BASE_MODELS)
    entry = get(model_id)
    if entry is None:
        text = str(model_id or "").strip()
        match = by_ollama_tag(text)
        if match:
            return (f"{text} is an Ollama model; fine-tuning loads Hugging Face weights. Choose "
                    f"{match['id']} and download it in Settings > Training libraries.")
        if ":" in text:
            return (f"{text} looks like an Ollama model; fine-tuning loads Hugging Face weights. "
                    f"Choose one of: {names}.")
        return f"{text or 'That'} is not a base model Guaardvark can fine-tune. Choose one of: {names}."
    if vision and not entry.get("vision"):
        return f"{entry['name']} is a text model; no vision base model is declared yet."
    if not trainable(entry):
        return (f"{entry['name']} is not offered for training yet: its GPU memory has not been "
                f"measured on a training run.")
    fits, why = fit(entry)
    if not fits:
        return f"{entry['name']} does not fit this machine: {why}."
    if local_snapshot(entry) is None:
        return (f"{entry['name']} is not downloaded yet. Download it ({entry['size_gb']:g} GB) in "
                f"Settings > Training libraries > Base models.")
    return None


def resolve_for_training(model_id: Any) -> tuple[dict[str, Any], str]:
    """(entry, local snapshot folder) for a job's base model, or
    BaseModelUnavailable with the reason."""
    entry = get(model_id)
    if entry is None:
        raise BaseModelUnavailable(refusal_for_job(model_id))
    path = local_snapshot(entry)
    if path is None:
        raise BaseModelUnavailable(
            f"{entry['name']} is not on this machine. Download it in Settings > Training "
            f"libraries > Base models; training never downloads on its own.")
    return entry, path


# ---- download and remove --------------------------------------------------------

_lock = threading.Lock()
_download: dict[str, Any] = {}


def _idle_download() -> dict[str, Any]:
    return {"state": "idle", "model": None, "progress": 0, "downloaded_gb": 0.0, "error": None,
            "started_at": None, "finished_at": None, "epoch": 0}


_download.update(_idle_download())


def _download_snapshot() -> dict[str, Any]:
    with _lock:
        return {k: v for k, v in _download.items() if k != "epoch"}


def _spawn(target, *args, name: str) -> None:
    threading.Thread(target=target, args=args, daemon=True, name=name).start()


def _cache_bytes(folder: Path) -> int:
    total = 0
    if not folder.exists():
        return 0
    # snapshots/<rev>/ holds symlinks into blobs/; count each blob once.
    for f in folder.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def _download_error(exc: BaseException, repo: str) -> str:
    text = str(exc).lower()
    if "offline" in text:
        return ("Hugging Face downloads are switched off in this backend process "
                "(HF_HUB_OFFLINE). Unset it and restart Guaardvark to download.")
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return "Hugging Face rate-limited the download. Wait a few minutes and click Download again."
    from backend.services.video_model_registry import classify_hf_download_error
    return classify_hf_download_error(exc, repo_id=repo)


def start_install(token: Any, model_id: Any) -> dict[str, Any]:
    """Download one declared base model in a background thread."""
    from backend.services import training_libraries

    if not training_libraries.take_plan_token(token):
        raise Refused(NEEDS_CLICK, 403, "NEEDS_CLICK")
    entry = get(model_id)
    if entry is None:
        raise Refused(f"{model_id} is not a declared base model.", 404, "UNKNOWN_MODEL")
    fits, why = fit(entry)
    if not fits:
        raise Refused(f"{entry['name']} does not fit this machine: {why}. Nothing was downloaded.",
                      409, "DOES_NOT_FIT")
    if local_snapshot(entry):
        raise Refused(f"{entry['name']} is already downloaded.", 409, "ALREADY_INSTALLED")
    with _lock:
        if _download["state"] == "running":
            raise Refused(f"{_download['model']} is downloading; wait for it to finish.", 409, "BUSY")
        epoch = _download["epoch"] + 1
        _download.update(_idle_download())
        _download.update({"state": "running", "model": entry["id"], "started_at": time.time(),
                          "epoch": epoch})
    _spawn(_run_download, entry, epoch, name=f"training-base-model-{entry['id']}")
    return status()


def _run_download(entry: dict[str, Any], epoch: int) -> None:
    from huggingface_hub import snapshot_download
    from backend.services.local_weights import hf_repo_cache_dir

    cache = hf_repo_cache_dir(entry["repo"])
    baseline = _cache_bytes(cache)
    total = max(1, int(entry["size_gb"] * 1024 ** 3))
    stop = threading.Event()

    def update(**fields) -> None:
        with _lock:
            if _download["epoch"] == epoch and _download["state"] == "running":
                _download.update(fields)

    def monitor() -> None:
        last, changed = -1, time.monotonic()
        while not stop.wait(1.0):
            done = max(0, _cache_bytes(cache) - baseline)
            if done != last:
                last, changed = done, time.monotonic()
            elif time.monotonic() - changed > _DOWNLOAD_STALL_SECONDS:
                update(state="failed", finished_at=time.time(),
                       error=(f"The download stalled: nothing arrived for {_DOWNLOAD_STALL_SECONDS} s. "
                              f"Check the network and click Download again."))
                return
            update(progress=min(99, int(100 * done / total)), downloaded_gb=round(done / 1024 ** 3, 2))

    watcher = threading.Thread(target=monitor, daemon=True, name="training-base-model-progress")
    watcher.start()

    def stop_watching() -> None:
        stop.set()
        watcher.join(timeout=2)

    try:
        path = snapshot_download(repo_id=entry["repo"], revision=entry["revision"], token=False)
        stop_watching()
        missing = _missing_files(Path(path))
        if missing:
            raise RuntimeError(f"the download finished without {', '.join(missing)}")
        record = _read_record()
        record[entry["id"]] = {"revision": Path(path).name, "repo": entry["repo"],
                               "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        _write_record(record)
        update(state="completed", progress=100, downloaded_gb=entry["size_gb"],
               finished_at=time.time())
        logger.info("Training base model downloaded: %s at %s", entry["id"], Path(path).name)
    except Exception as e:
        logger.error("Training base model download failed: %s", e, exc_info=True)
        stop_watching()
        update(state="failed", error=_download_error(e, entry["repo"]), finished_at=time.time())
    finally:
        stop.set()


def start_remove(token: Any, model_id: Any) -> dict[str, Any]:
    """Delete one base model's weights from the Hugging Face cache."""
    from backend.services import training_libraries
    from backend.services.local_weights import hf_repo_cache_dir

    if not training_libraries.take_plan_token(token):
        raise Refused(NEEDS_CLICK, 403, "NEEDS_CLICK")
    entry = get(model_id)
    if entry is None:
        raise Refused(f"{model_id} is not a declared base model.", 404, "UNKNOWN_MODEL")
    with _lock:
        if _download["state"] == "running" and _download["model"] == entry["id"]:
            raise Refused(f"{entry['name']} is downloading; wait for it to finish.", 409, "BUSY")
    cache = hf_repo_cache_dir(entry["repo"])
    if not cache.exists():
        raise Refused(f"{entry['name']} is not downloaded; there is nothing to remove.", 409,
                      "NOTHING_TO_REMOVE")
    shutil.rmtree(cache)
    record = _read_record()
    if record.pop(entry["id"], None) is not None:
        _write_record(record)
    logger.info("Training base model removed: %s", entry["id"])
    return status()
