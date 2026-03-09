#!/usr/bin/env python3
"""
Tracking Analysis Utility
Analyzes bulk generation tracking JSON files and extracts data for retry operations
"""

import json
import logging
import os
from typing import Dict, List, Optional, Any
from collections import Counter

logger = logging.getLogger(__name__)

class TrackingAnalyzer:
    """Analyzes tracking JSON files for retry operations."""
    
    def __init__(self, tracking_path: str):
        """Initialize analyzer with tracking file path."""
        self.tracking_path = tracking_path
        self.data = None
        self._load_tracking_data()
    
    def _load_tracking_data(self):
        """Load and validate tracking JSON data."""
        try:
            if not os.path.exists(self.tracking_path):
                raise FileNotFoundError(f"Tracking file not found: {self.tracking_path}")
            
            with open(self.tracking_path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
            
            if 'row_records' not in self.data:
                raise ValueError("Invalid tracking file: missing 'row_records'")
            
            logger.info(f"Loaded tracking data: {len(self.data['row_records'])} rows")
            
        except Exception as e:
            logger.error(f"Error loading tracking file {self.tracking_path}: {e}")
            raise
    
    def analyze_tracking_file(self) -> Dict[str, Any]:
        """Analyze tracking JSON and return comprehensive statistics."""
        if not self.data:
            return {}
        
        row_records = self.data.get('row_records', [])
        metadata = self.data.get('metadata', {})
        
        # Basic counts
        total_rows = len(row_records)
        active_rows = len([r for r in row_records if r.get('is_active', False)])
        inactive_rows = total_rows - active_rows
        
        # Failure analysis
        zero_attempts = len([r for r in row_records if not r.get('is_active', False) and r.get('replacement_count', 0) == 0])
        partial_attempts = len([r for r in row_records if not r.get('is_active', False) and 0 < r.get('replacement_count', 0) < 5])
        max_attempts_reached = len([r for r in row_records if r.get('max_attempts_reached', False)])
        
        # Success rate
        success_rate = (active_rows / total_rows * 100) if total_rows > 0 else 0
        
        # Failure reasons analysis
        failure_reasons = []
        for record in row_records:
            if not record.get('is_active', False) and record.get('replacement_history'):
                for attempt in record['replacement_history']:
                    if not attempt.get('success', False) and attempt.get('validation_failure'):
                        reason = attempt['validation_failure'].get('reason', 'unknown')
                        failure_reasons.append(reason)
        
        common_failure_reasons = Counter(failure_reasons).most_common(5)
        
        return {
            'total_rows': total_rows,
            'active_rows': active_rows,
            'inactive_rows': inactive_rows,
            'zero_attempts': zero_attempts,
            'partial_attempts': partial_attempts,
            'max_attempts_reached': max_attempts_reached,
            'success_rate': round(success_rate, 2),
            'common_failure_reasons': [{'reason': reason, 'count': count} for reason, count in common_failure_reasons],
            'job_id': metadata.get('job_id', 'unknown'),
            'target_row_count': metadata.get('target_row_count', 0),
            'max_replacement_attempts': metadata.get('max_replacement_attempts', 5)
        }
    
    def extract_failed_topics(self, retry_mode: str = "all_inactive") -> List[Dict[str, Any]]:
        """Extract topics and metadata for failed rows based on retry mode."""
        if not self.data:
            return []
        
        row_records = self.data.get('row_records', [])
        
        # OPTIMIZATION: Pre-filter records based on retry mode to avoid processing all 500 rows
        if retry_mode == "all_inactive":
            # Pre-filter to only inactive records (much faster than checking each record)
            candidate_records = [r for r in row_records if not r.get('is_active', False)]
        elif retry_mode == "failed_only":
            candidate_records = [r for r in row_records if not r.get('is_active', False) and r.get('replacement_count', 0) > 0]
        elif retry_mode == "zero_attempts":
            candidate_records = [r for r in row_records if not r.get('is_active', False) and r.get('replacement_count', 0) == 0]
        elif retry_mode == "partial_failures":
            candidate_records = [r for r in row_records if not r.get('is_active', False) and 0 < r.get('replacement_count', 0) < 5]
        elif retry_mode == "max_attempts_reached":
            candidate_records = [r for r in row_records if r.get('max_attempts_reached', False)]
        else:
            # Fallback to original method for unknown retry modes
            candidate_records = row_records
        
        failed_topics = []
        
        # Now process only the pre-filtered records (17 instead of 500)
        for record in candidate_records:
            replacement_count = record.get('replacement_count', 0)
            max_attempts_reached = record.get('max_attempts_reached', False)
            
            # Extract failure reason from last attempt
            failure_reason = "never_attempted"
            if record.get('replacement_history'):
                last_attempt = record['replacement_history'][-1]
                if not last_attempt.get('success', False) and last_attempt.get('validation_failure'):
                    failure_reason = last_attempt['validation_failure'].get('reason', 'unknown')
            
            failed_topics.append({
                'topic': record.get('original_topic', ''),
                'task_id': record.get('original_task_id', ''),
                'original_sequence': record.get('sequence_number', 0),
                'attempts': replacement_count,
                'failure_reason': failure_reason,
                'unique_id': record.get('unique_id', ''),
                'max_attempts_reached': max_attempts_reached
            })
        
        logger.info(f"Extracted {len(failed_topics)} failed topics for retry mode '{retry_mode}' (processed {len(candidate_records)}/{len(row_records)} records)")
        return failed_topics
    
    def extract_job_context(self) -> Dict[str, Any]:
        """Extract original job context from tracking metadata."""
        if not self.data:
            return {}
        
        metadata = self.data.get('metadata', {})
        job_params = metadata.get('job_parameters', {})
        
        # Use stored job parameters if available
        context = {
            'prompt_rule_id': job_params.get('prompt_rule_id'),
            'model_name': job_params.get('model_name'),
            'client': job_params.get('client', 'Professional Services'),
            'project': job_params.get('project', 'Content Generation'),
            'website': job_params.get('website', 'website.com'),
            'client_notes': job_params.get('client_notes', ''),
            'target_word_count': job_params.get('target_word_count', 500),
            'concurrent_workers': job_params.get('concurrent_workers', 10),
            'batch_size': job_params.get('batch_size', 50),
            'insert_content': job_params.get('insert_content'),
            'insert_position': job_params.get('insert_position'),
            'original_job_id': metadata.get('job_id', 'unknown')
        }
        
        logger.info(f"Extracted job context from stored parameters: {context}")
        return context
    
    def get_retry_statistics(self) -> Dict[str, Any]:
        """Get statistics specifically for retry operations."""
        analysis = self.analyze_tracking_file()
        
        return {
            'retry_candidates': {
                'all_inactive': analysis['inactive_rows'],
                'zero_attempts': analysis['zero_attempts'],
                'partial_attempts': analysis['partial_attempts'],
                'max_attempts_reached': analysis['max_attempts_reached']
            },
            'success_metrics': {
                'original_success_rate': analysis['success_rate'],
                'total_generated': analysis['active_rows'],
                'total_failed': analysis['inactive_rows']
            },
            'job_info': {
                'original_job_id': analysis['job_id'],
                'target_count': analysis['target_row_count'],
                'max_attempts': analysis['max_replacement_attempts']
            }
        }
    
    def merge_tracking_files(self, retry_tracking_paths: List[str]) -> List[Dict]:
        """
        Merge original tracking with retry tracking files.
        Returns list of all active (successful) rows from all sources.
        """
        if not self.data:
            return []
        
        # Start with active rows from original tracking
        original_rows = self.data.get('row_records', [])
        active_rows = [record for record in original_rows if record.get('is_active', False)]
        
        logger.info(f"Original tracking has {len(active_rows)} active rows")
        
        # Add active rows from retry tracking files
        for retry_path in retry_tracking_paths:
            try:
                with open(retry_path, 'r', encoding='utf-8') as f:
                    retry_data = json.load(f)
                
                retry_rows = retry_data.get('row_records', [])
                retry_active = [record for record in retry_rows if record.get('is_active', False)]
                
                logger.info(f"Retry file {retry_path} has {len(retry_active)} active rows")
                
                # Add retry active rows to our collection
                active_rows.extend(retry_active)
                
            except Exception as e:
                logger.error(f"Error reading retry tracking file {retry_path}: {e}")
                continue
        
        logger.info(f"Total merged active rows: {len(active_rows)}")
        return active_rows


def analyze_tracking_file(tracking_path: str) -> Dict[str, Any]:
    """Convenience function to analyze a tracking file."""
    analyzer = TrackingAnalyzer(tracking_path)
    return analyzer.analyze_tracking_file()


def extract_failed_topics(tracking_path: str, retry_mode: str = "all_inactive") -> List[Dict[str, Any]]:
    """Convenience function to extract failed topics."""
    analyzer = TrackingAnalyzer(tracking_path)
    return analyzer.extract_failed_topics(retry_mode)


def extract_job_context(tracking_path: str) -> Dict[str, Any]:
    """Convenience function to extract job context."""
    analyzer = TrackingAnalyzer(tracking_path)
    return analyzer.extract_job_context()


if __name__ == '__main__':
    # Example usage
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python tracking_analyzer.py <tracking_file_path>")
        sys.exit(1)
    
    tracking_file = sys.argv[1]
    
    try:
        analyzer = TrackingAnalyzer(tracking_file)
        
        # Analyze the file
        analysis = analyzer.analyze_tracking_file()
        print(f"Analysis Results:")
        print(f"  Total rows: {analysis['total_rows']}")
        print(f"  Active (successful): {analysis['active_rows']}")
        print(f"  Inactive (failed): {analysis['inactive_rows']}")
        print(f"  Success rate: {analysis['success_rate']}%")
        print(f"  Zero attempts: {analysis['zero_attempts']}")
        print(f"  Partial attempts: {analysis['partial_attempts']}")
        print(f"  Max attempts reached: {analysis['max_attempts_reached']}")
        
        # Extract failed topics
        failed_topics = analyzer.extract_failed_topics("all_inactive")
        print(f"\nFailed topics for retry: {len(failed_topics)}")
        
        # Extract job context
        context = analyzer.extract_job_context()
        print(f"\nJob context: {context}")
        
    except Exception as e:
        print(f"Error analyzing tracking file: {e}")
        sys.exit(1)
