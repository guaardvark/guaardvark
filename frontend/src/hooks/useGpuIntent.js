/**
 * useGpuIntent — Signals frontend navigation intent to the GPU orchestrator.
 *
 * Mount ONCE in AppLayout (alongside useNavigationCancel). On every route
 * change it tells the backend which page the user navigated to, so the
 * orchestrator can predictively load/unload GPU models.
 *
 * - Debounces 300ms to collapse rapid page switches
 * - Uses fetch (marked _persistent so useNavigationCancel won't abort it)
 * - Fires and forgets — never blocks navigation
 *
 * startPluginsForRoute is the same signal for a person's Start click.
 */

import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { BASE_URL, handleResponse } from "../api/apiClient";

const INTENT_URL = `${BASE_URL}/gpu/memory/intent`;

/**
 * A person clicked Start for the plugins `route` needs. The backend lifts
 * their earlier disable and runs the navigation path, so a plugin it starts is
 * stopped again when they leave that page; one already running stays up.
 * Resolves to the backend's answer: { success, error, started: [{ plugin_id,
 * running, outcome, error }] }. Waits for the start, which can take minutes.
 */
export async function startPluginsForRoute(route) {
  const response = await fetch(INTENT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ route, user_start: true }),
    _persistent: true, // Exempt from useNavigationCancel
  });
  return handleResponse(response);
}

export default function useGpuIntent() {
  const location = useLocation();
  const previousPath = useRef(location.pathname);
  const debounceRef = useRef(null);

  useEffect(() => {
    if (previousPath.current === location.pathname) return;
    previousPath.current = location.pathname;

    // Debounce: if user clicks through pages rapidly, only signal the final one
    if (debounceRef.current) clearTimeout(debounceRef.current);

    debounceRef.current = setTimeout(() => {
      try {
        fetch(INTENT_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ route: location.pathname }),
          _persistent: true, // Exempt from useNavigationCancel
        }).catch(() => {
          // Fire-and-forget — silent on failure
        });
      } catch {
        // fetch itself may throw if offline
      }
    }, 300);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [location.pathname]);
}
