import os
import logging

from celery import Celery
from flask import Flask

# Under memory pressure the kernel must kill THIS worker, never the desktop
# (2026-08-04 client box lockups). Early, before any heavy allocation.
try:
    from backend.oom_priority import apply_oom_score_adj
    apply_oom_score_adj()
except Exception:
    pass

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
        broker_connection_retry=True,
        broker_connection_max_retries=None,  # retry forever
        
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
            'indexing.resume_pending_tick': {'queue': 'indexing'},
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
            'social_outreach.*': {'queue': 'default'},
            'memory.*': {'queue': 'default'},
            # Custom-named like production.* — explicit route so the per-clip
            # tail-call dispatch can't fall to the unconsumed 'celery' queue.
            'music_video.*': {'queue': 'default'},
            # video_render_tasks.* were dispatched to an unconsumed 'renders' queue;
            # route explicitly so send_task(..., queue=...) overrides still land on main.
            'video_render_tasks.*': {'queue': 'default'},
            # google_indexing.* tasks use custom names (not backend.tasks.*), so
            # they need their own route — without it the on-demand
            # google_indexing.submit_batch_for_site dispatched via .delay() falls
            # to Celery's default 'celery' queue, which no worker consumes, and
            # the "Submit to Index" button silently produces a ghost task.
            'google_indexing.*': {'queue': 'default'},
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
            # Documents left PENDING by an interrupted run — a reboot, a killed
            # worker — would otherwise sit there indefinitely, because indexing
            # progress is durable but nothing restarted it. Deliberately slow and
            # small: the task yields to the Pause Indexing toggle and to GPU
            # pressure, so it catches up during quiet periods instead of competing
            # with generation work.
            'resume-pending-indexing': {
                'task': 'indexing.resume_pending_tick',
                'schedule': float(os.environ.get("GUAARDVARK_INDEX_RESUME_INTERVAL_S", 900)),
                'options': {'queue': 'indexing'},
            },
            'google-indexing-drip': {
                'task': 'google_indexing.drip_tick',
                'schedule': 900.0,  # every 15 min; per-site daily quota caps real submissions
                'options': {'queue': 'default'},
            },
            # Cluster heartbeat sweeper is scheduled conditionally below — only
            # when CLUSTER_ROLE == "master". On non-master nodes the task just
            # early-returns {'skipped': 'not_master'}, so scheduling it every few
            # seconds only spams the log. See the conditional block after task
            # registration.
            # Social outreach loops — beat-driven so they run unattended.
            # Cadence here is the upper bound; kill_switch.cadence_allows_post
            # enforces the actual 30-min-per-platform / 8-per-day caps.
            'social-outreach-reddit-tick': {
                'task': 'social_outreach.tick_reddit_outreach',
                'schedule': 2700.0,  # 45 minutes
                'options': {'queue': 'default'},
            },
            'social-outreach-self-share-tick': {
                'task': 'social_outreach.tick_self_share',
                'schedule': 14400.0,  # 4 hours
                'options': {'queue': 'default'},
            },
            'social-outreach-process-approved': {
                'task': 'social_outreach.tick_process_approved_drafts',
                'schedule': 60.0,  # 1 minute
                'options': {'queue': 'default'},
            },
            # Reap Subjects stuck in training_status='training' past the train cap
            # (worker died mid-run → Celery task lost → never marked failed). Flips
            # them to 'failed' so the Cast UI re-enables the Train button.
            'reap-stuck-lora-training': {
                'task': 'lora_trainer.reap_stuck_training',
                'schedule': 300.0,  # every 5 min
                'options': {'queue': 'default'},
            },
            # YouTube reply-scrape recon is intentionally NOT scheduled until
            # recon._fetch_recent_replies_to_guaardvark is implemented (stub
            # returns []). Fire on demand via /run-pass or hand-seed via
            # recon.enqueue_youtube_reply_candidate.
            # Reap outreach rows stuck at status=processing after worker kill.
            'social-outreach-reap-stuck-processing': {
                'task': 'social_outreach.tick_reap_stuck_processing',
                'schedule': 300.0,  # every 5 min
                'options': {'queue': 'default'},
            },
            # Reap memory_state_<session_id> rows from system_setting that
            # haven't been touched in GUAARDVARK_MEMORY_RETENTION_DAYS days.
            # MemoryManager writes these on every chat turn; without this
            # sweep they grow unbounded.
            'memory-cleanup-old-session-state': {
                'task': 'memory.cleanup_old_session_state',
                'schedule': 86400.0,  # 24 hours — daily is plenty for a 30-day retention
                'options': {'queue': 'default'},
            },
            # Scan belief_update memories and stage PendingFix rows where ≥N
            # sessions have agreed a knowledge-file line is wrong. Idempotent
            # (skips groups that already have an open proposal) and review-
            # gated (PendingFix never auto-applies). The original opt-in
            # design (lesson_reconciler.py:22-26) feared auto-fired file edits
            # — those don't happen here. Disable with
            # GUAARDVARK_RECONCILER_BEAT_DISABLED=1 if you want the old cadence
            # back without removing the schedule entry.
            'memory-reconcile-belief-updates': {
                'task': 'memory.reconcile_belief_updates',
                'schedule': 21600.0,  # 6 hours
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
                # Runtime-liveness: record that this task ACTUALLY RAN. Hot
                # path — never let an audit failure break a real task, and
                # never do DB I/O here (the tracker buffers in-memory).
                try:
                    from backend.services.execution_context_tracker import (
                        get_tracker,
                        MODE_CELERY_TASK,
                    )
                    tracker = get_tracker()
                    tracker.record_hit(
                        f"task:{self.name}",
                        "task",
                        self.name,
                        self.name.rsplit('.', 1)[0] if self.name and '.' in self.name else self.name,
                        mode_bit=MODE_CELERY_TASK,
                    )
                    # Time-based flush so a steady worker doesn't grow the
                    # buffer unbounded between the beat-driven flush ticks.
                    tracker.maybe_flush(interval_s=60.0)
                except Exception:  # noqa: BLE001 - never fail a task on audit
                    pass
                return TaskBase.__call__(self, *args, **kwargs)

    celery_app.Task = ContextTask

    # A task that raises must not leave its progress entry parked at 0 %: mark it
    # errored with the exception text so the UI shows a failure, not a stall.
    try:
        from celery.signals import task_failure

        @task_failure.connect
        def _surface_task_failure(sender=None, task_id=None, exception=None, kwargs=None, **_ignored):
            try:
                from backend.utils.progress_failure import mark_progress_failed

                mark_progress_failed(task_id, exception, kwargs)
            except Exception:  # noqa: BLE001 - never fail a failing task twice
                pass
    except Exception:  # noqa: BLE001
        pass

    # Flush the runtime-liveness buffer when a worker child recycles
    # (max_tasks_per_child=50) or the worker shuts down, so a recycling child
    # doesn't drop its buffered hits. Solo/concurrency=1 means count-based
    # flush alone isn't enough.
    try:
        from celery.signals import worker_process_shutdown

        @worker_process_shutdown.connect
        def _flush_runtime_hits_on_shutdown(**_kwargs):
            try:
                with minimal_app.app_context():
                    from backend.services.execution_context_tracker import get_tracker
                    get_tracker().flush()
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not register runtime-audit shutdown flush: {e}")
    
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

    try:
        from backend.tasks.production_swarm_tasks import create_production_swarm_tasks
        create_production_swarm_tasks(celery_app)
        logger.info("Production swarm tasks registered successfully")
    except ImportError as e:
        logger.warning(f"Could not import production swarm tasks: {e}")

    try:
        from backend.tasks.lora_trainer_tasks import create_lora_trainer_tasks
        create_lora_trainer_tasks(celery_app)
        logger.info("LoRA trainer tasks registered successfully")
    except ImportError as e:
        logger.warning(f"Could not import lora_trainer tasks: {e}")

    try:
        from backend.tasks.music_video_tasks import create_music_video_tasks
        create_music_video_tasks(celery_app)
        logger.info("Music video tasks registered successfully")
    except ImportError as e:
        logger.warning(f"Could not import music video tasks: {e}")

    try:
        from backend.tasks.character_generation_tasks import create_character_generation_tasks
        create_character_generation_tasks(celery_app)
        logger.info("Character generation tasks registered successfully")
    except ImportError as e:
        logger.warning(f"Could not import character generation tasks: {e}")

    try:
        from backend.tasks.social_outreach_tasks import (
            engage_with_subreddit,
            self_share,
            discord_pass,
            tick_reddit_outreach,
            tick_self_share,
            tick_process_approved_drafts,
            tick_reap_stuck_processing,
            tick_recon_youtube_replies,  # on-demand only; not in beat until scrape lands
        )
        logger.info("Social outreach tasks imported successfully")
    except ImportError as e:
        logger.warning(f"Could not import social outreach tasks: {e}")

    # Beat entries a Settings toggle governs are held back by the scheduler
    # while the toggle is off (backend/celery_beat_gates.py); declared here,
    # next to the entries, so a new loop cannot be added without deciding.
    from backend.celery_beat_gates import gate_beat_entries
    gate_beat_entries(celery_app, {
        'social-outreach-reddit-tick': 'social_outreach',
        'social-outreach-self-share-tick': 'social_outreach',
        'social-outreach-process-approved': 'social_outreach',
        'social-outreach-reap-stuck-processing': 'social_outreach',
    })

    try:
        from backend.tasks.memory_maintenance_tasks import cleanup_old_session_memory  # noqa: F401
        logger.info("Memory maintenance tasks imported successfully")
    except ImportError as e:
        logger.warning(f"Could not import memory maintenance tasks: {e}")

    try:
        from backend.tasks.self_improvement_tasks import (
            create_self_improvement_tasks,
            schedule_self_improvement_tasks,
        )
        create_self_improvement_tasks(celery_app)
        schedule_self_improvement_tasks(celery_app)
        logger.info("Self-improvement Celery Beat tasks registered successfully")
    except ImportError as e:
        logger.warning(f"Could not import self-improvement tasks: {e}")

    try:
        from backend.tasks.rag_autoresearch_tasks import (
            create_autoresearch_tasks,
            schedule_autoresearch_tasks,
        )
        create_autoresearch_tasks(celery_app)
        schedule_autoresearch_tasks(celery_app)
        logger.info("RAG Autoresearch Celery tasks registered successfully")
    except ImportError as e:
        logger.warning(f"Could not import autoresearch tasks: {e}")

    try:
        from backend.tasks.video_render_tasks import create_video_render_tasks
        create_video_render_tasks(celery_app)
        logger.info("Video render tasks registered successfully")
    except ImportError as e:
        logger.warning(f"Could not import video render tasks: {e}")

    try:
        from backend.tasks.backup_tasks import daily_backup  # noqa: F401
        logger.info("Backup tasks imported successfully")
    except ImportError as e:
        logger.warning(f"Could not import backup tasks: {e}")

    try:
        from backend.tasks import cluster_heartbeat_sweeper  # noqa: F401 - registers task
        # Only schedule the periodic sweep when this node is an actual cluster
        # master. Otherwise the task fires every few seconds and early-returns
        # {'skipped': 'not_master'}, which does nothing but flood the log.
        if os.environ.get("CLUSTER_ROLE") == "master":
            celery_app.conf.beat_schedule['cluster-heartbeat-sweep'] = {
                'task': 'cluster.sweep_node_heartbeats',
                'schedule': float(os.environ.get("CLUSTER_SWEEP_INTERVAL_S", 5)),
                'options': {'queue': 'default'},
            }
            logger.info("Cluster heartbeat sweeper task registered and scheduled (master node)")
        else:
            logger.info("Cluster heartbeat sweeper task registered (not scheduled — node is not cluster master)")
    except ImportError as e:
        logger.warning(f"Could not import cluster heartbeat sweeper: {e}")

    try:
        from backend.tasks import interconnector_client_heartbeat  # noqa: F401 - registers task
        # Scheduled on EVERY node; the task early-returns cheaply unless this node
        # is a configured, enabled client with a master URL. Makes a worker's
        # liveness independent of whether a browser tab is open.
        celery_app.conf.beat_schedule['interconnector-client-heartbeat'] = {
            'task': 'interconnector.client_heartbeat',
            'schedule': float(os.environ.get("INTERCONNECTOR_HEARTBEAT_INTERVAL_S", 60)),
            'options': {'queue': 'health'},
        }
        # Beat holds it back unless this node is an enabled client with a master.
        gate_beat_entries(celery_app, {'interconnector-client-heartbeat': 'interconnector_client'})
        logger.info("Interconnector client-heartbeat task registered and scheduled")
    except ImportError as e:
        logger.warning(f"Could not import interconnector client heartbeat: {e}")

    try:
        from backend.tasks.plugin_tasks import reconcile_plugin_deps  # noqa: F401
        logger.info("Plugin dependency reconciler task imported successfully")
    except ImportError as e:
        logger.warning(f"Could not import plugin dependency task: {e}")

    try:
        from backend.tasks.runtime_audit_tasks import (
            create_runtime_audit_tasks,
            schedule_runtime_audit_tasks,
        )
        create_runtime_audit_tasks(celery_app)
        schedule_runtime_audit_tasks(celery_app)
        logger.info("Runtime-audit Celery tasks registered successfully")
    except ImportError as e:
        logger.warning(f"Could not import runtime-audit tasks: {e}")

    try:
        # Registers analyze_repository_task so /files/folder/<id>/toggle-repo can
        # actually run repository analysis. Without this import the task is never
        # registered and the worker rejects the dispatched message with
        # "Received unregistered task ... analyze_repository_task" (KeyError),
        # so marking a folder as a repository silently never analyzes it.
        from backend.tasks.repo_analysis_tasks import analyze_repository_task  # noqa: F401
        logger.info("Repository analysis task registered successfully")
    except ImportError as e:
        logger.warning(f"Could not import repository analysis task: {e}")

    try:
        from backend.tasks.google_indexing_tasks import (  # noqa: F401
            indexing_drip_tick,
            submit_indexing_batch_for_site,
        )
        logger.info("Google Indexing tasks imported successfully")
    except ImportError as e:
        logger.warning(f"Could not import Google Indexing tasks: {e}")

    try:
        from backend.tasks.index_resume_tasks import (  # noqa: F401
            resume_pending_tick,
        )
        logger.info("Index auto-resume task imported successfully")
    except Exception as e:  # noqa: BLE001
        logger.warning("Index auto-resume task not available: %s", e)

    try:
        from backend.tasks.raptor_tasks import build_raptor_tree_task  # noqa: F401
        logger.info("RAPTOR build task registered (on demand only, never scheduled)")
    except Exception as e:  # noqa: BLE001
        logger.warning("RAPTOR build task not available: %s", e)

    # Extensions: tasks/*.py register at import; beat entries come from
    # extension.json. See backend/extensions.
    try:
        from backend import extensions as _ext
        for _ext_id, _mods in _ext.register_tasks(_ext.discover(), celery_app).items():
            logger.info("extension %s: %d task module(s) registered", _ext_id, len(_mods))
    except Exception as e:  # noqa: BLE001
        logger.error("Extension task registration failed: %s", e, exc_info=True)

    logger.info("Celery app configured with enhanced performance settings and Beat schedule")
    return celery_app


celery = create_celery_app()

# CRITICAL: make this Celery instance the "current app" so @shared_task
# decorators throughout the codebase bind to it (not to a default empty
# Celery() that Celery's lib creates on first lookup). Without this, tasks
# defined via @shared_task in any module imported BEFORE celery_app — or
# where the import order is non-deterministic — fall through to a default
# instance with no task_routes config, and .delay() messages get published
# to the literal "celery" queue instead of "default" where the worker
# subscribes. The user-visible symptom: /run-pass returns task_ids that
# never execute (ghost tasks). See test_celery_routing.py for the
# regression that catches this.
celery.set_default()
