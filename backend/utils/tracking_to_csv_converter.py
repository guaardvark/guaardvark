#!/usr/bin/env python3
"""
Tracking JSON to CSV Converter for Llamanator Import
Converts bulk generation tracking JSON files to Llamanator-compatible CSV format
"""

import csv
import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class TrackingToCSVConverter:
    """
    Converts bulk generation tracking JSON files to Llamanator-compatible CSV format.
    
    Reads tracking JSON files created by the bulk CSV generator and converts them
    to CSV format that can be imported directly into WordPress via Llamanator plugin.
    """

    def __init__(self):
        """Initialize the converter."""
        self.logger = logging.getLogger(__name__)

    def convert_tracking_to_csv(
        self, 
        tracking_json_path: str, 
        output_csv_path: str,
        include_inactive: bool = False
    ) -> Dict:
        """
        Convert tracking JSON file to Llamanator-compatible CSV.
        
        Args:
            tracking_json_path: Path to tracking JSON file
            output_csv_path: Path for output CSV file
            include_inactive: Whether to include inactive rows (default: False)
            
        Returns:
            Dict with conversion results and statistics
        """
        try:
            self.logger.info(f"Converting tracking JSON to CSV: {tracking_json_path}")
            
            # Load tracking data
            tracking_data = self._load_tracking_json(tracking_json_path)
            if not tracking_data:
                return {'success': False, 'error': 'Failed to load tracking JSON'}
            
            # Extract active row records
            active_rows = self._extract_active_rows(tracking_data, include_inactive)
            if not active_rows:
                return {'success': False, 'error': 'No active rows found in tracking data'}
            
            # Write CSV file
            self._write_csv_file(active_rows, output_csv_path)
            
            # Calculate statistics
            stats = {
                'total_rows_in_tracking': len(tracking_data.get('row_records', [])),
                'active_rows_exported': len(active_rows),
                'inactive_rows_skipped': len(tracking_data.get('row_records', [])) - len(active_rows),
                'output_file': output_csv_path,
                'file_size': os.path.getsize(output_csv_path) if os.path.exists(output_csv_path) else 0
            }
            
            self.logger.info(f"CSV conversion complete: {stats['active_rows_exported']} rows exported")
            
            return {
                'success': True,
                'file_path': output_csv_path,
                'statistics': stats,
                'message': f"Converted {stats['active_rows_exported']} rows to CSV format"
            }
            
        except Exception as e:
            self.logger.error(f"CSV conversion failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'message': f"CSV conversion failed: {e}"
            }

    def _load_tracking_json(self, json_path: str) -> Optional[Dict]:
        """Load and validate tracking JSON file."""
        try:
            if not os.path.exists(json_path):
                self.logger.error(f"Tracking JSON file not found: {json_path}")
                return None
                
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            if 'row_records' not in data:
                self.logger.error("Invalid tracking JSON: missing 'row_records'")
                return None
                
            self.logger.info(f"Loaded tracking JSON with {len(data.get('row_records', []))} row records")
            return data
            
        except Exception as e:
            self.logger.error(f"Error loading tracking JSON: {e}")
            return None

    def _extract_active_rows(self, tracking_data: Dict, include_inactive: bool = False) -> List[Dict]:
        """Extract active row records from tracking data."""
        active_rows = []
        
        for record in tracking_data.get('row_records', []):
            # Check if row is active
            is_active = record.get('is_active', False)
            if not is_active and not include_inactive:
                continue
                
            # Get current row data
            current_row_data = record.get('current_row_data', {})
            if not current_row_data:
                self.logger.warning(f"Row {record.get('unique_id', 'unknown')} has no current_row_data")
                continue
                
            # Add sequence number for sorting
            current_row_data['_sequence_number'] = record.get('sequence_number', 0)
            current_row_data['_unique_id'] = record.get('unique_id', '')
            current_row_data['_status'] = record.get('status', 'unknown')
            
            active_rows.append(current_row_data)
        
        # Sort by sequence number to maintain order
        active_rows.sort(key=lambda x: x.get('_sequence_number', 0))
        
        self.logger.info(f"Extracted {len(active_rows)} active rows")
        return active_rows

    def _write_csv_file(self, active_rows: List[Dict], output_path: str):
        """Write CSV file with Llamanator-compatible format."""
        # Define CSV headers in the order Llamanator expects
        headers = [
            'ID', 'Title', 'Content', 'Excerpt', 'Category', 'Tags', 'slug',
            'Status', 'Type', 'Date', 'Featured_Image', 'Meta_Description'
        ]
        
        with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=headers, extrasaction='ignore')
            
            # Write header
            writer.writeheader()
            
            # Write data rows
            for row_data in active_rows:
                # Prepare row data for CSV
                csv_row = {}
                
                # Map tracking data to CSV columns
                csv_row['ID'] = row_data.get('ID', '')
                csv_row['Title'] = row_data.get('Title', '')
                csv_row['Content'] = row_data.get('Content', '')
                csv_row['Excerpt'] = row_data.get('Excerpt', '')
                
                # Fix category field
                category = self._fix_category_field(row_data.get('Category', ''))
                csv_row['Category'] = category
                
                csv_row['Tags'] = row_data.get('Tags', '')
                csv_row['slug'] = row_data.get('slug', '')
                csv_row['Status'] = row_data.get('Status', 'draft')
                csv_row['Type'] = row_data.get('Type', 'post')
                csv_row['Date'] = row_data.get('Date', '')
                csv_row['Featured_Image'] = row_data.get('Featured_Image', row_data.get('Featured Image', ''))
                csv_row['Meta_Description'] = row_data.get('Meta Description', row_data.get('meta_description', ''))
                
                writer.writerow(csv_row)
        
        self.logger.info(f"CSV file written to: {output_path}")

    def _fix_category_field(self, category: str) -> str:
        """Fix malformed category field from tracking data."""
        if not category:
            return ''
            
        # Handle malformed JSON array format like '[\"Law Firm\"'
        if category.startswith('[\"') and not category.endswith('\"]'):
            # Extract the category name from malformed JSON
            category = category.replace('[\"', '').replace('\"', '').strip()
        elif category.startswith('[') and category.endswith(']'):
            # Handle proper JSON array format
            try:
                categories = json.loads(category)
                if isinstance(categories, list):
                    category = '|'.join(categories)
            except (ValueError, TypeError):
                # If JSON parsing fails, clean up the string
                category = category.strip('[]').replace('"', '').replace("'", '')
        
        return category


def convert_tracking_file(tracking_json_path: str, output_csv_path: str, include_inactive: bool = False) -> Dict:
    """
    Convenience function to convert a tracking JSON file to CSV.
    
    Args:
        tracking_json_path: Path to tracking JSON file
        output_csv_path: Path for output CSV file
        include_inactive: Whether to include inactive rows
        
    Returns:
        Dict with conversion results
    """
    converter = TrackingToCSVConverter()
    return converter.convert_tracking_to_csv(tracking_json_path, output_csv_path, include_inactive)


if __name__ == '__main__':
    # Example usage
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python tracking_to_csv_converter.py <tracking_json> <output_csv> [include_inactive]")
        sys.exit(1)
    
    tracking_file = sys.argv[1]
    output_file = sys.argv[2]
    include_inactive = len(sys.argv) > 3 and sys.argv[3].lower() == 'true'
    
    result = convert_tracking_file(tracking_file, output_file, include_inactive)
    
    if result['success']:
        print(f"✅ Conversion successful: {result['message']}")
        print(f"📁 Output file: {result['file_path']}")
        print(f"📊 Statistics: {result['statistics']}")
    else:
        print(f"❌ Conversion failed: {result['error']}")
        sys.exit(1)
