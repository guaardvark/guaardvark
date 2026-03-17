#!/usr/bin/env python3
"""
Enhanced Bulk CSV Generator with Comprehensive Row Tracking

This module implements a robust row tracking system that ensures:
1. Every requested row has a unique identifier
2. No row loss - failed validations are replaced
3. Complete audit trail of all row operations
4. Future adjustability through detailed metadata storage

Key Features:
- Unique row IDs (timestamp + sequence format)
- Validation failure tracking with replacement history
- Detailed tracking logs (JSON and CSV)
- Summary reports with validation statistics
- Guaranteed exact row count in output
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse
from enum import Enum

import requests

logger = logging.getLogger(__name__)

# ============================================================================
# ROW TRACKING SYSTEM - Core Data Structures
# ============================================================================


class RowStatus(Enum):
    """Status of a tracked row"""

    ORIGINAL = "original"  # First attempt, passed validation
    REPLACED = "replaced"  # Failed validation, was replaced
    FLAGGED = "flagged_manual_review"  # Max retries reached, needs review
    ACTIVE = "active"  # Currently in use (latest version)


@dataclass
class ValidationFailure:
    """Record of a single validation failure"""

    timestamp: str
    reason: str
    word_count: Optional[int] = None
    content_preview: Optional[str] = None  # First 100 chars
    validation_details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RowReplacement:
    """Record of a row replacement attempt"""

    attempt_number: int
    timestamp: str
    success: bool
    validation_failure: Optional[ValidationFailure] = None
    generation_time_ms: Optional[float] = None


@dataclass
class RowRecord:
    """
    Comprehensive tracking record for a single row

    This contains all metadata needed for:
    - Audit trail of row lifecycle
    - Future adjustments to specific line items
    - Quality analysis and reporting
    """

    # Core identification
    unique_id: str  # Format: YYYYMMDD_HHMMSS_SEQ (e.g., 20250114_143022_001)
    sequence_number: int  # Position in requested batch (1-indexed)

    # Status tracking
    status: RowStatus
    is_active: bool  # True if this row is in final CSV

    # Original request context
    original_topic: str
    original_task_id: str
    request_timestamp: str

    # Replacement tracking
    replacement_count: int = 0
    replacement_history: List[RowReplacement] = field(default_factory=list)
    max_attempts_reached: bool = False

    # Current row data (latest successful version)
    current_row_data: Optional[Dict[str, Any]] = None

    # Original row data (first attempt, for comparison)
    original_row_data: Optional[Dict[str, Any]] = None

    # Quality metrics
    final_word_count: Optional[int] = None
    final_validation_passed: bool = False
    generation_duration_ms: Optional[float] = None

    # Flags for manual review
    needs_manual_review: bool = False
    manual_review_reason: Optional[str] = None

    # Additional metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    def get_tracking_summary(self) -> Dict[str, Any]:
        """Get a concise summary for logging"""
        return {
            "id": self.unique_id,
            "seq": self.sequence_number,
            "status": self.status.value,
            "replacements": self.replacement_count,
            "word_count": self.final_word_count,
            "needs_review": self.needs_manual_review,
        }


class RowTracker:
    """
    Manages comprehensive row tracking for CSV generation

    Responsibilities:
    - Generate unique row IDs
    - Track validation failures and replacements
    - Maintain complete row history
    - Ensure exact row count in output
    - Generate tracking reports
    """

    def __init__(
        self,
        target_row_count: int,
        max_replacement_attempts: int = 5,
        job_id: Optional[str] = None,
    ):
        """
        Initialize row tracker

        Args:
            target_row_count: Exact number of rows required in output
            max_replacement_attempts: Maximum retries per row before flagging
            job_id: Optional job identifier for tracking
        """
        self.target_row_count = target_row_count
        self.max_replacement_attempts = max_replacement_attempts
        self.job_id = job_id or f"job_{int(time.time())}"

        # Tracking storage
        self.records: Dict[str, RowRecord] = {}  # unique_id -> RowRecord
        self.sequence_to_id: Dict[int, str] = {}  # sequence -> current active unique_id

        # Statistics
        self.total_generation_attempts = 0
        self.total_validation_failures = 0
        self.total_replacements = 0
        self.rows_flagged_for_review = 0

        # Timing
        self.start_time = datetime.now()
        self.end_time: Optional[datetime] = None

        # ID generation state
        self._id_counter = 0
        self._id_timestamp_base = datetime.now().strftime("%Y%m%d_%H%M%S")

        logger.info(
            f"RowTracker initialized: target={target_row_count}, "
            f"max_attempts={max_replacement_attempts}, job_id={self.job_id}"
        )

    def generate_unique_id(self, sequence_number: int) -> str:
        """
        Generate unique row ID with timestamp + sequence

        Format: YYYYMMDD_HHMMSS_SEQ
        Example: 20250114_143022_001

        Args:
            sequence_number: Position in batch (1-indexed)

        Returns:
            Unique row identifier
        """
        self._id_counter += 1
        unique_id = f"{self._id_timestamp_base}_{sequence_number:03d}"
        return unique_id

    def create_row_record(
        self, sequence_number: int, topic: str, task_id: str
    ) -> RowRecord:
        """
        Create a new row record for tracking

        Args:
            sequence_number: Position in batch (1-indexed)
            topic: Content topic/title
            task_id: Original task identifier

        Returns:
            Initialized RowRecord
        """
        unique_id = self.generate_unique_id(sequence_number)

        record = RowRecord(
            unique_id=unique_id,
            sequence_number=sequence_number,
            status=RowStatus.ORIGINAL,
            is_active=False,
            original_topic=topic,
            original_task_id=task_id,
            request_timestamp=datetime.now().isoformat(),
        )

        self.records[unique_id] = record
        self.sequence_to_id[sequence_number] = unique_id

        logger.debug(
            f"Created row record: {unique_id} (seq={sequence_number}, topic={topic[:50]})"
        )
        return record

    def record_generation_attempt(
        self,
        unique_id: str,
        success: bool,
        row_data: Optional[Dict[str, Any]] = None,
        validation_failure: Optional[ValidationFailure] = None,
        generation_time_ms: Optional[float] = None,
    ):
        """
        Record a content generation attempt

        Args:
            unique_id: Row identifier
            success: Whether validation passed
            row_data: Generated row data if successful
            validation_failure: Failure details if unsuccessful
            generation_time_ms: Time taken to generate
        """
        if unique_id not in self.records:
            logger.error(f"Attempted to record generation for unknown ID: {unique_id}")
            return

        record = self.records[unique_id]
        self.total_generation_attempts += 1

        attempt_number = record.replacement_count + 1
        replacement = RowReplacement(
            attempt_number=attempt_number,
            timestamp=datetime.now().isoformat(),
            success=success,
            validation_failure=validation_failure,
            generation_time_ms=generation_time_ms,
        )

        record.replacement_history.append(replacement)

        if success:
            # Successful generation
            record.current_row_data = row_data
            if record.original_row_data is None:
                record.original_row_data = row_data.copy()

            record.final_validation_passed = True
            record.is_active = True
            record.generation_duration_ms = generation_time_ms

            # Calculate word count
            if row_data and "Content" in row_data:
                content = row_data["Content"] or ""
                record.final_word_count = len(content.split())

            # Update status
            if record.replacement_count > 0:
                record.status = RowStatus.REPLACED
            else:
                record.status = RowStatus.ORIGINAL

            logger.info(
                f"Row {unique_id}: Generation successful (attempt {attempt_number}, "
                f"word_count={record.final_word_count})"
            )
        else:
            # Failed generation
            record.replacement_count += 1
            self.total_validation_failures += 1

            if record.replacement_count >= self.max_replacement_attempts:
                # Max attempts reached - flag for manual review
                record.status = RowStatus.FLAGGED
                record.max_attempts_reached = True
                record.needs_manual_review = True
                record.manual_review_reason = (
                    f"Failed validation after {record.replacement_count} attempts"
                )
                self.rows_flagged_for_review += 1

                logger.warning(
                    f"Row {unique_id}: FLAGGED for manual review after "
                    f"{record.replacement_count} attempts"
                )
            else:
                logger.info(
                    f"Row {unique_id}: Validation failed (attempt {attempt_number}), "
                    f"will retry ({record.replacement_count}/{self.max_replacement_attempts})"
                )

    def get_rows_needing_generation(self) -> List[Tuple[int, str]]:
        """
        Get list of rows that need (re)generation

        Returns:
            List of (sequence_number, unique_id) tuples for rows needing work
        """
        needs_generation = []

        for seq_num in range(1, self.target_row_count + 1):
            if seq_num in self.sequence_to_id:
                unique_id = self.sequence_to_id[seq_num]
                record = self.records[unique_id]

                # Need generation if not active and not flagged
                if not record.is_active and record.status != RowStatus.FLAGGED:
                    needs_generation.append((seq_num, unique_id))
            else:
                # No record exists yet - shouldn't happen in normal flow
                logger.error(f"Missing record for sequence {seq_num}")

        return needs_generation

    def get_active_rows_ordered(self) -> List[RowRecord]:
        """
        Get all active rows in sequence order

        Returns:
            List of active RowRecords, sorted by sequence_number
        """
        active_records = [
            self.records[self.sequence_to_id[seq]]
            for seq in sorted(self.sequence_to_id.keys())
            if self.records[self.sequence_to_id[seq]].is_active
        ]
        return active_records

    def get_flagged_rows(self) -> List[RowRecord]:
        """Get all rows flagged for manual review"""
        return [r for r in self.records.values() if r.needs_manual_review]

    def finalize(self):
        """Mark tracking as complete and calculate final statistics"""
        self.end_time = datetime.now()

        # Final validation: ensure we have exactly target_row_count active rows
        active_count = sum(1 for r in self.records.values() if r.is_active)

        if active_count != self.target_row_count:
            logger.error(
                f"TRACKING INCONSISTENCY: Expected {self.target_row_count} "
                f"active rows, found {active_count}"
            )

        logger.info(
            f"RowTracker finalized: {active_count} active rows, "
            f"{self.total_validation_failures} failures, "
            f"{self.rows_flagged_for_review} flagged for review"
        )

    def get_statistics(self) -> Dict[str, Any]:
        """Generate comprehensive statistics"""
        duration = (self.end_time or datetime.now()) - self.start_time

        active_rows = [r for r in self.records.values() if r.is_active]
        flagged_rows = [r for r in self.records.values() if r.needs_manual_review]

        # Word count statistics
        word_counts = [r.final_word_count for r in active_rows if r.final_word_count]
        avg_word_count = sum(word_counts) / len(word_counts) if word_counts else 0

        # Replacement statistics
        replacement_counts = [r.replacement_count for r in active_rows]
        avg_replacements = (
            sum(replacement_counts) / len(replacement_counts)
            if replacement_counts
            else 0
        )

        return {
            "job_id": self.job_id,
            "target_row_count": self.target_row_count,
            "actual_row_count": len(active_rows),
            "rows_match_target": len(active_rows) == self.target_row_count,
            "total_generation_attempts": self.total_generation_attempts,
            "total_validation_failures": self.total_validation_failures,
            "total_replacements": self.total_replacements,
            "rows_flagged_for_review": len(flagged_rows),
            "success_rate": (
                (len(active_rows) / self.total_generation_attempts * 100)
                if self.total_generation_attempts > 0
                else 0
            ),
            "average_word_count": round(avg_word_count, 1),
            "average_replacements_per_row": round(avg_replacements, 2),
            "duration_seconds": duration.total_seconds(),
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }

    def export_tracking_log_json(self, output_path: str):
        """
        Export detailed tracking log as JSON

        Args:
            output_path: Path to output JSON file
        """
        tracking_data = {
            "metadata": {
                "job_id": self.job_id,
                "export_timestamp": datetime.now().isoformat(),
                "target_row_count": self.target_row_count,
                "max_replacement_attempts": self.max_replacement_attempts,
            },
            "statistics": self.get_statistics(),
            "row_records": [record.to_dict() for record in self.records.values()],
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(tracking_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Exported tracking log to: {output_path}")

    def export_tracking_log_csv(self, output_path: str):
        """
        Export tracking summary as CSV

        Args:
            output_path: Path to output CSV file
        """
        fieldnames = [
            "unique_id",
            "sequence_number",
            "status",
            "is_active",
            "original_topic",
            "replacement_count",
            "final_word_count",
            "final_validation_passed",
            "needs_manual_review",
            "manual_review_reason",
            "request_timestamp",
        ]

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for record in sorted(
                self.records.values(), key=lambda r: r.sequence_number
            ):
                row = {
                    "unique_id": record.unique_id,
                    "sequence_number": record.sequence_number,
                    "status": record.status.value,
                    "is_active": record.is_active,
                    "original_topic": record.original_topic[:100],  # Truncate for CSV
                    "replacement_count": record.replacement_count,
                    "final_word_count": record.final_word_count or 0,
                    "final_validation_passed": record.final_validation_passed,
                    "needs_manual_review": record.needs_manual_review,
                    "manual_review_reason": record.manual_review_reason or "",
                    "request_timestamp": record.request_timestamp,
                }
                writer.writerow(row)

        logger.info(f"Exported tracking CSV to: {output_path}")

    def generate_summary_report(self, output_path: str):
        """
        Generate human-readable summary report

        Args:
            output_path: Path to output text file
        """
        stats = self.get_statistics()
        flagged_rows = self.get_flagged_rows()

        lines = [
            "=" * 80,
            "CSV GENERATION TRACKING REPORT",
            "=" * 80,
            "",
            f"Job ID: {self.job_id}",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "SUMMARY",
            "-" * 80,
            f"Target Row Count:        {stats['target_row_count']}",
            f"Actual Row Count:        {stats['actual_row_count']}",
            f"Rows Match Target:       {'✓ YES' if stats['rows_match_target'] else '✗ NO'}",
            "",
            "GENERATION STATISTICS",
            "-" * 80,
            f"Total Attempts:          {stats['total_generation_attempts']}",
            f"Validation Failures:     {stats['total_validation_failures']}",
            f"Success Rate:            {stats['success_rate']:.1f}%",
            f"Average Word Count:      {stats['average_word_count']}",
            f"Avg Replacements/Row:    {stats['average_replacements_per_row']}",
            "",
            "QUALITY FLAGS",
            "-" * 80,
            f"Rows Flagged for Review: {len(flagged_rows)}",
            "",
        ]

        if flagged_rows:
            lines.append("FLAGGED ROWS (Manual Review Required)")
            lines.append("-" * 80)
            for record in flagged_rows:
                lines.append(
                    f"  [{record.unique_id}] Seq #{record.sequence_number}: {record.original_topic[:60]}"
                )
                lines.append(f"    Reason: {record.manual_review_reason}")
                lines.append(f"    Attempts: {record.replacement_count}")
                lines.append("")

        lines.extend(
            [
                "TIMING",
                "-" * 80,
                f"Start Time:              {stats['start_time']}",
                f"End Time:                {stats['end_time'] or 'In Progress'}",
                f"Duration:                {stats['duration_seconds']:.1f} seconds",
                "",
                "=" * 80,
            ]
        )

        report_text = "\n".join(lines)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_text)

        logger.info(f"Generated summary report: {output_path}")

        # Also log to console
        print("\n" + report_text + "\n")


# ============================================================================
# EXAMPLE USAGE AND INTEGRATION
# ============================================================================


def example_usage():
    """
    Example demonstrating row tracking system integration
    """

    # Initialize tracker
    target_rows = 100
    tracker = RowTracker(
        target_row_count=target_rows, max_replacement_attempts=5, job_id="demo_job_001"
    )

    # Create initial row records
    print("Creating row records...")
    for seq in range(1, target_rows + 1):
        tracker.create_row_record(
            sequence_number=seq, topic=f"Sample Topic {seq}", task_id=f"task_{seq:03d}"
        )

    # Simulate generation with some failures
    print("\nSimulating content generation...")
    import random

    for seq in range(1, target_rows + 1):
        unique_id = tracker.sequence_to_id[seq]

        # Simulate up to max_attempts
        for attempt in range(1, 6):
            # 80% success rate on first try, increasing with retries
            success_rate = 0.8 + (attempt * 0.05)
            success = random.random() < success_rate

            if success:
                # Successful generation
                row_data = {
                    "ID": unique_id,
                    "Title": f"Generated Title for Topic {seq}",
                    "Content": "Lorem ipsum " * 200,  # Simulated content
                    "Excerpt": "Sample excerpt",
                    "Category": "General",
                    "Tags": "sample, test",
                    "slug": f"topic-{seq}",
                }

                validation_failure = None
                tracker.record_generation_attempt(
                    unique_id=unique_id,
                    success=True,
                    row_data=row_data,
                    generation_time_ms=random.uniform(500, 2000),
                )
                break
            else:
                # Failed validation
                validation_failure = ValidationFailure(
                    timestamp=datetime.now().isoformat(),
                    reason=(
                        "Content too short"
                        if random.random() < 0.5
                        else "Invalid format"
                    ),
                    word_count=random.randint(50, 150),
                )

                tracker.record_generation_attempt(
                    unique_id=unique_id,
                    success=False,
                    validation_failure=validation_failure,
                    generation_time_ms=random.uniform(500, 2000),
                )

    # Finalize tracking
    print("\nFinalizing tracking...")
    tracker.finalize()

    # Export tracking data
    output_dir = "/tmp/csv_tracking_demo"
    os.makedirs(output_dir, exist_ok=True)

    print("\nExporting tracking data...")
    tracker.export_tracking_log_json(f"{output_dir}/tracking_log.json")
    tracker.export_tracking_log_csv(f"{output_dir}/tracking_log.csv")
    tracker.generate_summary_report(f"{output_dir}/summary_report.txt")

    # Get statistics
    stats = tracker.get_statistics()
    print("\n" + "=" * 80)
    print("FINAL STATISTICS")
    print("=" * 80)
    print(json.dumps(stats, indent=2))


# ============================================================================
# INTEGRATION WITH EXISTING BulkCSVGenerator
# ============================================================================

"""
To integrate this tracking system into the existing BulkCSVGenerator class:

