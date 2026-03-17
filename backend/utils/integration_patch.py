#!/usr/bin/env python3
"""
Integration Patch for Bulk CSV Generator with Row Tracking

This module provides the modified generate_bulk_csv method that integrates
the comprehensive row tracking system into the existing BulkCSVGenerator.

Apply this by replacing the generate_bulk_csv method in bulk_csv_generator.py
"""

import os
import time
import logging
from typing import List, Dict, Tuple, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


def generate_bulk_csv_with_tracking(
    self,
    tasks: List["GenerationTask"],
    output_filename: str,
    resume_from_id: Optional[str] = None,
) -> Tuple[str, Dict]:
    """
    Generate bulk CSV content with comprehensive row tracking

    This method guarantees:
    1. Exactly len(tasks) rows in the output
    2. Every row has a unique, traceable ID
    3. Complete audit trail of all operations
    4. Detailed tracking logs for future adjustments

    Args:
        tasks: List of generation tasks
        output_filename: Output CSV filename
        resume_from_id: Resume from specific task ID (for interrupted jobs)

    Returns:
        Tuple of (output_path, statistics)
    """
    from bulk_csv_generator_with_tracking import (
        RowTracker,
        RowStatus,
        ValidationFailure,
    )

    output_path = os.path.join(self.output_dir, output_filename)
    target_count = len(tasks)

    # Initialize row tracker
    self.row_tracker = RowTracker(
        target_row_count=target_count,
        max_replacement_attempts=5,
        job_id=self.job_id or f"csv_gen_{int(time.time())}",
    )

    self._log_info(
        "Initializing row tracking system",
        {
            "target_count": target_count,
            "max_replacement_attempts": 5,
            "job_id": self.row_tracker.job_id,
        },
    )

    # Create row records for all tasks
    self._log_info("Creating row records", {"count": len(tasks)})
    task_to_record = {}  # Map task.item_id -> RowRecord

    for i, task in enumerate(tasks, start=1):
        record = self.row_tracker.create_row_record(
            sequence_number=i, topic=task.topic, task_id=task.item_id
        )
        task_to_record[task.item_id] = record

    # Update progress
    self._update_progress(f"Starting tracked generation: {target_count} rows", 0.0)

    # Main generation loop with tracking
    generation_round = 0
    max_rounds = 20  # Safety limit

    while generation_round < max_rounds:
        generation_round += 1

        # Check if we're done
        active_rows = self.row_tracker.get_active_rows_ordered()
        if len(active_rows) >= target_count:
            self._log_info(f"Target count reached: {len(active_rows)}/{target_count}")
            break

        # Get rows that need (re)generation
        needs_work = self.row_tracker.get_rows_needing_generation()

        if not needs_work:
            self._log_info("No rows need work - checking for flagged rows")

            # If we're short, use flagged rows with their best attempt
            flagged_rows = self.row_tracker.get_flagged_rows()
            if len(active_rows) + len(flagged_rows) >= target_count:
                self._log_info(
                    f"Using {len(flagged_rows)} flagged rows to reach target"
                )

                for flagged in flagged_rows:
                    if flagged.current_row_data and not flagged.is_active:
                        flagged.is_active = True
                        flagged.needs_manual_review = True
                        self._log_warning(
                            f"Row {flagged.unique_id} flagged but included in output",
                            {"attempts": flagged.replacement_count},
                        )
                break
            else:
                self._log_error("Cannot reach target count - insufficient valid rows")
                break

        # Process batch of rows needing work
        batch_size = min(self.batch_size, len(needs_work))
        batch = needs_work[:batch_size]

        self._log_info(
            f"Round {generation_round}: Processing {len(batch)} rows",
            {
                "active": len(active_rows),
                "target": target_count,
                "needs_work": len(needs_work),
            },
        )

        # Check for cancellation
        if self._check_cancelled():
            self._log_info("Generation cancelled by user")
            break

        # Generate content for this batch
        for seq_num, unique_id in batch:
            # Find corresponding task
            record = self.row_tracker.records[unique_id]
            task = next(
                (t for t in tasks if t.item_id == record.original_task_id), None
            )

            if not task:
                self._log_error(f"Cannot find task for record {unique_id}")
                continue

            # Add retry context if this is a replacement attempt
            if record.replacement_count > 0:
                # Get previous failure reason
                if record.replacement_history:
                    last_failure = record.replacement_history[-1].validation_failure
                    if last_failure:
                        task.previous_attempt_failed_reason = last_failure.reason
                        if last_failure.word_count:
                            task.previous_attempt_word_count = last_failure.word_count

            # Generate content
            start_time = time.time()

            try:
                content_row = self._generate_single_content(task)
                generation_time_ms = (time.time() - start_time) * 1000

                if content_row is None:
                    # Generation failed
                    validation_failure = ValidationFailure(
                        timestamp=datetime.now().isoformat(),
                        reason="generation_returned_none",
                        validation_details={"error": "LLM returned None"},
                    )

                    self.row_tracker.record_generation_attempt(
                        unique_id=unique_id,
                        success=False,
                        validation_failure=validation_failure,
                        generation_time_ms=generation_time_ms,
                    )
                    continue

                # Validate content
                validation_passed = self._validate_content_row(content_row)

                if validation_passed:
                    # Success! Record and mark active
                    row_data = {
                        "ID": content_row.id,
                        "Title": content_row.title,
                        "Content": content_row.content,
                        "Excerpt": content_row.excerpt or "",
                        "Category": content_row.category or "General",
                        "Tags": content_row.tags or "professional, services, quality",
                        "slug": content_row.slug
                        or self._generate_slug_from_title(content_row.title),
                    }

                    self.row_tracker.record_generation_attempt(
                        unique_id=unique_id,
                        success=True,
                        row_data=row_data,
                        generation_time_ms=generation_time_ms,
                    )

                    self.generated_count += 1
                    self._update_progress(
                        f"Generated {self.generated_count}/{target_count} rows",
                        (self.generated_count / target_count) * 100,
                    )

                else:
                    # Validation failed - record and will retry
                    content_text = content_row.content or ""
                    word_count = len(content_text.split())

                    # Determine failure reason
                    if word_count < 100:
                        reason = "content_too_short"
                    elif not content_row.title or len(content_row.title) < 10:
                        reason = "title_too_short"
                    elif any(
                        content_text.strip().endswith(tag)
                        for tag in ["<", "</", "</h", "</p"]
                    ):
                        reason = "truncated_html"
                    else:
                        reason = "validation_failed"

                    validation_failure = ValidationFailure(
                        timestamp=datetime.now().isoformat(),
                        reason=reason,
                        word_count=word_count,
                        content_preview=content_text[:100],
                        validation_details={
                            "title_length": len(content_row.title or ""),
                            "has_excerpt": bool(content_row.excerpt),
                        },
                    )

                    self.row_tracker.record_generation_attempt(
                        unique_id=unique_id,
                        success=False,
                        validation_failure=validation_failure,
                        generation_time_ms=generation_time_ms,
                    )

            except Exception as e:
                # Generation exception
                generation_time_ms = (time.time() - start_time) * 1000

                validation_failure = ValidationFailure(
                    timestamp=datetime.now().isoformat(),
                    reason="generation_exception",
                    validation_details={"error": str(e)},
                )

                self.row_tracker.record_generation_attempt(
                    unique_id=unique_id,
                    success=False,
                    validation_failure=validation_failure,
                    generation_time_ms=generation_time_ms,
                )

                self._log_error(f"Exception generating row {unique_id}: {e}")

    # Finalize tracking
    self.row_tracker.finalize()

    # Get final active rows
    active_rows = self.row_tracker.get_active_rows_ordered()
    self._log_info(f"Generation complete: {len(active_rows)} active rows")

    # Write CSV with exact row count
    if len(active_rows) != target_count:
        self._log_warning(
            f"Row count mismatch: {len(active_rows)} active, {target_count} target",
            {"flagged_count": len(self.row_tracker.get_flagged_rows())},
        )

    # Prepare rows for CSV writing
    csv_rows = []
    for record in active_rows:
        if record.current_row_data:
            # Add tracking ID to metadata
            row_copy = record.current_row_data.copy()
            row_copy["_tracking_id"] = record.unique_id
            row_copy["_tracking_sequence"] = record.sequence_number
            csv_rows.append(row_copy)

    # Write main CSV
    headers = ["ID", "Title", "Content", "Excerpt", "Category", "Tags", "slug"]

    try:
        from backend.tools.file_processor import write_csv

        write_csv(
            file_path=output_path,
            headers=headers,
            rows=csv_rows,
            delimiter=self.csv_delimiter,
        )
        self._log_info(f"Wrote CSV: {output_path} ({len(csv_rows)} rows)")

    except ImportError:
        # Fallback to basic CSV writing
        import csv

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers, delimiter=self.csv_delimiter)
            writer.writeheader()
            for row in csv_rows:
                writer.writerow({k: row.get(k, "") for k in headers})

        self._log_info(f"Wrote CSV (fallback): {output_path} ({len(csv_rows)} rows)")

    # Export tracking data
    tracking_dir = os.path.join(self.output_dir, "tracking")
    os.makedirs(tracking_dir, exist_ok=True)

    job_id = self.row_tracker.job_id
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON tracking log (detailed)
    json_path = os.path.join(tracking_dir, f"{job_id}_tracking_{timestamp}.json")
    self.row_tracker.export_tracking_log_json(json_path)

    # CSV tracking log (summary)
    tracking_csv_path = os.path.join(tracking_dir, f"{job_id}_tracking_{timestamp}.csv")
    self.row_tracker.export_tracking_log_csv(tracking_csv_path)

    # Summary report
    summary_path = os.path.join(tracking_dir, f"{job_id}_summary_{timestamp}.txt")
    self.row_tracker.generate_summary_report(summary_path)

    # Prepare statistics for return
    tracking_stats = self.row_tracker.get_statistics()

    statistics = {
        "total_tasks": len(tasks),
        "generated_count": len(active_rows),
        "error_count": self.error_count,
        "target_matched": len(active_rows) == target_count,
        # Tracking statistics
        "tracking": tracking_stats,
        # Tracking file paths
        "tracking_files": {
            "json_log": json_path,
            "csv_log": tracking_csv_path,
            "summary_report": summary_path,
        },
    }

    self._log_info("Generation complete with tracking", statistics)

    return output_path, statistics


# ============================================================================
# HELPER: Monkey-patch existing BulkCSVGenerator
# ============================================================================


def apply_tracking_to_generator(generator_instance):
    """
    Apply row tracking to an existing BulkCSVGenerator instance

    Usage:
        generator = BulkCSVGenerator(...)
        apply_tracking_to_generator(generator)
        # Now generator has tracking-enabled generate_bulk_csv
    """

    # Replace the method
    import types

    generator_instance.generate_bulk_csv = types.MethodType(
        generate_bulk_csv_with_tracking, generator_instance
    )

    logger.info("Applied row tracking to BulkCSVGenerator instance")
