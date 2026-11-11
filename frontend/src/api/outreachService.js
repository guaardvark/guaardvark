/**
 * Shared Outreach API client — used by OutreachPage and /outreach slash.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

async function parseError(res) {
  const body = await res.json().catch(() => ({}));
  return body.error || body.message || `HTTP ${res.status}`;
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}/social-outreach${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  if (res.status === 204) return null;
  return res.json();
}

export function getOutreachBaseUrl() {
  return BASE_URL;
}

export function fetchStatus() {
  return request("/status");
}

export function fetchQueue() {
  return request("/queue");
}

export function fetchApproved() {
  return request("/approved");
}

export function fetchAudit(limit = 100) {
  return request(`/audit?limit=${limit}`);
}

export function fetchSnippets() {
  return request("/snippets");
}

// The local "what you promote" profile behind drafts, links and snippets.
export function fetchProfile() {
  return request("/profile");
}

export function saveProfile(profile) {
  return request("/profile", {
    method: "PUT",
    body: JSON.stringify({ profile }),
  });
}

export function enableOutreach() {
  return request("/enable", { method: "POST" });
}

export function killOutreach() {
  return request("/kill", { method: "POST" });
}

// The stop on ALL public posting (outreach and Connections), separate from
// the outreach Enabled switch.
export function stopAllPosting() {
  return request("/stop-posting", { method: "POST" });
}

export function resumePosting() {
  return request("/resume-posting", { method: "POST" });
}

export function approveDraft(id, draftText) {
  const body = {};
  if (draftText != null) body.draft_text = draftText;
  return request(`/approve/${id}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function rejectDraft(id) {
  return request(`/reject/${id}`, { method: "POST", body: "{}" });
}

// Reddit, YouTube and Facebook drafts post one at a time, each started by a
// person watching the Agent Screen. This reads whether the viewer is
// streaming, the post running now, how the last one ended, and the approved
// drafts waiting to be started.
export function fetchWatchedPost() {
  return request("/post-now");
}

// "Post now, I'm watching": starts the one browser post for an approved draft.
export function postNow(id) {
  return request(`/post-now/${id}`, { method: "POST", body: "{}" });
}

// The rows with these ids as a person reads them: `text` is what would post.
export function fetchDraftsByIds(ids) {
  return request(`/drafts?ids=${(ids || []).join(",")}`);
}

// Where a run started by executeIntent stands; `follow` is the value that
// answer carried ({ task_ids, after_id, platform }).
export function fetchRunResult(follow) {
  const params = new URLSearchParams({
    task_ids: (follow.task_ids || []).join(","),
    after_id: String(follow.after_id ?? 0),
  });
  if (follow.platform) params.set("platform", follow.platform);
  return request(`/run-result?${params.toString()}`);
}

export function patchDraft(id, payload) {
  return request(`/drafts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function createDraft(payload) {
  return request("/drafts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function draftComment(payload) {
  return request("/draft-comment", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function scoutUrl(url) {
  return request("/scout-url", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function fetchMeta(url) {
  return request("/fetch-meta", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function runPass(payload) {
  return request("/run-pass", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function executeIntent(payload) {
  return request("/intent", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export default {
  getOutreachBaseUrl,
  fetchStatus,
  fetchQueue,
  fetchApproved,
  fetchAudit,
  fetchSnippets,
  fetchProfile,
  saveProfile,
  enableOutreach,
  killOutreach,
  stopAllPosting,
  resumePosting,
  approveDraft,
  rejectDraft,
  fetchWatchedPost,
  postNow,
  patchDraft,
  createDraft,
  draftComment,
  scoutUrl,
  fetchMeta,
  runPass,
  executeIntent,
};
