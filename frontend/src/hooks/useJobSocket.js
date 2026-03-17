/* global process */
import { useEffect, useRef } from "react";
import { io } from "socket.io-client";
import { SOCKET_URL } from "../api/apiClient";

/**
 * @deprecated Use UnifiedProgressContext instead for progress tracking.
 * This hook creates a separate socket connection per job, which can conflict
 * with the global connection in UnifiedProgressContext.
 *
 * Migration: Replace useJobSocket with useUnifiedProgress() from UnifiedProgressContext
 * and use addProcessListener() for job-specific updates.
 */
export default function useJobSocket(jobId, onProgress) {
  // Log deprecation warning in development
  useEffect(() => {
    if (process.env.NODE_ENV === 'development') {
      console.warn(
        `useJobSocket is deprecated. Use UnifiedProgressContext.addProcessListener() instead for job: ${jobId}`
      );
    }
  }, [jobId]);
  const socketRef = useRef(null);

  useEffect(() => {
    if (!jobId) return;
    const socket = io(SOCKET_URL, { 
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
      upgrade: true,
      rememberUpgrade: true,
    });
    socketRef.current = socket;

    socket.on("connect", () => {
      socket.emit("subscribe", { job_id: jobId });
    });

    socket.on("job_progress", (data) => {
      if (data?.job_id === jobId && onProgress) {
        onProgress(data);
      }
    });

    return () => {
      socket.off("job_progress");
      socket.disconnect();
    };
  }, [jobId, onProgress]);

  return socketRef.current;
}