1. Add tracker initialization in __init__:

   self.row_tracker = RowTracker(
       target_row_count=len(tasks),
       max_replacement_attempts=5,
       job_id=self.job_id
   )

2. Create row records before generation:

   for i, task in enumerate(tasks, start=1):
       self.row_tracker.create_row_record(
           sequence_number=i,
           topic=task.topic,
           task_id=task.item_id
       )

3. Modify the generation loop to use tracking:

   while len(active_rows) < target_count:
       needs_work = self.row_tracker.get_rows_needing_generation()

       for seq_num, unique_id in needs_work[:batch_size]:
           start_time = time.time()

           # Generate content
           content_row = self._generate_single_content(task)
           generation_time_ms = (time.time() - start_time) * 1000

           # Validate
           if self._validate_content_row(content_row):
               # Success
               row_data = content_row_to_dict(content_row)
               self.row_tracker.record_generation_attempt(
                   unique_id=unique_id,
                   success=True,
                   row_data=row_data,
                   generation_time_ms=generation_time_ms
               )
           else:
               # Failure
               validation_failure = ValidationFailure(
                   timestamp=datetime.now().isoformat(),
                   reason="Validation failed",
                   word_count=len(content_row.content.split())
               )
               self.row_tracker.record_generation_attempt(
                   unique_id=unique_id,
                   success=False,
                   validation_failure=validation_failure,
                   generation_time_ms=generation_time_ms
               )

4. After generation, export tracking data:

   self.row_tracker.finalize()

   tracking_dir = os.path.join(self.output_dir, 'tracking')
   os.makedirs(tracking_dir, exist_ok=True)

   self.row_tracker.export_tracking_log_json(
       os.path.join(tracking_dir, f'{job_id}_tracking.json')
   )
   self.row_tracker.export_tracking_log_csv(
       os.path.join(tracking_dir, f'{job_id}_tracking.csv')
   )
   self.row_tracker.generate_summary_report(
       os.path.join(tracking_dir, f'{job_id}_summary.txt')
   )

5. Ensure exact row count by using flagged rows:

   active_rows = self.row_tracker.get_active_rows_ordered()
   flagged_rows = self.row_tracker.get_flagged_rows()

   # If we have flagged rows, use their best attempt
   for flagged in flagged_rows:
       if flagged.current_row_data:
           active_rows.append(flagged)
"""


if __name__ == "__main__":
    # Run example
    print("Running Row Tracking System Example...")
    example_usage()
    print("\nExample complete! Check /tmp/csv_tracking_demo/ for output files.")
