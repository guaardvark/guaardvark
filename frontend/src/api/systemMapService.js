// frontend/src/api/systemMapService.js
// Fetches the SystemMap snapshot from the backend.
// Backed by /api/system-map/snapshot — see backend/api/system_map_api.py.

import { BASE_URL, handleResponse } from "./apiClient";

/**
 * Fetch the stored SystemMap. A read never rebuilds: the map comes back with
 * `_cache: { stale, refreshing, age_seconds }`, and a stale map is rebuilt in
 * the background. Before the first map exists the answer is
 * `{ success: false, status: "computing" }` (HTTP 202); ask again shortly.
 * `{ refresh: true }` also starts a rebuild.
 *
 * Returns the SystemMap dict from backend.services.system_mapper.SystemMap:
 *   { root, generated_at, languages, file_count, dependency_graph,
 *     lazy_imports, node_meta, reachability, tool_graph, celery_tasks,
 *     subsystems, findings, stats, _cache }
 */
export async function fetchSystemMap({ refresh = false, root = null } = {}) {
  const params = new URLSearchParams();
  if (refresh) params.set("refresh", "1");
  if (root) params.set("root", root);
  const qs = params.toString();
  const url = `${BASE_URL}/system-map/snapshot${qs ? `?${qs}` : ""}`;
  const resp = await fetch(url, { method: "GET" });
  return handleResponse(resp);
}

/**
 * Fetch the ranked findings list (lightweight — findings only, not the graph).
 * Each finding: { id, kind, severity, summary, paths, evidence, dismissed,
 *                 dispatchable }. Returns { findings, counts, total, stats }.
 */
export async function fetchFindings({
  root = null,
  severity = null,
  kind = null,
  includeDismissed = false,
} = {}) {
  const params = new URLSearchParams();
  if (root) params.set("root", root);
  if (severity) params.set("severity", severity);
  if (kind) params.set("kind", kind);
  if (includeDismissed) params.set("include_dismissed", "1");
  const qs = params.toString();
  const url = `${BASE_URL}/system-map/findings${qs ? `?${qs}` : ""}`;
  const resp = await fetch(url, { method: "GET" });
  return handleResponse(resp);
}

/**
 * Hand a finding to the self-improvement agent as a directed task. The agent
 * proposes a fix (staged as a PendingFix for human review in Settings).
 */
export async function dispatchFinding(findingId, { root = null, priority = "medium" } = {}) {
  const resp = await fetch(`${BASE_URL}/system-map/findings/${findingId}/dispatch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ root, priority }),
  });
  return handleResponse(resp);
}

/** Dismiss (or un-dismiss) a finding so it stops showing. Persisted server-side. */
export async function dismissFinding(findingId, { root = null, undo = false } = {}) {
  const resp = await fetch(`${BASE_URL}/system-map/findings/${findingId}/dismiss`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ root, undo }),
  });
  return handleResponse(resp);
}

/** Start rebuilding the map in the background; poll fetchSystemMap for it. */
export async function refreshSystemMap({ root = null } = {}) {
  const resp = await fetch(`${BASE_URL}/system-map/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ root }),
  });
  return handleResponse(resp);
}

/** Ask the map a question: { mode, q } → { answer } (text). */
export async function querySystemMap({ mode = "", q = "" } = {}) {
  const params = new URLSearchParams();
  if (mode) params.set("mode", mode);
  params.set("q", q);
  const resp = await fetch(`${BASE_URL}/system-map/query?${params.toString()}`, { method: "GET" });
  return handleResponse(resp);
}

/** Whether the checkout has a code search index, and whether one is building. */
export async function fetchCodeIndexStatus() {
  const resp = await fetch(`${BASE_URL}/system-map/code-index`, { method: "GET" });
  return handleResponse(resp);
}

/** Build or refresh the code search index with the zvec_grep plugin, in the background. */
export async function buildCodeIndex() {
  const resp = await fetch(`${BASE_URL}/system-map/code-index`, { method: "POST" });
  return handleResponse(resp);
}
