import { useCallback, useEffect, useRef, useState } from "react";
import { fetchPublishes } from "../api/connectionsService";
import { inboundGuardService } from "../api/inboundGuardService";
import { isActionable } from "../api/heldChanges";
import { fetchQueue as fetchOutreachQueue, fetchWatchedPost } from "../api/outreachService";
import { translate } from "../i18n";

// A pending publish has no Task row until it is approved, so it is invisible to
// the jobs API. The queue and the sidebar badge both read the publish records
// directly, and share this hook so they cannot disagree about the count. Code
// the inbound guard holds, outreach drafts waiting for approval and approved
// Reddit/YouTube/Facebook drafts waiting for a person's "Post now" wait on the
// same person, so they count here too.
const POLL_MS = 30000;
const LIMIT = 200;

/**
 * Track publishes awaiting approval and code changes the inbound guard holds.
 *
 * @param {object}  options
 * @param {boolean} options.notify  raise a desktop notification when a count rises
 * @returns {{count: number, pending: object[], held: object[], outreach: object[],
 *            outreachWaiting: object[], loading: boolean, heldLoading: boolean,
 *            error: string|null, refresh: function}}
 */
export const usePendingApprovals = ({ notify = false } = {}) => {
  const [pending, setPending] = useState([]);
  const [held, setHeld] = useState([]);
  const [outreach, setOutreach] = useState([]);
  const [outreachWaiting, setOutreachWaiting] = useState([]);
  const [outreachLoading, setOutreachLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [heldLoading, setHeldLoading] = useState(true);
  const [error, setError] = useState(null);
  // Notify on a rising edge only — a poll that finds the same queue is not news.
  const previousCount = useRef(null);
  const previousHeld = useRef(null);

  const refreshHeld = useCallback(async () => {
    try {
      const res = await inboundGuardService.listScans("open", LIMIT, { git: false });
      const rows = (res?.data?.scans || []).filter(isActionable);
      setHeld(rows);
      return rows;
    } catch {
      // The guard may be off or the route refused; publishes still count.
      return null;
    } finally {
      setHeldLoading(false);
    }
  }, []);

  const refreshOutreach = useCallback(async () => {
    try {
      const [rows, watched] = await Promise.all([
        fetchOutreachQueue(),
        fetchWatchedPost().catch(() => null),
      ]);
      setOutreach(Array.isArray(rows) ? rows : []);
      setOutreachWaiting(Array.isArray(watched?.waiting) ? watched.waiting : []);
      return rows;
    } catch {
      // Outreach routes answer only this machine without an API key.
      return null;
    } finally {
      setOutreachLoading(false);
    }
  }, []);

  const refreshPublishes = useCallback(async () => {
    try {
      const rows = await fetchPublishes({
        status: "awaiting_approval",
        limit: LIMIT,
      });
      setPending(rows);
      setError(null);
      return rows;
    } catch (err) {
      setError(err?.message || translate("messages.approvals.loadFailed"));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(
    async () => (await Promise.all([refreshPublishes(), refreshHeld(), refreshOutreach()]))[0],
    [refreshPublishes, refreshHeld, refreshOutreach],
  );

  useEffect(() => {
    let active = true;
    const tick = async () => {
      const [rows, heldRows] = await Promise.all([refreshPublishes(), refreshHeld(), refreshOutreach()]);
      if (!active) return;
      if (rows !== null) {
        const previous = previousCount.current;
        previousCount.current = rows.length;
        if (notify && previous !== null && rows.length > previous) {
          raiseDesktopNotification(rows.length - previous, rows);
        }
      }
      if (heldRows !== null) {
        const previous = previousHeld.current;
        previousHeld.current = heldRows.length;
        if (notify && previous !== null && heldRows.length > previous) {
          raiseHeldNotification(heldRows.length - previous, heldRows);
        }
      }
    };
    tick();
    const timer = setInterval(tick, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [refreshPublishes, refreshHeld, refreshOutreach, notify]);

  return {
    count: pending.length + held.length + outreach.length + outreachWaiting.length,
    pending,
    held,
    outreach,
    outreachWaiting,
    loading,
    heldLoading,
    outreachLoading,
    error,
    refresh,
  };
};

function raiseHeldNotification(added, rows) {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  try {
    new Notification(
      translate("messages.approvals.heldWaiting", { count: added }),
      { body: rows[0]?.subject || "", tag: "guaardvark-held-code" },
    );
  } catch {
    // Same as publishes: the badge is the guaranteed signal.
  }
}

/** Best-effort desktop notification. Silent when unsupported or not granted. */
function raiseDesktopNotification(added, rows) {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  const newest = rows[0];
  const detail = newest
    ? `${newest.platform}${newest.title ? ` · ${newest.title}` : ""}`
    : "";
  try {
    new Notification(
      translate("messages.approvals.publishWaiting", { count: added }),
      { body: detail, tag: "guaardvark-publish-approvals" },
    );
  } catch {
    // Some browsers throw for constructed notifications outside a service
    // worker. The badge is the guaranteed signal; this is an enhancement.
  }
}

/** True once the browser will actually show notifications. */
export const desktopNotificationsAvailable = () =>
  typeof window !== "undefined" && "Notification" in window;

export const desktopNotificationsGranted = () =>
  desktopNotificationsAvailable() && Notification.permission === "granted";

/** Ask for permission. Must be called from a user gesture, never on load. */
export const requestDesktopNotifications = async () => {
  if (!desktopNotificationsAvailable()) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  const result = await Notification.requestPermission();
  return result === "granted";
};
