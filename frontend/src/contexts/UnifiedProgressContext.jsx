// frontend/src/contexts/UnifiedProgressContext.jsx
// Unified Progress Context - Consolidates all progress tracking mechanisms
// Replaces fragmented ProgressContext.jsx, window events, and individual progress hooks

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useReducer,
  useMemo,
} from "react";
import { io } from "socket.io-client";

import { useTheme } from "@mui/material/styles";
import { BASE_URL as API_BASE, SOCKET_URL } from "../api/apiClient";

const UnifiedProgressContext = createContext();

export const useUnifiedProgress = () => {
  const context = useContext(UnifiedProgressContext);
  if (!context) {
    throw new Error(
      "useUnifiedProgress must be used within a UnifiedProgressProvider"
    );
  }
  return context;
};

// Action types for reducer
const ACTIONS = {
  ADD_PROCESS: 'ADD_PROCESS',
  UPDATE_PROCESS: 'UPDATE_PROCESS',
  REMOVE_PROCESS: 'REMOVE_PROCESS',
  CLEAR_OLD_PROCESSES: 'CLEAR_OLD_PROCESSES',
  SYNC_PROCESSES: 'SYNC_PROCESSES',
};

// Reducer for atomic state management
const progressReducer = (state, action) => {
  switch (action.type) {
    case ACTIONS.ADD_PROCESS:
    case ACTIONS.UPDATE_PROCESS: {
      const newProcesses = new Map(state.activeProcesses);
      newProcesses.set(action.payload.job_id, action.payload);
      return { ...state, activeProcesses: newProcesses };
    }
    case ACTIONS.REMOVE_PROCESS: {
      const newProcesses = new Map(state.activeProcesses);
      newProcesses.delete(action.payload.job_id);
      return { ...state, activeProcesses: newProcesses };
    }
    case ACTIONS.CLEAR_OLD_PROCESSES: {
      const newProcesses = new Map();
      const now = Date.now();
      const maxAge = 5 * 60 * 1000;

      state.activeProcesses.forEach((process, id) => {
        if (now - process.timestamp < maxAge) {
          newProcesses.set(id, process);
        }
      });

      return { ...state, activeProcesses: newProcesses };
    }
    case ACTIONS.SYNC_PROCESSES: {
      const validJobIds = new Set(action.payload.validJobIds);
      const newProcesses = new Map(state.activeProcesses);
      let changed = false;
      const now = Date.now();

      for (const [jobId, process] of state.activeProcesses.entries()) {
        // Only purge ghost jobs that are:
        // 1. In 'processing' status but missing from the server
        // 2. AND older than 60 seconds (gives new jobs time to appear in metadata files)
        const age = now - (process.timestamp || 0);
        if (!validJobIds.has(jobId) && process.status === 'processing' && age > 60000) {
          newProcesses.delete(jobId);
          changed = true;
        }
      }

      return changed ? { ...state, activeProcesses: newProcesses } : state;
    }
    default:
      return state;
  }
};

