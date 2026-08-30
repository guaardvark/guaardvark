// frontend/src/pages/AutoresearchPage.jsx
// Autoresearch 2.0 — nightly research runs, promotions, and experiment metrics.
/* eslint-env browser */

import React, { useState, useEffect, useRef, useCallback } from "react";
import PageLayout from "../components/layout/PageLayout";
import {
  Box,
  Typography,
  Paper,
  Button,
  Chip,
  TextField,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  TableContainer,
  LinearProgress,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Tooltip,
  Switch,
} from "@mui/material";
import {
  Science as ScienceIcon,
  Refresh as RefreshIcon,
  Download as DownloadIcon,
  RestartAlt as RevertIcon,
  CheckCircle as ActivateIcon,
  NightsStay as NightIcon,
  Stop as StopIcon,
} from "@mui/icons-material";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { io } from "socket.io-client";
import { SOCKET_URL } from "../api/apiClient";
import { ragAutoresearchService } from "../api/ragAutoresearchService";
import { selfImprovementService } from "../api/selfImprovementService";
import AlertSnackbar from "../components/common/AlertSnackbar";

const RUN_STATUS_COLORS = {
  running: "success",
  pending: "info",
  completed: "primary",
  halted: "warning",
  failed_precondition: "error",
  killed: "error",
};

const formatDate = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

const formatScore = (v) =>
  typeof v === "number" ? v.toFixed(3) : "—";

