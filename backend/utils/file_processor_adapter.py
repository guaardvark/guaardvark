# backend/utils/file_processor_adapter.py
# Adapter to integrate EnhancedFileProcessor into LlamaIndex indexing pipeline
# This module bridges ProcessedContent to LlamaIndex Documents

import logging
import hashlib
from typing import List, Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular dependencies
LlamaDocument = None
EnhancedFileProcessor = None
ProcessedContent = None
FileFormat = None


def _ensure_imports():
    """Lazy import dependencies to avoid circular imports"""
    global LlamaDocument, EnhancedFileProcessor, ProcessedContent, FileFormat

    if LlamaDocument is None:
        try:
            from llama_index.core import Document as LlamaDoc

            LlamaDocument = LlamaDoc
        except ImportError:
            logger.error("LlamaIndex not available")
            raise ImportError("LlamaIndex is required for file processing")

    if EnhancedFileProcessor is None:
        try:
            from backend.utils.enhanced_file_processor import (
                EnhancedFileProcessor as EFP,
                ProcessedContent as PC,
                FileFormat as FF,
            )

            EnhancedFileProcessor = EFP
            ProcessedContent = PC
            FileFormat = FF
        except ImportError:
            logger.error("EnhancedFileProcessor not available")
            raise ImportError("EnhancedFileProcessor is required")


def processed_content_to_llamaindex_docs(
    processed: "ProcessedContent",
    file_path: str,
    client: Optional[str] = None,
    upload_date: Optional[str] = None,
    additional_metadata: Optional[Dict[str, Any]] = None,
) -> List["LlamaDocument"]:
    """
    Convert ProcessedContent from EnhancedFileProcessor to LlamaIndex Documents.

    Args:
        processed: ProcessedContent object from EnhancedFileProcessor
        file_path: Original file path
        client: Optional client name for metadata
        upload_date: Optional upload date for metadata
        additional_metadata: Optional additional metadata to include

    Returns:
        List of LlamaIndex Document objects
    """
    _ensure_imports()

    if processed is None:
        logger.warning(f"No processed content for {file_path}")
        return []

    documents = []
    path_obj = Path(file_path)
    filename = path_obj.name

    # Build base metadata from ProcessedContent
    metadata = {
        "source_filename": filename,
        "file_path": str(path_obj),
        "file_type": (
            processed.metadata.format.value if processed.metadata.format else "unknown"
        ),
        "file_extension": path_obj.suffix.lower(),
        "file_size_bytes": processed.metadata.size_bytes,
        "mime_type": processed.metadata.mime_type,
        "word_count": processed.metadata.word_count,
        "extraction_method": "enhanced_file_processor",
        "client": client,
        "upload_date": upload_date,
    }

    # Add optional metadata fields if present
    if processed.metadata.encoding:
        metadata["encoding"] = processed.metadata.encoding
    if processed.metadata.page_count:
        metadata["page_count"] = processed.metadata.page_count
    if processed.metadata.author:
        metadata["author"] = processed.metadata.author
    if processed.metadata.title:
        metadata["title"] = processed.metadata.title
    if processed.metadata.created_date:
        metadata["created_date"] = processed.metadata.created_date
    if processed.metadata.image_dimensions:
        metadata["image_dimensions"] = processed.metadata.image_dimensions
    if processed.metadata.extraction_confidence:
        metadata["extraction_confidence"] = processed.metadata.extraction_confidence
    if processed.metadata.vision_model_used:
        metadata["vision_model_used"] = processed.metadata.vision_model_used

    # Add structured data info
    if processed.structured_data:
        metadata["has_structured_data"] = True
        metadata["structured_data_keys"] = list(processed.structured_data.keys())

    # Add extraction results info
    if processed.extraction_results:
        metadata["has_extraction_results"] = True

    # Merge additional metadata
    if additional_metadata:
        metadata.update(additional_metadata)

    # Generate document ID
    doc_id = f"{filename}_{hashlib.md5(str(path_obj).encode()).hexdigest()[:8]}"

    # Create main document
    text_content = (
        processed.text_content or f"File: {filename} (no text content extracted)"
    )

    document = LlamaDocument(text=text_content, metadata=metadata, doc_id=doc_id)
    documents.append(document)

    logger.info(
        f"Converted ProcessedContent to LlamaIndex document: {filename} ({len(text_content)} chars)"
    )

    return documents


