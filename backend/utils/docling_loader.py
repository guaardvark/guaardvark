"""Structure-aware document loading via Docling.

The previous PDF path (LlamaIndex PDFReader) yielded page text and nothing else.
Docling additionally recovers reading order, section headers and per-item page
provenance with bounding boxes, which is what makes a retrieved chunk citable
back to a location rather than just to a filename.

One LlamaDocument is emitted per page: fine enough to carry a page number and a
heading breadcrumb, coarse enough that the chunker downstream still has material
to work with.

OCR is switched off explicitly. The `docling` package pulls RapidOCR through
its standard extra, and Docling's pipeline defaults to running OCR on every PDF
page when an engine is importable, which is a download of OCR artifacts and a
GPU/CPU cost this stack never asked for. Born-digital PDFs are read from the
text layer and need no OCR; scanned PDFs will produce little or no text here and
are reported as such. Enabling OCR is a product decision with its own
model-download review, not a flag flip.

The PDF pipeline needs two models, layout and table structure. Left to itself
Docling fetches them from Hugging Face and asks huggingface.co for the
revision every time a converter is built, even when they are cached. Here they
are read only from this machine: Install (Settings > Knowledge) puts them in
the Hugging Face cache, and every converter is given an artifacts folder, so
Docling never resolves them online. Without them a PDF is not parsed here and
goes down the caller's fallback chain as plain text. DOCX and PPTX need no models.
"""

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Formats where recovering layout earns the extra cost. Plain text and markdown
# are handled by the lighter markdown-aware path instead.
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx"}

REPLACEMENT_CHAR = "�"

# What the PDF pipeline reads with the options below: the repos and revisions
# Docling's defaults name (layout: Heron on the transformers engine; tables:
# TableFormer in accurate mode), and the files it opens from each. Install
# fetches exactly these files. If a Docling upgrade changes its defaults, the
# PDF fails to load with the missing repo named, and the caller falls back.
PDF_MODELS = (
    {"repo_id": "docling-project/docling-layout-heron", "revision": "main",
     "files": ("config.json", "preprocessor_config.json", "model.safetensors")},
    {"repo_id": "docling-project/docling-models", "revision": "v2.3.0",
     "files": ("model_artifacts/tableformer/accurate/tm_config.json",
               "model_artifacts/tableformer/accurate/tableformer_accurate.safetensors")},
)
# Sum of the files above as they sit in the Hugging Face cache (Heron main at
# 8f39ad3c, TableFormer v2.3.0). Drives install progress and the size on the button.
INSTALL_BYTES = 384_428_156

_converter = None
_missing_logged = False

_install_lock = threading.Lock()
_install: Dict[str, Any] = {"state": "idle", "progress": None,
                            "downloaded_gb": 0.0, "error": None}


def is_enabled() -> bool:
    return os.environ.get("GUAARDVARK_DOCLING_ENABLED", "true").lower() == "true"


def supports(file_extension: str) -> bool:
    return is_enabled() and file_extension.lower() in SUPPORTED_EXTENSIONS


def _repo_folder(repo_id: str) -> str:
    """The folder name Docling looks for under an artifacts path."""
    return repo_id.replace("/", "--")


def _has_files(folder: Path, model: Dict[str, Any]) -> bool:
    from backend.utils.model_file_integrity import file_complete
    return all(file_complete(folder / name) for name in model["files"])


def _own_artifacts_path() -> Optional[Path]:
    """DOCLING_ARTIFACTS_PATH, Docling's own setting, when someone has set it."""
    own = os.environ.get("DOCLING_ARTIFACTS_PATH", "").strip()
    return Path(own).expanduser() if own else None


def _links_dir() -> Path:
    """Where the installed snapshots are linked in the layout Docling expects."""
    try:
        from backend.config import CACHE_DIR
    except Exception:  # noqa: BLE001
        CACHE_DIR = str(Path(__file__).resolve().parents[2] / "data" / "cache")
    return Path(CACHE_DIR) / "docling_artifacts"


