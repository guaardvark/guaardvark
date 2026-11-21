// frontend/src/api/nightShiftService.js
//
// Client for /api/night-shift (backend/api/night_shift_api.py): jobs held for
// the night shift or for a time, their order, and the window they run in.
import { BASE_URL, handleResponse } from "./apiClient";

export const WHEN = { NIGHT_SHIFT: "night_shift", AT: "at", NOW: "now" };

// Told to every listener after a job is scheduled, so the Jobs page, the
// footer count and the dashboard card re-read instead of waiting for a poll.
export const NIGHT_SHIFT_CHANGED_EVENT = "guaardvark:night-shift-changed";
const announce = () => {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(NIGHT_SHIFT_CHANGED_EVENT));
};

const call = async (path, method = "GET", body) => {
  const response = await fetch(`${BASE_URL}/night-shift${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const parsed = await handleResponse(response);
  if (parsed && typeof parsed === "object" && parsed.error) {
    throw new Error(parsed.error?.message || parsed.error);
  }
  return parsed?.data ?? parsed;
};

const changed = async (promise) => {
  const out = await promise;
  announce();
  return out;
};

// A schedule choice as the API takes it: { when } or { when: "at", at: ISO }.
const whenBody = ({ when, at }) => (when === WHEN.AT ? { when, at: new Date(at).toISOString() } : { when });

export const getNightShiftStatus = () => call("/status");

export const setNightShiftWindow = (start, end) => changed(call("/window", "PUT", { start, end }));

export const setNightShiftPaused = (paused) => changed(call("/pause", "POST", { paused }));

// Hold an existing job for the night shift or a time, or start a held one now.
export const scheduleJob = (taskId, choice) => changed(call(`/jobs/${taskId}/schedule`, "POST", whenBody(choice)));

export const setNightShiftOrder = (ids) => changed(call("/order", "POST", { ids }));

// Save a render batch to start later. `request` is the body its page would
// have sent now; `type` and `variant` name the start route
// (backend/services/job_handoff.py).
export const createScheduledBatch = ({ type, variant, name, request }, choice) =>
  changed(call("/jobs", "POST", { type, variant, name, request, ...whenBody(choice) }));
