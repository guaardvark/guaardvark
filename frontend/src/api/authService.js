// frontend/src/api/authService.js
// This install's API key, network access and this browser's sign-in
// (backend/api/auth_api.py).
// The key is sent to signIn once and never kept; the browser then holds an
// HttpOnly cookie it sends by itself (see apiAuth.js).
/* eslint-env browser */

import { BASE_URL, handleResponse } from "./apiClient";

const API_KEY_HEADER = "X-API-Key";
const JSON_BODY = { "Content-Type": "application/json" };

/**
 * { key_required, this_machine, key_ok, session_ok, session_rejected,
 *   can_run_protected, can_manage_key, manage_note, restart_needed, docker,
 *   tool_endpoints_protected, protected, machine, network_access,
 *   can_manage_network_access, network_access_note, on_local_network }
 * @param {{key?: string}} [options] test this typed key (X-API-Key) without
 *   signing in with it
 */
export const getAuthStatus = async ({ key } = {}) =>
  handleResponse(
    await fetch(`${BASE_URL}/auth/status`, {
      cache: "no-store",
      headers: key ? { [API_KEY_HEADER]: key } : {},
    }),
  );

/** Sign this browser in with the key. { session: true } */
export const signIn = async (key) =>
  handleResponse(
    await fetch(`${BASE_URL}/auth/session`, { method: "POST", headers: JSON_BODY, body: JSON.stringify({ key }) }),
  );

/** Sign this browser out. { session: false } */
export const signOut = async () =>
  handleResponse(await fetch(`${BASE_URL}/auth/session`, { method: "DELETE" }));

/** { key } — shown once; the same response signs this browser in with it. */
export const createApiKey = async () =>
  handleResponse(await fetch(`${BASE_URL}/auth/key`, { method: "POST", headers: JSON_BODY, body: "{}" }));

/** { key } — the old key and every sign-in made with it stop working at once. */
export const replaceApiKey = async () =>
  handleResponse(await fetch(`${BASE_URL}/auth/key`, { method: "PUT", headers: JSON_BODY, body: "{}" }));

/** { removed } — signs this browser out too. */
export const removeApiKey = async () =>
  handleResponse(await fetch(`${BASE_URL}/auth/key`, { method: "DELETE" }));

/** { network_access } — on: every device on the local network may run protected actions. */
export const setNetworkAccess = async (enabled) =>
  handleResponse(
    await fetch(`${BASE_URL}/auth/network-access`, {
      method: "POST",
      headers: JSON_BODY,
      body: JSON.stringify({ enabled }),
    }),
  );
