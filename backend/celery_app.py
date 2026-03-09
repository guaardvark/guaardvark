import os
import logging

from celery import Celery
from flask import Flask

def create_minimal_celery_flask_app():
    minimal_app = Flask(__name__)

    # Use the same DATABASE_URL as the main app (PostgreSQL)
    database_url = os.environ.get(
        'DATABASE_URL',
        'postgresql://guaardvark:guaardvark@localhost:5432/guaardvark'
    )

    minimal_app.config.update({
        'SQLALCHEMY_DATABASE_URI': database_url,
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
        'SECRET_KEY': os.environ.get('SECRET_KEY', 'dev-secret-key'),
    })

    from backend.models import db
    db.init_app(minimal_app)

    return minimal_app

logger = logging.getLogger(__name__)

def create_celery_app():
    import multiprocessing
    
    try:
        current_method = multiprocessing.get_start_method(allow_none=True)
        if current_method is None or current_method != 'spawn':
            multiprocessing.set_start_method('spawn', force=True)
            logger.info(f"Set multiprocessing start method to 'spawn'")
        else:
            logger.info(f"Multiprocessing start method already set to '{current_method}'")
    except RuntimeError as e:
        logger.warning(f"Could not set multiprocessing start method: {e}")
    
    broker_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    result_backend = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

    celery_app = Celery(
        __name__,
        broker=broker_url,
        backend=result_backend,
    )

    try:
        celery_app.conf.update(
        broker_connection_retry_on_startup=True,
        
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='UTC',
        enable_utc=True,
        
        OUTPUT_DIR=os.environ.get('GUAARDVARK_OUTPUT_DIR', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'outputs')),
        
        task_routes={
            'backend.celery_tasks_isolated.ping': {'queue': 'health'},
            'backend.celery_tasks_isolated.index_document_task': {'queue': 'indexing'},
            'backend.celery_tasks_isolated.bulk_import_documents_task': {'queue': 'indexing'},
            'backend.celery_tasks_isolated.generate_bulk_csv_v2_task': {'queue': 'generation'},
            'backend.tasks.proven_csv_generation.generate_proven_csv_task': {'queue': 'generation'},
            'backend.tasks.unified_task_executor.execute_unified_task': {'queue': 'default'},
            'backend.tasks.task_scheduler_celery.check_scheduled_tasks': {'queue': 'default'},
            'backend.tasks.task_scheduler_celery.recover_stuck_tasks': {'queue': 'default'},
            'backend.tasks.task_scheduler_celery.scheduler_health_check': {'queue': 'health'},
            'training.finetune_model': {'queue': 'training_gpu'},
            'training.export_gguf': {'queue': 'training_gpu'},
            'training.parse_transcripts': {'queue': 'training'},
            'training.filter_dataset': {'queue': 'training'},
            'training.import_ollama': {'queue': 'training'},
            'training.full_pipeline': {'queue': 'training_gpu'},
            'training.*': {'queue': 'training'},
            'maintenance.daily_backup': {'queue': 'default'},
            'backend.celery_tasks_isolated.*': {'queue': 'default'},
            'backend.tasks.*': {'queue': 'default'},
        },

        beat_schedule={
            'check-scheduled-tasks': {
                'task': 'backend.tasks.task_scheduler_celery.check_scheduled_tasks',
                'schedule': 60.0,
                'options': {'queue': 'default'},
            },
            'recover-stuck-tasks': {
                'task': 'backend.tasks.task_scheduler_celery.recover_stuck_tasks',
                'schedule': 300.0,
                'options': {'queue': 'default'},
            },
            'scheduler-health-check': {
                'task': 'backend.tasks.task_scheduler_celery.scheduler_health_check',
                'schedule': 600.0,
                'options': {'queue': 'health'},
            },
            'daily-backup': {
                'task': 'maintenance.daily_backup',
                'schedule': 86400.0,  # 24 hours
                'options': {'queue': 'default'},
            },
        },
        
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        worker_pool_restarts=False,
        
        task_soft_time_limit=1800,
        task_time_limit=2400,
        worker_disable_rate_limits=False,
        
        worker_max_tasks_per_child=50,
        worker_max_memory_per_child=1024000,
        
        worker_pool='solo',
        worker_concurrency=1,
        
        worker_redirect_stdouts=False,
        worker_redirect_stdouts_level='INFO',
        worker_log_format='[%(asctime)s: %(levelname)s/%(processName)s] %(message)s',
        worker_task_log_format='[%(asctime)s: %(levelname)s/%(processName)s][%(task_name)s(%(task_id)s)] %(message)s',
        
        result_expires=259200,
        result_backend_transport_options={
            'master_name': 'mymaster',
            'visibility_timeout': 172800,
        },
        
        task_reject_on_worker_lost=True,
        task_ignore_result=False,
        
        broker_transport_options={
            'visibility_timeout': 172800,
            'fanout_prefix': True,
            'fanout_patterns': True,
        },
        
            task_create_missing_queues=True,
            task_default_queue='default',
            task_default_exchange='default',
            task_default_exchange_type='direct',
            task_default_routing_key='default',
        )
        logger.info("Celery configuration updated successfully")
    except Exception as e:
        logger.error(f"Error updating Celery configuration: {e}")
        raise

    minimal_app = create_minimal_celery_flask_app()
    
    TaskBase = celery_app.Task

    class ContextTask(TaskBase):
        abstract = True

        def __call__(self, *args, **kwargs):
            with minimal_app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)

    celery_app.Task = ContextTask
    
    try:
        from backend.celery_tasks_isolated import ping, index_document_task, generate_bulk_csv_v2_task, bulk_import_documents_task

        ping_task = celery_app.task(ping, name='backend.celery_tasks_isolated.ping')
        index_document_task_registered = celery_app.task(index_document_task, name='backend.celery_tasks_isolated.index_document_task')
        generate_bulk_csv_v2_task_registered = celery_app.task(generate_bulk_csv_v2_task, name='backend.celery_tasks_isolated.generate_bulk_csv_v2_task')
        bulk_import_documents_task_registered = celery_app.task(bulk_import_documents_task, name='backend.celery_tasks_isolated.bulk_import_documents_task')

        import backend.celery_tasks_isolated
        backend.celery_tasks_isolated.ping = ping_task
        backend.celery_tasks_isolated.index_document_task = index_document_task_registered
        backend.celery_tasks_isolated.generate_bulk_csv_v2_task = generate_bulk_csv_v2_task_registered
        backend.celery_tasks_isolated.bulk_import_documents_task = bulk_import_documents_task_registered

        logger.info(f"Isolated tasks registered successfully: ping, index_document_task, generate_bulk_csv_v2_task, bulk_import_documents_task")
    except ImportError as e:
        logger.error(f"Could not import isolated tasks: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error registering isolated tasks: {e}", exc_info=True)
    
    try:
        from backend.tasks.proven_csv_generation import generate_proven_csv_task
        logger.info("Proven CSV generation task imported successfully")
    except ImportError as e:
        logger.warning(f"Could not import proven CSV generation task: {e}")

    try:
        from backend.tasks import training_tasks
        logger.info("Training tasks imported successfully")
    except ImportError as e:
        logger.warning(f"Could not import training tasks: {e}")

    try:
        from backend.tasks.unified_task_executor import execute_unified_task
        logger.info("Unified task executor imported successfully")
    except ImportError as e:
        logger.warning(f"Could not import unified task executor: {e}")

    try:
        from backend.tasks.task_scheduler_celery import (
            check_scheduled_tasks,
            recover_stuck_tasks,
            scheduler_health_check
        )
        logger.info("Task scheduler Celery Beat tasks imported successfully")
    except ImportError as e:
        logger.warning(f"Could not import task scheduler Beat tasks: {e}")

    logger.info("Celery app configured with enhanced performance settings and Beat schedule")
    return celery_app


celery = create_celery_app()
