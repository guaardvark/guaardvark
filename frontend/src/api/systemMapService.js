// frontend/src/api/systemMapService.js
// Fetches the SystemMap snapshot from the backend.
// Backed by /api/system-map/snapshot — see backend/api/system_map_api.py.

import { BASE_URL, handleResponse } from "./apiClient";

/**
 * Fetch the current SystemMap. Cached server-side (5-min TTL); pass
 * { refresh: true } to force a re-compute.
 *
 * Returns the SystemMap dict from backend.services.system_mapper.SystemMap:
 *   { root, generated_at, languages, file_count,
 *     dependency_graph, reachability, tool_graph,
 *     findings, stats, _cache }
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
