# backend/utils/progress_integration_helper.py
# Progress Integration Helper - Easy integration patterns for existing services
# Provides decorators and utilities to quickly add progress tracking to existing code

import functools
import logging
from typing import Optional, Callable, Any, Dict
from contextlib import contextmanager

from .unified_progress_system import (
    get_unified_progress, 
    ProcessType, 
    ProcessStatus,
    ProgressTracker
)

logger = logging.getLogger(__name__)

def with_progress_tracking(
    process_type: ProcessType,
    description: str = "",
    progress_callback: Optional[Callable] = None
):
    """
    Decorator to add progress tracking to any function.
    
    Args:
        process_type: Type of process being tracked
        description: Description of the process
        progress_callback: Optional callback function to receive progress updates
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            progress_system = get_unified_progress()
            
            # Create progress tracker
            process_id = progress_system.create_process(
                process_type=process_type,
                description=description
            )
            
            try:
                # If progress_callback is provided, set up listener
                if progress_callback:
                    def progress_listener(event):
                        try:
                            progress_callback(event.progress, event.message, event.status)
                        except Exception as e:
                            logger.error(f"Progress callback error: {e}")
                    
                    progress_system.add_listener(progress_listener)
                
                # Execute the function
                result = func(*args, **kwargs)
                
                # Mark as complete
                progress_system.complete_process(process_id, "Complete")
                
                return result
                
            except Exception as e:
                # Mark as error
                progress_system.error_process(process_id, f"Error: {str(e)}")
                raise
            finally:
                # Clean up listener if set
                if progress_callback:
                    progress_system.remove_listener(progress_listener)
        
        return wrapper
    return decorator

@contextmanager
def progress_context(
    process_type: ProcessType,
    description: str = "",
    initial_progress: int = 0
):
    """
    Context manager for progress tracking.
    
    Usage:
        with progress_context(ProcessType.INDEXING, "Processing documents") as progress:
            progress.update(25, "Parsing files...")
            # do work
            progress.update(50, "Indexing content...")
            # do more work
            progress.update(100, "Complete!")
    """
    progress_system = get_unified_progress()
    process_id = progress_system.create_process(
        process_type=process_type,
        description=description
    )
    
    class ProgressContext:
        def update(self, progress: int, message: str):
            progress_system.update_process(process_id, progress, message)
        
        def complete(self, message: str = "Complete"):
            progress_system.complete_process(process_id, message)
        
        def error(self, message: str = "Error"):
            progress_system.error_process(process_id, message)
        
        def cancel(self, message: str = " Cancelled"):
            progress_system.cancel_process(process_id, message)
    
    progress = ProgressContext()
    
    try:
        if initial_progress > 0:
            progress.update(initial_progress, "Starting...")
        yield progress
        progress.complete()
    except Exception as e:
        progress.error(f"Error: {str(e)}")
        raise

def track_bulk_operation(
    total_items: int,
    process_type: ProcessType,
    description: str = ""
):
    """
    Decorator for bulk operations with item-based progress tracking.
    
    Args:
        total_items: Total number of items to process
        process_type: Type of process
        description: Description of the process
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            progress_system = get_unified_progress()
            process_id = progress_system.create_process(
                process_type=process_type,
                description=description
            )
            
            processed_items = 0
            
            def update_progress(item_count: int, message: str = ""):
                nonlocal processed_items
                processed_items = item_count
                progress_percent = int((processed_items / total_items) * 100)
                progress_system.update_process(
                    process_id, 
                    progress_percent, 
                    f"{message} ({processed_items}/{total_items})"
                )
            
            try:
                # Pass the progress updater to the function
                result = func(*args, **kwargs, update_progress=update_progress)
                progress_system.complete_process(process_id, f"Completed {total_items} items")
                return result
            except Exception as e:
                progress_system.error_process(process_id, f"Error after {processed_items} items: {str(e)}")
                raise
        
        return wrapper
    return decorator

def track_file_operation(
    process_type: ProcessType,
    description: str = ""
):
    """
    Decorator for file operations with byte-based progress tracking.
    
    Args:
        process_type: Type of process
        description: Description of the process
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            progress_system = get_unified_progress()
            process_id = progress_system.create_process(
                process_type=process_type,
                description=description
            )
            
            def update_progress(bytes_processed: int, total_bytes: int, message: str = ""):
                progress_percent = int((bytes_processed / total_bytes) * 100) if total_bytes > 0 else 0
                progress_system.update_process(
                    process_id,
                    progress_percent,
                    f"{message} ({bytes_processed}/{total_bytes} bytes)"
                )
            
            try:
                result = func(*args, **kwargs, update_progress=update_progress)
                progress_system.complete_process(process_id, "File operation complete")
                return result
            except Exception as e:
                progress_system.error_process(process_id, f"File operation error: {str(e)}")
                raise
        
        return wrapper
    return decorator

def track_stage_operation(
    stages: list,
    process_type: ProcessType,
    description: str = ""
):
    """
    Decorator for multi-stage operations.
    
    Args:
        stages: List of stage names
        process_type: Type of process
        description: Description of the process
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            progress_system = get_unified_progress()
            process_id = progress_system.create_process(
                process_type=process_type,
                description=description
            )
            
            total_stages = len(stages)
            
            def update_stage(stage_index: int, message: str = ""):
                if 0 <= stage_index < total_stages:
                    stage_name = stages[stage_index]
                    progress_percent = int(((stage_index + 1) / total_stages) * 100)
                    progress_system.update_process(
                        process_id,
                        progress_percent,
                        f"{stage_name}: {message}"
                    )
            
            try:
                result = func(*args, **kwargs, update_stage=update_stage)
                progress_system.complete_process(process_id, "All stages complete")
                return result
            except Exception as e:
                progress_system.error_process(process_id, f"Stage operation error: {str(e)}")
                raise
        
        return wrapper
    return decorator

