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
} from "@mui/material";
import {
  Science as ScienceIcon,
  Refresh as RefreshIcon,
  Download as DownloadIcon,
  RestartAlt as RevertIcon,
  CheckCircle as ActivateIcon,
  NightsStay as NightIcon,
} from "@mui/icons-material";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { io } from "socket.io-client";
import { SOCKET_URL } from "../api/apiClient";
import { ragAutoresearchService } from "../api/ragAutoresearchService";
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

const AutoresearchPage = () => {
  const [runs, setRuns] = useState([]);
  const [promotions, setPromotions] = useState([]);
  const [metrics, setMetrics] = useState([]);
  const [loading, setLoading] = useState(true);
  const [budgetHours, setBudgetHours] = useState(6);
  const [starting, setStarting] = useState(false);
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

  const fetchAll = useCallback(async () => {
    await Promise.all([fetchRuns(), fetchPromotions(), fetchMetrics()]);
    setLoading(false);
  }, [fetchRuns, fetchPromotions, fetchMetrics]);

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
      });

      socket.on("autoresearch:run_complete", () => {
        fetchRuns();
        fetchPromotions();
        fetchMetrics();
      });
    } catch {
      // Socket not available — polling covers it
    }

    pollRef.current = setInterval(fetchRuns, 30000);

    return () => {
      if (socketRef.current) {
        socketRef.current.disconnect();
        socketRef.current = null;
      }
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchAll, fetchRuns, fetchPromotions, fetchMetrics]);

  const activeRun = runs.find(
    (r) => r.status === "running" || r.status === "pending",
  );

  const handleResearchTonight = async () => {
    setStarting(true);
    try {
      await ragAutoresearchService.createRun({
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
              </Typography>
            </Box>
            <LinearProgress sx={{ borderRadius: 1 }} />
          </Box>
        )}
      </Paper>

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
                    <TableCell>{exp.parameter}</TableCell>
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
