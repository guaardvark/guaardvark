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
"""

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Formats where recovering layout earns the extra cost. Plain text and markdown
# are handled by the lighter markdown-aware path instead.
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx"}

REPLACEMENT_CHAR = "�"

_converter = None


def is_enabled() -> bool:
    return os.environ.get("GUAARDVARK_DOCLING_ENABLED", "true").lower() == "true"


def supports(file_extension: str) -> bool:
    return is_enabled() and file_extension.lower() in SUPPORTED_EXTENSIONS


def _get_converter():
    global _converter
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        pdf_options = PdfPipelineOptions()
        pdf_options.do_ocr = False  # see module docstring
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