# Migration helpers for existing code
def migrate_progress_manager_calls():
    """
    Helper function to identify and migrate existing progress_manager calls.
    This provides a mapping of old progress_manager functions to new unified system.
    """
    migration_map = {
        # Old progress_manager functions -> New unified system functions
        "progress_manager.start_job": "get_unified_progress().create_process",
        "progress_manager.update_job_status": "get_unified_progress().update_process",
        "progress_manager.complete_job": "get_unified_progress().complete_process",
        
        # Old progress_emitter functions -> New unified system functions
        "progress_emitter.create_progress_tracker": "get_unified_progress().create_process",
        "progress_emitter.update_progress": "get_unified_progress().update_process",
        "progress_emitter.complete_progress": "get_unified_progress().complete_process",
        "progress_emitter.error_progress": "get_unified_progress().error_process",
    }
    
    return migration_map

def create_progress_wrapper(service_name: str):
    """
    Create a progress wrapper for a specific service.
    This provides a service-specific interface to the unified progress system.
    """
    progress_system = get_unified_progress()
    
    class ServiceProgressWrapper:
        def __init__(self, service_name: str):
            self.service_name = service_name
            self.active_processes = {}
        
        def start_operation(self, operation_type: str, description: str = ""):
            """Start a new operation for this service"""
            try:
                process_type = ProcessType(operation_type)
            except ValueError:
                process_type = ProcessType.UNKNOWN
            
            process_id = progress_system.create_process(
                process_type=process_type,
                description=f"{self.service_name}: {description}"
            )
            
            self.active_processes[process_id] = {
                "type": operation_type,
                "description": description
            }
            
            return process_id
        
        def update_operation(self, process_id: str, progress: int, message: str):
            """Update an operation's progress"""
            if process_id in self.active_processes:
                progress_system.update_process(process_id, progress, message)
        
        def complete_operation(self, process_id: str, message: str = "Complete"):
            """Complete an operation"""
            if process_id in self.active_processes:
                progress_system.complete_process(process_id, message)
                del self.active_processes[process_id]
        
        def error_operation(self, process_id: str, message: str = "Error"):
            """Mark an operation as error"""
            if process_id in self.active_processes:
                progress_system.error_process(process_id, message)
                del self.active_processes[process_id]
        
        def get_active_operations(self):
            """Get all active operations for this service"""
            return list(self.active_processes.keys())
    
    return ServiceProgressWrapper(service_name)

# Example usage patterns
def example_usage():
    """
    Example usage patterns for the progress integration helpers.
    """
    
    # Example 1: Simple function with progress tracking
    @with_progress_tracking(ProcessType.INDEXING, "Processing documents")
    def process_documents(documents):
        # Function implementation
        pass
    
    # Example 2: Bulk operation with item tracking
    @track_bulk_operation(100, ProcessType.FILE_GENERATION, "Generating CSV files")
    def generate_csv_files(data, update_progress):
        for i, item in enumerate(data):
            # Process item
            update_progress(i + 1, f"Processing item {i + 1}")
    
    # Example 3: File operation with byte tracking
    @track_file_operation(ProcessType.UPLOAD, "Uploading file")
    def upload_file(file_path, update_progress):
        # File upload implementation
        pass
    
    # Example 4: Multi-stage operation
    @track_stage_operation(
        ["Parse", "Process", "Index", "Save"],
        ProcessType.DOCUMENT_PROCESSING,
        "Processing document"
    )
    def process_document(document, update_stage):
        update_stage(0, "Parsing document structure")
        # Parse document
        
        update_stage(1, "Processing content")
        # Process content
        
        update_stage(2, "Creating index")
        # Create index
        
        update_stage(3, "Saving to database")
        # Save to database
    
    # Example 5: Context manager usage
    def process_with_context():
        with progress_context(ProcessType.ANALYSIS, "Analyzing data") as progress:
            progress.update(25, "Loading data...")
            # Load data
            
            progress.update(50, "Processing data...")
            # Process data
            
            progress.update(75, "Generating report...")
            # Generate report
            
            progress.update(100, "Complete!")
    
    # Example 6: Service-specific wrapper
    def use_service_wrapper():
        indexing_progress = create_progress_wrapper("IndexingService")
        
        process_id = indexing_progress.start_operation("indexing", "Indexing documents")
        
        try:
            # Do indexing work
            indexing_progress.update_operation(process_id, 50, "Halfway done")
            # More work
            indexing_progress.complete_operation(process_id, "Indexing complete")
        except Exception as e:
            indexing_progress.error_operation(process_id, f"Indexing failed: {e}") 