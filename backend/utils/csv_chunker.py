import logging
from pathlib import Path
from typing import List, Optional

import pandas as pd

try:
    from llama_index.core.schema import Document
except ImportError:
    logging.critical("llama-index-core not found. CSV chunking will fail.")
    Document = None

logger = logging.getLogger(__name__)


def _parse_csv_chunks(
    file_path: Path,
    encoding: str,
    separator: str,
    doc_id_prefix: Optional[str],
    client: Optional[str],
    upload_date: Optional[str],
    include_headers_in_text: bool,
    chunk_size: int = 10000,
) -> List[Document]:
    """
    Parse large CSV files in chunks to avoid memory issues
    """
    if not Document:
        logger.error("LlamaIndex Document class not available. Cannot parse CSV.")
        return []

    documents: List[Document] = []
    filename = file_path.name
    total_rows = 0
    chunk_count = 0

    try:
        # Increase CSV field size limit for large files
        import csv

        csv.field_size_limit(500000)  # 500KB field limit

        # Process CSV in chunks
        chunk_reader = pd.read_csv(
            file_path,
            encoding=encoding,
            sep=separator,
            engine="python",
            on_bad_lines="warn",
            chunksize=chunk_size,
        )

        for chunk_idx, df_chunk in enumerate(chunk_reader):
            if df_chunk.empty:
                continue

            chunk_count += 1
            chunk_rows = len(df_chunk)
            total_rows += chunk_rows

            logger.info(f"Processing chunk {chunk_idx + 1} with {chunk_rows} rows")

            # Create a summary document for this chunk
            chunk_doc_id = f"{doc_id_prefix or filename}_chunk_{chunk_idx + 1}"
            chunk_summary = [
                f"CSV Chunk {chunk_idx + 1} from {filename}",
                f"Rows in chunk: {chunk_rows}",
                f"Total rows processed so far: {total_rows}",
                f"Columns: {', '.join(df_chunk.columns)}",
            ]

            # Add sample data from this chunk
            sample_rows = df_chunk.head(3).to_dict(orient="records")
            chunk_summary.append(f"Sample rows from chunk: {sample_rows}")

            chunk_document = Document(
                id_=chunk_doc_id,
                text="\n".join(chunk_summary),
                metadata={
                    "chunk_index": chunk_idx + 1,
                    "chunk_rows": chunk_rows,
                    "filename": filename,
                    "client": client or "unknown",
                    "upload_date": upload_date or "unknown",
                    "source_type": "csv_chunk",
                },
            )
            documents.append(chunk_document)

            # Process individual rows (but limit to first 100 rows per chunk to prevent explosion)
            rows_to_process = min(100, chunk_rows)
            if rows_to_process < chunk_rows:
                logger.info(
                    f"Large chunk detected, processing only first {rows_to_process} rows out of {chunk_rows}"
                )

            for row_idx, (_, row) in enumerate(
                df_chunk.head(rows_to_process).iterrows()
            ):
                row_id = f"{doc_id_prefix or filename}_chunk_{chunk_idx + 1}_row_{row_idx + 1}"

                # Build row text content
                row_parts = []
                if include_headers_in_text:
                    for col, val in row.items():
                        if pd.notna(val):
                            row_parts.append(f"{col}: {val}")
                else:
                    row_parts = [str(val) for val in row.values if pd.notna(val)]

                if row_parts:
                    row_document = Document(
                        id_=row_id,
                        text=" | ".join(row_parts),
                        metadata={
                            "row_index": total_rows - chunk_rows + row_idx + 1,
                            "chunk_index": chunk_idx + 1,
                            "filename": filename,
                            "client": client or "unknown",
                            "upload_date": upload_date or "unknown",
                            "source_type": "csv_row",
                        },
                    )
                    documents.append(row_document)

        # Create overall summary document
        summary_lines = [
            f"Large CSV File Summary: {filename}",
            f"Client: {client or 'unknown'}",
            f"Upload date: {upload_date or 'unknown'}",
            f"Total chunks processed: {chunk_count}",
            f"Total rows: {total_rows}",
            f"Processing method: Chunked (chunk_size={chunk_size})",
        ]

        summary_doc = Document(
            id_=f"{doc_id_prefix or filename}_summary",
            text="\n".join(summary_lines),
            metadata={
                "total_chunks": chunk_count,
                "total_rows": total_rows,
                "filename": filename,
                "client": client or "unknown",
                "upload_date": upload_date or "unknown",
                "source_type": "csv_summary",
            },
        )
        documents.insert(0, summary_doc)  # Put summary first

        logger.info(
            f"Successfully processed large CSV file in {chunk_count} chunks, {total_rows} total rows, created {len(documents)} documents"
        )
        return documents

    except Exception as e:
        logger.error(f"Error processing large CSV file {file_path}: {e}")
        return []