def process_file_to_llamaindex(
    file_path: str,
    client: Optional[str] = None,
    upload_date: Optional[str] = None,
    additional_metadata: Optional[Dict[str, Any]] = None,
) -> List["LlamaDocument"]:
    """
    Process a file using EnhancedFileProcessor and convert to LlamaIndex Documents.

    This is the main entry point for using the enhanced file processor in the indexing pipeline.

    Args:
        file_path: Path to the file to process
        client: Optional client name for metadata
        upload_date: Optional upload date for metadata
        additional_metadata: Optional additional metadata to include

    Returns:
        List of LlamaIndex Document objects, empty list if processing fails
    """
    _ensure_imports()

    path_obj = Path(file_path)
    filename = path_obj.name

    if not path_obj.exists():
        logger.error(f"File not found: {file_path}")
        return []

    if not path_obj.is_file():
        logger.error(f"Path is not a file: {file_path}")
        return []

    try:
        # Create processor instance
        processor = EnhancedFileProcessor()

        # Check if file format is supported
        if not processor.can_process(file_path):
            logger.info(
                f"EnhancedFileProcessor does not support {filename}, returning empty list for fallback"
            )
            return []

        # Process the file
        processed = processor.process_file(file_path)

        if processed is None:
            logger.warning(f"EnhancedFileProcessor returned None for {filename}")
            return []

        # Convert to LlamaIndex documents
        documents = processed_content_to_llamaindex_docs(
            processed=processed,
            file_path=file_path,
            client=client,
            upload_date=upload_date,
            additional_metadata=additional_metadata,
        )

        return documents

    except Exception as e:
        logger.error(
            f"Error processing file with EnhancedFileProcessor: {filename} - {e}",
            exc_info=True,
        )
        return []


def is_enhanced_processing_available(file_path: str) -> bool:
    """
    Check if enhanced file processing is available for a given file.

    Args:
        file_path: Path to the file

    Returns:
        True if EnhancedFileProcessor can handle this file type
    """
    try:
        _ensure_imports()
        processor = EnhancedFileProcessor()
        return processor.can_process(file_path)
    except Exception as e:
        logger.debug(f"Enhanced processing not available for {file_path}: {e}")
        return False


def get_enhanced_processor_formats() -> List[str]:
    """
    Get list of file formats supported by EnhancedFileProcessor.

    Returns:
        List of format names (e.g., ['csv', 'pdf', 'docx', ...])
    """
    try:
        _ensure_imports()
        processor = EnhancedFileProcessor()
        formats = processor.get_supported_formats()
        return [f.value for f in formats]
    except Exception as e:
        logger.error(f"Error getting supported formats: {e}")
        return []


def get_processor_info(file_path: str) -> Dict[str, Any]:
    """
    Get information about how a file would be processed.

    Args:
        file_path: Path to the file

    Returns:
        Dict with processing information
    """
    try:
        _ensure_imports()
        processor = EnhancedFileProcessor()

        format_type = processor.detect_format(file_path)
        if format_type is None:
            return {
                "supported": False,
                "format": None,
                "processor": None,
                "can_generate": False,
            }

        format_info = processor.get_format_info(format_type)

        return {
            "supported": True,
            "format": format_type.value,
            "processor": format_info.get("name", "Unknown"),
            "can_process": format_info.get("can_process", False),
            "can_generate": format_info.get("can_generate", False),
            "features": format_info.get("features", []),
            "mime_types": format_info.get("mime_types", []),
        }

    except Exception as e:
        logger.error(f"Error getting processor info for {file_path}: {e}")
        return {"supported": False, "error": str(e)}
