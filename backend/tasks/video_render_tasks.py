import logging
from pathlib import Path

from backend.models import db, Document
from backend.services.job_operation_gate import get_gate
from backend.services.job_types import JobKind
from backend.utils.unified_progress_system import get_unified_progress, ProcessType
from backend.services.video_timeline_render import render_timeline, VideoOverlayError
from backend.services.output_registration import register_file

logger = logging.getLogger(__name__)

def create_video_render_tasks(celery_app):
    @celery_app.task(bind=True, name="video_render_tasks.render_timeline_task")
    def render_timeline_task(self, payload, output_path_str, job_id):
        progress_system = get_unified_progress()
        progress_system.update_process(job_id, 0, "Render starting")
        
        output_path = Path(output_path_str)
        render_id = output_path.stem
        gate = get_gate()
        gate.register_running(JobKind.VIDEO_RENDER, render_id)
        
        try:
            # Need to re-fetch documents inside the worker
            from backend.app import create_app
            from backend.api.video_overlay_api import _resolve_video_path
            app = create_app()
            with app.app_context():
                video_doc_id = payload.get("video_document_id")
                video_doc = db.session.get(Document, video_doc_id)
                if video_doc is None:
                    raise ValueError("Video document not found")
                video_path = _resolve_video_path(video_doc)
                if video_path is None:
                    raise ValueError(f"Video file not on disk: {video_doc.path}")
                
                audio_path = None
                audio_doc_id = payload.get("audio_document_id")
                if audio_doc_id is not None:
                    audio_doc = db.session.get(Document, audio_doc_id)
                    if audio_doc is None:
                        raise ValueError("Audio document not found")
                    audio_path = _resolve_video_path(audio_doc)
                    if audio_path is None:
                        raise ValueError(f"Audio file not on disk: {audio_doc.path}")
                
                text_elements = payload.get("text_elements") or []
                
                render_timeline(
                    video_input_path=video_path,
                    output_path=output_path,
                    text_elements=text_elements,
                    video_trim_start=payload.get("video_trim_start"),
                    video_trim_end=payload.get("video_trim_end"),
                    audio_input_path=audio_path,
                    audio_volume=float(payload.get("audio_volume", 1.0)),
                )
                
                new_doc = register_file(
                    physical_path=str(output_path),
                    folder_name="Videos",
                    subfolder_name="Editor Renders",
                    filename=output_path.name,
                    file_type=".mp4",
                    file_metadata={
                        "source_document_id": video_doc.id,
                        "source_filename": video_doc.filename,
                        "audio_document_id": audio_doc_id,
                        "text_element_count": len(text_elements),
                        "trim_start": payload.get("video_trim_start"),
                        "trim_end": payload.get("video_trim_end"),
                    },
                )
                
                if new_doc is None:
                    raise ValueError("Render succeeded but Document registration failed")
                
                progress_system.complete_process(
                    job_id, 
                    "Render complete", 
                    additional_data={"document_id": new_doc.id}
                )
                
        except VideoOverlayError as e:
            logger.warning("render_timeline_task failed: %s", e)
            progress_system.error_process(job_id, f"Render failed: {e}")
        except Exception as e:
            logger.exception("render_timeline_task unexpected failure")
            progress_system.error_process(job_id, f"Render failed: {type(e).__name__}: {e}")
        finally:
            gate.unregister_running(JobKind.VIDEO_RENDER, render_id)

    return {"render_timeline_task": render_timeline_task}