export const UnifiedProgressProvider = ({ children }) => {
  const theme = useTheme();
  const [state, dispatch] = useReducer(progressReducer, {
    activeProcesses: new Map(),
  });

  const { activeProcesses } = state;

  const socketRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const processTimeoutRef = useRef(new Map());
  const listenersRef = useRef(new Map());
  const activeProcessesRef = useRef(activeProcesses);
  const [connectionState, setConnectionState] = useState('disconnected');
  const cleanupRef = useRef(new Set()); // Track all cleanup functions

  // Keep ref in sync with activeProcesses for use in async callbacks
  activeProcessesRef.current = activeProcesses;

  // Detect process type from message - enhanced for ALL services
  const detectProcessType = useCallback((message) => {
    if (!message || typeof message !== 'string') {
      return "unknown";
    }
    const msg = message.toLowerCase();

    // Primary service types from requirements - match backend ProcessType enum
    if (
      msg.includes("index") ||
      msg.includes("parsing") ||
      msg.includes("preparing") ||
      msg.includes("document") ||
      msg.includes("doc")
    ) {
      return "indexing";
    } else if (
      msg.includes("csv") ||
      msg.includes("bulk csv") ||
      msg.includes("csv_processing") ||
      msg.includes("proven csv")
    ) {
      return "csv_processing";
    } else if (
      msg.includes("image") ||
      msg.includes("batch") ||
      msg.includes("diffusion") ||
      msg.includes("stable diffusion") ||
      msg.includes("generated image") ||
      msg.includes("image generation")
    ) {
      return "image_generation";
    } else if (
      msg.includes("file") ||
      msg.includes("generat") ||
      msg.includes("filegen") ||
      msg.includes("xml") ||
      msg.includes("bulk generation")
    ) {
      return "file_generation";
    } else if (
      msg.includes("analyz") ||
      msg.includes("analyzing") ||
      msg.includes("analysis") ||
      msg.includes("codegen") ||
      msg.includes("code generation")
    ) {
      return "analysis";
    } else if (
      msg.includes("search") ||
      msg.includes("websearch") ||
      msg.includes("web search") ||
      msg.includes("crawl") ||
      msg.includes("scrap")
    ) {
      return "web_scraping";
    } else if (
      msg.includes("llm") ||
      msg.includes("model") ||
      msg.includes("chat") ||
      msg.includes("generation")
    ) {
      return "llm_processing";
    } else if (msg.includes("backup") || msg.includes("export")) {
      return "backup";
    } else if (msg.includes("upload")) {
      return "upload";
    } else if (msg.includes("task") || msg.includes("queue")) {
      return "task_processing";
    } else if (msg.includes("train") || msg.includes("learn")) {
      return "training";
    } else if (msg.includes("voice") || msg.includes("audio")) {
      return "voice_processing";
    }
    return "processing";  // Changed from "unknown" to "processing"
  }, []);

  // Ref for handleJobProgress to avoid stale closures in socket listeners
  const handleJobProgressRef = useRef();

  // Fetch active jobs from backend to restore state
  const fetchActiveJobs = useCallback(async () => {
    try {
      // console.log('UnifiedProgressContext: Fetching active jobs from /api/meta/active_jobs');
      const abortCtl = new AbortController();
      const abortTimer = setTimeout(() => abortCtl.abort(), 10000);
      const response = await fetch(`${API_BASE}/meta/active_jobs`, { signal: abortCtl.signal });
      clearTimeout(abortTimer);

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      const activeJobs = data.active_jobs || [];

      // console.log(`UnifiedProgressContext: Loaded ${activeJobs.length} active jobs from server`);

      // Restore jobs into state — but don't overwrite live SocketIO data
      // File-based metadata can lag behind real-time SocketIO events
      activeJobs.forEach(job => {
        // Only restore if not complete
        if (job.status !== 'complete' && job.status !== 'error' && !job.is_complete) {
          // Skip if we already have a more recent update from SocketIO
          const existing = activeProcessesRef.current.get(job.job_id);
          if (existing && existing.timestamp > (new Date(job.last_update || job.timestamp || 0).getTime())) {
            return; // SocketIO data is newer, keep it
          }

          const processData = {
            job_id: job.job_id,
            progress: job.progress || 0,
            message: job.message || job.description || 'Processing...',
            status: job.status || 'processing',
            process_type: job.process_type || 'unknown',
            timestamp: job.timestamp || job.last_update || Date.now(),
            generated_count: job.additional_data?.generated_count,
            target_count: job.additional_data?.target_count,
            ...job.additional_data
          };

          if (handleJobProgressRef.current) {
            handleJobProgressRef.current(processData);
          }
        }
      });

      // Purge ghost jobs that are marked active on frontend but not on backend
      const validJobIds = activeJobs.map(j => j.job_id);
      dispatch({ type: ACTIONS.SYNC_PROCESSES, payload: { validJobIds } });

      return activeJobs.length;
    } catch (error) {
      if (error.name === 'AbortError') return 0; // Navigation cancel — not an error
      console.error('UnifiedProgressContext: Failed to fetch active jobs:', error);
      return 0;
    }
  }, [dispatch]);

  // Initialize SocketIO connection
  useEffect(() => {

    const initializeSocket = () => {
      try {
        const socket = io(SOCKET_URL, {
          reconnection: true,
          reconnectionAttempts: 5,
          reconnectionDelay: 1000,
          reconnectionDelayMax: 10000,
          timeout: 10000,
          upgrade: true,
          rememberUpgrade: true,
        });

        socketRef.current = socket;

        socket.on("connect", async () => {
          // console.log("UnifiedProgressContext: Connected to SocketIO");
          setConnectionState('connected');

          // CRITICAL: Fetch and restore active processes from backend
          try {
            const _jobCount = await fetchActiveJobs();
            // console.log(`UnifiedProgressContext: Restored ${jobCount} active jobs from server`);
          } catch (error) {
            console.error("UnifiedProgressContext: Failed to restore active jobs on connect:", error);
          }

          // Subscribe to global progress updates
          socket.emit("subscribe", { job_id: "global_progress" });
          // console.log("UnifiedProgressContext: Subscribed to global_progress room");
        });

        socket.on("job_progress", (data) => {
          try {
            // Enhanced logging for debugging progress updates
            // console.log("UnifiedProgressContext: Received job_progress event:", {
            //   job_id: data?.job_id,
            //   status: data?.status,
            //   progress: data?.progress,
            //   process_type: data?.process_type,
            //   message: data?.message?.substring(0, 50) + '...'
            // });

            // Validate data structure
            if (!data || !data.job_id) {
              console.error("UnifiedProgressContext: Invalid progress data received:", data);
              return;
            }

            handleJobProgressRef.current(data);
          } catch (error) {
            console.error("UnifiedProgressContext: Error handling job progress:", error, data);
          }
        });

        socket.on("disconnect", () => {
          // console.log("UnifiedProgressContext: Disconnected from SocketIO");
          setConnectionState('disconnected');

          // BUG FIX: Removed manual reconnection - Socket.IO's native reconnection
          // (configured above with reconnection: true) handles this automatically.
          // Manual reconnection created duplicate sockets and missed subscriptions.
        });

        // BUG FIX: Handle reconnection to re-subscribe and sync state
        socket.on("reconnect", async () => {
          // console.log("UnifiedProgressContext: Reconnected to SocketIO");
          setConnectionState('connected');

          // Re-subscribe to global progress updates
          socket.emit("subscribe", { job_id: "global_progress" });
          // console.log("UnifiedProgressContext: Re-subscribed to global_progress room after reconnect");

          // Sync current jobs after reconnection
          try {
            const _jobCount = await fetchActiveJobs();
            // console.log(`UnifiedProgressContext: Restored ${jobCount} jobs after reconnect`);
          } catch (error) {
            console.error("UnifiedProgressContext: Failed to restore jobs after reconnect:", error);
          }
        });

        socket.on("error", (error) => {
          console.error("UnifiedProgressContext: SocketIO error:", error);
          setConnectionState('error');
        });

        // Uncle Claude and Self-Improvement events
        socket.on("self_improvement:started", (_data) => {
          // console.log("Self-improvement run started:", data);
        });
        socket.on("self_improvement:completed", (_data) => {
          // console.log("Self-improvement run completed:", data);
        });
        socket.on("uncle:directive", (_data) => {
          // console.warn("Uncle Claude directive received:", data.directive, data.reason);
        });
        socket.on("family:learning", (_data) => {
          // console.log("Family learning received:", data);
        });
      } catch (error) {
        console.error(
          "UnifiedProgressContext: Failed to initialize socket:",
          error
        );
      }
    };

    initializeSocket();

    return () => {
      // Cleanup socket connection
      if (socketRef.current) {
        socketRef.current.disconnect();
        socketRef.current = null;
      }

      // Clear reconnection timeout
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }

      // Clear all process timeouts
      processTimeoutRef.current.forEach((timeout) => clearTimeout(timeout));
      processTimeoutRef.current.clear();

      // Clear all listeners
      listenersRef.current.clear();

      // Execute all registered cleanup functions
      cleanupRef.current.forEach(cleanup => {
        try {
          cleanup();
        } catch (error) {
          console.error('Cleanup function error:', error);
        }
      });
      cleanupRef.current.clear();
    };
  }, []); // Empty deps - socket init once, use ref for handleJobProgress

  // Handle job progress updates with atomic state management
  const handleJobProgress = useCallback(
    (data) => {
      const { job_id, progress, message, status, process_type, generated_count, target_count, ...remainingData } = data;

      // Enhanced logging for debugging progress updates
      // if (status === "start" || status === "complete" || status === "error" || progress % 25 === 0) {
      //   console.log("UnifiedProgressContext: Progress update:", {
      //     job_id, status, progress, process_type,
      //     message: message?.substring(0, 50) + '...',
      //     activeProcesses: activeProcesses.size,
      //   });
      // }

      // Clear any existing timeout for this process
      if (processTimeoutRef.current.has(job_id)) {
        clearTimeout(processTimeoutRef.current.get(job_id));
        processTimeoutRef.current.delete(job_id);
      }

      // Get existing process to preserve progress if not explicitly provided
      const existingProcess = activeProcesses.get(job_id);

      // Preserve existing additional_data and merge with new values
      const existingAdditionalData = existingProcess?.additional_data || {};
      const additional_data = {
        ...existingAdditionalData,
        ...(generated_count !== undefined && { generated_count }),
        ...(target_count !== undefined && { target_count }),
        // Include any other fields from remainingData that aren't standard fields
        ...Object.fromEntries(
          Object.entries(remainingData).filter(([key]) =>
            !['job_id', 'progress', 'message', 'status', 'process_type', 'timestamp'].includes(key)
          )
        )
      };

      const processData = {
        job_id,
        progress: (progress !== undefined && progress !== null) ? progress : (
          (status === "complete" || status === "end") ? 100 :
            status === "error" ? 0 :
              status === "cancelled" ? (existingProcess?.progress || 0) :
                existingProcess?.progress || 0
        ),
        message: message ||
          (status === "complete" || status === "end" ? "Complete" :
            status === "error" ? "Error" :
              status === "cancelled" ? "Cancelled" :
                existingProcess?.message || "Processing..."),
        status: status || "processing",
        timestamp: Date.now(),
        processType: process_type || existingProcess?.processType || detectProcessType(message || ""),
        additional_data
      };

      // BUG FIX: Include 'end' as terminal status (backend may send 'end' instead of 'complete')
      if (status === "complete" || status === "end" || status === "error" || status === "cancelled") {
        // Update process first
        dispatch({ type: ACTIONS.UPDATE_PROCESS, payload: processData });

        // Clear any existing timeout to prevent race conditions
        const existingTimeout = processTimeoutRef.current.get(job_id);
        if (existingTimeout) {
          // console.log(`UnifiedProgressContext: Clearing existing timeout for ${job_id}`);
          clearTimeout(existingTimeout);
        }

        // Remove completed processes after 3 seconds — long enough for the footer bar
        // to show completion state, short enough to not linger as ghost jobs
        const timeout = setTimeout(() => {
          dispatch({ type: ACTIONS.REMOVE_PROCESS, payload: { job_id } });
          processTimeoutRef.current.delete(job_id);
        }, 3000);

        processTimeoutRef.current.set(job_id, timeout);

        // console.log(`UnifiedProgressContext: Process ${job_id} marked as ${status}`);
      } else {
        // Add or update active process
        dispatch({ type: ACTIONS.UPDATE_PROCESS, payload: processData });
        // Only log progress updates for significant milestones
        if (progress === 0 || progress === 100 || progress % 25 === 0) {
          // console.log(`UnifiedProgressContext: Process ${job_id} progress: ${progress}% (${status})`);
        }
      }

      // Notify specific listeners for this process
      const processListeners = listenersRef.current.get(job_id);
      if (processListeners) {
        processListeners.forEach((listener) => {
          try {
            listener(data);
          } catch (error) {
            console.error(`Progress listener error for ${job_id}:`, error);
          }
        });
      }
    },
    [activeProcesses, detectProcessType, dispatch]
  );

  // Keep ref in sync with latest handleJobProgress (synchronous assignment, no effect delay)
  handleJobProgressRef.current = handleJobProgress;

  // Window progress events have been eliminated - all progress now comes through SocketIO
  // This prevents the dual event source problem that was causing component instability

  // Memoized global progress calculation
  const globalProgress = useMemo(() => {
    if (activeProcesses.size === 0) {
      return {
        active: false,
        progress: 0,
        message: "",
        processCount: 0,
      };
    }

    // Calculate weighted progress (completed processes = 100%, active processes = their progress)
    let totalWeight = 0;
    let weightedProgress = 0;
    let mostRecentMessage = "";
    let mostRecentTime = 0;
    let activeCount = 0;

    // Create a snapshot to avoid concurrent modification issues
    const processValues = Array.from(activeProcesses.values());

    processValues.forEach((process) => {
      // BUG FIX: Include 'end' as terminal status (backend may send 'end' instead of 'complete')
      const isComplete = process.status === 'complete' || process.status === 'end';
      const isError = process.status === 'error';
      const isCancelled = process.status === 'cancelled';
      const isTerminal = isComplete || isError || isCancelled;

      const weight = 1;
      const progress = isComplete ? 100 : (process.progress || 0);

      totalWeight += weight;
      weightedProgress += progress * weight;

      // Only count as active if not in terminal state
      if (!isTerminal) activeCount++;

      // Use most recent non-terminal process message, or fallback to most recent overall
      if (!isTerminal || mostRecentTime === 0) {
        if (process.timestamp > mostRecentTime) {
          mostRecentTime = process.timestamp;
          mostRecentMessage = process.message || "Processing...";
        }
      }
    });

    const averageProgress = totalWeight > 0 ? weightedProgress / totalWeight : 0;

    return {
      active: activeCount > 0,
      progress: Math.round(averageProgress),
      message: mostRecentMessage,
      processCount: activeProcesses.size,
      activeCount,
    };
  }, [activeProcesses]);

  // Auto-cleanup old processes with optimized interval
  useEffect(() => {
    const cleanupInterval = setInterval(() => {
      // Only cleanup if we have processes
      if (activeProcesses.size === 0) return;

      const now = Date.now();
      const maxAge = 5 * 60 * 1000; // 5 minutes
      let cleanedCount = 0;

      // Create a snapshot of processes to avoid concurrent modification issues
      const processEntries = Array.from(activeProcesses.entries());

      // Clean up timeouts and listeners for old processes
      processEntries.forEach(([id, process]) => {
        if (now - process.timestamp >= maxAge) {
          // Clear timeout for old process
          if (processTimeoutRef.current.has(id)) {
            clearTimeout(processTimeoutRef.current.get(id));
            processTimeoutRef.current.delete(id);
          }
          // Clear listeners for old process
          listenersRef.current.delete(id);
          cleanedCount++;
        }
      });

      if (cleanedCount > 0) {
        // console.log(`🧹 Cleaned up ${cleanedCount} old progress processes`);
        dispatch({ type: ACTIONS.CLEAR_OLD_PROCESSES });
      }
    }, 60000); // Check every 60 seconds

    const cleanup = () => clearInterval(cleanupInterval);
    cleanupRef.current.add(cleanup);

    return cleanup;
  }, [dispatch]); // Stable dependency - only dispatch

  // Periodic sync fallback - fetch active jobs every 30 seconds to catch missed updates
  useEffect(() => {
    // Only run periodic sync if connected
    if (connectionState !== 'connected') {
      // console.log('UnifiedProgressContext: Skipping periodic sync - not connected');
      return;
    }

    // console.log('UnifiedProgressContext: Starting periodic sync (30s interval)');

    const syncInterval = setInterval(async () => {
      try {
        // console.log('UnifiedProgressContext: Running periodic sync...');
        const jobCount = await fetchActiveJobs();

        if (jobCount > 0) {
          // console.log(`UnifiedProgressContext: Periodic sync restored/updated ${jobCount} jobs`);
        }
      } catch (error) {
        console.error('UnifiedProgressContext: Periodic sync error:', error);
      }
    }, 30000); // 30 seconds

    const cleanup = () => {
      // console.log('UnifiedProgressContext: Stopping periodic sync');
      clearInterval(syncInterval);
    };
    cleanupRef.current.add(cleanup);

    return cleanup;
  }, [connectionState, fetchActiveJobs]); // Re-create interval when connection state or fetchActiveJobs changes

  // Memoized process management functions
  const startProcess = useCallback(
    (processId, message, processType = "unknown") => {
      handleJobProgress({
        job_id: processId,
        progress: 0,
        message: message,
        status: "start",
        process_type: processType,
      });

      // Subscribe to SocketIO updates for this process
      if (socketRef.current && socketRef.current.connected && connectionState === 'connected') {
        try {
          socketRef.current.emit("subscribe", { job_id: processId });
        } catch (error) {
          console.error(`Failed to subscribe to process ${processId}:`, error);
        }
      }

      return processId;
    },
    [handleJobProgress, connectionState]
  );

  const updateProcess = useCallback(
    (processId, progress, message) => {
      handleJobProgress({
        job_id: processId,
        progress: progress,
        message: message,
        status: "processing",
      });
    },
    [handleJobProgress]
  );

  const completeProcess = useCallback(
    (processId, message = "Complete") => {
      handleJobProgress({
        job_id: processId,
        progress: 100,
        message: message,
        status: "complete",
      });
    },
    [handleJobProgress]
  );

  const errorProcess = useCallback(
    (processId, message = "Error") => {
      handleJobProgress({
        job_id: processId,
        progress: 0,
        message: message,
        status: "error",
      });
    },
    [handleJobProgress]
  );

  const cancelProcess = useCallback(
    (processId, message = "Cancelled") => {
      handleJobProgress({
        job_id: processId,
        progress: 0,
        message: message,
        status: "cancelled",
      });
    },
    [handleJobProgress]
  );

  // Query functions
  const getProcessesByType = useCallback(
    (processType) => {
      // Create a snapshot to avoid concurrent modification issues
      const processValues = Array.from(activeProcesses.values());
      return processValues.filter(process => process.processType === processType);
    },
    [activeProcesses]
  );

  const isProcessActive = useCallback(
    (processId) => {
      return activeProcesses.has(processId);
    },
    [activeProcesses]
  );

  const getProcess = useCallback(
    (processId) => {
      return activeProcesses.get(processId);
    },
    [activeProcesses]
  );

  // Enhanced listener management with proper cleanup tracking
  const addProcessListener = useCallback((processId, listener) => {
    if (!listenersRef.current.has(processId)) {
      listenersRef.current.set(processId, new Set());
    }
    listenersRef.current.get(processId).add(listener);

    // Return cleanup function and register it
    const cleanup = () => {
      const processListeners = listenersRef.current.get(processId);
      if (processListeners) {
        processListeners.delete(listener);
        if (processListeners.size === 0) {
          listenersRef.current.delete(processId);
        }
      }
      // Remove from cleanupRef to prevent memory leak
      cleanupRef.current.delete(cleanup);
    };

    cleanupRef.current.add(cleanup);
    return cleanup;
  }, []);

  const removeProcessListener = useCallback((processId, listener) => {
    const processListeners = listenersRef.current.get(processId);
    if (processListeners) {
      processListeners.delete(listener);
      if (processListeners.size === 0) {
        listenersRef.current.delete(processId);
      }
    }
  }, []);

  // Window events have been eliminated - all progress events now go through SocketIO

  const getProcessTypeIcon = useCallback((processType) => {
    const icons = {
      indexing: "",
      file_generation: "",
      llm_processing: "",
      backup: "",
      upload: "",
      task_processing: "",
      web_scraping: "",
      training: "",
      analysis: "",
      voice_processing: "",
      document_processing: "",
      csv_processing: "",
      unknown: "",
    };
    return icons[processType] || icons.unknown;
  }, []);

  const getProcessTypeColor = useCallback((processType) => {
    const colors = {
      indexing: theme.palette.primary.main,
      file_generation: theme.palette.success.main,
      image_generation: theme.palette.secondary.main,
      llm_processing: theme.palette.secondary.main,
      backup: theme.palette.warning.main,
      upload: theme.palette.info.main,
      task_processing: theme.palette.grey[500],
      web_scraping: theme.palette.error.main,
      training: theme.palette.success.light,
      analysis: theme.palette.warning.dark,
      voice_processing: theme.palette.secondary.light,
      document_processing: theme.palette.primary.dark,
      csv_processing: theme.palette.info.dark,
      processing: theme.palette.grey[500],
      unknown: theme.palette.grey[500],
    };
    return colors[processType] || colors.unknown;
  }, [theme]);

  // Memoized context value to prevent unnecessary re-renders
  const contextValue = useMemo(() => ({
    // State
    activeProcesses,
    globalProgress,
    socketRef,
    connectionState,

    // Process management
    startProcess,
    updateProcess,
    completeProcess,
    errorProcess,
    cancelProcess,

    // Queries
    getProcessesByType,
    isProcessActive,
    getProcess,

    // Listener management
    addProcessListener,
    removeProcessListener,

    // Utility
    handleJobProgress,
    getProcessTypeIcon,
    getProcessTypeColor,
    detectProcessType,
  }), [
    activeProcesses,
    globalProgress,
    connectionState,
    startProcess,
    updateProcess,
    completeProcess,
    errorProcess,
    cancelProcess,
    getProcessesByType,
    isProcessActive,
    getProcess,
    addProcessListener,
    removeProcessListener,
    handleJobProgress,
    getProcessTypeIcon,
    getProcessTypeColor,
    detectProcessType,
  ]);

  return (
    <UnifiedProgressContext.Provider value={contextValue}>
      {children}
    </UnifiedProgressContext.Provider>
  );
};

export default UnifiedProgressContext;
