import logging
from pathlib import Path

import requests

from backend.models import db, Document
from backend.services.job_operation_gate import get_gate
from backend.services.job_types import JobKind
from backend.utils.unified_progress_system import get_unified_progress, ProcessType
from backend.services.video_timeline_render import render_timeline, VideoOverlayError
from backend.services.output_registration import register_file

logger = logging.getLogger(__name__)

# video_editor plugin (Shotcut/MLT backend) — see plugins/video_editor/.
_VIDEO_EDITOR_PLUGIN_URL = "http://127.0.0.1:8207"

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

    @celery_app.task(bind=True, name="video_render_tasks.mlt_render_timeline_task")
    def mlt_render_timeline_task(self, payload, output_path_str, job_id):
        """Render a timeline via the video_editor plugin (Shotcut/MLT backend).

        Same external contract as render_timeline_task — same job_id, same
        progress_system updates, same Document registration shape — so the
        frontend polls and renders identically.
        """
        progress_system = get_unified_progress()
        progress_system.update_process(job_id, 0, "MLT render starting")

        output_path = Path(output_path_str)
        render_id = output_path.stem
        gate = get_gate()
        gate.register_running(JobKind.VIDEO_RENDER, render_id)

        try:
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

                progress_system.update_process(job_id, 20, "Composing MLT project")
                compose_payload = {
                    "video_path": str(video_path),
                    "audio_path": str(audio_path) if audio_path else None,
                    "video_trim_start": float(payload.get("video_trim_start") or 0.0),
                    "video_trim_end": payload.get("video_trim_end"),
                    "audio_volume": float(payload.get("audio_volume", 1.0)),
                    "text_elements": text_elements,
                    "render_mp4": True,
                    "register": False,  # we register here with full metadata
                }

                try:
                    response = requests.post(
                        f"{_VIDEO_EDITOR_PLUGIN_URL}/shotcut/compose",
                        json=compose_payload,
                        timeout=1200,
                    )
                except requests.ConnectionError as e:
                    raise ValueError("video_editor plugin not running on :8207") from e

                if response.status_code >= 400:
                    raise ValueError(f"video_editor plugin returned {response.status_code}: {response.text[:500]}")

                body = response.json()
                rendered_mp4 = body.get("rendered_mp4")
                if not rendered_mp4 or not Path(rendered_mp4).is_file():
                    raise ValueError(f"video_editor returned no rendered_mp4: {body}")

                progress_system.update_process(job_id, 90, "Registering output")

                # The plugin already wrote the MP4 to data/outputs/videos/editor-renders/.
                # Register it through the same Documents pipeline the ffmpeg path uses
                # so the UI sees a Document with the expected source-tracking metadata.
                new_doc = register_file(
                    physical_path=str(rendered_mp4),
                    folder_name="Videos",
                    subfolder_name="Editor Renders",
                    filename=Path(rendered_mp4).name,
                    file_type=".mp4",
                    file_metadata={
                        "source_document_id": video_doc.id,
                        "source_filename": video_doc.filename,
                        "audio_document_id": audio_doc_id,
                        "text_element_count": len(text_elements),
                        "trim_start": payload.get("video_trim_start"),
                        "trim_end": payload.get("video_trim_end"),
                        "render_engine": "mlt",
                        "mlt_project_path": body.get("mlt_path"),
                    },
                )
                if new_doc is None:
                    raise ValueError("MLT render succeeded but Document registration failed")

                progress_system.complete_process(
                    job_id,
                    "Render complete",
                    additional_data={"document_id": new_doc.id},
                )
        except Exception as e:
            logger.exception("mlt_render_timeline_task failed")
            progress_system.error_process(job_id, f"MLT render failed: {type(e).__name__}: {e}")
        finally:
            gate.unregister_running(JobKind.VIDEO_RENDER, render_id)

    return {
        "render_timeline_task": render_timeline_task,
        "mlt_render_timeline_task": mlt_render_timeline_task,
    }
