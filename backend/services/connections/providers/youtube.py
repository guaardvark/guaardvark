"""YouTube publishing via the Data API v3.

Uploads are ``private`` unless the person turns on "Allow public uploads" on
the connection (``Capabilities.public_opt_in``): making a video public is a
deliberate choice, not a side effect of generating one. A scheduled release
(``status.publishAt``) makes the video public at that time, so it needs the
same opt-in as a public upload. The publish path refuses such a post before
it gets here; ``publish`` and ``schedule`` check again and refuse it too.

The resumable upload protocol is driven directly over HTTPS rather than through
google-api-python-client, which keeps the backend venv free of that dependency
and gives real per-chunk progress for the multi-gigabyte case.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timezone
from typing import Dict, Tuple

import requests

from backend.services.connections import gates
from backend.services.connections.base import (
    AUTH_OAUTH2,
    BOT_LABEL_NONE,
    DISCLOSE_GENERATED_MEDIA,
    DISCLOSURE_FLAG,
    FAMILY_SOCIAL,
    SCHEDULE_GONE,
    SCHEDULE_PENDING,
    SCHEDULE_PUBLISHED,
    AIDisclosure,
    BotLabel,
    Capabilities,
    ConfigField,
    ConnCtx,
    CredentialField,
    DeleteRequest,
    DeleteResult,
    NativeScheduling,
    ProgressFn,
    ProviderSpec,
    PublicOptIn,
    PublishRequest,
    PublishResult,
    RateLimit,
    ScheduleCheck,
)

logger = logging.getLogger(__name__)

TIMEOUT = 120
CHUNK_BYTES = 8 * 1024 * 1024
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API_URL = "https://www.googleapis.com/youtube/v3"
COMMENT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
SCOPES = ["https://www.googleapis.com/auth/youtube", COMMENT_SCOPE]
# Comments and uploads share one account, so posting comments is switched on per
# account: a person with two channels must not comment from the wrong one.
OUTREACH_COMMENTS_FIELD = "outreach_comments"
COMMENT_PAGES_SEARCHED = 3
CATEGORY_SCIENCE_TECH = "28"

AI_DISCLOSURE_SOURCE = "https://support.google.com/youtube/answer/14328491"
DEVELOPER_POLICIES = "https://developers.google.com/youtube/terms/developer-policies"
VIDEOS_RESOURCE = "https://developers.google.com/youtube/v3/docs/videos"

SPEC = ProviderSpec(
    provider="youtube",
    family=FAMILY_SOCIAL,
    label="YouTube",
    auth_kinds=(AUTH_OAUTH2,),
    credential_fields=(
        CredentialField(
            name="client_id",
            label="OAuth client ID",
            help="From an OAuth 'Desktop app' client in Google Cloud Console.",
        ),
        CredentialField(name="client_secret", label="OAuth client secret"),
        CredentialField(
            name="refresh_token",
            label="Refresh token",
            required=False,
            help="Filled in automatically once you complete the authorization step.",
        ),
    ),
    config_fields=(
        ConfigField(
            name=OUTREACH_COMMENTS_FIELD,
            label="Post approved Outreach comments from this account",
            default="no",
            choices=("no", "yes"),
            help=(
                "Yes sends approved YouTube comments through the official API as this channel. "
                "Each one still waits for your Post now. Reauthorize once if this account was "
                "connected before comments existed."
            ),
        ),
    ),
    capabilities=Capabilities(
        text=True,
        max_text_chars=5000,
        images=False,
        video=True,
        max_video_bytes=128 * 1024 * 1024 * 1024,
        requires_media=True,
        supports_title=True,
        supports_tags=True,
        visibilities=("private", "unlisted", "public"),
        default_visibility="private",
        rate_limits=(
            # Uploads draw on their own bucket of 100 videos.insert calls a day,
            # shared by every channel that uses the same Google Cloud project.
            RateLimit(
                posts=10,
                window_seconds=24 * 3600,
                platform_limit="100 videos.insert calls per day per Google Cloud project",
                source="https://developers.google.com/youtube/v3/determine_quota_cost",
            ),
        ),
        ai_disclosure=AIDisclosure(
            required_when=DISCLOSE_GENERATED_MEDIA,
            mechanism=DISCLOSURE_FLAG,
            flag="status.containsSyntheticMedia",
            rule=(
                "YouTube requires disclosure of realistic content made or meaningfully "
                "altered with AI. A generated video is uploaded with YouTube's altered or "
                "synthetic content flag."
            ),
            source=AI_DISCLOSURE_SOURCE,
        ),
        bot_label=BotLabel(
            kind=BOT_LABEL_NONE,
            how=(
                "YouTube has no account label for automation. Its developer policies ask "
                "for the channel owner's prior, specific consent to automated uploads; "
                "Guaardvark uploads only what a person approved."
            ),
            source=DEVELOPER_POLICIES,
        ),
        native_scheduling=NativeScheduling(
            field="status.publishAt",
            # Not a published minimum: YouTube releases the video at publishAt
            # only if the upload has finished, so the lead covers the upload.
            min_lead_seconds=15 * 60,
            # publishAt is the moment a private video becomes public.
            visibilities=("public",),
            source=VIDEOS_RESOURCE,
        ),
        deletable=True,
        public_opt_in=PublicOptIn(
            visibilities=("unlisted", "public"),
            label="Allow public uploads",
            explanation=(
                "Off: every upload from this account is private, and you make a video "
                "public yourself in YouTube Studio. On: an upload can also be public or "
                "unlisted, and a scheduled upload can be released publicly by YouTube at "
                "its time (status.publishAt). Every upload still waits for your approval "
                "on the Approvals page. YouTube's own rules apply: it requires disclosure "
                "of realistic content made or meaningfully altered with AI (a generated "
                "video is uploaded with its altered or synthetic content flag), and its "
                "developer policies ask for the channel owner's prior, specific consent to "
                "automated uploads."
            ),
            refusal=(
                "Public and unlisted uploads are off for this YouTube account, so it "
                "uploads as private only; a scheduled upload goes out as private at its "
                "time. To allow them, and YouTube's own scheduled public release, turn on "
                "\"Allow public uploads\" on the account's card on the Connections page."
            ),
            sources=(
                ("AI disclosure", AI_DISCLOSURE_SOURCE),
                ("Developer policies", DEVELOPER_POLICIES),
                ("status.publishAt", VIDEOS_RESOURCE),
            ),
            # Read for a connection whose card has no choice stored yet.
            legacy_setting="youtube_allow_public_publish",
        ),
    ),
    hint_field="client_secret",
    docs_url="https://developers.google.com/youtube/v3/guides/uploading_a_video",
    setup_help=(
        "Create an OAuth Desktop-app client in Google Cloud Console, enable the "
        "YouTube Data API v3, then authorize this connection."
    ),
)


def authorize_url(ctx: ConnCtx, redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode

    client_id = (ctx.secrets.get("client_id") or "").strip()
    if not client_id:
        raise ValueError("No OAuth client ID configured.")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        # Without this, Google omits the refresh token on re-authorization.
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URI}?{urlencode(params)}"


def exchange_code(ctx: ConnCtx, code: str, redirect_uri: str) -> Dict[str, str]:
    resp = requests.post(
        TOKEN_URI,
        data={
            "code": code,
            "client_id": (ctx.secrets.get("client_id") or "").strip(),
            "client_secret": (ctx.secrets.get("client_secret") or "").strip(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}).")
    payload = resp.json()
    refresh = payload.get("refresh_token")
    if not refresh:
        raise RuntimeError(
            "Google returned no refresh token. Revoke the app's access and retry."
        )
    return {"refresh_token": refresh, "access_token": payload.get("access_token", "")}


def _access_token(ctx: ConnCtx) -> str:
    """Exchange the stored refresh token for a fresh access token."""
    refresh = (ctx.secrets.get("refresh_token") or "").strip()
    if not refresh:
        raise ValueError("Not authorized yet — complete the Google sign-in step.")

    resp = requests.post(
        TOKEN_URI,
        data={
            "refresh_token": refresh,
            "client_id": (ctx.secrets.get("client_id") or "").strip(),
            "client_secret": (ctx.secrets.get("client_secret") or "").strip(),
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code == 400:
        raise RuntimeError("Refresh token rejected — reauthorize this connection.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Token refresh failed ({resp.status_code}).")

    token = (resp.json() or {}).get("access_token")
    if not token:
        raise RuntimeError("Token refresh returned no access token.")
    return token


def test(ctx: ConnCtx) -> Tuple[bool, str, Dict]:
    try:
        token = _access_token(ctx)
        resp = requests.get(
            f"{API_URL}/channels",
            headers={"Authorization": f"Bearer {token}"},
            params={"part": "snippet", "mine": "true"},
            timeout=TIMEOUT,
        )
    except ValueError as e:
        return False, str(e), {}
    except RuntimeError as e:
        return False, str(e), {}
    except requests.RequestException as e:
        return False, f"Could not reach YouTube: {e}", {}

    if resp.status_code >= 400:
        return False, f"YouTube returned {resp.status_code}.", {}

    items = (resp.json() or {}).get("items") or []
    if not items:
        return False, "No channel is associated with this Google account.", {}

    snippet = items[0].get("snippet") or {}
    title = snippet.get("title") or "YouTube channel"
    return True, f"Connected to '{title}'.", {
        "handle": snippet.get("customUrl") or title,
        "display_name": title,
        "config": {"channel_id": items[0].get("id")},
    }


def _api_error(resp, doing: str) -> RuntimeError:
    """Say why YouTube refused, in words a person can act on."""
    try:
        errors = ((resp.json() or {}).get("error") or {}).get("errors") or []
        reason = (errors[0] or {}).get("reason") or ""
    except (ValueError, AttributeError, IndexError):
        reason = ""
    if reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"):
        return RuntimeError("This connection cannot post comments yet. Reauthorize it.")
    if reason == "commentsDisabled":
        return RuntimeError("Comments are turned off on this video.")
    if reason in ("quotaExceeded", "rateLimitExceeded"):
        return RuntimeError("YouTube's daily API quota is used up. It resets at midnight Pacific.")
    return RuntimeError(f"YouTube refused to {doing} ({resp.status_code} {reason}).".replace(" )", ")"))


def _video_channel(token: str, video_id: str) -> str:
    resp = requests.get(
        f"{API_URL}/videos",
        headers={"Authorization": f"Bearer {token}"},
        params={"part": "snippet", "id": video_id},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _api_error(resp, "find the video")
    items = (resp.json() or {}).get("items") or []
    if not items:
        raise RuntimeError(f"YouTube has no public video {video_id}.")
    return items[0]["snippet"]["channelId"]


def post_comment(ctx: ConnCtx, video_id: str, text: str) -> str:
    """Post a top-level comment as this account. Returns the comment id."""
    token = _access_token(ctx)
    resp = requests.post(
        f"{API_URL}/commentThreads",
        headers={"Authorization": f"Bearer {token}"},
        params={"part": "snippet"},
        json={"snippet": {
            "channelId": _video_channel(token, video_id),
            "videoId": video_id,
            "topLevelComment": {"snippet": {"textOriginal": text}},
        }},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _api_error(resp, "post the comment")
    return (resp.json() or {})["id"]


def post_reply(ctx: ConnCtx, parent_id: str, text: str) -> str:
    """Reply under comment ``parent_id`` as this account. Returns the reply's id."""
    resp = requests.post(
        f"{API_URL}/comments",
        headers={"Authorization": f"Bearer {_access_token(ctx)}"},
        params={"part": "snippet"},
        json={"snippet": {"parentId": parent_id, "textOriginal": text}},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise _api_error(resp, "post the reply")
    return (resp.json() or {})["id"]


def find_comment(ctx: ConnCtx, video_id: str, text: str) -> str | None:
    """The id of the top-level comment on ``video_id`` that starts with ``text``, if any."""
    wanted = " ".join((text or "").split()).lower()
    if not wanted:
        return None
    token = _access_token(ctx)
    page = None
    for _ in range(COMMENT_PAGES_SEARCHED):
        params = {"part": "snippet", "videoId": video_id, "maxResults": 100, "textFormat": "plainText"}
        if page:
            params["pageToken"] = page
        resp = requests.get(
            f"{API_URL}/commentThreads",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            raise _api_error(resp, "read the comments")
        data = resp.json() or {}
        for thread in data.get("items") or []:
            top = thread["snippet"]["topLevelComment"]
            body = " ".join((top["snippet"].get("textOriginal") or "").split()).lower()
            if body.startswith(wanted) or wanted.startswith(body):
                return top["id"]
        page = data.get("nextPageToken")
        if not page:
            break
    return None


def comment_text(ctx: ConnCtx, comment_id: str) -> str | None:
    """The text YouTube now holds for ``comment_id``, or None when it shows nothing."""
    resp = requests.get(
        f"{API_URL}/comments",
        headers={"Authorization": f"Bearer {_access_token(ctx)}"},
        params={"part": "snippet", "id": comment_id, "textFormat": "plainText"},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        return None
    items = (resp.json() or {}).get("items") or []
    return items[0]["snippet"].get("textOriginal") if items else None


def _start_resumable(token: str, body: Dict, item) -> str:
    resp = requests.post(
        UPLOAD_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(item.bytes),
            "X-Upload-Content-Type": item.mime,
        },
        params={"uploadType": "resumable", "part": "snippet,status"},
        data=json.dumps(body),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Could not start the upload ({resp.status_code}).")
    location = resp.headers.get("Location")
    if not location:
        raise RuntimeError("Upload session returned no Location header.")
    return location


def _upload_chunks(session_url: str, item, on_progress: ProgressFn) -> Dict:
    total = item.bytes
    sent = 0
    with open(item.path, "rb") as handle:
        while sent < total:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            end = sent + len(chunk) - 1
            resp = requests.put(
                session_url,
                headers={
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {sent}-{end}/{total}",
                },
                data=chunk,
                timeout=TIMEOUT,
            )
            # 308 means the server wants the next chunk.
            if resp.status_code in (200, 201):
                on_progress(95, "Finalising…")
                return resp.json()
            if resp.status_code != 308:
                raise RuntimeError(f"Upload failed ({resp.status_code}).")
            sent = end + 1
            on_progress(20 + int(70 * sent / max(total, 1)), f"Uploading… {sent * 100 // total}%")
    raise RuntimeError("Upload ended without a response from YouTube.")


def _video_body(req: PublishRequest, item, status: Dict) -> Dict:
    if req.ai_disclosure:
        # Sent only when on: leaving it out makes no claim either way.
        status = {**status, "containsSyntheticMedia": True}
    return {
        "snippet": {
            "title": (req.title or os.path.basename(item.path))[:100],
            "description": (req.body or "")[:5000],
            "tags": list(req.tags or []),
            "categoryId": CATEGORY_SCIENCE_TECH,
        },
        "status": {**status, "selfDeclaredMadeForKids": False},
    }


def _upload(ctx: ConnCtx, req: PublishRequest, status: Dict, on_progress: ProgressFn):
    """Upload the request's video with ``status``. Returns (video_id, error)."""
    videos = [m for m in req.media if m.kind == "video"]
    if not videos:
        return None, "YouTube requires a video file."
    item = videos[0]
    try:
        on_progress(10, "Authenticating…")
        token = _access_token(ctx)
        on_progress(15, "Starting upload…")
        session_url = _start_resumable(token, _video_body(req, item, status), item)
        result = _upload_chunks(session_url, item, on_progress)
    except ValueError as e:
        return None, str(e)
    except (OSError, RuntimeError) as e:
        return None, str(e)
    except requests.RequestException as e:
        return None, f"Could not reach YouTube: {e}"
    return result.get("id"), None


def _watch_url(video_id) -> str | None:
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else None


def publish(ctx: ConnCtx, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
    visibility = req.visibility or SPEC.capabilities.default_visibility
    refusal = gates.visibility_refusal(SPEC.provider, ctx.config, visibility)
    if refusal:
        return PublishResult(ok=False, message=refusal)

    video_id, error = _upload(ctx, req, {"privacyStatus": visibility}, on_progress)
    if error:
        return PublishResult(ok=False, message=error)
    return PublishResult(
        ok=True,
        remote_id=video_id,
        remote_url=_watch_url(video_id),
        message=f"Uploaded to YouTube ({visibility}).",
    )


def schedule(ctx: ConnCtx, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
    """Upload as private now; YouTube makes it public at ``req.publish_at``."""
    if req.publish_at is None:
        return PublishResult(ok=False, message="No release time was given.")
    # The release makes it public, whatever visibility the request names.
    refusal = gates.visibility_refusal(SPEC.provider, ctx.config, "public")
    if refusal:
        return PublishResult(ok=False, message=refusal)
    publish_at = req.publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = {"privacyStatus": "private", "publishAt": publish_at}
    video_id, error = _upload(ctx, req, status, on_progress)
    if error:
        return PublishResult(ok=False, message=error)
    return PublishResult(
        ok=True,
        remote_id=video_id,
        remote_url=_watch_url(video_id),
        message=f"Uploaded to YouTube as private; YouTube makes it public at {publish_at}.",
    )


def scheduled_state(ctx: ConnCtx, remote_id: str) -> ScheduleCheck:
    """Whether YouTube has made a scheduled video public (1 quota unit)."""
    try:
        token = _access_token(ctx)
        resp = requests.get(
            f"{API_URL}/videos",
            headers={"Authorization": f"Bearer {token}"},
            params={"part": "status", "id": remote_id},
            timeout=TIMEOUT,
        )
    except (ValueError, RuntimeError) as e:
        return ScheduleCheck(state=SCHEDULE_PENDING, message=f"Could not check: {e}")
    except requests.RequestException as e:
        return ScheduleCheck(state=SCHEDULE_PENDING, message=f"Could not reach YouTube: {e}")
    if resp.status_code >= 400:
        return ScheduleCheck(state=SCHEDULE_PENDING, message=f"YouTube returned {resp.status_code}.")

    items = (resp.json() or {}).get("items") or []
    if not items:
        return ScheduleCheck(state=SCHEDULE_GONE, message="The video is no longer on YouTube.")
    status = items[0].get("status") or {}
    upload = status.get("uploadStatus")
    if upload in ("failed", "rejected", "deleted"):
        reason = status.get("failureReason") or status.get("rejectionReason") or upload
        return ScheduleCheck(state=SCHEDULE_GONE, message=f"YouTube did not publish it ({reason}).")
    if status.get("privacyStatus") == "public":
        return ScheduleCheck(state=SCHEDULE_PUBLISHED, remote_url=_watch_url(remote_id))
    return ScheduleCheck(state=SCHEDULE_PENDING)


def delete(ctx: ConnCtx, req: DeleteRequest) -> DeleteResult:
    """Delete the video from the channel (videos.delete, 50 quota units)."""
    try:
        token = _access_token(ctx)
        resp = requests.delete(
            f"{API_URL}/videos",
            headers={"Authorization": f"Bearer {token}"},
            params={"id": req.remote_id},
            timeout=TIMEOUT,
        )
    except (ValueError, RuntimeError) as e:
        return DeleteResult(ok=False, message=str(e))
    except requests.RequestException as e:
        return DeleteResult(ok=False, message=f"Could not reach YouTube: {e}")
    if resp.status_code == 404:
        return DeleteResult(ok=True, message="The video was already gone from YouTube.")
    if resp.status_code >= 400:
        return DeleteResult(ok=False, message=f"YouTube returned {resp.status_code}.")
    return DeleteResult(ok=True, message="Deleted from YouTube.")
