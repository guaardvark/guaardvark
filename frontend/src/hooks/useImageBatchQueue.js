// frontend/src/hooks/useImageBatchQueue.js
//
// The image batch queue (/api/batch-image/queue) as Image Gen and the
// Infographic page show it: one worker drains image and infographic batches
// in order, so both pages watch the same line.
//
// Polls every 2.5 s while something is waiting or running (or `keepWatching`
// says the page has a batch of its own in flight), every 15 s while finished
// rows are still listed, and not at all once the panel is empty.

import { useCallback, useEffect, useRef, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

export const POLLABLE_STATUSES = new Set(['queued', 'pending', 'running']);
export const TERMINAL_BATCH_STATUSES = new Set(['completed', 'error', 'cancelled']);

// Finished rows a person cleared stay hidden on every page that shows the queue.
const CLEARED_KEY = 'clearedImageQueueIds';
// A poll: one miss stays quiet; a failure that persists is said once.
const FAILURES_BEFORE_NOTICE = 3;

const readCleared = () => {
  try {
    return JSON.parse(localStorage.getItem(CLEARED_KEY) || '[]');
  } catch (e) {
    return [];
  }
};

/**
 * @param {Object} [options]
 * @param {(rows: Object[]) => void} [options.onRows] - every successful poll, before the change check
 * @param {(error: Error) => void} [options.onStalled] - once, when polls keep failing
 * @param {boolean} [options.keepWatching] - poll fast while the page has its own batch running
 * @param {boolean} [options.watchIdle] - keep the slow poll even with nothing listed
 */
export default function useImageBatchQueue({ onRows, onStalled, keepWatching = false, watchIdle = false } = {}) {
  const [queue, setQueue] = useState([]);
  const signatureRef = useRef('');
  const failuresRef = useRef(0);
  // Read at call time so a page can pass inline callbacks without re-creating fetchQueue.
  const onRowsRef = useRef(onRows);
  const onStalledRef = useRef(onStalled);
  onRowsRef.current = onRows;
  onStalledRef.current = onStalled;

  const fetchQueue = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/queue`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const rows = data?.data?.queue ?? data?.queue ?? [];
      // Only terminal rows can be hidden, so a live batch is never masked.
      const cleared = readCleared();
      const visible = (Array.isArray(rows) ? rows : []).filter(
        (q) => !(TERMINAL_BATCH_STATUSES.has(q.status) && cleared.includes(q.batch_id)),
      );
      onRowsRef.current?.(visible);
      failuresRef.current = 0;
      // Commit only when something moved: a fresh array every tick re-renders the page.
      const signature = visible
        .map((q) => `${q.batch_id}:${q.status}:${q.completed_images ?? ''}/${q.total_images ?? ''}`)
        .join('|');
      if (signature === signatureRef.current) return;
      signatureRef.current = signature;
      setQueue(visible);
    } catch (err) {
      failuresRef.current += 1;
      if (failuresRef.current === FAILURES_BEFORE_NOTICE) onStalledRef.current?.(err);
    }
  }, []);

  const clearCompleted = useCallback(() => {
    setQueue((prev) => {
      const doneIds = prev.filter((q) => TERMINAL_BATCH_STATUSES.has(q.status)).map((q) => q.batch_id);
      if (doneIds.length === 0) return prev;
      try {
        localStorage.setItem(CLEARED_KEY, JSON.stringify(Array.from(new Set([...readCleared(), ...doneIds]))));
      } catch (e) {
        // Storage refused: the rows still leave this view.
      }
      return prev.filter((q) => !TERMINAL_BATCH_STATUSES.has(q.status));
    });
    // The next poll re-commits against the new baseline.
    signatureRef.current = '';
  }, []);

  useEffect(() => { fetchQueue(); }, [fetchQueue]);

  const anythingRunning = keepWatching || queue.some((q) => POLLABLE_STATUSES.has(q.status));
  const shouldPoll = anythingRunning || watchIdle || queue.length > 0;
  const pollIntervalMs = anythingRunning ? 2500 : 15000;
  useEffect(() => {
    if (!shouldPoll) return undefined;
    const id = setInterval(fetchQueue, pollIntervalMs);
    return () => clearInterval(id);
  }, [shouldPoll, pollIntervalMs, fetchQueue]);

  return { queue, fetchQueue, clearCompleted };
}
