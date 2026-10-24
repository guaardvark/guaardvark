// Rows for the dashboard cards' recent-runs lists, from the same feeds the full
// pages read: image batches from /api/batch-image/list (the Image Gen page's
// Recent Batches) and jobs from /api/tasks (the Jobs page).

import { useCallback, useEffect, useState } from "react";
import { taskPath } from "../../utils/entityLinks";
import { BASE_URL, handleResponse } from "../../api/apiClient";
import { getTasks } from "../../api/taskService";
import { translate } from "../../i18n";

/** How many rows a card shows before its "View all" link. */
export const RECENT_LIMIT = 5;

const statuses = () => ({
  completed: [translate("dashboard.recentRuns.status.completed"), "success"],
  running: [translate("dashboard.recentRuns.status.running"), "primary"],
  "in-progress": [translate("dashboard.recentRuns.status.running"), "primary"],
  queued: [translate("dashboard.recentRuns.status.queued"), "warning"],
  pending: [translate("dashboard.recentRuns.status.pending"), "warning"],
  paused: [translate("dashboard.recentRuns.status.paused"), "default"],
  error: [translate("dashboard.recentRuns.status.failed"), "error"],
  failed: [translate("dashboard.recentRuns.status.failed"), "error"],
  cancelled: [translate("dashboard.recentRuns.status.cancelled"), "default"],
});

/** Chip label and colour for a batch or job status. */
export function runStatus(status) {
  const [label, color] = statuses()[String(status || "").toLowerCase()] || [
    status || translate("dashboard.recentRuns.status.unknown"),
    "default",
  ];
  return { label, color };
}

const newestFirst = (dateOf) => (a, b) => (Date.parse(dateOf(b)) || 0) - (Date.parse(dateOf(a)) || 0);

/** Image batches, newest first, each opening that batch on the Image Gen page. */
export function imageBatchRows(batches) {
  return [...(batches || [])]
    .sort(newestFirst((b) => b.start_time))
    .map((b) => ({
      key: b.batch_id,
      name: b.display_name || b.batch_id,
      status: b.status,
      date: b.start_time,
      detail: b.total_images
        ? translate("dashboard.recentRuns.images", { done: b.completed_images || 0, total: b.total_images })
        : null,
      path: `/batch-images?batch=${encodeURIComponent(b.batch_id)}`,
    }));
}

/** Jobs that pass `keep`, newest first, each opening that job on the Jobs page. */
export function jobRows(tasks, keep = () => true) {
  return (Array.isArray(tasks) ? tasks : [])
    .filter(keep)
    .sort(newestFirst((t) => t.created_at))
    .map((t) => ({
      key: t.id,
      name: t.name || translate("dashboard.recentRuns.jobNumber", { id: t.id }),
      status: t.status,
      date: t.created_at,
      detail: t.output_filename || null,
      path: taskPath(t.id),
    }));
}

/** A file-generation job that writes a CSV (the File Generation page's runs). */
export function isCsvJob(task) {
  return /\.csv$/i.test(task?.output_filename || "");
}

/** Rows for the Image Generation card. */
export async function loadImageBatchRows() {
  const data = await handleResponse(await fetch(`${BASE_URL}/batch-image/list`));
  return imageBatchRows(data?.data?.batches);
}

/** Rows for a card that lists one type of job. */
export async function loadJobRows(type, keep) {
  const tasks = await getTasks(null, null, type);
  if (tasks?.error) throw new Error(tasks.error);
  return jobRows(tasks, keep);
}

/**
 * Load a card's rows on mount and on `refresh()`.
 * @param {function(): Promise<Array>} load  stable across renders
 */
export function useRecentRuns(load) {
  const [state, setState] = useState({ rows: [], loading: true, error: null });
  const refresh = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      setState({ rows: await load(), loading: false, error: null });
    } catch (err) {
      setState({
        rows: [],
        loading: false,
        error: translate("dashboard.recentRuns.loadFailed", { message: err?.message || err }),
      });
    }
  }, [load]);
  useEffect(() => {
    refresh();
  }, [refresh]);
  return { ...state, refresh };
}
