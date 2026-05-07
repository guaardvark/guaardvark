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