const ScoreSparkline = ({ points }) => {
  const vals = (points || []).filter((v) => typeof v === "number");
  if (vals.length < 2) return null;
  const w = 160;
  const h = 28;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const d = vals
    .map((v, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = h - 2 - ((v - min) / span) * (h - 4);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} aria-label="score sparkline">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
};

const AutoresearchPage = () => {
  const [runs, setRuns] = useState([]);
  const [promotions, setPromotions] = useState([]);
  const [metrics, setMetrics] = useState([]);
  const [loading, setLoading] = useState(true);
  const [budgetHours, setBudgetHours] = useState(6);
  const [runMode, setRunMode] = useState("unified");
  const [codeKeeps, setCodeKeeps] = useState([]);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [status, setStatus] = useState(null);
  const [settings, setSettings] = useState({});
  const [evalCount, setEvalCount] = useState(null);
  const [regenerating, setRegenerating] = useState(false);
  const [selectedRun, setSelectedRun] = useState(null);
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const [revertConfirmOpen, setRevertConfirmOpen] = useState(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: "",
    severity: "info",
  });
  const socketRef = useRef(null);
  const pollRef = useRef(null);

  const showMessage = useCallback((message, severity = "info") => {
    setSnackbar({ open: true, message, severity });
  }, []);

  const fetchRuns = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.listRuns();
      setRuns(data.runs || []);
    } catch (e) {
      /* backend may be offline */
    }
  }, []);

  const fetchPromotions = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.getPromotions();
      setPromotions(data.promotions || []);
    } catch (e) {
      /* ignore */
    }
  }, []);

  const fetchMetrics = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.getMetrics(50);
      setMetrics(data.experiments || []);
    } catch (e) {
      /* ignore */
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.getStatus();
      setStatus(data);
      if (typeof data.eval_pair_count === "number") {
        setEvalCount(data.eval_pair_count);
      }
      if (Array.isArray(data.code_keeps)) {
        setCodeKeeps(data.code_keeps);
      }
    } catch (e) {
      /* ignore */
    }
  }, []);

  const fetchSettings = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.getSettings();
      setSettings(data || {});
    } catch (e) {
      /* ignore */
    }
  }, []);

  const fetchEvalPairs = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.getEvalPairs();
      setEvalCount(data.count ?? (data.pairs || []).length);
    } catch (e) {
      /* ignore */
    }
  }, []);

  const fetchAll = useCallback(async () => {
    await Promise.all([
      fetchRuns(),
      fetchPromotions(),
      fetchMetrics(),
      fetchStatus(),
      fetchSettings(),
      fetchEvalPairs(),
    ]);
    setLoading(false);
  }, [
    fetchRuns,
    fetchPromotions,
    fetchMetrics,
    fetchStatus,
    fetchSettings,
    fetchEvalPairs,
  ]);

  // Initial load + 30s poll (same cadence as the dashboard card) +
  // Socket.IO push updates, following the GpuStatusCard pattern.
  useEffect(() => {
    fetchAll();

    try {
      const socket = io(SOCKET_URL, {
        reconnection: true,
        reconnectionAttempts: 3,
        transports: ["polling", "websocket"],
      });
      socketRef.current = socket;

      socket.on("autoresearch:experiment_complete", () => {
        fetchMetrics();
        fetchRuns();
        fetchStatus();
      });

      socket.on("autoresearch:run_complete", () => {
        fetchRuns();
        fetchPromotions();
        fetchMetrics();
        fetchStatus();
      });
    } catch {
      // Socket not available — polling covers it
    }

    pollRef.current = setInterval(() => {
      fetchRuns();
      fetchStatus();
    }, 30000);

    return () => {
      if (socketRef.current) {
        socketRef.current.disconnect();
        socketRef.current = null;
      }
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchAll, fetchRuns, fetchPromotions, fetchMetrics, fetchStatus]);

  const activeRun =
    runs.find((r) => r.status === "running" || r.status === "pending") ||
    status?.active_run ||
    null;

  const handleStop = async () => {
    setStopping(true);
    try {
      await ragAutoresearchService.stop();
      showMessage("Stop requested — the run will halt at the next check", "info");
      fetchStatus();
      fetchRuns();
    } catch (e) {
      showMessage(`Failed to stop: ${e.message}`, "error");
    } finally {
      setStopping(false);
    }
  };

  const handleSettingChange = async (key, value) => {
    const next = { ...settings, [key]: String(value) };
    setSettings(next);
    try {
      await ragAutoresearchService.updateSettings({ [key]: String(value) });
    } catch (e) {
      showMessage(`Failed to update ${key}: ${e.message}`, "error");
    }
  };

  const handleRegeneratePairs = async () => {
    setRegenerating(true);
    try {
      const data = await ragAutoresearchService.regenerateEvalPairs();
      showMessage(`Regenerated ${data.count ?? 0} eval pairs`, "success");
      fetchEvalPairs();
      fetchStatus();
    } catch (e) {
      showMessage(`Failed to regenerate eval pairs: ${e.message}`, "error");
    } finally {
      setRegenerating(false);
    }
  };

  const handleResearchTonight = async () => {
    setStarting(true);
    try {
      await ragAutoresearchService.createRun({
        mode: runMode,
        budget_hours: Number(budgetHours) || 6,
      });
      showMessage("Research run started", "success");
      fetchRuns();
    } catch (e) {
      if (e.status === 409) {
        showMessage("A research run is already in progress", "warning");
      } else {
        showMessage(`Failed to start research run: ${e.message}`, "error");
      }
    } finally {
      setStarting(false);
    }
  };

  const handleSelectRun = async (runId) => {
    setRunDetailLoading(true);
    try {
      const run = await ragAutoresearchService.getRun(runId);
      setSelectedRun(run);
    } catch (e) {
      showMessage(`Failed to load run: ${e.message}`, "error");
    } finally {
      setRunDetailLoading(false);
    }
  };

  const handleActivatePromotion = async (configId) => {
    try {
      await ragAutoresearchService.activatePromotion(configId);
      showMessage("Config activated", "success");
      fetchPromotions();
    } catch (e) {
      showMessage(`Failed to activate: ${e.message}`, "error");
    }
  };

  const handleCodeKeep = async (fixId, action) => {
    try {
      if (action === "approve") {
        await selfImprovementService.approveFix(fixId);
        showMessage("Code keep approved — apply from Settings Pending Fixes", "success");
      } else {
        await selfImprovementService.rejectFix(fixId);
        showMessage("Code keep rejected", "info");
      }
      fetchStatus();
    } catch (e) {
      showMessage(`Failed to ${action} code keep: ${e.message}`, "error");
    }
  };

  const handleRevert = async () => {
    setRevertConfirmOpen(false);
    try {
      const result = await ragAutoresearchService.revertPromotion();
      if (result.status === "nothing_active") {
        showMessage("No active config to revert", "info");
      } else {
        showMessage("Reverted to previous config", "success");
      }
      fetchPromotions();
    } catch (e) {
      showMessage(`Failed to revert: ${e.message}`, "error");
    }
  };

  return (
    <PageLayout
      title="Autoresearch"
      variant="standard"
      actions={
        <Button
          size="small"
          startIcon={<RefreshIcon />}
          onClick={fetchAll}
          disabled={loading}
        >
          Refresh
        </Button>
      }
    >
      {/* --- Research Tonight --- */}
      <Paper
        elevation={0}
        sx={{ p: 2, mb: 3, border: 1, borderColor: "divider" }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 2,
            flexWrap: "wrap",
          }}
        >
          <NightIcon color="primary" />
          <Box sx={{ flex: 1, minWidth: 200 }}>
            <Typography variant="subtitle1">Overnight research run</Typography>
            <Typography variant="caption" color="text.secondary">
              Runs a bounded experiment loop against the current RAG config and
              writes a report when it finishes.
            </Typography>
          </Box>
          <Box sx={{ display: "flex", gap: 0.5 }}>
            {[
              { id: "unified", label: "Unified" },
              { id: "rag_tuning", label: "Retrieval" },
              { id: "code_tuning", label: "Code" },
            ].map((m) => (
              <Button
                key={m.id}
                size="small"
                variant={runMode === m.id ? "contained" : "outlined"}
                onClick={() => setRunMode(m.id)}
                disabled={Boolean(activeRun)}
              >
                {m.label}
              </Button>
            ))}
          </Box>
          <TextField
            label="Budget (hours)"
            type="number"
            size="small"
            value={budgetHours}
            onChange={(e) => setBudgetHours(e.target.value)}
            inputProps={{ min: 1, max: 12, step: 1 }}
            sx={{ width: 130 }}
          />
          <Button
            variant="contained"
            startIcon={
              starting ? <CircularProgress size={16} /> : <ScienceIcon />
            }
            onClick={handleResearchTonight}
            disabled={starting || Boolean(activeRun)}
          >
            Research Tonight
          </Button>
          <Button
            variant="outlined"
            color="warning"
            startIcon={
              stopping ? <CircularProgress size={16} /> : <StopIcon />
            }
            onClick={handleStop}
            disabled={stopping || !activeRun}
          >
            Stop
          </Button>
        </Box>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 2,
            flexWrap: "wrap",
            mt: 2,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography variant="body2">Auto nightly</Typography>
            <Switch
              size="small"
              checked={settings.rag_autoresearch_auto_enabled === "true"}
              onChange={(e) =>
                handleSettingChange(
                  "rag_autoresearch_auto_enabled",
                  e.target.checked,
                )
              }
            />
          </Box>
          <TextField
            label="Nightly window"
            size="small"
            placeholder="20:00-02:00"
            value={settings.autoresearch_nightly_window || ""}
            onChange={(e) =>
              setSettings({
                ...settings,
                autoresearch_nightly_window: e.target.value,
              })
            }
            onBlur={(e) =>
              handleSettingChange("autoresearch_nightly_window", e.target.value)
            }
            sx={{ width: 160 }}
          />
          <TextField
            label="Proposer model"
            size="small"
            placeholder="(active model)"
            value={settings.autoresearch_proposer_model || ""}
            onChange={(e) =>
              setSettings({
                ...settings,
                autoresearch_proposer_model: e.target.value,
              })
            }
            onBlur={(e) =>
              handleSettingChange("autoresearch_proposer_model", e.target.value)
            }
            sx={{ minWidth: 180 }}
          />
          <TextField
            label="Judge model"
            size="small"
            placeholder="(active model)"
            value={settings.autoresearch_judge_model || ""}
            onChange={(e) =>
              setSettings({
                ...settings,
                autoresearch_judge_model: e.target.value,
              })
            }
            onBlur={(e) =>
              handleSettingChange("autoresearch_judge_model", e.target.value)
            }
            sx={{ minWidth: 180 }}
          />
          <Typography variant="body2" color="text.secondary">
            Eval pairs: {evalCount ?? "—"}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={handleRegeneratePairs}
            disabled={regenerating || Boolean(activeRun)}
          >
            {regenerating ? "Regenerating…" : "Regenerate eval pairs"}
          </Button>
        </Box>
        {activeRun && (
          <Box sx={{ mt: 2 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}>
              <Chip
                label={activeRun.status}
                size="small"
                color={RUN_STATUS_COLORS[activeRun.status] || "default"}
              />
              <Typography variant="body2">
                {activeRun.run_tag} — {activeRun.experiments_completed ?? 0}{" "}
                experiments completed
                {status?.current_parameter
                  ? ` · ${status.current_parameter}`
                  : ""}
              </Typography>
              <Box sx={{ ml: "auto", color: "primary.main" }}>
                <ScoreSparkline
                  points={metrics
                    .filter(
                      (m) =>
                        !activeRun.run_tag || m.run_tag === activeRun.run_tag,
                    )
                    .map((m) => m.composite_score)
                    .reverse()}
                />
              </Box>
            </Box>
            <LinearProgress
              variant={
                typeof (status?.active_run?.budget_remaining_s) === "number" &&
                activeRun.wall_clock_budget_s
                  ? "determinate"
                  : "indeterminate"
              }
              value={
                typeof (status?.active_run?.budget_remaining_s) === "number" &&
                activeRun.wall_clock_budget_s
                  ? Math.max(
                      0,
                      Math.min(
                        100,
                        (1 -
                          status.active_run.budget_remaining_s /
                            activeRun.wall_clock_budget_s) *
                          100,
                      ),
                    )
                  : undefined
              }
              sx={{ borderRadius: 1 }}
            />
            {typeof status?.active_run?.budget_remaining_s === "number" && (
              <Typography variant="caption" color="text.secondary">
                {Math.ceil(status.active_run.budget_remaining_s / 60)} min remaining
              </Typography>
            )}
          </Box>
        )}
      </Paper>

      {/* --- Code keeps (PendingFixes staged by the director) --- */}
      {codeKeeps.length > 0 && (
        <Paper
          elevation={0}
          sx={{ p: 2, mb: 3, border: 1, borderColor: "divider" }}
        >
          <Typography variant="h6" sx={{ mb: 1 }}>
            Code keeps
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
            Swarm arms that passed preserve-and-extend. Approve here, then apply
            from Settings — nothing auto-merges to main.
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Description</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Created</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {codeKeeps.map((fix) => (
                  <TableRow key={fix.id}>
                    <TableCell>{fix.fix_description}</TableCell>
                    <TableCell>
                      <Chip label={fix.status} size="small" sx={{ height: 20, fontSize: "0.7rem" }} />
                    </TableCell>
                    <TableCell>{formatDate(fix.created_at)}</TableCell>
                    <TableCell align="right">
                      {fix.status === "proposed" && (
                        <>
                          <Button size="small" onClick={() => handleCodeKeep(fix.id, "approve")}>
                            Approve
                          </Button>
                          <Button size="small" color="warning" onClick={() => handleCodeKeep(fix.id, "reject")}>
                            Reject
                          </Button>
                        </>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      )}

      {/* --- Runs --- */}
      <Paper
        elevation={0}
        sx={{ p: 2, mb: 3, border: 1, borderColor: "divider" }}
      >
        <Typography variant="h6" sx={{ mb: 1 }}>
          Runs
        </Typography>
        {runs.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No research runs yet.
          </Typography>
        ) : (
          <TableContainer sx={{ maxHeight: 320 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>Run</TableCell>
                  <TableCell>Mode</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Started</TableCell>
                  <TableCell>Ended</TableCell>
                  <TableCell align="right">Experiments</TableCell>
                  <TableCell align="right">Baseline → Best</TableCell>
                  <TableCell>Halt reason</TableCell>
                  <TableCell align="right">Ledger</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {runs.map((run) => (
                  <TableRow
                    key={run.id}
                    hover
                    selected={selectedRun?.id === run.id}
                    onClick={() => handleSelectRun(run.id)}
                    sx={{ cursor: "pointer" }}
                  >
                    <TableCell>{run.run_tag}</TableCell>
                    <TableCell>{run.mode}</TableCell>
                    <TableCell>
                      <Chip
                        label={run.status}
                        size="small"
                        color={RUN_STATUS_COLORS[run.status] || "default"}
                        sx={{ height: 20, fontSize: "0.7rem" }}
                      />
                    </TableCell>
                    <TableCell>{formatDate(run.started_at)}</TableCell>
                    <TableCell>{formatDate(run.ended_at)}</TableCell>
                    <TableCell align="right">
                      {run.experiments_completed ?? 0}
                      {run.experiments_planned
                        ? ` / ${run.experiments_planned}`
                        : ""}
                    </TableCell>
                    <TableCell align="right">
                      {formatScore(run.baseline_score)} →{" "}
                      {formatScore(run.best_score)}
                    </TableCell>
                    <TableCell>{run.halt_reason || "—"}</TableCell>
                    <TableCell align="right">
                      <Tooltip title="Download experiment ledger (TSV)">
                        <Button
                          size="small"
                          component="a"
                          href={ragAutoresearchService.getRunLedgerUrl(run.id)}
                          download={`${run.run_tag}-ledger.tsv`}
                          onClick={(e) => e.stopPropagation()}
                          startIcon={<DownloadIcon sx={{ fontSize: 14 }} />}
                        >
                          TSV
                        </Button>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}

        {/* Selected run report */}
        {runDetailLoading && <LinearProgress sx={{ mt: 2, borderRadius: 1 }} />}
        {selectedRun && !runDetailLoading && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Report — {selectedRun.run_tag}
            </Typography>
            {selectedRun.report_md ? (
              <Box
                sx={{
                  p: 2,
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 1,
                  maxHeight: 400,
                  overflow: "auto",
                  "& h1, & h2, & h3": { mt: 1.5, mb: 0.5 },
                  "& p, & li": { fontSize: "0.85rem" },
                  "& code": { fontFamily: "monospace", fontSize: "0.8rem" },
                  "& table": { borderCollapse: "collapse" },
                  "& th, & td": {
                    border: 1,
                    borderColor: "divider",
                    px: 1,
                    py: 0.25,
                    fontSize: "0.8rem",
                  },
                }}
              >
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedRun.report_md}</ReactMarkdown>
              </Box>
            ) : (
              <Typography variant="body2" color="text.secondary">
                No report yet — the run writes its report when it finishes.
              </Typography>
            )}
          </Box>
        )}
      </Paper>

      {/* --- Promotions --- */}
      <Paper
        elevation={0}
        sx={{ p: 2, mb: 3, border: 1, borderColor: "divider" }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            mb: 1,
          }}
        >
          <Typography variant="h6">Promotions</Typography>
          <Button
            size="small"
            color="warning"
            startIcon={<RevertIcon />}
            onClick={() => setRevertConfirmOpen(true)}
          >
            Revert to previous
          </Button>
        </Box>
        {promotions.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No promoted configs yet.
          </Typography>
        ) : (
          <TableContainer sx={{ maxHeight: 320 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>Created</TableCell>
                  <TableCell>Source</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Active</TableCell>
                  <TableCell align="right">Score</TableCell>
                  <TableCell>Params</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {promotions.map((p) => (
                  <TableRow key={p.id} hover>
                    <TableCell>{formatDate(p.created_at)}</TableCell>
                    <TableCell>{p.source || "—"}</TableCell>
                    <TableCell>
                      <Chip
                        label={p.status || "—"}
                        size="small"
                        color={p.status === "promoted" ? "primary" : "default"}
                        sx={{ height: 20, fontSize: "0.7rem" }}
                      />
                    </TableCell>
                    <TableCell>
                      {p.is_active && (
                        <Chip
                          label="active"
                          size="small"
                          color="success"
                          sx={{ height: 20, fontSize: "0.7rem" }}
                        />
                      )}
                    </TableCell>
                    <TableCell align="right">
                      {formatScore(p.composite_score)}
                    </TableCell>
                    <TableCell
                      sx={{
                        maxWidth: 320,
                        fontFamily: "monospace",
                        fontSize: "0.7rem",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      <Tooltip
                        title={
                          <pre style={{ margin: 0, fontSize: "0.7rem" }}>
                            {JSON.stringify(p.params, null, 2)}
                          </pre>
                        }
                      >
                        <span>{JSON.stringify(p.params)}</span>
                      </Tooltip>
                    </TableCell>
                    <TableCell align="right">
                      {!p.is_active && (
                        <Button
                          size="small"
                          startIcon={<ActivateIcon sx={{ fontSize: 14 }} />}
                          onClick={() => handleActivatePromotion(p.id)}
                        >
                          Activate
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      {/* --- Experiments --- */}
      <Paper
        elevation={0}
        sx={{ p: 2, mb: 3, border: 1, borderColor: "divider" }}
      >
        <Typography variant="h6" sx={{ mb: 1 }}>
          Experiments
        </Typography>
        {metrics.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No experiments recorded yet.
          </Typography>
        ) : (
          <TableContainer sx={{ maxHeight: 400 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>Created</TableCell>
                  <TableCell>Parameter</TableCell>
                  <TableCell>New value</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell align="right">Delta</TableCell>
                  <TableCell>Source</TableCell>
                  <TableCell>Judge</TableCell>
                  <TableCell align="right">Hit rate</TableCell>
                  <TableCell align="right">MRR</TableCell>
                  <TableCell align="right">nDCG</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {metrics.map((exp) => (
                  <TableRow key={exp.id} hover>
                    <TableCell>{formatDate(exp.created_at)}</TableCell>
                    <TableCell>
                      <Tooltip title={exp.hypothesis || ""}>
                        <span>{exp.parameter}</span>
                      </Tooltip>
                    </TableCell>
                    <TableCell>{exp.new_value}</TableCell>
                    <TableCell>
                      <Chip
                        label={exp.status}
                        size="small"
                        color={
                          exp.status === "keep"
                            ? "success"
                            : exp.status === "crash"
                              ? "error"
                              : "default"
                        }
                        sx={{ height: 20, fontSize: "0.7rem" }}
                      />
                    </TableCell>
                    <TableCell align="right">
                      {typeof exp.delta === "number"
                        ? `${exp.delta > 0 ? "+" : ""}${exp.delta.toFixed(3)}`
                        : "—"}
                    </TableCell>
                    <TableCell>
                      {exp.proposal_source && (
                        <Chip
                          label={exp.proposal_source}
                          size="small"
                          color={
                            exp.proposal_source === "llm"
                              ? "secondary"
                              : "default"
                          }
                          variant={
                            exp.proposal_source === "llm"
                              ? "filled"
                              : "outlined"
                          }
                          sx={{ height: 20, fontSize: "0.7rem" }}
                        />
                      )}
                    </TableCell>
                    <TableCell>{exp.judge_model || "—"}</TableCell>
                    <TableCell align="right">
                      {formatScore(exp.retrieval_metrics?.hit_rate_at_k)}
                    </TableCell>
                    <TableCell align="right">
                      {formatScore(exp.retrieval_metrics?.mrr)}
                    </TableCell>
                    <TableCell align="right">
                      {formatScore(exp.retrieval_metrics?.ndcg_at_10)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      {/* Revert confirm dialog */}
      <Dialog
        open={revertConfirmOpen}
        onClose={() => setRevertConfirmOpen(false)}
      >
        <DialogTitle>Revert to previous config?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This deactivates the currently active RAG config and re-activates
            the previously promoted one. With no predecessor, retrieval falls
            back to the legacy defaults.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRevertConfirmOpen(false)}>Cancel</Button>
          <Button color="warning" variant="contained" onClick={handleRevert}>
            Revert
          </Button>
        </DialogActions>
      </Dialog>

      <AlertSnackbar
        open={snackbar.open}
        message={snackbar.message}
        severity={snackbar.severity}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
      />
    </PageLayout>
  );
};

export default AutoresearchPage;