def _cached_snapshot(model: Dict[str, Any]) -> Optional[Path]:
    """The model's folder in the Hugging Face cache, or None. Never touches the network."""
    try:
        from huggingface_hub import snapshot_download
        folder = Path(snapshot_download(
            repo_id=model["repo_id"], revision=model["revision"],
            allow_patterns=list(model["files"]), local_files_only=True, token=False,
        ))
    except Exception:  # noqa: BLE001
        return None
    return folder if _has_files(folder, model) else None


def _link(link: Path, target: Path) -> None:
    """Point `link` at `target`, swapping out an older link atomically."""
    if link.is_symlink() and Path(os.readlink(link)) == target:
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(f".{link.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    if tmp.is_symlink():
        tmp.unlink()
    os.symlink(target, tmp, target_is_directory=True)
    os.replace(tmp, link)


def is_installed() -> bool:
    """True when the PDF models are on this machine. Never touches the network."""
    own = _own_artifacts_path()
    if own is not None:
        return all(_has_files(own / _repo_folder(m["repo_id"]), m) for m in PDF_MODELS)
    return all(_cached_snapshot(m) is not None for m in PDF_MODELS)


def artifacts_path() -> Optional[Path]:
    """The folder the PDF pipeline reads its models from, or None when they are
    not installed. Never touches the network.

    DOCLING_ARTIFACTS_PATH is used as it stands. Otherwise the installed
    snapshots are linked into one folder under the cache dir, which is what
    Docling's artifacts_path expects (<folder>/<org>--<repo>/...).
    """
    own = _own_artifacts_path()
    if own is not None:
        return own if is_installed() else None
    folder = _links_dir()
    for model in PDF_MODELS:
        snapshot = _cached_snapshot(model)
        if snapshot is None:
            return None
        try:
            _link(folder / _repo_folder(model["repo_id"]), snapshot)
        except OSError as e:
            logger.warning("Docling: could not link %s into %s: %s", model["repo_id"], folder, e)
            return None
    return folder


def not_installed_reason() -> str:
    own = _own_artifacts_path()
    if own is not None:
        return (f"DOCLING_ARTIFACTS_PATH ({own}) does not hold the PDF layout models "
                f"({', '.join(m['repo_id'] for m in PDF_MODELS)}). Until it does, PDFs are "
                f"indexed as plain text, without sections or page numbers.")
    return (f"the PDF layout models ({', '.join(m['repo_id'] for m in PDF_MODELS)}, about "
            f"{INSTALL_BYTES / 1e9:.2f} GB) are not installed; install them in Settings > "
            f"Knowledge. Until then PDFs are indexed as plain text, without sections or "
            f"page numbers.")


def install_status() -> Dict[str, Any]:
    """For Settings > Knowledge: on or off, installed or not, and any install in progress."""
    with _install_lock:
        job = dict(_install)
    return {
        "enabled": is_enabled(),
        "model": ", ".join(f"{m['repo_id']}@{m['revision']}" for m in PDF_MODELS),
        "installed": is_installed(),
        "size_gb": round(INSTALL_BYTES / 1e9, 2),
        "install": job,
    }


def start_install() -> Dict[str, Any]:
    """Download the PDF models in the background. The only path that fetches them."""
    if is_installed():
        return install_status()
    start = False
    # install_status() takes the lock too, so it is called only once this block has released it.
    with _install_lock:
        if _install["state"] != "running":
            if _own_artifacts_path() is not None:
                _install.update({"state": "failed", "progress": None, "error": (
                    "DOCLING_ARTIFACTS_PATH is set, so the models are read from that folder "
                    "only. Put them there with Docling's own tools, or unset it and restart.")})
            else:
                _install.update({"state": "running", "progress": 0, "downloaded_gb": 0.0,
                                 "error": None, "started_at": time.time()})
                start = True
    if start:
        threading.Thread(target=_run_install, daemon=True, name="docling-models-install").start()
    return install_status()


def _run_install() -> None:
    from huggingface_hub import snapshot_download
    from backend.services.local_weights import hf_repo_cache_dir

    caches = [hf_repo_cache_dir(m["repo_id"]) for m in PDF_MODELS]
    stop = threading.Event()

    def _bytes() -> int:
        n = 0
        for cache in caches:
            for root, _dirs, files in os.walk(cache):
                for f in files:
                    path = os.path.join(root, f)
                    if os.path.islink(path):  # snapshots/ links to blobs/; count each file once
                        continue
                    try:
                        n += os.path.getsize(path)
                    except OSError:
                        pass
        return n

    baseline = _bytes()

    def _watch() -> None:
        while not stop.wait(1.0):
            done = max(0, _bytes() - baseline)
            with _install_lock:
                if _install["state"] != "running":
                    return
                _install["downloaded_gb"] = round(done / 1e9, 2)
                _install["progress"] = min(99, int(100 * done / INSTALL_BYTES))

    threading.Thread(target=_watch, daemon=True, name="docling-models-install-progress").start()
    try:
        for model in PDF_MODELS:
            logger.info("Docling: installing %s@%s", model["repo_id"], model["revision"])
            snapshot_download(repo_id=model["repo_id"], revision=model["revision"],
                              allow_patterns=list(model["files"]), token=False)
        missing = [m["repo_id"] for m in PDF_MODELS if _cached_snapshot(m) is None]
        if missing:
            raise RuntimeError(f"the download finished without the files of {', '.join(missing)}")
        with _install_lock:
            _install.update({"state": "done", "progress": 100, "finished_at": time.time(),
                             "downloaded_gb": round(max(0, _bytes() - baseline) / 1e9, 2)})
        logger.info("Docling: PDF layout models installed")
    except Exception as e:  # noqa: BLE001 - every failure is a reason on the button
        logger.error("Docling models install failed: %s", e)
        with _install_lock:
            _install.update({"state": "failed", "error": f"{e.__class__.__name__}: {str(e)[:200]}",
                             "finished_at": time.time()})
    finally:
        stop.set()


def _get_converter():
    global _converter
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        pdf_options = PdfPipelineOptions()
        pdf_options.do_ocr = False  # see module docstring
        if os.environ.get("CELERY_WORKER_MODE", "false").lower() == "true":
            # A GPU job running in another process can only reclaim models in its
            # own process, so layout models left on the card by a worker's indexing
            # task held ~1.4GB until the worker restarted and kept the next image
            # batch from fitting. In a worker they run on the CPU.
            from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
            pdf_options.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU)
        # Always set: with no artifacts path Docling resolves its models on
        # huggingface.co. A PDF converted before Install then fails to find
        # them locally instead of downloading them.
        folder = _own_artifacts_path()
        if folder is None:
            folder = _links_dir()
            folder.mkdir(parents=True, exist_ok=True)
        pdf_options.artifacts_path = folder
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)}
        )
    return _converter


