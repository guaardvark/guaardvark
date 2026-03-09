# backend/tools/file_processor.py
# Version 1.4: Added content validation in write_text/write_csv.

import csv
import logging
import os
import re
from urllib.parse import urlparse

import pandas as pd
from lxml import etree


def _sanitize_csv_field(value):
    """Normalize line breaks and escape quotes for a CSV field."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace('"', '""')
    return value


logger = logging.getLogger(__name__)


def _get_config_path(config_key, default_path):
    """Safely gets a path from Flask config or returns default."""
    try:
        from flask import current_app

        if current_app:
            path = current_app.config.get(config_key)
            if path:
                os.makedirs(path, exist_ok=True)
                return path
            else:
                logger.warning(f"Config key '{config_key}' not found.")
        else:
            logger.warning(f"Flask current_app not available for '{config_key}'.")
    except ImportError:
        logger.warning(f"Flask not installed/context unavailable for '{config_key}'.")
    except Exception as e:
        logger.error(f"Error getting config key '{config_key}': {e}", exc_info=True)

    logger.warning(f"Using default path for '{config_key}': {default_path}")
    os.makedirs(default_path, exist_ok=True)
    return default_path


def _safe_path(base_dir_key, full_path_input, default_base_dir):
    """Validates that the provided full path is within the allowed base directory."""
    base_dir = _get_config_path(base_dir_key, default_base_dir)

    if not os.path.isdir(base_dir):
        logger.error(f"_safe_path error: Base directory '{base_dir}' does not exist.")
        raise FileNotFoundError(f"Base directory '{base_dir}' not found.")

    abs_base_dir = os.path.normpath(os.path.abspath(base_dir))
    abs_full_path = os.path.normpath(os.path.abspath(full_path_input))

    if (
        not abs_full_path.startswith(abs_base_dir + os.sep)
        and abs_full_path != abs_base_dir
    ):
        logger.error(
            f"_safe_path security error: Path '{abs_full_path}' outside base '{abs_base_dir}'."
        )
        raise ValueError(f"Path is outside the allowed directory: '{full_path_input}'")

    logger.debug(
        f"_safe_path: Validated path: '{abs_full_path}' within '{abs_base_dir}'"
    )
    return abs_full_path


# --- File Reading Tools ---


def read_csv(input_filename):
    """Reads a CSV file from the UPLOAD_FOLDER into a pandas DataFrame."""
    logger.info(f"Reading CSV: {input_filename}")
    try:
        safe_input_path = _safe_path("UPLOAD_FOLDER", input_filename, "backend/uploads")
        df = pd.read_csv(safe_input_path)
        logger.info(f"Successfully read {len(df)} rows from {input_filename}")
        return df
    except FileNotFoundError:
        logger.error(f"CSV input file not found: {input_filename}")
        raise
    except ValueError as ve:
        logger.error(f"CSV Read Error: Invalid path - {ve}")
        raise
    except Exception as e:
        logger.error(f"Error reading CSV {input_filename}: {e}", exc_info=True)
        raise RuntimeError(f"Failed to read CSV file: {e}")


def read_xml_sitemap(sitemap_path):
    """Parses an XML sitemap and extracts URLs."""
    logger.info(f"Reading XML Sitemap: {sitemap_path}")
    urls = []
    try:
        safe_sitemap_path = _safe_path("UPLOAD_FOLDER", sitemap_path, "backend/uploads")
        tree = etree.parse(safe_sitemap_path)
        root = tree.getroot()
        namespaces = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.xpath("//s:url/s:loc", namespaces=namespaces):
            if loc.text:
                urls.append(loc.text.strip())
        logger.info(f"Extracted {len(urls)} URLs from {sitemap_path}")
        return urls
    except FileNotFoundError:
        logger.error(f"XML Sitemap input file not found: {sitemap_path}")
        raise
    except ValueError as ve:
        logger.error(f"XML Sitemap Read Error: Invalid path - {ve}")
        raise
    except etree.XMLSyntaxError as xml_err:
        logger.error(
            f"Error parsing XML sitemap {sitemap_path}: {xml_err}", exc_info=True
        )
        raise RuntimeError(f"Failed to parse XML sitemap: {xml_err}")
    except Exception as e:
        logger.error(f"Error reading XML sitemap {sitemap_path}: {e}", exc_info=True)
        raise RuntimeError(f"Failed to read XML sitemap: {e}")


# --- File Writing Tools ---


def write_text(content: str, full_output_path: str) -> str:
    """
    Writes text content to the specified full path after validation.

    Args:
        content (str): The text content to write.
        full_output_path (str): The desired absolute path for the output file.

    Returns:
        str: The absolute path of the written file.
    """
    logger.info(f"Attempting to write text file to: {full_output_path}")

    # <<< CONTENT VALIDATION >>>
    if content is None:
        logger.error(
            f"write_text error: Received None content for path {full_output_path}"
        )
        raise TypeError("Cannot write None content to text file.")
    if not isinstance(content, str):
        logger.error(
            f"write_text error: Content must be a string, got {type(content)} for path {full_output_path}"
        )
        raise TypeError(f"Content must be a string, but received {type(content)}.")
    # <<< END CONTENT VALIDATION >>>

    try:
        validated_path = _safe_path("OUTPUT_DIR", full_output_path, "data/outputs")
        with open(validated_path, "w", encoding="utf-8") as f:
            f.write(
                content
            )  # This line caused the previous TypeError if content was None
        logger.info(f"Successfully wrote text content to: {validated_path}")
        return validated_path
    except ValueError as ve:
        logger.error(f"Text Output Path Error: {ve}")
        raise
    except IOError as e:
        logger.error(f"Error writing text file {full_output_path}: {e}", exc_info=True)
        raise IOError(f"Failed to write text file: {e}")
    except TypeError as te:  # Catch the type error we might raise
        logger.error(f"Text Output Content Error: {te}")
        raise  # Re-raise it so the execution fails clearly
    except Exception as e:
        logger.error(
            f"Unexpected error writing text file {full_output_path}: {e}", exc_info=True
        )
        raise RuntimeError(f"Unexpected error writing text file: {e}")


def write_csv(data, full_output_path: str) -> str:
    """
    Writes data to a CSV file at the specified full path after validation.

    Args:
        data: The data to write (list of lists or list of dicts).
        full_output_path (str): The desired absolute path for the output CSV file.

    Returns:
        str: The absolute path of the written file.
    """
    logger.info(f"Attempting to write CSV file to: {full_output_path}")

    # <<< CONTENT VALIDATION >>>
    if data is None:
        logger.error(f"write_csv error: Received None data for path {full_output_path}")
        raise TypeError("Cannot write None data to CSV file.")
    if not isinstance(data, list):
        logger.error(
            f"write_csv error: Data must be a list, got {type(data)} for path {full_output_path}"
        )
        raise TypeError(f"Data must be a list, but received {type(data)}.")
    # <<< END CONTENT VALIDATION >>>

    # Further checks happen below based on list content (dicts vs lists)

    try:
        validated_path = _safe_path("OUTPUT_DIR", full_output_path, "data/outputs")

        is_list_of_lists = all(isinstance(row, list) for row in data)
        is_list_of_dicts = all(isinstance(row, dict) for row in data)

        # Handle empty list case separately
        if not data:
            logger.warning(f"Writing empty CSV file: {validated_path}")
            with open(validated_path, "w", newline="", encoding="utf-8") as csvfile:
                pass  # Creates an empty file
            return validated_path

        # Validate content type if list is not empty
        if not is_list_of_lists and not is_list_of_dicts:
            logger.error(
                f"Invalid data format for CSV writing: list contains mixed types or invalid types ({type(data[0])}). Expected list of lists or list of dicts."
            )
            raise ValueError(
                "Invalid data format for CSV. Must be list of lists or list of dicts."
            )

        with open(validated_path, "w", newline="", encoding="utf-8") as csvfile:
            if is_list_of_dicts:
                # Data is already validated to be non-empty list of dicts here
                fieldnames = list(data[0].keys())
                mismatched = [
                    idx
                    for idx, row in enumerate(data)
                    if set(row.keys()) != set(fieldnames)
                ]
                if mismatched:
                    logger.warning(f"Rows with mismatched columns: {mismatched}")
                writer = csv.DictWriter(
                    csvfile, fieldnames=fieldnames, quoting=csv.QUOTE_ALL
                )
                writer.writeheader()
                for row in data:
                    sanitized = {
                        k: _sanitize_csv_field(row.get(k, "")) for k in fieldnames
                    }
                    writer.writerow(sanitized)
            else:  # is_list_of_lists
                writer = csv.writer(csvfile, quoting=csv.QUOTE_ALL)
                for idx, row in enumerate(data):
                    if len(row) != len(data[0]):
                        logger.warning(
                            f"Row {idx} column count mismatch: expected {len(data[0])}, got {len(row)}"
                        )
                    sanitized_row = [_sanitize_csv_field(col) for col in row]
                    writer.writerow(sanitized_row)

        logger.info(f"Successfully wrote {len(data)} rows to CSV: {validated_path}")
        return validated_path
    except ValueError as ve:
        logger.error(f"CSV Output Path/Data Error: {ve}")
        raise
    except TypeError as te:  # Catch the type error we might raise
        logger.error(f"CSV Output Content Error: {te}")
        raise
    except IOError as e:
        logger.error(f"Error writing CSV file {full_output_path}: {e}", exc_info=True)
        raise IOError(f"Failed to write CSV file: {e}")
    except Exception as e:
        logger.error(
            f"Unexpected error writing CSV file {full_output_path}: {e}", exc_info=True
        )
        raise RuntimeError(f"Unexpected error writing CSV file: {e}")


def save_generated_file(filename: str, content: str, output_dir: str) -> str:
    """Safely saves generated text content to the output directory.

    This helper sanitizes the filename, ensures the path is within the
    configured ``OUTPUT_DIR`` and delegates to ``write_text`` for the
    actual write operation.

    Args:
        filename: Desired output filename provided by the caller.
        content: Text content to save.
        output_dir: Directory where the file should be saved.

    Returns:
        str: Absolute path to the written file.
    """
    from werkzeug.utils import secure_filename

    safe_filename = secure_filename(filename)
    if not safe_filename:
        raise ValueError("Invalid filename provided for generated file.")

    full_output_path = os.path.join(output_dir, safe_filename)
    return write_text(content, full_output_path)
