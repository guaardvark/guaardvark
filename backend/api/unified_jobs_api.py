# backend/api/unified_jobs_api.py
# Unified Job Creation API - Routes all job types through single system with progress tracking

import logging
import json
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app

from backend.utils.unified_progress_system import get_unified_progress, ProcessType
from backend.utils.db_utils import ensure_db_session_cleanup, DatabaseConnectionManager
from backend.models import db, Task, Client, Project

unified_jobs_bp = Blueprint("unified_jobs", __name__, url_prefix="/api/jobs")
logger = logging.getLogger(__name__)

@unified_jobs_bp.route("/create", methods=["POST"])
@ensure_db_session_cleanup
def create_unified_job():
    """
    Create a unified job that routes to appropriate backend systems based on workflow type
    """
    logger.info("API: Received POST /api/jobs/create request")
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No job data provided"}), 400
            
        # Validate required fields
        required_fields = ['name', 'type', 'client_id']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        workflow_config = data.get('workflow_config', {})
        execution_type = workflow_config.get('execution_type', 'standard_task')
        
        # Create progress tracking first
        progress_system = get_unified_progress()
        process_type = ProcessType.FILE_GENERATION if execution_type == 'bulk_csv_generation' else ProcessType.TASK_PROCESSING
        
        job_id = progress_system.create_process(
            process_type,
            f"Creating {data['type']} job: {data['name']}"
        )
        
        # Create database record using unified metadata system
        from backend.utils.unified_job_metadata import unified_job_metadata
        
        task_id = unified_job_metadata.create_job_record(
            job_type=data['type'],
            name=data['name'],
            description=data.get('description', ''),
            client_id=data.get('client_id'),
            project_id=data.get('project_id'),
            workflow_config=workflow_config,
            metadata=data.get('metadata', {}),
            progress_job_id=job_id
        )
        
        if not task_id:
            return jsonify({"error": "Failed to create job record"}), 500
            
        # Update progress with task creation
        progress_system.update_process(job_id, 10, f"Task {task_id} created, routing to {execution_type}")
        
        # Route to appropriate backend system based on execution type
        result = route_job_execution(task_id, job_id, execution_type, workflow_config, data)
        
        return jsonify({
            "success": True,
            "message": "Unified job created successfully",
            "task_id": task_id,
            "job_id": job_id,
            "execution_type": execution_type,
            "routing_result": result
        }), 201
        
    except Exception as e:
        logger.error(f"Error creating unified job: {e}", exc_info=True)
        return jsonify({"error": f"Failed to create job: {str(e)}"}), 500

def route_job_execution(task_id, job_id, execution_type, workflow_config, original_data):
    """
    Route job execution to appropriate backend system
    """
    try:
        if execution_type == 'bulk_csv_generation':
            # Route to bulk CSV generation system using existing API
            logger.info(f"Routing bulk CSV generation for task {task_id}, job {job_id}")
            
            # Use the existing bulk generation API infrastructure
            from backend.celery_tasks_isolated import generate_bulk_csv_v2_task
            
            # Prepare configuration compatible with bulk generation system
            csv_config = {
                'output_filename': workflow_config.get('output_filename'),
                'client': workflow_config.get('client_name', 'Client'),
                'project': workflow_config.get('context_variables', {}).get('project', 'Project'),
                'website': workflow_config.get('context_variables', {}).get('website', 'website.com'),
                'topics': workflow_config.get('topics', 'auto'),
                'num_items': workflow_config.get('quantity', 100),
                'target_word_count': workflow_config.get('target_word_count', 500),
                'concurrent_workers': workflow_config.get('concurrent_workers', 10),
                'batch_size': workflow_config.get('batch_size', 25),
                'resume_from_id': workflow_config.get('resume_from_id'),
                'task_id': task_id,  # Link back to task for updates
                'job_id': job_id,    # Link to progress tracking
            }
            
            # Update the task with the configuration
            try:
                from backend.models import Task, db
                task = db.session.get(Task, task_id)
                if task:
                    task.output_filename = csv_config['output_filename']
                    task.status = 'in-progress'
                    db.session.commit()
                    logger.info(f"Updated task {task_id} with output filename: {csv_config['output_filename']}")
            except Exception as db_error:
                logger.warning(f"Failed to update task {task_id}: {db_error}")
            
            # Submit to Celery for background processing
            celery_task = generate_bulk_csv_v2_task.apply_async(
                (csv_config, job_id), 
                queue='generation'
            )
            
            logger.info(f"Submitted bulk CSV generation to Celery: task_id={celery_task.id}")
            
            return {
                "routed_to": "bulk_csv_generation",
                "celery_task_id": celery_task.id,
                "config": csv_config,
                "estimated_duration_minutes": round((csv_config['num_items'] * 3) / 60, 1),  # ~3 seconds per item
                "file_will_be_available_at": f"/api/tasks/{task_id}/download"
            }
            
        elif execution_type == 'website_analysis':
            # Route to website analysis system
            # TODO: Implement website analysis routing
            return {
                "routed_to": "website_analysis",
                "status": "not_implemented"
            }
            
        elif execution_type == 'sequential_tasks':
            # Route to sequential task processing
            # TODO: Implement sequential processing routing
            return {
                "routed_to": "sequential_tasks", 
                "status": "not_implemented"
            }
            
        else:
            # Standard task processing
            return {
                "routed_to": "standard_task",
                "status": "queued_for_processing"
            }
            
    except Exception as e:
        logger.error(f"Error routing job execution: {e}")
        return {
            "routed_to": "error",
            "error": str(e)
        }

@unified_jobs_bp.route("/<int:task_id>/status", methods=["GET"])
@ensure_db_session_cleanup
def get_job_status(task_id):
    """
    Get comprehensive job status including progress and database state
    """
    try:
        with DatabaseConnectionManager():
            task = db.session.get(Task, task_id)
            if not task:
                return jsonify({"error": "Task not found"}), 404
                
            # Get progress information
            progress_system = get_unified_progress()
            progress = None
            if task.job_id:
                try:
                    progress = progress_system.get_process(task.job_id)
                except Exception:
                    pass
                    
            return jsonify({
                "task_id": task.id,
                "job_id": task.job_id,
                "name": task.name,
                "type": task.type,
                "status": task.status,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "progress": {
                    "percentage": progress.progress if progress else 0,
                    "status": progress.status.value if progress else "unknown",
                    "message": progress.message if progress else "No progress data",
                    "last_update": progress.timestamp.isoformat() if progress and progress.timestamp else None
                } if progress else None,
                "workflow_config": json.loads(task.workflow_config) if task.workflow_config else None,
            })
            
    except Exception as e:
        logger.error(f"Error getting job status: {e}")
        return jsonify({"error": f"Failed to get job status: {str(e)}"}), 500