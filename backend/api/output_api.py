#!/usr/bin/env python3
"""
Output Management API
Flask endpoints for managing bulk generation tracking files
Supports listing, viewing, downloading (CSV/XML), and deleting tracking files
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from flask import Blueprint, current_app, jsonify, request, send_file, abort
from werkzeug.utils import secure_filename

# Import existing converters
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'utils'))
from tracking_to_csv_converter import convert_tracking_file as convert_to_csv
from tracking_to_xml_converter import convert_tracking_file as convert_to_xml

output_bp = Blueprint("output_api", __name__, url_prefix="/api/outputs")
logger = logging.getLogger(__name__)

# Configuration
TRACKING_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'outputs', 'tracking')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'outputs')

def get_tracking_files() -> List[Dict]:
    """Get list of all tracking JSON files with metadata."""
    if not os.path.exists(TRACKING_DIR):
        return []
    
    files = []
    for filename in os.listdir(TRACKING_DIR):
        if filename.endswith('.json') and 'tracking' in filename:
            filepath = os.path.join(TRACKING_DIR, filename)
            try:
                stat = os.stat(filepath)
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Extract metadata
                metadata = data.get('metadata', {})
                row_records = data.get('row_records', [])
                
                # Calculate statistics
                total_rows = len(row_records)
                active_rows = len([r for r in row_records if r.get('is_active', False)])
                inactive_rows = total_rows - active_rows
                replaced_rows = len([r for r in row_records if r.get('replacement_count', 0) > 0])
                # Failed rows are the inactive rows (not generated successfully)
                failed_rows = inactive_rows
                
                # Check if this job has retry files
                has_retries = len(metadata.get('retry_files', [])) > 0

                # Extract job parameters
                job_params = metadata.get('job_parameters', {})

                files.append({
                    'filename': filename,
                    'job_id': metadata.get('job_id', 'unknown'),
                    'export_timestamp': metadata.get('export_timestamp', ''),
                    'target_row_count': metadata.get('target_row_count', 0),
                    'total_rows': total_rows,
                    'active_rows': active_rows,
                    'inactive_rows': inactive_rows,
                    'replaced_rows': replaced_rows,
                    'failed_rows': failed_rows,
                    'success_rate': (active_rows / total_rows * 100) if total_rows > 0 else 0,
                    'file_size': stat.st_size,
                    'created_at': datetime.fromtimestamp(stat.st_ctime).isoformat(),
                    'modified_at': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    'has_retries': has_retries,
                    'model_name': job_params.get('model_name', 'Unknown'),
                    'client': job_params.get('client', ''),
                    'project': job_params.get('project', '')
                })
            except Exception as e:
                logger.error(f"Error reading tracking file {filename}: {e}")
                continue
    
    # Sort by creation time (newest first)
    files.sort(key=lambda x: x['created_at'], reverse=True)
    return files

@output_bp.route('/generated_images/<image_name>', methods=['GET'])
def serve_generated_image(image_name):
    """Serve generated images from the outputs directory."""
    try:
        # Security: only allow image filenames
        safe_name = secure_filename(image_name)
        if not safe_name:
            abort(400, "Invalid filename")
        image_dir = os.path.join(OUTPUT_DIR, "generated_images")
        image_path = os.path.join(image_dir, safe_name)
        if not os.path.exists(image_path):
            abort(404, "Image not found")
        return send_file(image_path, mimetype="image/png")
    except Exception as e:
        logger.error(f"Error serving generated image {image_name}: {e}")
        abort(404, "Image not found")


@output_bp.route('/generated_animations/<filename>', methods=['GET'])
def serve_generated_animation(filename):
    """Serve generated animations (GIF/MP4) from the outputs directory."""
    try:
        safe_name = secure_filename(filename)
        if not safe_name:
            abort(400, "Invalid filename")
        anim_dir = os.path.join(OUTPUT_DIR, "generated_animations")
        file_path = os.path.join(anim_dir, safe_name)
        if not os.path.exists(file_path):
            abort(404, "Animation not found")
        if safe_name.endswith('.gif'):
            mimetype = "image/gif"
        elif safe_name.endswith('.mp4'):
            mimetype = "video/mp4"
        else:
            mimetype = "application/octet-stream"
        return send_file(file_path, mimetype=mimetype)
    except Exception as e:
        logger.error(f"Error serving generated animation {filename}: {e}")
        abort(404, "Animation not found")


@output_bp.route('/', methods=['GET'])
def list_outputs():
    """List all tracking files with metadata."""
    try:
        files = get_tracking_files()
        return jsonify({
            'success': True,
            'outputs': files,
            'total': len(files)
        })
    except Exception as e:
        logger.error(f"Error listing outputs: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@output_bp.route('/<filename>', methods=['GET'])
def get_output_details(filename):
    """Get detailed content of a specific tracking file."""
    try:
        # Security check
        if not filename.endswith('.json') or '..' in filename or '/' in filename:
            abort(400, "Invalid filename")
        
        filepath = os.path.join(TRACKING_DIR, filename)
        if not os.path.exists(filepath):
            abort(404, "Tracking file not found")
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return jsonify({
            'success': True,
            'data': data
        })
    except Exception as e:
        logger.error(f"Error getting output details for {filename}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@output_bp.route('/<filename>/csv', methods=['GET'])
def download_csv(filename):
    """Convert tracking JSON to CSV and return as download."""
    try:
        # Security check
        if not filename.endswith('.json') or '..' in filename or '/' in filename:
            abort(400, "Invalid filename")
        
        tracking_path = os.path.join(TRACKING_DIR, filename)
        if not os.path.exists(tracking_path):
            abort(404, "Tracking file not found")
        
        # Generate unique CSV filename
        base_name = filename.replace('_tracking_', '_content_').replace('.json', '.csv')
        csv_path = os.path.join(OUTPUT_DIR, base_name)
        
        # Convert using existing converter
        result = convert_to_csv(tracking_path, csv_path)
        
        if not result.get('success', False):
            abort(500, f"CSV conversion failed: {result.get('error', 'Unknown error')}")
        
        # Return file for download
        return send_file(
            csv_path,
            as_attachment=True,
            download_name=base_name,
            mimetype='text/csv'
        )
    except Exception as e:
        logger.error(f"Error downloading CSV for {filename}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@output_bp.route('/<filename>/xml', methods=['GET'])
def download_xml(filename):
    """Convert tracking JSON to XML and return as download."""
    try:
        # Security check
        if not filename.endswith('.json') or '..' in filename or '/' in filename:
            abort(400, "Invalid filename")
        
        tracking_path = os.path.join(TRACKING_DIR, filename)
        if not os.path.exists(tracking_path):
            abort(404, "Tracking file not found")
        
        # Generate unique XML filename
        base_name = filename.replace('_tracking_', '_content_').replace('.json', '.xml')
        xml_path = os.path.join(OUTPUT_DIR, base_name)
        
        # Convert using existing converter
        result = convert_to_xml(tracking_path, xml_path)
        
        if not result.get('success', False):
            abort(500, f"XML conversion failed: {result.get('error', 'Unknown error')}")
        
        # Return file for download
        return send_file(
            xml_path,
            as_attachment=True,
            download_name=base_name,
            mimetype='application/xml'
        )
    except Exception as e:
        logger.error(f"Error downloading XML for {filename}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@output_bp.route('/<filename>', methods=['DELETE'])
def delete_output(filename):
    """Delete a tracking file."""
    try:
        # Security check
        if not filename.endswith('.json') or '..' in filename or '/' in filename:
            abort(400, "Invalid filename")
        
        filepath = os.path.join(TRACKING_DIR, filename)
        if not os.path.exists(filepath):
            abort(404, "Tracking file not found")
        
        # Delete the file
        os.remove(filepath)
        
        logger.info(f"Deleted tracking file: {filename}")
        return jsonify({
            'success': True,
            'message': f'Tracking file {filename} deleted successfully'
        })
    except Exception as e:
        logger.error(f"Error deleting output {filename}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@output_bp.route('/<filename>/merged-csv', methods=['GET'])
def get_merged_csv(filename):
    """
    Download merged CSV with original + retry results.
    Returns only successful rows (active rows).
    """
    try:
        if not filename or not filename.endswith('.json'):
            abort(400, "Invalid filename")
        
        filepath = os.path.join(TRACKING_DIR, filename)
        if not os.path.exists(filepath):
            abort(404, "Tracking file not found")
        
        # Import TrackingAnalyzer
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'utils'))
        from tracking_analyzer import TrackingAnalyzer
        
        # Load original tracking file
        analyzer = TrackingAnalyzer(filepath)
        
        # Find retry tracking files (look for files with same job_id prefix)
        metadata = analyzer.data.get('metadata', {})
        original_job_id = metadata.get('job_id', '')
        
        retry_files = []
        if original_job_id:
            # Look for retry files with same job_id prefix
            for retry_filename in os.listdir(TRACKING_DIR):
                if (retry_filename.startswith('retry_') and 
                    retry_filename.endswith('.json') and
                    retry_filename != filename):
                    retry_filepath = os.path.join(TRACKING_DIR, retry_filename)
                    try:
                        with open(retry_filepath, 'r', encoding='utf-8') as f:
                            retry_data = json.load(f)
                        retry_metadata = retry_data.get('metadata', {})
                        # Check if this retry is linked to our original job
                        if (retry_metadata.get('original_job_id') == original_job_id or
                            retry_metadata.get('job_id', '').startswith('retry_')):
                            retry_files.append(retry_filepath)
                    except Exception as e:
                        logger.warning(f"Error reading retry file {retry_filename}: {e}")
                        continue
        
        logger.info(f"Found {len(retry_files)} retry files for {filename}")
        
        # Merge tracking files
        merged_active_rows = analyzer.merge_tracking_files(retry_files)
        
        if not merged_active_rows:
            abort(404, "No active rows found in tracking files")
        
        # Convert merged active rows to CSV format
        csv_content = convert_merged_rows_to_csv(merged_active_rows)
        
        # Create temporary CSV file
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as temp_file:
            temp_file.write(csv_content)
            temp_file_path = temp_file.name
        
        # Generate download filename
        base_name = filename.replace('_tracking_', '_merged_').replace('.json', '.csv')
        
        logger.info(f"Generated merged CSV with {len(merged_active_rows)} rows")
        return send_file(
            temp_file_path,
            as_attachment=True,
            download_name=base_name,
            mimetype='text/csv'
        )
        
    except Exception as e:
        logger.error(f"Error generating merged CSV for {filename}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def convert_merged_rows_to_csv(active_rows: List[Dict]) -> str:
    """Convert merged active rows to CSV format."""
    if not active_rows:
        return ""
    
    # Get CSV headers from first active row
    first_row = active_rows[0]
    current_data = first_row.get('current_row_data', {})
    headers = list(current_data.keys())
    
    # Build CSV content
    csv_lines = [','.join(f'"{header}"' for header in headers)]
    
    for row in active_rows:
        current_data = row.get('current_row_data', {})
        if current_data:
            # Escape and quote values
            values = []
            for header in headers:
                value = current_data.get(header, '')
                # Escape quotes and wrap in quotes
                escaped_value = str(value).replace('"', '""')
                values.append(f'"{escaped_value}"')
            csv_lines.append(','.join(values))
    
    return '\n'.join(csv_lines)
