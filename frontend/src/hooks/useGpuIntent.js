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
 */

import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { BASE_URL } from "../api/apiClient";

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
        fetch(`${BASE_URL}/gpu/memory/intent`, {
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