def is_loaded() -> bool:
    return _converter is not None


def unload() -> bool:
    """Drop the converter and its GPU-resident layout/table models.

    docling's accelerator device defaults to "auto", which resolves to cuda here,
    so ingesting a PDF leaves ~0.5GB of layout + tableformer weights on the card
    for the life of the process. Nothing else releases them: the converter is a
    module global and docling caches its built pipelines internally
    (DocumentConverter.initialized_pipelines) with no eviction of its own.

    Returns True if something was released. Never raises.
    """
    global _converter
    if _converter is None:
        return False
    try:
        # The pipeline cache holds the model references; clearing the converter
        # alone would leave them alive inside docling.
        cache = getattr(_converter, "initialized_pipelines", None)
        if isinstance(cache, dict):
            cache.clear()
    except Exception as e:  # noqa: BLE001
        logger.debug("docling pipeline cache clear failed: %s", e)
    _converter = None
    try:
        import gc
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass
    logger.info("Docling converter unloaded")
    return True


def _heading_level(item, fallback: int) -> int:
    lvl = getattr(item, "level", None)
    return int(lvl) if isinstance(lvl, int) and lvl > 0 else fallback


def load_documents(file_path: str, filename: str, LlamaDocument) -> Optional[List[Any]]:
    """Convert a document into one LlamaDocument per page.

    Returns None when Docling is unavailable or fails, so the caller can fall back
    to its existing reader rather than losing the file.
    """
    global _missing_logged
    if Path(file_path).suffix.lower() == ".pdf" and artifacts_path() is None:
        # Once per process at warning level; a folder of PDFs would otherwise
        # repeat the same advice for every file.
        log = logger.info if _missing_logged else logger.warning
        _missing_logged = True
        log("Docling skipped %s: %s", filename, not_installed_reason())
        return None
    try:
        doc = _get_converter().convert(file_path).document
    except Exception as e:
        logger.warning("Docling failed on %s (%s: %s) — falling back",
                       filename, e.__class__.__name__, str(e)[:160])
        return None

    pages: Dict[int, Dict[str, Any]] = {}
    heading_stack: List[tuple] = []  # (level, text)

    try:
        for item, depth in doc.iterate_items():
            label = str(getattr(item, "label", "") or "")
            text = (getattr(item, "text", "") or "").strip()
            prov = getattr(item, "prov", None)
            page_no = prov[0].page_no if prov else None

            if label == "section_header" and text:
                lvl = _heading_level(item, depth or 1)
                while heading_stack and heading_stack[-1][0] >= lvl:
                    heading_stack.pop()
                heading_stack.append((lvl, text))

            if not text:
                continue

            # DOCX and PPTX carry no page provenance. Bucketing those under a single
            # pageless key keeps one code path instead of two, and page_label is simply
            # omitted downstream rather than invented.
            bucket_key = page_no if page_no is not None else 0
            bucket = pages.setdefault(bucket_key, {"lines": [], "headings": None, "boxes": 0})
            if bucket["headings"] is None:
                bucket["headings"] = " > ".join(h for _, h in heading_stack)
            if label == "section_header":
                bucket["lines"].append(f"\n## {text}")
            else:
                bucket["lines"].append(text)
            if prov and getattr(prov[0], "bbox", None) is not None:
                bucket["boxes"] += 1
    except Exception as e:
        logger.warning("Docling item walk failed on %s: %s — falling back", filename, e)
        return None

    documents = []
    for bucket_key in sorted(pages):
        bucket = pages[bucket_key]
        text = "\n".join(bucket["lines"]).strip()
        if not text:
            continue
        bad = text.count(REPLACEMENT_CHAR)
        meta = {
            "source_filename": filename,
            "file_path": str(file_path),
            "parsed_by": "docling",
            "has_positions": bucket["boxes"] > 0,
            "positioned_items": bucket["boxes"],
        }
        if bucket_key:
            meta["page_label"] = str(bucket_key)
        if bucket["headings"]:
            meta["heading_path"] = bucket["headings"]
        if bad:
            # The glyph is already U+FFFD by the time it reaches us -- the mapping is
            # lost in the PDF's font encoding, so it cannot be repaired, only declared.
            # Guessing the original ligature would be fabrication.
            meta["text_quality"] = "degraded"
            meta["replacement_chars"] = bad
        documents.append(LlamaDocument(text=text, metadata=meta))

    if not documents:
        logger.warning(
            "Docling extracted no text from %s — likely a scanned document (OCR is not installed)",
            filename,
        )
        return None

    degraded = sum(d.metadata.get("replacement_chars", 0) for d in documents)
    logger.info(
        "Docling parsed %s: %d page(s), positions=%s%s",
        filename, len(documents),
        any(d.metadata.get("has_positions") for d in documents),
        f", {degraded} undecodable char(s)" if degraded else "",
    )
    return documents
