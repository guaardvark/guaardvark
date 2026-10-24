import { useState, useEffect, useRef, useCallback } from "react";
import { io } from "socket.io-client";
import { SOCKET_URL, handleResponse } from "../api/apiClient";
import { formatUiError } from "../utils/uiError";
import { translate } from "../i18n";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const ACTIVE_STATUSES = new Set(["queued", "pending", "running"]);
const TERMINAL_STATUSES = new Set(["completed", "error", "cancelled"]);
const STATUS_POLL_MS = 2000;
// Queue and list polls: this many misses in a row is an outage, said once.
const POLL_FAILURES_BEFORE_NOTICE = 3;

/**
 * Batch video queue state, WebSocket progress, and batch lifecycle handlers.
 * HTTP status polling runs while a batch is active so progress survives WS drops.
 */
export function useBatchVideo({ setError, setSuccess, computedParams } = {}) {
  const [activeBatchId, setActiveBatchId] = useState(null);
  const [batchStatus, setBatchStatus] = useState(null);
  const [batches, setBatches] = useState([]);
  const [queue, setQueue] = useState([]);

  const pollingRef = useRef(null);
  const queuePollingRef = useRef(null);
  const socketRef = useRef(null);
  const activeBatchIdRef = useRef(null);
  const listFailuresRef = useRef(0);
  const queueFailuresRef = useRef(0);

  useEffect(() => {
    activeBatchIdRef.current = activeBatchId;
  }, [activeBatchId]);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  const fetchBatches = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/list`);
      const data = await handleResponse(res, { quiet: true });
      listFailuresRef.current = 0;
      if (data.success) {
        const sorted = (data.data.batches || []).sort((a, b) => {
          const ta = a.start_time || a.end_time || "";
          const tb = b.start_time || b.end_time || "";
          return tb.localeCompare(ta);
        });
        setBatches(sorted);
      }
    } catch (e) {
      // The first load failing would look like "no batches"; a repeated
      // refresh failing is said once.
      listFailuresRef.current += 1;
      if (listFailuresRef.current === 1 || listFailuresRef.current === POLL_FAILURES_BEFORE_NOTICE) {
        setError?.(translate("messages.batchVideo.listFailed", { reason: formatUiError(e) || translate("messages.batchVideo.requestFailed") }));
      }
    }
  }, []);

  const fetchQueue = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/queue`);
      const data = await handleResponse(res, { quiet: true });
      queueFailuresRef.current = 0;
      if (data.success) {
        setQueue(data.data.queue || []);
      }
    } catch (e) {
      // A poll: one miss stays quiet; a failure that persists is said once.
      queueFailuresRef.current += 1;
      if (queueFailuresRef.current === POLL_FAILURES_BEFORE_NOTICE) {
        setError?.(translate("messages.batchVideo.queueFailed", { reason: formatUiError(e) || translate("messages.batchVideo.requestFailed") }));
      }
    }
  }, []);

  // Clears finished batches from the backend's queue snapshot, so they are
  // gone for every browser; their clips and history stay.
  const handleClearCompletedQueue = useCallback(async () => {
    if (!queue.some((q) => TERMINAL_STATUSES.has(q.status))) return;
    try {
      const res = await fetch(`${API_BASE}/batch-video/queue/clear-finished`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        setError?.(data.error || translate("messages.batchVideo.clearFailedStatus", { status: res.status }));
        return;
      }
      setQueue(data.data?.queue || []);
    } catch (e) {
      setError?.(translate("messages.batchVideo.clearFailed", { reason: e.message }));
    }
  }, [queue, setError]);

  const fetchStatusOnce = useCallback(async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/status/${batchId}`);
      const data = await res.json();
      if (data.success) {
        setBatchStatus(data.data);
        if (TERMINAL_STATUSES.has(data.data.status)) {
          stopPolling();
          fetchBatches();
        }
        return data.data;
      }
    } catch (e) {
      console.error(e);
    }
    return null;
  }, [stopPolling, fetchBatches]);

  const startPollingStatus = useCallback((batchId) => {
    stopPolling();
    if (socketRef.current?.connected) {
      socketRef.current.emit("subscribe", { job_id: batchId });
    }
    // Immediate snapshot, then keep polling while active (WS is the fast path).
    fetchStatusOnce(batchId).then((snap) => {
      if (snap && ACTIVE_STATUSES.has(snap.status)) {
        pollingRef.current = setInterval(() => {
          fetchStatusOnce(batchId);
        }, STATUS_POLL_MS);
      }
    });
  }, [stopPolling, fetchStatusOnce]);

  useEffect(() => {
    socketRef.current = io(SOCKET_URL);
    socketRef.current.on("video_batch:update", (data) => {
      // Only apply WS updates for the active batch (or unset until first poll).
      if (
        activeBatchIdRef.current &&
        data?.batch_id &&
        data.batch_id !== activeBatchIdRef.current
      ) {
        return;
      }
      setBatchStatus(data);
      if (TERMINAL_STATUSES.has(data.status)) {
        stopPolling();
        fetchBatches();
      }
    });
    return () => {
      socketRef.current?.disconnect();
    };
  }, [fetchBatches, stopPolling]);

  useEffect(() => {
    fetchBatches();
    fetchQueue();
    queuePollingRef.current = setInterval(fetchQueue, STATUS_POLL_MS);
    return () => {
      stopPolling();
      if (queuePollingRef.current) clearInterval(queuePollingRef.current);
    };
  }, [fetchBatches, fetchQueue, stopPolling]);

  const handleDownloadBatch = useCallback(async (batchId) => {
    window.open(`${API_BASE}/batch-video/download/${batchId}`, "_blank");
  }, []);

  const handleCombineFrames = useCallback(async (batchId, itemId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/combine-frames/${batchId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fps: computedParams?.fps || 7, item_id: itemId }),
      });
      await handleResponse(res, { quiet: true });
      await fetchBatches();
      if (activeBatchId === batchId) {
        startPollingStatus(batchId);
      }
    } catch (e) {
      setError?.(translate("messages.batchVideo.combineFailed", { reason: formatUiError(e) || translate("messages.batchVideo.requestFailed") }));
    }
  }, [activeBatchId, computedParams, fetchBatches, startPollingStatus, setError]);

  const handleDeleteBatch = useCallback(async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/delete/${batchId}`, { method: "DELETE" });
      if (res.ok) {
        await fetchBatches();
        await fetchQueue();
        if (activeBatchId === batchId) {
          setActiveBatchId(null);
          setBatchStatus(null);
          stopPolling();
        }
        setSuccess?.(translate("messages.batchVideo.deleted"));
      } else {
        const data = await res.json().catch(() => ({}));
        setError?.(data.error || translate("messages.batchVideo.deleteFailedStatus", { status: res.status }));
      }
    } catch (e) {
      setError?.(translate("messages.batchVideo.deleteFailed", { reason: e.message }));
    }
  }, [activeBatchId, fetchBatches, fetchQueue, setError, setSuccess, stopPolling]);

  const handleCancelBatch = useCallback(async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/batch/${batchId}/cancel`, { method: "POST" });
      await handleResponse(res, { quiet: true });
      await fetchBatches();
      await fetchQueue();
      if (activeBatchId === batchId) {
        startPollingStatus(batchId);
      }
    } catch (e) {
      setError?.(translate("messages.batchVideo.cancelFailed", { reason: formatUiError(e) || translate("messages.batchVideo.requestFailed") }));
    }
  }, [activeBatchId, fetchBatches, fetchQueue, startPollingStatus, setError]);

  const handleRetryBatch = useCallback(async (batchId) => {
    try {
      setError?.("");
      const res = await fetch(`${API_BASE}/batch-video/retry/${batchId}`, { method: "POST" });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setError?.(errData.error || translate("messages.batchVideo.retryFailedStatus", { status: res.status }));
        return;
      }
      const data = await res.json();
      if (data.success && data.data?.batch_id) {
        const newBatchId = data.data.batch_id;
        setActiveBatchId(newBatchId);
        setBatchStatus(null);
        startPollingStatus(newBatchId);
        await fetchBatches();
        await fetchQueue();
        setSuccess?.(translate("messages.batchVideo.retried", { batchId: newBatchId }));
      }
    } catch (e) {
      setError?.(translate("messages.batchVideo.retryFailed", { reason: e.message }));
    }
  }, [fetchBatches, fetchQueue, setError, setSuccess, startPollingStatus]);

  // A clip a quality check held: keep it as rendered (it is then added to Files).
  const handleApproveClip = useCallback(async (batchId, itemId) => {
    try {
      setError?.("");
      const res = await fetch(`${API_BASE}/batch-video/review/${batchId}/${itemId}/approve`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        setError?.(data.error || translate("messages.batchVideo.approveFailedStatus", { status: res.status }));
        return;
      }
      setBatchStatus((prev) => (prev && prev.batch_id === batchId
        ? { ...prev, results: (prev.results || []).map((r) => (
          r.item_id === itemId ? { ...r, review: data.data?.review } : r)) }
        : prev));
      setSuccess?.(translate("messages.batchVideo.clipApproved"));
    } catch (e) {
      setError?.(translate("messages.batchVideo.approveFailed", { reason: e.message }));
    }
  }, [setError, setSuccess]);

  // ...or render it again as a new one-clip batch (same settings, new seed).
  const handleRerenderClip = useCallback(async (batchId, itemId) => {
    try {
      setError?.("");
      const res = await fetch(`${API_BASE}/batch-video/review/${batchId}/${itemId}/rerender`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success || !data.data?.batch_id) {
        setError?.(data.error || translate("messages.batchVideo.rerenderFailedStatus", { status: res.status }));
        return;
      }
      const newBatchId = data.data.batch_id;
      setActiveBatchId(newBatchId);
      setBatchStatus(null);
      startPollingStatus(newBatchId);
      await fetchBatches();
      await fetchQueue();
      setSuccess?.(translate("messages.batchVideo.rerendering", { newBatchId, batchId }));
    } catch (e) {
      setError?.(translate("messages.batchVideo.rerenderFailed", { reason: e.message }));
    }
  }, [fetchBatches, fetchQueue, setError, setSuccess, startPollingStatus]);

  return {
    activeBatchId,
    setActiveBatchId,
    batchStatus,
    setBatchStatus,
    batches,
    queue,
    fetchBatches,
    fetchQueue,
    startPollingStatus,
    stopPolling,
    handleDownloadBatch,
    handleCombineFrames,
    handleDeleteBatch,
    handleCancelBatch,
    handleRetryBatch,
    handleApproveClip,
    handleRerenderClip,
    handleClearCompletedQueue,
  };
}

export default useBatchVideo;
