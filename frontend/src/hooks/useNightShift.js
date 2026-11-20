// frontend/src/hooks/useNightShift.js
//
// The night shift as /api/night-shift/status reports it, re-read on a timer
// and whenever this tab schedules, reorders or pauses something.
import { useCallback, useEffect, useRef, useState } from "react";
import { getNightShiftStatus, NIGHT_SHIFT_CHANGED_EVENT } from "../api/nightShiftService";

const useNightShift = (pollMs = 15000) => {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const data = await getNightShiftStatus();
      if (!alive.current) return null;
      setStatus(data);
      setError(null);
      return data;
    } catch (e) {
      // Keep the last reading; the next poll tries again.
      if (alive.current) setError(e?.message || String(e));
      return null;
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    refresh();
    const timer = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) refresh();
    }, pollMs);
    window.addEventListener(NIGHT_SHIFT_CHANGED_EVENT, refresh);
    return () => {
      alive.current = false;
      clearInterval(timer);
      window.removeEventListener(NIGHT_SHIFT_CHANGED_EVENT, refresh);
    };
  }, [refresh, pollMs]);

  return { status, error, refresh };
};

// Jobs that have not started: the night's line, then timed, then held.
export const scheduledCount = (status) =>
  (status?.waiting?.length || 0) + (status?.at?.length || 0) + (status?.held?.length || 0);

export default useNightShift;
