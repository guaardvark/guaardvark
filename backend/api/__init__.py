def register_blueprints(app):
    """Register API blueprints with the given Flask app."""
    from .celery_monitor_api import celery_monitor_bp
    from .diagnostics_api import diagnostics_bp
    from .files_api import files_bp

    # REMOVED: generation_api moved to archived_scripts/ - using bulk_generation_api instead
    from .bulk_generation_api import bulk_gen_bp
    from .index_mgmt_api import index_mgmt_bp
    from .jobs_api import jobs_bp
    from .log_api import log_bp
    from .model_api import model_bp
    from .voice_api import voice_bp
    from .enhanced_chat_api import enhanced_chat_bp
    from .web_search_api import web_search_bp
    from .docs_api import docs_bp
    from .tasks_api import tasks_bp

    # Add missing blueprint imports
    from .query_api import query_bp
    from .backup_api import backup_bp

    # DEPRECATED: upload_api moved to docs_api - from .upload_api import upload_bp
    from .rules_api import rules_bp
    from .cache_stats_api import cache_stats_bp
    from .doc_query_api import doc_query_bp
    from .system_api import system_bp
    from .entity_indexing_api import entity_indexing_bp
    from .wordpress_api import wordpress_bp
    from .interconnector_api import interconnector_bp
    from .batch_video_generation_api import batch_video_bp

    blueprints = [
        jobs_bp,
        diagnostics_bp,
        files_bp,
        # REMOVED: generation_bp - consolidated into bulk_generation_api
        bulk_gen_bp,
        index_mgmt_bp,
        log_bp,
        celery_monitor_bp,
        model_bp,
        voice_bp,
        enhanced_chat_bp,
        web_search_bp,
        docs_bp,
        tasks_bp,
        # Add missing blueprints
        query_bp,
        backup_bp,
        # DEPRECATED: upload_bp,
        rules_bp,
        cache_stats_bp,
        doc_query_bp,
        system_bp,
        entity_indexing_bp,
        wordpress_bp,
        interconnector_bp,
        batch_video_bp,
    ]
    for bp in blueprints:
        if bp.name not in app.blueprints:
            app.register_blueprint(bp)
