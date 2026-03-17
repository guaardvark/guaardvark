import logging
from backend.celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(bind=True)
def analyze_repository_task(self, folder_id):
    """
    Celery task to analyze a repository folder.
    """
    from backend.services.repository_analysis_service import RepositoryAnalysisService

    logger.info(f"Starting repository analysis for folder {folder_id}")
    try:
        RepositoryAnalysisService.analyze_repository(folder_id)
        logger.info(f"Completed repository analysis for folder {folder_id}")
        return {"status": "success", "folder_id": folder_id}
    except Exception as e:
        logger.error(f"Error in repository analysis task: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
