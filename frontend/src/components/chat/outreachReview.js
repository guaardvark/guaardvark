// The chat side of a one-at-a-time outreach review.
//
// /outreach opens a review for a chat session. While it is open, the chat
// hands each message it is about to send (typed or spoken) to answerInReview
// first: the server reads it as an answer to the draft on show ("yes",
// "skip", "change it to ...") and the chat model never sees it. A "yes"
// comes back as a post to carry out, which runPost does: it opens the Agent
// Screen when the platform posts through the watched browser, waits out the
// delay a spoken "yes" carries, and then asks the server to approve and post.
import {
  answerReview,
  fetchReview,
  fetchWatchedPost,
  postReview,
  startReview,
} from "../../api/outreachService";
import { useAppStore } from "../../stores/useAppStore";
import { translate } from "../../i18n";

const POLL_MS = 3000;
// How long the Agent Screen gets to start streaming before the post is tried.
const SCREEN_WAIT_MS = 30000;
// A scout or a watched post that has not ended by now is no longer followed.
const FOLLOW_MS = 15 * 60 * 1000;

const open = new Set();
const resuming = new Set();
let seq = 0;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const messageId = (kind) => `outreach-review-${kind}-${Date.now()}-${seq++}`;
const message = (fields) => ({
  role: "system",
  type: "command",
  timestamp: new Date().toISOString(),
  ...fields,
});

/** True while this chat's messages are answers to a review. */
export const reviewOpen = (sessionId) => open.has(sessionId);

const say = (ctx, content) => {
  if (content) ctx.addMessage(message({ content, tempId: messageId("say") }));
};

const showItem = (ctx, view) => {
  const draft = view.current;
  const remaining = view.remaining || 0;
  ctx.addMessage(
    message({
      content: translate(
        remaining > 0 ? "approvals.outreach.chat.reviewItemMore" : "approvals.outreach.chat.reviewItem",
        { id: draft.id, platform: draft.platform, count: remaining }
      ),
      tempId: messageId("item"),
      outreach: { draftIds: [draft.id], review: true },
    })
  );
};

// Follow a view until it has a draft to show (a scout may still be running),
// then show it. Returns false when the review ended with nothing to show.
async function settle(sessionId, first, ctx) {
  let view = first;
  const until = Date.now() + FOLLOW_MS;
  while (view.open && view.waiting && Date.now() < until) {
    await sleep(POLL_MS);
    view = await fetchReview(sessionId);
  }
  if (!view.open) {
    open.delete(sessionId);
    return false;
  }
  open.add(sessionId);
  if (view.current && !view.waiting) showItem(ctx, view);
  return true;
}

/**
 * Open a review for this chat. `body` is { follow } for a queued scout,
 * { draft_ids } for drafts that exist, or { waiting: true, platform } for
 * everything that waits for approval. `ctx` is { addMessage }.
 * Resolves to false when there was nothing to review.
 */
export async function beginReview(sessionId, body, ctx) {
  const view = await startReview({ session_id: sessionId, ...body });
  if (view.open) open.add(sessionId);
  return settle(sessionId, view, ctx);
}

/**
 * Pick a review back up after the chat was reloaded or reopened.
 * `ctx.hasItem(draftId)` (optional) says the chat already shows that draft.
 */
export async function resumeReview(sessionId, ctx) {
  if (!sessionId || resuming.has(sessionId)) return;
  resuming.add(sessionId);
  try {
    const view = await fetchReview(sessionId);
    if (!view.open) {
      open.delete(sessionId);
      return;
    }
    open.add(sessionId);
    if (view.waiting) await settle(sessionId, view, ctx);
    else if (view.current && !ctx.hasItem?.(view.current.id)) showItem(ctx, view);
  } catch {
    // The chat works without the review; the drafts stay on the Approvals page.
  } finally {
    resuming.delete(sessionId);
  }
}

async function runPost(sessionId, post, ctx) {
  try {
    const due = Date.now() + (post.delay_ms || 0);
    if (post.needs_screen) {
      useAppStore.getState().setAgentScreenOpen(true);
      const until = Date.now() + SCREEN_WAIT_MS;
      while (Date.now() < until) {
        const watched = await fetchWatchedPost().catch(() => null);
        if (watched?.watching) break;
        await sleep(1000);
      }
    }
    if (Date.now() < due) await sleep(due - Date.now());
    let out = await postReview({ session_id: sessionId, draft_id: post.id });
    if (out.early) {
      await sleep(500);
      out = await postReview({ session_id: sessionId, draft_id: post.id });
    }
    // A "stop" took the yes back; that answer already said so.
    if (out.cancelled) return;
    say(ctx, out.message);

    let view = out;
    const until = Date.now() + FOLLOW_MS;
    while (view.open && view.posting && Date.now() < until) {
      await sleep(POLL_MS);
      view = await fetchReview(sessionId);
    }
    if (out.started && view.last?.id === post.id && view.last.message !== out.message) {
      say(ctx, view.last.message);
    }
    if (!view.open) {
      open.delete(sessionId);
      say(ctx, translate("approvals.outreach.chat.reviewDone"));
      return;
    }
    if (view.current && !view.posting && view.current.id !== post.id) showItem(ctx, view);
  } catch (err) {
    say(ctx, translate("approvals.outreach.chat.reviewFailed", { reason: err.message }));
  }
}

/**
 * Give a message the chat is about to send to the open review. Resolves to
 * true when the review took it (the chat must not send it to the model).
 * `ctx` is { addMessage }; `spoken` marks a voice turn.
 */
export async function answerInReview(sessionId, text, { spoken = false } = {}, ctx) {
  if (!open.has(sessionId)) return false;
  const mine = message({
    role: "user",
    content: text,
    tempId: messageId("answer"),
    voice: spoken || undefined,
  });
  let res;
  try {
    res = await answerReview({ session_id: sessionId, text, spoken });
  } catch (err) {
    // Unknown whether the review is open: keep the message away from the model.
    ctx.addMessage(mine);
    say(ctx, translate("approvals.outreach.chat.reviewFailed", { reason: err.message }));
    return true;
  }
  if (!res.handled) {
    open.delete(sessionId);
    return false;
  }
  ctx.addMessage(mine);
  say(ctx, res.say);
  if (!res.open) open.delete(sessionId);
  if (res.post) {
    // Not awaited: the chat stays free for a "stop" while the post waits.
    runPost(sessionId, res.post, ctx);
  } else if (res.current && ["skip", "reject", "edit", "redraft"].includes(res.kind)) {
    showItem(ctx, res);
  }
  return true;
}
