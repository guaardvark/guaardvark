"""
Self-share loop — submits a link post to a subreddit, with the title/body
drafted by the LLM in the user's voice (per persona.SHARE_FRAMING).

Same hybrid as reddit_outreach: HTTP for rules check + dedupe; servo for the
actual submit. Different recipe sequence (link post, not comment).
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Callable, Optional

import requests

from backend.services.social_outreach import audit, kill_switch, persona
from backend.services.social_outreach.reddit_outreach import (
    web_access_refused,
    REDDIT_BASE,
    fetch_subreddit_rules,
    reddit_reading,
    is_self_promo_banned,
    backend_headers,
    backend_url,
    bidi_evaluate_json,
    bidi_reachable,
    page_check_unavailable_reason,
    SERVO_SETTLE_SECONDS,
    _bidi_navigate,
    _same_text,
)
from backend.services.social_outreach.transitions import WITHDRAWN_BEFORE_SUBMIT

logger = logging.getLogger(__name__)

# Reads the page after the submit: its address, whether the title is on it
# (outside any text box, compared with all whitespace removed), and, for a
# subreddit feed, the permalink of a listed post with this title and link
# created since the submit. Reddit sometimes returns to the feed after Post.
_POST_CHECK_JS = r"""(() => {
  const squash = (s) => (s || '').replace(/\s+/g, '');
  const want = squash(__TITLE__);
  const link = squash(__LINK__).replace(/\/+$/, '');
  const since = __SINCE__;
  const when = (s) => Date.parse((s || '').replace(/(\.\d{3})\d*/, '$1').replace(/([+-]\d\d)(\d\d)$/, '$1:$2')) || 0;
  let listed = '';
  for (const p of document.querySelectorAll('shreddit-post')) {
    const href = squash(p.getAttribute('content-href')).replace(/\/+$/, '');
    if (want && squash(p.getAttribute('post-title')) === want && (!link || href === link)
        && when(p.getAttribute('created-timestamp')) >= since) {
      listed = p.getAttribute('permalink') || '';
      break;
    }
  }
  const SKIP = 'script, style, noscript, template, textarea, [contenteditable]:not([contenteditable="false"])';
  let text = document.title || '';
  const walk = (root) => {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeType === 1 && n.matches(SKIP))
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    let n;
    while ((n = w.nextNode())) {
      if (n.nodeType === 3) text += n.nodeValue;
      else if (n.shadowRoot) walk(n.shadowRoot);
    }
  };
  walk(document.body || document.documentElement);
  return JSON.stringify({url: location.href, title_on_page: !!want && squash(text).includes(want), listed});
})()"""


# Reads the link-post form: its kind, the title and link it holds, and the
# Post button (centre in screen pixels, after scrolling it into view). The
# fields sit inside the form's components, so shadow roots are searched too.
_FORM_JS = r"""(() => {
  const deep = (root, sel) => {
    const hit = root.querySelector(sel);
    if (hit) return hit;
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) { const h = deep(el.shadowRoot, sel); if (h) return h; }
    }
    return null;
  };
  const kind = document.getElementById('post-composer_type');
  const title = deep(document, 'textarea[name="title"]');
  const link = deep(document, 'textarea[name="link"]');
  const post = deep(document, '#inner-post-submit-button');
  let button = null;
  if (post) {
    post.scrollIntoView({block: 'center', behavior: 'instant'});
    const r = post.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    button = {
      disabled: !!post.disabled || post.getAttribute('aria-disabled') === 'true',
      shown: r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight,
      x: Math.round((window.mozInnerScreenX + r.left + r.width / 2) * dpr),
      y: Math.round((window.mozInnerScreenY + r.top + r.height / 2) * dpr),
    };
  }
  return JSON.stringify({
    url: location.href, kind: kind ? kind.value : '',
    title: title ? title.value : null, link: link ? link.value : null, button,
  });
})()"""

# How long the submit form gets to render, and the new post to load after Post.
_FORM_WAIT_S = 10.0
_LANDED_WAIT_S = 20.0


def _read_form() -> tuple[Optional[dict], str]:
    return bidi_evaluate_json(_FORM_JS)


def _form_problem(form: Optional[dict], subreddit: str, title: str, link_url: str) -> str:
    """Why the form must not be submitted, or "" when it holds exactly the draft."""
    from urllib.parse import urlparse

    if form is None:
        return "the form could not be read"
    path = urlparse(form.get("url") or "").path.rstrip("/").lower()
    if path != f"/r/{subreddit}/submit".lower():
        return f"the browser is on {str(form.get('url') or '')[:200]}, not r/{subreddit}'s submit form"
    if form.get("kind") != "LINK":
        return f"the form is a {form.get('kind') or 'unknown'} post, not a link post"
    if form.get("title") is None or not _same_text(form["title"], title):
        return f"the title field holds {str(form.get('title') or '')[:80]!r}, not the approved title"
    if (form.get("link") or "").strip() != link_url.strip():
        return f"the link field holds {str(form.get('link') or '')[:120]!r}, not the approved link"
    button = form.get("button") or {}
    if not button or button.get("disabled") or not button.get("shown"):
        return "the Post button is missing, disabled or off screen"
    return ""


def _is_post_url(url: str, subreddit: str) -> bool:
    """True when ``url`` is a post in ``subreddit``: reddit.com/r/<sub>/comments/<id>/."""
    from urllib.parse import urlparse

    from backend.utils.hosts import host_matches

    parsed = urlparse(url or "")
    if not host_matches(parsed.hostname, "reddit.com"):
        return False
    pattern = rf"/r/{re.escape(subreddit)}/comments/[A-Za-z0-9]+(?:/|$)"
    return re.match(pattern, parsed.path, re.IGNORECASE) is not None


def _post_landed(subreddit: str, title: str, link_url: str = "", since: float = 0.0) -> tuple[bool, str]:
    """Read the page after the submit; ``(landed, url)``.

    Landed means the browser is on the new post's page in ``subreddit`` with
    the title on it, or on a feed that lists a post in ``subreddit`` with this
    title and link created at or after ``since`` (seconds since the epoch).
    A failed submit leaves the browser on /submit.
    """
    script = (
        _POST_CHECK_JS.replace("__TITLE__", json.dumps(title.strip()))
        .replace("__LINK__", json.dumps((link_url or "").strip()))
        .replace("__SINCE__", str(int(since * 1000)))
    )
    data, why = bidi_evaluate_json(script)
    if data is None:
        return False, f"page not readable ({why})"
    url = str(data.get("url") or "")[:300]
    listed = str(data.get("listed") or "")
    if listed and _is_post_url(REDDIT_BASE + listed, subreddit):
        return True, REDDIT_BASE + listed
    if not _is_post_url(url, subreddit):
        return False, url
    if not data.get("title_on_page"):
        return False, f"{url} (title not on the page)"
    return True, url


def _human_pause(min_s: float = 0.3, max_s: float = 2.0) -> None:
    """Random sleep to avoid deterministic bot timing fingerprints.
    
    Don't make this call site-specific — uniform jitter across all servo
    actions is fine. Cross-platform spam filters look for *constant* delays
    much more than for specific values.
    """
    time.sleep(random.uniform(min_s, max_s))


def _draft_share(subreddit: str, link_url: str, task_id: Optional[int]) -> Optional[dict]:
    try:
        resp = requests.post(
            f"{backend_url()}/social-outreach/draft-comment",
            json={
                "platform": "reddit",
                "mode": "share",
                "share_target": f"r/{subreddit}",
                "share_link": link_url,
                "target_url": f"{REDDIT_BASE}/r/{subreddit}",
                "task_id": task_id,
            },
            headers=backend_headers(),
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("share draft failed: %s", e)
        return None


def _submit_post_via_servo(
    subreddit: str,
    title: str,
    link_url: str,
    *,
    before_submit: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """Submit a link post to ``subreddit`` through the agent's Firefox on :99.

    Reddit's submit page takes the link and title in its address (the way its
    own share buttons fill it), so nothing is typed: the form is opened filled,
    read back, and posted only when it holds exactly the approved title and
    link. The title never reaches a model prompt. Success also needs the page
    after the submit to be the new post with the title on it
    (``_post_landed``); otherwise the reason is ``submit_unverified: <url>``.

    ``before_submit`` is called once the form is read back and immediately
    before the click that publishes; when it returns False nothing is
    published and the reason is ``transitions.WITHDRAWN_BEFORE_SUBMIT``.
    """
    from urllib.parse import urlencode

    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.utils.agent_display_utils import start_agent_display_if_needed

    if not (title or "").strip() or not (link_url or "").strip():
        return False, "share row missing title or link"

    if get_agent_control_service().is_active:
        return False, "agent_busy"

    if not start_agent_display_if_needed():
        logger.warning("display not available for self_share: start failed")
        return False, "display_unavailable"

    try:
        screen = LocalScreenBackend()
    except Exception as e:
        logger.warning("display not available for self_share: %s", e)
        return False, "display_unavailable"
    readable, why = bidi_reachable()
    if not readable:
        return False, page_check_unavailable_reason(why)

    submit_url = f"{REDDIT_BASE}/r/{subreddit}/submit/?" + urlencode(
        {"type": "LINK", "url": link_url.strip(), "title": title.strip()}
    )
    if not _bidi_navigate(submit_url, settle_seconds=SERVO_SETTLE_SECONDS, nav_timeout=30.0):
        return False, "navigate_failed: nothing was posted; the submit form did not load"

    deadline = time.monotonic() + _FORM_WAIT_S
    while True:
        form, why = _read_form()
        problem = _form_problem(form, subreddit, title, link_url)
        if not problem or time.monotonic() >= deadline:
            break
        time.sleep(1.0)
    if problem:
        logger.info("self_share: r/%s form not ready: %s (%s)", subreddit, problem, why)
        return False, f"form_not_ready: nothing was posted; {problem}"
    _human_pause()

    if before_submit is not None and not before_submit():
        return False, WITHDRAWN_BEFORE_SUBMIT
    # Clocks differ between this machine and Reddit, so a listed post may
    # carry a time a little before the click.
    clicked_at = time.time() - 120.0
    screen.click(int(form["button"]["x"]), int(form["button"]["y"]))

    # The post counts only once the browser is on the new post with the
    # title on it; a refused submit leaves it on the form.
    deadline = time.monotonic() + _LANDED_WAIT_S
    while True:
        time.sleep(2.0)
        landed, where = _post_landed(subreddit, title, link_url, clicked_at)
        if landed or time.monotonic() >= deadline:
            break
    logger.info("self_share: post-submit check r/%s: landed=%s %s", subreddit, landed, where)
    if not landed:
        return False, f"submit_unverified: {where}"
    return True, "ok"


class SelfShareLoop:
    def run_one_pass(self, subreddit: str, link_url: str, task_id: Optional[int] = None) -> dict:
        with reddit_reading():
            return self._run_one_pass(subreddit, link_url, task_id)

    def _run_one_pass(self, subreddit: str, link_url: str, task_id: Optional[int] = None) -> dict:
        report = {
            "subreddit": subreddit,
            "link_url": link_url,
            "drafted": 0,
            "posted": 0,
            "aborted": 0,
            "skipped": 0,
            "reason": None,
        }

        if not subreddit or not link_url:
            report["reason"] = "missing_args"
            return report

        if not kill_switch.is_enabled():
            report["reason"] = "kill_switch_off"
            audit.log_outreach_event(
                platform="reddit", action="abort",
                target_url=f"{REDDIT_BASE}/r/{subreddit}",
                status="aborted", abort_reason="kill_switch_off",
                task_id=task_id,
            )
            return report
        if web_access_refused(subreddit, report, task_id):
            return report

        rules = fetch_subreddit_rules(subreddit)
        if rules is None:
            report["reason"] = "rules_unreadable"
            audit.log_outreach_event(
                platform="reddit", action="abort",
                target_url=f"{REDDIT_BASE}/r/{subreddit}",
                status="aborted", abort_reason="rules_unreadable",
                task_id=task_id,
            )
            report["aborted"] += 1
            return report
        ban_match = is_self_promo_banned("\n".join(rules))
        if ban_match:
            report["reason"] = f"no_self_promo_rule:{ban_match}"
            audit.log_outreach_event(
                platform="reddit", action="abort",
                target_url=f"{REDDIT_BASE}/r/{subreddit}",
                status="aborted",
                abort_reason=f"sub bans self-promo: {ban_match}",
                task_id=task_id,
            )
            report["aborted"] += 1
            return report

        cadence_ok, cadence_reason = kill_switch.cadence_allows_post("reddit")
        if not cadence_ok:
            report["reason"] = f"cadence_block:{cadence_reason}"
            return report

        draft = _draft_share(subreddit, link_url, task_id)
        if not draft:
            report["reason"] = "draft_failed"
            return report

        report["drafted"] += 1
        # Draft-only: a person approves the draft and starts the post
        # (watched_post). Never servo-post from this loop.
        if draft.get("would_post"):
            report["queued_for_post"] = 1
            report["reason"] = "queued_for_process_approved"
        else:
            report["reason"] = "awaiting_approval_or_gated"
        return report