def parse_csv_rows(
    file_path: str,
    include_headers_in_text: bool = True,
    encoding: str = "utf-8",
    separator: str = ",",
    doc_id_prefix: Optional[str] = None,
    client: Optional[str] = None,
    upload_date: Optional[str] = None,
    max_file_size_mb: int = 50,
) -> List[Document]:
    """
    Parses a CSV file and creates a LlamaIndex Document for each row,
    skipping malformed lines. Also creates a summary document with stats.
    """
    if not Document:
        logger.error("LlamaIndex Document class not available. Cannot parse CSV.")
        return []

    documents: List[Document] = []
    path = Path(file_path)
    filename = path.name if file_path else "unknown_file"

    try:
        logger.info(
            f"Attempting to parse CSV file: {file_path} with encoding '{encoding}' and separator '{separator}'"
        )

        # Check file size first
        import os

        file_size_mb = os.path.getsize(path) / (1024 * 1024)
        logger.info(f"CSV file size: {file_size_mb:.2f}MB")

        # Use chunking for large files to avoid memory issues
        if file_size_mb > max_file_size_mb:
            logger.info(
                f"Large CSV file detected ({file_size_mb:.2f}MB > {max_file_size_mb}MB), using chunked processing"
            )
            return _parse_csv_chunks(
                path,
                encoding,
                separator,
                doc_id_prefix,
                client,
                upload_date,
                include_headers_in_text,
            )

        # For smaller files, use the original method
        # Set CSV field size limit for compatibility
        import csv

        csv.field_size_limit(500000)  # 500KB field limit

        df = pd.read_csv(
            path, encoding=encoding, sep=separator, engine="python", on_bad_lines="warn"
        )

        if df.empty:
            logger.warning(f"CSV file is empty: {file_path}")
            return []

        logger.info(f"Successfully loaded {len(df)} rows from {file_path}")

        # --- Compute summary statistics ---
        summary_lines = [
            f"Filename: {filename}",
            f"Client: {client or 'unknown'}",
            f"Upload date: {upload_date or 'unknown'}",
            f"Rows: {len(df)}",
            f"Columns: {', '.join(df.columns)}",
        ]
        # Numeric stats
        numeric_cols = df.select_dtypes(include=["number"]).columns
        for col in numeric_cols:
            summary_lines.append(
                f"Column '{col}': min={df[col].min()}, max={df[col].max()}, mean={df[col].mean()}"
            )
        # Sample rows
        sample_rows = df.head(3).to_dict(orient="records")
        summary_lines.append(f"Sample rows: {sample_rows}")
        summary_text = "\n".join(summary_lines)
        summary_doc = Document(
            id_=f"{doc_id_prefix or filename}_summary",
            text=summary_text,
            metadata={
                "source_filename": filename,
                "content_type": "csv_summary",
                "client": client,
                "upload_date": upload_date,
            },
        )
        documents.append(summary_doc)

        for index, row in df.iterrows():
            if not isinstance(row, pd.Series):
                logger.warning(
                    f"Skipping malformed row at index {index} in {file_path}."
                )
                continue

            if include_headers_in_text:
                row_content_items = [
                    f"{str(col)}: {str(row[col])}"
                    for col in df.columns
                    if col in row and pd.notna(row[col])
                ]
            else:
                row_content_items = [
                    str(value) for value in row.values if pd.notna(value)
                ]
            row_text = ", ".join(row_content_items)

            row_num = index + 1
            metadata = {
                "source_filename": filename,
                "row_number": row_num,
                "content_type": "csv_row",
                "client": client,
                "upload_date": upload_date,
            }

            doc_id = f"{doc_id_prefix or filename}_row_{row_num}"

            doc = Document(
                id_=doc_id,
                text=row_text,
                metadata=metadata,
            )
            documents.append(doc)

        logger.info(
            f"Successfully parsed {len(documents)} valid rows into Document objects from {file_path}"
        )

    except FileNotFoundError:
        logger.error(f"CSV file not found: {file_path}")
    except pd.errors.EmptyDataError:
        logger.warning(
            f"CSV file is empty or contains no data after parsing: {file_path}"
        )
    except Exception as e:
        logger.error(
            f"An unexpected error occurred parsing CSV {file_path}: {e}", exc_info=True
        )

    return documents
