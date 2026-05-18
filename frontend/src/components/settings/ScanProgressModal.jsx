import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Stack,
  Chip,
  Typography,
  CircularProgress,
  LinearProgress,
  Alert,
  IconButton,
  Divider,
} from "@mui/material";
import {
  Close as CloseIcon,
  Autorenew as AutorenewIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Block as BlockIcon,
} from "@mui/icons-material";
import io from "socket.io-client";
import { selfImprovementService } from "../../api/selfImprovementService";

// The six phases the modal walks through. Keys align with the labels we
// derive from the backend `stage` field below.
const PHASES = [
  { key: "dispatch", label: "Dispatching" },
  { key: "baseline", label: "Test baseline" },
  { key: "analyze",  label: "Analyzing" },
  { key: "propose",  label: "Proposing fixes" },
  { key: "verify",   label: "Verifying fixes" },
  { key: "done",     label: "Complete" },
];

// Translate the backend SocketIO `stage` string into one of our modal phases.
// The service has more granular stages than the modal cares about, so we
// collapse them into the six we actually render.
const STAGE_TO_PHASE = {
  starting: "dispatch",
  testing: "baseline",
  analyzed: "analyze",
  fixing: "propose",
  verifying: "verify",
  complete: "done",
  error: "done",
};

// Fallback — infer the phase from a polled run row if no live events have
// arrived yet. Less precise than the socket feed but survives a dropped
// connection or a cold-start race.
function inferPhase(run) {
  if (!run) return "dispatch";
  if (run.status !== "running") return "done";
  if (run.changes_made && run.changes_made.length > 0) return "verify";
  if (run.test_results_before) return "analyze";
  return "baseline";
}

function formatElapsed(sec) {
  if (sec == null) return "—";
  if (sec < 60) return `${sec.toFixed(0)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}m ${s}s`;
}

