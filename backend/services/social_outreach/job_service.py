"""Queue social outreach runs as first-class Task rows.

The Outreach HTTP API, chat tools, and slash commands should all use this
service instead of firing direct Celery tasks. That keeps user-initiated
Outreach visible in TaskPage, ActivityPage, and the unified progress footer.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from backend.models import Task, db
from backend.services.social_outreach import kill_switch, profile

logger = logging.getLogger(__name__)


OUTREACH_TASK_TYPES = {
    "reddit": "social_outreach_reddit",
    "self_share": "social_outreach_share",
    "share": "social_outreach_share",
    "recon": "social_outreach_recon",
    "youtube": "social_outreach_youtube",
    "youtube_recon": "social_outreach_youtube",
    "youtube_replies": "social_outreach_youtube_replies",
    "draft": "social_outreach_draft",
    "discord": "social_outreach_discord",
    "facebook": "social_outreach_facebook",
}

# Runs that read an outside site, by what they read; with web access off they
# are not queued, and each pass checks again when it runs.
WEB_READING_RUNS = {
    "social_outreach_youtube": "scout YouTube for outreach",
    "social_outreach_recon": "scout Reddit for outreach",
    "social_outreach_reddit": "scout Reddit for outreach",
    "social_outreach_share": "scout Reddit for outreach",
    "social_outreach_youtube_replies": "read the comments on your YouTube videos",
    "social_outreach_facebook": "scout Facebook groups for outreach",
}


def queue_outreach_run(
    platform: str,
    *,
    subreddit: str | None = None,
    link_url: str | None = None,
    batch_size: int | None = None,
    keyword_profiles: list[str] | None = None,
    search_queries: list[str] | None = None,
    groups: list[str] | None = None,
    post_url: str | None = None,
    chain_draft: bool = False,
    priority: int = 2,
    created_by: str = "outreach",
) -> dict[str, Any]:
    """Create and enqueue a Task-backed outreach run.

    Raises:
        ValueError: unsupported platform.
        RuntimeError: Outreach is disabled, web access is off for a run that
            reads an outside site, or Celery submission fails.
    """
    normalized = (platform or "").strip().lower()
    task_type = OUTREACH_TASK_TYPES.get(normalized)
    if not task_type:
        supported = ", ".join(sorted(OUTREACH_TASK_TYPES))
        raise ValueError(f"unsupported platform '{platform}'. Use one of: {supported}.")

    if not kill_switch.is_enabled():
        raise RuntimeError("outreach is disabled (kill switch is off)")

    if task_type in WEB_READING_RUNS:
        from backend.utils.settings_utils import web_access_block_reason

        blocked = web_access_block_reason(WEB_READING_RUNS[task_type])
        if blocked:
            raise RuntimeError(blocked)

    workflow_config: dict[str, Any] = {
        "platform": normalized,
        "created_by": created_by,
        "chain_draft": bool(chain_draft),
    }
    if subreddit:
        workflow_config["subreddit"] = subreddit.strip().lstrip("/").removeprefix("r/")
    if link_url:
        workflow_config["link_url"] = link_url.strip()
    elif task_type == "social_outreach_share":
        # No site in the profile: the executor skips the pass with the reason.
        site = profile.site_url()
        if site:
            workflow_config["link_url"] = site
    if search_queries:
        # Reddit recon: read Reddit's search for these instead of a
        # community's front listing.
        cleaned = [str(q).strip() for q in search_queries if q and str(q).strip()]
        if cleaned:
            workflow_config["search_queries"] = cleaned[:8]
    if groups:
        # Facebook: the groups to read instead of the saved or matching ones.
        cleaned = [str(g).strip() for g in groups if g and str(g).strip()]
        if cleaned:
            workflow_config["groups"] = cleaned[:12]
    if post_url:
        # Facebook: draft a reply to this one post.
        workflow_config["post_url"] = post_url.strip()
    if batch_size is not None:
        workflow_config["batch_size"] = max(1, min(int(batch_size), 25))
    if keyword_profiles:
        cleaned = [str(p).strip() for p in keyword_profiles if p and str(p).strip()]
        if cleaned:
            workflow_config["keyword_profiles"] = cleaned[:8]

    task = Task(
        name=_task_name(normalized, workflow_config),
        description=_task_description(normalized, workflow_config),
        status="queued",
        priority=priority,
        type=task_type,
        workflow_config=json.dumps(workflow_config),
        schedule_type="immediate",
        task_handler="social_outreach",
        handler_config={"platform": normalized, "source": created_by},
        progress=0,
    )
    db.session.add(task)
    db.session.commit()

    task.job_id = f"task_{task.id}"
    task.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.session.commit()

    try:
        from backend.tasks.unified_task_executor import execute_unified_task

        celery_result = execute_unified_task.apply_async(args=[task.id], queue="default")
    except Exception as exc:
        task.status = "failed"
        task.error_message = f"Failed to queue Outreach task: {exc}"
        task.updated_at = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit()
        logger.exception("Failed to enqueue Outreach task %s", task.id)
        raise RuntimeError(str(exc)) from exc

    payload = task.to_dict()
    payload.update(
        {
            "task_id": task.id,
            "job_id": task.job_id,
            "celery_task_id": celery_result.id,
            "platform": normalized,
            "message": _queued_message(normalized, workflow_config, task.id),
        }
    )
    return payload


def _task_name(platform: str, workflow_config: dict[str, Any]) -> str:
    if platform == "reddit":
        subreddit = workflow_config.get("subreddit")
        return f"Outreach Reddit pass{f' for r/{subreddit}' if subreddit else ''}"
    if platform in ("self_share", "share"):
        return "Outreach self-share pass"
    if platform in ("youtube", "youtube_recon"):
        profiles = workflow_config.get("keyword_profiles") or []
        suffix = f" ({', '.join(profiles[:3])})" if profiles else ""
        return f"Outreach YouTube recon{suffix}"
    if platform == "recon":
        searches = workflow_config.get("search_queries") or []
        if searches:
            return f"Outreach Reddit search ({', '.join(searches[:3])})"
        subreddit = workflow_config.get("subreddit")
        return f"Outreach recon pass{f' for r/{subreddit}' if subreddit else ''}"
    if platform == "youtube_replies":
        return "Outreach replies to comments on your videos"
    if platform == "draft":
        return "Outreach draft candidates"
    if platform == "discord":
        return "Outreach Discord pass"
    if platform == "facebook":
        if workflow_config.get("post_url"):
            return "Outreach reply to a Facebook group post"
        return "Outreach Facebook groups scout"
    return "Outreach pass"


def _task_description(platform: str, workflow_config: dict[str, Any]) -> str:
    if platform == "reddit":
        subreddit = workflow_config.get("subreddit") or "next target from social_outreach_targets.json"
        return f"Discover and draft a Reddit Outreach comment for {subreddit}."
    if platform in ("self_share", "share"):
        return "Draft a self-share link post through the supervised Outreach pipeline."
    if platform in ("youtube", "youtube_recon"):
        profiles = workflow_config.get("keyword_profiles") or ["configured profiles"]
        draft = " then draft" if workflow_config.get("chain_draft") else ""
        return f"Scout YouTube for {profiles}{draft}; leave drafts for human approve."
    if platform == "recon":
        draft = " and draft a reply for each" if workflow_config.get("chain_draft") else ""
        searches = workflow_config.get("search_queries") or []
        if searches:
            return f"Search Reddit for {searches}{draft}; drafts wait for approval."
        return f"Scout a Reddit community and add candidate Outreach rows{draft}."
    if platform == "youtube_replies":
        return "Read the new comments on your YouTube videos and draft a reply for each; drafts wait for approval."
    if platform == "draft":
        return "Draft pending Outreach candidates into reviewable queue rows."
    if platform == "discord":
        return "Discord Outreach is handled by the Discord plugin cog (Celery path is a noop)."
    if platform == "facebook":
        if workflow_config.get("post_url"):
            return f"Read {workflow_config['post_url']} and draft a reply; the draft waits for approval."
        return "Read the newest posts in your Facebook groups and draft a reply for each that fits; drafts wait for approval."
    return "Run an Outreach pass."


def _queued_message(platform: str, workflow_config: dict[str, Any], task_id: int) -> str:
    if platform == "reddit" and workflow_config.get("subreddit"):
        return f"Reddit Outreach pass for r/{workflow_config['subreddit']} added to the Job Queue as task #{task_id}."
    if platform in ("youtube", "youtube_recon"):
        profiles = workflow_config.get("keyword_profiles") or []
        topic = f" ({', '.join(profiles[:3])})" if profiles else ""
        return f"YouTube Outreach recon{topic} added to the Job Queue as task #{task_id}."
    labels = {
        "reddit": "Reddit Outreach pass",
        "self_share": "Self-share Outreach pass",
        "share": "Self-share Outreach pass",
        "recon": "Recon Outreach pass",
        "youtube_replies": "Replies to comments on your videos",
        "draft": "Drafting Outreach pass",
        "discord": "Discord Outreach pass",
        "facebook": "Facebook groups Outreach pass",
    }
    return f"{labels.get(platform, 'Outreach pass')} added to the Job Queue as task #{task_id}."
