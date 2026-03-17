import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List

from .base_handler import BaseTaskHandler, TaskResult, TaskResultStatus

logger = logging.getLogger(__name__)


class CSVGenerationHandler(BaseTaskHandler):

    @property
    def handler_name(self) -> str:
        return "csv_generation"

    @property
    def display_name(self) -> str:
        return "CSV Content Generation"

    @property
    def process_type(self) -> str:
        return "csv_processing"

    @property
    def celery_queue(self) -> str:
        return "generation"

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "required": ["topics", "output_filename"],
            "properties": {
                "topics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of topics to generate content for",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Name of output CSV file",
                },
                "model_name": {
                    "type": "string",
                    "default": "default",
                    "description": "LLM model to use for generation",
                },
                "target_word_count": {
                    "type": "integer",
                    "default": 500,
                    "description": "Target word count per topic",
                },
                "client_name": {
                    "type": "string",
                    "description": "Client name for content context",
                },
                "project_name": {
                    "type": "string",
                    "description": "Project name for content context",
                },
                "website": {"type": "string", "description": "Website URL for context"},
                "target_website": {
                    "type": "string",
                    "description": "Target website URL",
                },
                "use_celery": {
                    "type": "boolean",
                    "default": True,
                    "description": "Use Celery for async execution",
                },
            },
        }

    def execute(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable[[int, str, Optional[Dict[str, Any]]], None],
    ) -> TaskResult:
        started_at = datetime.now()

        try:
            topics = config.get("topics", [])
            output_filename = config.get(
                "output_filename", f"task_{task.id}_output.csv"
            )
            model_name = config.get("model_name")
            target_word_count = config.get("target_word_count", 500)
            client_name = config.get("client_name", "Client")
            project_name = config.get("project_name", "Project")
            website = config.get("website", "")
            target_website = config.get("target_website", "")
            use_celery = config.get("use_celery", True)

            if not topics:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message="No topics provided",
                    error_message="topics list is empty or not provided",
                    started_at=started_at,
                    completed_at=datetime.now(),
                )

            progress_callback(
                0,
                f"Starting CSV generation for {len(topics)} topics",
                {"total_items": len(topics), "output_filename": output_filename},
            )

            if use_celery:
                return self._execute_celery(
                    task,
                    topics,
                    output_filename,
                    client_name,
                    project_name,
                    website,
                    target_website,
                    target_word_count,
                    model_name,
                    progress_callback,
                    started_at,
                )
            else:
                return self._execute_sync(
                    task,
                    topics,
                    output_filename,
                    client_name,
                    project_name,
                    website,
                    target_website,
                    target_word_count,
                    model_name,
                    progress_callback,
                    started_at,
                )

        except Exception as e:
            logger.error(f"CSV generation handler error: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"CSV generation failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_celery(
        self,
        task: Any,
        topics: List[str],
        output_filename: str,
        client_name: str,
        project_name: str,
        website: str,
        target_website: str,
        target_word_count: int,
        model_name: Optional[str],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        try:
            from backend.tasks.proven_csv_generation import generate_proven_csv_task

            job_id = task.job_id or f"csv_gen_{task.id}"

            progress_callback(
                5,
                "Submitting to Celery queue...",
                {"job_id": job_id, "queue": self.celery_queue},
            )

            celery_result = generate_proven_csv_task.apply_async(
                args=[
                    topics,
                    output_filename,
                    client_name,
                    project_name,
                    website,
                    target_website,
                    target_word_count,
                    job_id,
                    model_name,
                ],
                queue=self.celery_queue,
            )

            progress_callback(
                10,
                "Task submitted to generation queue",
                {"celery_task_id": celery_result.id, "job_id": job_id},
            )

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"CSV generation task submitted for {len(topics)} topics",
                output_data={
                    "celery_task_id": celery_result.id,
                    "job_id": job_id,
                    "topics_count": len(topics),
                    "output_filename": output_filename,
                    "async": True,
                },
                items_total=len(topics),
                started_at=started_at,
                completed_at=datetime.now(),
            )

        except ImportError as e:
            logger.warning(f"Celery task not available, falling back to sync: {e}")
            return self._execute_sync(
                task,
                topics,
                output_filename,
                client_name,
                project_name,
                website,
                target_website,
                target_word_count,
                model_name,
                progress_callback,
                started_at,
            )

    def _execute_sync(
        self,
        task: Any,
        topics: List[str],
        output_filename: str,
        client_name: str,
        project_name: str,
        website: str,
        target_website: str,
        target_word_count: int,
        model_name: Optional[str],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        import csv
        import re

        try:
            progress_callback(5, "Initializing LLM...", None)

            if model_name and model_name != "default":
                from llama_index.llms.ollama import Ollama
                from backend.config import OLLAMA_BASE_URL, LLM_REQUEST_TIMEOUT

                timeout_value = min(LLM_REQUEST_TIMEOUT, 180.0)
                llm = Ollama(
                    model=model_name,
                    base_url=OLLAMA_BASE_URL,
                    request_timeout=timeout_value,
                )
            else:
                from backend.utils.llm_service import get_default_llm

                llm = get_default_llm()

            output_dir = os.environ.get("GUAARDVARK_OUTPUT_DIR")
            if not output_dir:
                from backend.config import OUTPUT_DIR

                output_dir = OUTPUT_DIR
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, output_filename)

            progress_callback(
                10, "Starting content generation...", {"output_path": output_path}
            )

            rows_written = 0
            failed_topics = []

            with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(
                    [
                        "id",
                        "title",
                        "content",
                        "excerpt",
                        "category",
                        "tags",
                        "slug",
                        "image",
                    ]
                )

                for i, topic in enumerate(topics):
                    try:
                        from backend.utils.llm_service import ChatMessage, MessageRole

                        prompt = f"Write {target_word_count} words about {topic} for {client_name}. Use HTML tags. Be professional and detailed."

                        messages = [
                            ChatMessage(
                                role=MessageRole.SYSTEM,
                                content="You are a professional content writer. Write detailed HTML content. Respond with only the HTML content.",
                            ),
                            ChatMessage(role=MessageRole.USER, content=prompt),
                        ]

                        response = llm.chat(messages)
                        try:
                            content = (
                                response.message.content.strip()
                                if response and hasattr(response, "message")
                                else f"<p>Content for {topic}</p>"
                            )
                        except (ValueError, AttributeError):
                            blocks = (
                                getattr(response.message, "blocks", [])
                                if response and hasattr(response, "message")
                                else []
                            )
                            content = next(
                                (
                                    getattr(b, "text", str(b))
                                    for b in blocks
                                    if getattr(b, "text", None)
                                ),
                                f"<p>Content for {topic}</p>",
                            ).strip()

                        title = f"{topic} - {client_name}"[:100]
                        excerpt = f"Professional content about {topic}."[:250]
                        slug = re.sub(r"[^a-zA-Z0-9\s-]", "", topic.lower())
                        slug = (
                            re.sub(r"\s+", "-", slug.strip()).strip("-")[:50]
                            or f"page-{i+1}"
                        )

                        writer.writerow(
                            [
                                f"{10000 + i:05d}",
                                title,
                                content,
                                excerpt,
                                "General",
                                "content, professional",
                                slug,
                                "default",
                            ]
                        )
                        rows_written += 1

                    except Exception as e:
                        logger.error(
                            f"Failed to generate content for topic '{topic}': {e}"
                        )
                        failed_topics.append(topic)

                    progress = int(10 + (i + 1) / len(topics) * 85)
                    progress_callback(
                        progress,
                        f"Generated {i+1}/{len(topics)} topics",
                        {
                            "generated_count": rows_written,
                            "target_count": len(topics),
                            "current_topic": topic[:50],
                        },
                    )

            progress_callback(
                100,
                "CSV generation complete",
                {"rows_written": rows_written, "failed_count": len(failed_topics)},
            )

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            if failed_topics:
                return TaskResult(
                    status=TaskResultStatus.PARTIAL,
                    message=f"Generated {rows_written}/{len(topics)} topics ({len(failed_topics)} failed)",
                    output_files=[output_path],
                    output_data={
                        "rows_written": rows_written,
                        "failed_topics": failed_topics,
                    },
                    items_processed=rows_written,
                    items_total=len(topics),
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Successfully generated {rows_written} rows",
                output_files=[output_path],
                output_data={"rows_written": rows_written},
                items_processed=rows_written,
                items_total=len(topics),
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except Exception as e:
            logger.error(f"Sync CSV generation failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"CSV generation failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def get_estimated_duration(self, config: Dict[str, Any]) -> Optional[int]:
        topics = config.get("topics", [])
        return len(topics) * 30 if topics else None