export default function ScanProgressModal({ open, onClose, onComplete }) {
  const [run, setRun] = useState(null);
  const [liveEvent, setLiveEvent] = useState(null);
  const [pendingFixes, setPendingFixes] = useState([]);
  const [error, setError] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const [dispatched, setDispatched] = useState(false);
  const [socketConnected, setSocketConnected] = useState(false);
  const startTimeRef = useRef(null);
  const pollRef = useRef(null);
  const tickRef = useRef(null);
  const socketRef = useRef(null);
  const runIdRef = useRef(null);

  const stopTimers = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (tickRef.current) {
      clearInterval(tickRef.current);
      tickRef.current = null;
    }
  }, []);

  const teardownSocket = useCallback(() => {
    if (socketRef.current) {
      socketRef.current.off("self_improvement_progress");
      socketRef.current.off("connect");
      socketRef.current.off("disconnect");
      socketRef.current.disconnect();
      socketRef.current = null;
    }
  }, []);

  // Polled data — the fixes list doesn't come through socket events, and
  // polling the run row is a cheap safety net for when socket events drop.
  const poll = useCallback(async () => {
    try {
      const [runsRes, fixesRes] = await Promise.allSettled([
        selfImprovementService.getRuns(1, 0),
        selfImprovementService.listPendingFixes({ limit: 20 }),
      ]);

      if (runsRes.status === "fulfilled") {
        const latest = runsRes.value?.data?.runs?.[0];
        if (latest) {
          const latestTs = new Date(latest.timestamp).getTime();
          // Only accept runs that started after we opened — prevents us from
          // latching onto a stale completed run from earlier in the session.
          if (latestTs >= (startTimeRef.current || 0) - 5000) {
            setRun(latest);
            runIdRef.current = latest.id;
            if (latest.status !== "running") {
              stopTimers();
              onComplete?.(latest);
            }
          }
        }
      }

      if (fixesRes.status === "fulfilled") {
        const all = Array.isArray(fixesRes.value?.data) ? fixesRes.value.data : [];
        setPendingFixes(all);
      }
    } catch (err) {
      setError(err?.message || "Poll failed");
    }
  }, [onComplete, stopTimers]);

  // Open → dispatch the scan, connect the socket, start polling + tick.
  useEffect(() => {
    if (!open) {
      stopTimers();
      teardownSocket();
      setRun(null);
      setLiveEvent(null);
      setPendingFixes([]);
      setError(null);
      setElapsed(0);
      setDispatched(false);
      setSocketConnected(false);
      startTimeRef.current = null;
      runIdRef.current = null;
      return;
    }

    startTimeRef.current = Date.now();
    setDispatched(false);

    // Stand up the socket FIRST so we don't miss the earliest events.
    const socket = io({ path: "/socket.io", transports: ["websocket", "polling"] });
    socketRef.current = socket;
    socket.on("connect", () => setSocketConnected(true));
    socket.on("disconnect", () => setSocketConnected(false));
    socket.on("self_improvement_progress", (data) => {
      if (!data) return;
      // Filter events to the run we dispatched. If we don't yet know our
      // run_id, accept the event and pin to whatever run_id it carries.
      if (data.run_id != null) {
        if (runIdRef.current == null) {
          runIdRef.current = data.run_id;
        } else if (runIdRef.current !== data.run_id) {
          return;
        }
      }
      setLiveEvent(data);
      if (data.stage === "complete" || data.stage === "error") {
        // Let the final poll pull the row + fixes so the auto-transition has
        // complete data, then stop.
        setTimeout(() => {
          poll();
          stopTimers();
        }, 400);
      }
    });

    (async () => {
      try {
        await selfImprovementService.triggerRun();
        setDispatched(true);
      } catch (err) {
        setError(err?.message || "Failed to dispatch self-check");
        return;
      }
      // Slight delay — Celery needs a heartbeat to write the run row.
      setTimeout(poll, 500);
      // Poll every 4s as a safety net for socket drops. Live events will
      // usually beat this to the punch.
      pollRef.current = setInterval(poll, 4000);
      tickRef.current = setInterval(() => {
        setElapsed((Date.now() - (startTimeRef.current || Date.now())) / 1000);
      }, 500);
    })();

    return () => {
      stopTimers();
      teardownSocket();
    };
  }, [open, poll, stopTimers, teardownSocket]);

  // Safety net — don't spin forever if Celery never writes a row.
  useEffect(() => {
    if (!open) return;
    const timeout = setTimeout(() => {
      if (run?.status === "running" || !run) {
        stopTimers();
        setError("Self-check timed out after 3 minutes. Check backend logs.");
      }
    }, 180000);
    return () => clearTimeout(timeout);
  }, [open, run, stopTimers]);

  // Live events are authoritative when present; fall back to the polled row.
  const livePhase = liveEvent ? STAGE_TO_PHASE[liveEvent.stage] : null;
  const currentPhase = livePhase || inferPhase(run);
  const currentPhaseIdx = PHASES.findIndex((p) => p.key === currentPhase);

  const liveStageIsTerminal = liveEvent?.stage === "complete" || liveEvent?.stage === "error";
  const isRunning = !liveStageIsTerminal && (!!liveEvent || (!!run && run.status === "running") || dispatched);
  const isDone = liveStageIsTerminal || (!!run && run.status !== "running");

  // Prefer the live status if we've seen a terminal event, otherwise trust the row.
  const status = liveEvent?.status || run?.status || (dispatched ? "running" : null);

  const statusIcon = (() => {
    if (!run && !liveEvent) return <AutorenewIcon className="spin" />;
    if (status === "success") return <CheckCircleIcon color="success" />;
    if (status === "failed" || liveEvent?.stage === "error") return <ErrorIcon color="error" />;
    if (status === "blocked_by_guardian") return <BlockIcon color="warning" />;
    return <AutorenewIcon />;
  })();

  const testBefore = run?.test_results_before;
  const changes = run?.changes_made || [];
  const runFixes = run ? pendingFixes.filter((f) => f.run_id === run.id) : [];

  // LinearProgress: use the live progress value if we have one, else indeterminate
  const progressValue = liveEvent?.progress != null
    ? Math.max(0, Math.min(100, liveEvent.progress * 100))
    : null;

  return (
    <Dialog open={open} onClose={isRunning ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1, pr: 6 }}>
        {statusIcon}
        <Typography variant="h6" component="span">
          Self-Check in Progress
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Chip
          size="small"
          label={socketConnected ? "live" : "polling"}
          color={socketConnected ? "success" : "default"}
          variant="outlined"
          sx={{ height: 20, fontSize: "0.65rem" }}
        />
        <Typography variant="caption" color="text.secondary">
          {formatElapsed(run?.duration_seconds ?? elapsed)}
        </Typography>
        <IconButton
          size="small"
          onClick={onClose}
          disabled={isRunning}
          sx={{ position: "absolute", right: 8, top: 8 }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers sx={{ minHeight: 420 }}>
        {isRunning && (
          <LinearProgress
            variant={progressValue != null ? "determinate" : "indeterminate"}
            value={progressValue ?? undefined}
            sx={{ mb: 2 }}
          />
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>
        )}

        {/* Live detail line — the event's current status message */}
        {liveEvent?.detail && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ fontFamily: "monospace", mb: 2 }}
          >
            › {liveEvent.detail}
          </Typography>
        )}

        {/* Phase breadcrumb */}
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
          {PHASES.map((p, idx) => {
            const reached = idx <= currentPhaseIdx;
            const active = idx === currentPhaseIdx && isRunning;
            return (
              <Chip
                key={p.key}
                label={p.label}
                size="small"
                color={active ? "primary" : reached ? "success" : "default"}
                variant={reached ? "filled" : "outlined"}
                icon={active ? <CircularProgress size={12} sx={{ ml: 0.5 }} /> : undefined}
              />
            );
          })}
        </Stack>

        <Divider sx={{ mb: 2 }} />

        {/* Metadata */}
        <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
          <Box>
            <Typography variant="caption" color="text.secondary">Trigger</Typography>
            <Typography variant="body2">
              {run?.trigger || liveEvent?.trigger || (dispatched ? "dispatching…" : "—")}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">Run ID</Typography>
            <Typography variant="body2">
              {run?.id ? `#${run.id}` : liveEvent?.run_id ? `#${liveEvent.run_id}` : "—"}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">Status</Typography>
            <Typography
              variant="body2"
              sx={{
                textTransform: "uppercase",
                fontWeight: 600,
                color:
                  status === "success" ? "success.main"
                  : status === "failed" || liveEvent?.stage === "error" ? "error.main"
                  : status === "blocked_by_guardian" ? "warning.main"
                  : "text.primary",
              }}
            >
              {status || "pending"}
            </Typography>
          </Box>
          {liveEvent?.current_fix != null && liveEvent?.total_fixes != null && (
            <Box>
              <Typography variant="caption" color="text.secondary">Fix</Typography>
              <Typography variant="body2">
                {liveEvent.current_fix} / {liveEvent.total_fixes}
              </Typography>
            </Box>
          )}
        </Stack>

        {/* Test baseline */}
        {testBefore && (
          <Box sx={{ mb: 2, p: 1.5, border: 1, borderColor: "divider", borderRadius: 1 }}>
            <Typography variant="caption" color="text.secondary">Test baseline</Typography>
            <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
              {testBefore.return_code === 0
                ? "All tests passing"
                : `${testBefore.total_failures || 0} failing tests`}
            </Typography>
            {testBefore.failures?.length > 0 && (
              <Box sx={{ pl: 2, mt: 0.5 }}>
                {testBefore.failures.slice(0, 5).map((f, i) => (
                  <Typography
                    key={i}
                    variant="caption"
                    sx={{ display: "block", fontFamily: "monospace", color: "error.light" }}
                  >
                    • {typeof f === "string" ? f : f.test || f.name || JSON.stringify(f)}
                  </Typography>
                ))}
                {testBefore.failures.length > 5 && (
                  <Typography variant="caption" color="text.secondary">
                    +{testBefore.failures.length - 5} more…
                  </Typography>
                )}
              </Box>
            )}
          </Box>
        )}

        {/* Live fixes accumulating */}
        <Box sx={{ mb: 2 }}>
          <Typography variant="caption" color="text.secondary">
            Fixes proposed so far ({runFixes.length})
          </Typography>
          {runFixes.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
              {isRunning ? "Waiting for the scanner to find something…" : "No fixes proposed."}
            </Typography>
          ) : (
            <Stack spacing={0.5} sx={{ mt: 0.5 }}>
              {runFixes.slice(0, 8).map((f) => (
                <Stack key={f.id} direction="row" spacing={1} alignItems="center">
                  <Chip
                    label={f.severity}
                    size="small"
                    sx={{ height: 16, fontSize: "0.6rem", textTransform: "uppercase" }}
                  />
                  <Typography variant="caption" sx={{ fontFamily: "monospace" }} noWrap>
                    {f.file_path}
                  </Typography>
                </Stack>
              ))}
              {runFixes.length > 8 && (
                <Typography variant="caption" color="text.secondary">
                  +{runFixes.length - 8} more…
                </Typography>
              )}
            </Stack>
          )}
        </Box>

        {/* Changes logged on the run itself */}
        {changes.length > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="caption" color="text.secondary">
              Changes logged ({changes.length})
            </Typography>
            <Stack spacing={0.25} sx={{ mt: 0.5 }}>
              {changes.slice(0, 5).map((c, i) => (
                <Typography key={i} variant="caption" sx={{ fontFamily: "monospace" }}>
                  • {typeof c === "string" ? c : c.file || c.description || JSON.stringify(c)}
                </Typography>
              ))}
            </Stack>
          </Box>
        )}

        {/* Error from the run */}
        {run?.error_message && (
          <Alert severity="error" variant="outlined" sx={{ mb: 2 }}>
            {run.error_message}
          </Alert>
        )}

        {/* Uncle feedback */}
        {run?.uncle_feedback && (
          <Alert severity="info" variant="outlined" sx={{ mb: 2 }}>
            <Typography variant="caption" color="text.secondary">Uncle Claude</Typography>
            <Typography variant="body2">{run.uncle_feedback}</Typography>
          </Alert>
        )}
      </DialogContent>

      <DialogActions>
        <Button size="small" onClick={onClose} disabled={isRunning}>
          {isDone ? "Close" : "Running…"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
