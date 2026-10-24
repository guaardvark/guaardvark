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
import { alpha } from "@mui/material/styles";
import {
  Science as ScienceIcon,
  Refresh as RefreshIcon,
  Download as DownloadIcon,
  RestartAlt as RevertIcon,
  CheckCircle as ActivateIcon,
  NightsStay as NightIcon,
  Stop as StopIcon,
  Check as SavedIcon,
  DeleteSweep as ResetDefaultsIcon,
} from "@mui/icons-material";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { io } from "socket.io-client";
import { SOCKET_URL } from "../api/apiClient";
import { ragAutoresearchService } from "../api/ragAutoresearchService";
import { selfImprovementService } from "../api/selfImprovementService";
import { guardedMediaSrc } from "../utils/assetGuard";
import AlertSnackbar from "../components/common/AlertSnackbar";
import CodeKeepsTable, { formatServerTime as formatDate } from "../components/selfImprovement/CodeKeepsTable";
import EntityContextMenu from "../components/common/EntityContextMenu";
import useContextMenu from "../hooks/useContextMenu";
import {
  describeReasons,
  evalPairsText,
  experimentLabel,
  judgeWarning,
  runOutcome,
} from "../utils/autoresearchLabels";
import { translate, useUiTranslation } from "../i18n";

const RUN_STATUS_COLORS = {
  running: "success",
  pending: "info",
  completed: "primary",
  halted: "warning",
  failed_precondition: "error",
  killed: "error",
};

// A run's status as shown; the value from the API stays the one compared.
const runStatusLabel = (status) =>
  ({
    running: translate("autoresearch.runStatus.running"),
    pending: translate("autoresearch.runStatus.pending"),
    completed: translate("autoresearch.runStatus.completed"),
    halted: translate("autoresearch.runStatus.halted"),
    failed_precondition: translate("autoresearch.runStatus.failed_precondition"),
    killed: translate("autoresearch.runStatus.killed"),
  })[status] ?? status;

const formatScore = (v) =>
  typeof v === "number" ? v.toFixed(3) : "—";

const AUTO_START_ID = "autoresearch-auto-start";

// A function, so the labels are worded in the interface language at render.
// `name` is the label as it reads inside a sentence ("Failed to save judge model: …").
const settingFields = () => [
  {
    key: "autoresearch_proposer_model",
    label: translate("autoresearch.settings.proposerModel"),
    name: translate("autoresearch.settings.proposerModelName"),
    placeholder: translate("autoresearch.settings.activeModelPlaceholder"),
    width: 200,
  },
  {
    key: "autoresearch_judge_model",
    label: translate("autoresearch.settings.judgeModel"),
    name: translate("autoresearch.settings.judgeModelName"),
    placeholder: translate("autoresearch.settings.activeModelPlaceholder"),
    width: 200,
  },
  {
    key: "autoresearch_nightly_window",
    label: translate("autoresearch.settings.nightlyWindow"),
    name: translate("autoresearch.settings.nightlyWindowName"),
    placeholder: "01:00-06:00",
    width: 170,
  },
];

const ScoreSparkline = ({ points }) => {
  const { t } = useUiTranslation();
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
    <svg width={w} height={h} aria-label={t("autoresearch.sparkline")}>
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
};

const AutoresearchPage = () => {
  const { t } = useUiTranslation();
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
  // Text fields are edited as drafts and only written back to `settings`
  // once the PUT succeeds, so a failed save reverts to the stored value.
  const [settingDrafts, setSettingDrafts] = useState({});
  const [savedKeys, setSavedKeys] = useState({});
  const [resettingConfig, setResettingConfig] = useState(false);
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
  const savedTimersRef = useRef({});
  const runMenu = useContextMenu();

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
      setSettingDrafts({});
    } catch (e) {
      /* ignore */
    }
  }, []);

  // The eval-pair count comes only from /status (eval_pairs), so the page
  // shows one number with its unindexed share.
  const fetchAll = useCallback(async () => {
    await Promise.all([
      fetchRuns(),
      fetchPromotions(),
      fetchMetrics(),
      fetchStatus(),
      fetchSettings(),
    ]);
    setLoading(false);
  }, [fetchRuns, fetchPromotions, fetchMetrics, fetchStatus, fetchSettings]);

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

    const savedTimers = savedTimersRef.current;
    return () => {
      if (socketRef.current) {
        socketRef.current.disconnect();
        socketRef.current = null;
      }
      if (pollRef.current) clearInterval(pollRef.current);
      Object.values(savedTimers).forEach(clearTimeout);
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
      showMessage(t("autoresearch.run.stopRequested"), "info");
      fetchStatus();
      fetchRuns();
    } catch (e) {
      showMessage(t("autoresearch.run.stopFailed", { error: e.message }), "error");
    } finally {
      setStopping(false);
    }
  };

  const markSaved = useCallback((key) => {
    setSavedKeys((s) => ({ ...s, [key]: true }));
    if (savedTimersRef.current[key]) clearTimeout(savedTimersRef.current[key]);
    savedTimersRef.current[key] = setTimeout(() => {
      setSavedKeys((s) => {
        const next = { ...s };
        delete next[key];
        return next;
      });
      delete savedTimersRef.current[key];
    }, 2500);
  }, []);

  // Writes one setting. On failure the stored value stands and the field
  // snaps back to it — a rejected save must never look like it took.
  const saveSetting = useCallback(
    async (key, value, label) => {
      const next = String(value);
      try {
        await ragAutoresearchService.updateSettings({ [key]: next });
        setSettings((s) => ({ ...s, [key]: next }));
        setSettingDrafts((d) => {
          const rest = { ...d };
          delete rest[key];
          return rest;
        });
        markSaved(key);
        return true;
      } catch (e) {
        setSettingDrafts((d) => {
          const rest = { ...d };
          delete rest[key];
          return rest;
        });
        showMessage(t("autoresearch.settings.saveFailed", { setting: label, error: e.message }), "error");
        return false;
      }
    },
    [markSaved, showMessage],
  );

  const commitSettingField = useCallback(
    (key, label) => {
      const draft = settingDrafts[key];
      if (draft === undefined) return;
      if (draft === (settings[key] ?? "")) {
        setSettingDrafts((d) => {
          const rest = { ...d };
          delete rest[key];
          return rest;
        });
        return;
      }
      saveSetting(key, draft, label);
    },
    [settingDrafts, settings, saveSetting],
  );

  const settingFieldValue = (key) =>
    settingDrafts[key] !== undefined
      ? settingDrafts[key]
      : (settings[key] ?? "");

  const handleSettingFieldChange = (key, value) =>
    setSettingDrafts((d) => ({ ...d, [key]: value }));

  const RESET_CONFIRM = t("autoresearch.settings.resetConfirm");

  const handleResetToDefaults = async () => {
    if (!window.confirm(RESET_CONFIRM)) return;
    setResettingConfig(true);
    try {
      await ragAutoresearchService.resetConfig();
      setSettingDrafts({});
      await fetchAll();
      showMessage(
        t("autoresearch.settings.resetDone"),
        "success",
      );
    } catch (e) {
      showMessage(t("autoresearch.settings.resetFailed", { error: e.message }), "error");
    } finally {
      setResettingConfig(false);
    }
  };

  const handleRegeneratePairs = async () => {
    setRegenerating(true);
    try {
      const data = await ragAutoresearchService.regenerateEvalPairs();
      showMessage(t("autoresearch.evalPairs.regenerated", { count: data.count ?? 0 }), "success");
      fetchStatus();
    } catch (e) {
      showMessage(t("autoresearch.evalPairs.regenerateFailed", { error: e.message }), "error");
    } finally {
      setRegenerating(false);
    }
  };

  const handleResearchTonight = async () => {
    setStarting(true);
    try {
      const result = await ragAutoresearchService.createRun({
        mode: runMode,
        budget_hours: Number(budgetHours) || 6,
      });
      const warnings = result?.warnings || [];
      if (warnings.length) {
        showMessage(
          t("autoresearch.run.startedWithWarning", { warning: describeReasons(warnings.join("; ")) }),
          "warning",
        );
      } else {
        showMessage(t("autoresearch.run.started"), "success");
      }
      fetchRuns();
      fetchStatus();
    } catch (e) {
      if (e.status === 422) {
        // Refused before anything was queued; the run row records why.
        showMessage(t("autoresearch.run.notRun", { reason: describeReasons(e.message) }), "warning");
        fetchRuns();
        fetchStatus();
      } else if (e.status === 409) {
        showMessage(t("autoresearch.run.alreadyRunning"), "warning");
      } else {
        showMessage(t("autoresearch.run.startFailed", { error: e.message }), "error");
      }
    } finally {
      setStarting(false);
    }
  };

  const downloadLedger = (run) => {
    const link = document.createElement("a");
    link.href = ragAutoresearchService.getRunLedgerUrl(run.id);
    link.download = `${run.run_tag}-ledger.tsv`;
    link.click();
  };

  const runMenuActions = (run) => [
    { label: t("autoresearch.runs.viewReport"), onClick: () => handleSelectRun(run.id) },
    { label: t("autoresearch.runs.downloadLedger"), onClick: () => downloadLedger(run) },
  ];

  const handleSelectRun = async (runId) => {
    setRunDetailLoading(true);
    try {
      const run = await ragAutoresearchService.getRun(runId);
      setSelectedRun(run);
    } catch (e) {
      showMessage(t("autoresearch.runs.loadFailed", { error: e.message }), "error");
    } finally {
      setRunDetailLoading(false);
    }
  };

  const handleActivatePromotion = async (configId) => {
    try {
      await ragAutoresearchService.activatePromotion(configId);
      showMessage(t("autoresearch.promotions.activated"), "success");
      fetchPromotions();
    } catch (e) {
      showMessage(t("autoresearch.promotions.activateFailed", { error: e.message }), "error");
    }
  };

  const handleCodeKeep = async (fixId, action) => {
    try {
      if (action === "approve") {
        await selfImprovementService.approveFix(fixId);
        showMessage(t("autoresearch.codeKeepApproved"), "success");
      } else {
        await selfImprovementService.rejectFix(fixId);
        showMessage(t("selfImprovement.codeKeeps.rejected"), "info");
      }
      fetchStatus();
    } catch (e) {
      showMessage(
        action === "approve"
          ? t("selfImprovement.codeKeeps.approveFailed", { error: e.message })
          : t("selfImprovement.codeKeeps.rejectFailed", { error: e.message }),
        "error",
      );
    }
  };

  const handleRevert = async () => {
    setRevertConfirmOpen(false);
    try {
      const result = await ragAutoresearchService.revertPromotion();
      if (result.status === "nothing_active") {
        showMessage(t("autoresearch.promotions.nothingToRevert"), "info");
      } else {
        showMessage(t("autoresearch.promotions.reverted"), "success");
      }
      fetchPromotions();
    } catch (e) {
      showMessage(t("autoresearch.promotions.revertFailed", { error: e.message }), "error");
    }
  };

  return (
    <PageLayout
      title={t("autoresearch.title")}
      variant="standard"
      actions={
        <Button
          size="small"
          startIcon={<RefreshIcon />}
          onClick={fetchAll}
          disabled={loading}
        >
          {t("common.refresh")}
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
            <Typography variant="subtitle1">{t("autoresearch.run.title")}</Typography>
            <Typography variant="caption" color="text.secondary">
              {t("autoresearch.run.intro")}
            </Typography>
          </Box>
          <Box sx={{ display: "flex", gap: 0.5 }}>
            {[
              { id: "unified", label: t("autoresearch.run.modeUnified") },
              { id: "rag_tuning", label: t("autoresearch.run.modeRetrieval") },
              { id: "code_tuning", label: t("autoresearch.run.modeCode") },
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
            label={t("autoresearch.run.budget")}
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
            {t("autoresearch.run.researchTonight")}
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
            {t("autoresearch.run.stop")}
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
          <Typography variant="body2" color="text.secondary">
            {t("autoresearch.evalPairs.summary", { pairs: evalPairsText(status) })}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={handleRegeneratePairs}
            disabled={regenerating || Boolean(activeRun)}
          >
            {regenerating ? t("autoresearch.evalPairs.regenerating") : t("autoresearch.evalPairs.regenerate")}
          </Button>
        </Box>
        {activeRun && (
          <Box sx={{ mt: 2 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}>
              <Chip
                label={runStatusLabel(activeRun.status)}
                size="small"
                color={RUN_STATUS_COLORS[activeRun.status] || "default"}
              />
              <Typography variant="body2">
                {t("autoresearch.run.progress", {
                  tag: activeRun.run_tag,
                  count: activeRun.experiments_completed ?? 0,
                })}
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
                {t("autoresearch.run.minutesRemaining", { minutes: Math.ceil(status.active_run.budget_remaining_s / 60) })}
              </Typography>
            )}
          </Box>
        )}
      </Paper>

      {/* --- Settings --- */}
      <Paper
        elevation={0}
        sx={{ p: 2, mb: 3, border: 1, borderColor: "divider" }}
      >
        <Typography variant="h6" sx={{ mb: 1 }}>
          {t("autoresearch.settings.title")}
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mb: 2 }}
        >
          {t("autoresearch.settings.intro")}
        </Typography>

        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            gap: 2,
            flexWrap: "wrap",
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              minHeight: 40,
              mt: 0.5,
            }}
          >
            <Typography variant="body2" component="label" htmlFor={AUTO_START_ID}>
              {t("autoresearch.settings.autoStart")}
            </Typography>
            <Switch
              size="small"
              id={AUTO_START_ID}
              inputProps={{ "aria-label": t("autoresearch.settings.autoStart") }}
              checked={settings.rag_autoresearch_auto_enabled === "true"}
              disabled={resettingConfig}
              onChange={(e) =>
                saveSetting(
                  "rag_autoresearch_auto_enabled",
                  e.target.checked,
                  t("autoresearch.settings.autoStartName"),
                )
              }
            />
            {savedKeys.rag_autoresearch_auto_enabled && (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.25,
                  color: "success.main",
                }}
              >
                <SavedIcon sx={{ fontSize: 16 }} />
                <Typography variant="caption">{t("autoresearch.settings.saved")}</Typography>
              </Box>
            )}
          </Box>

          {settingFields().map((f) => {
            const warning =
              f.key === "autoresearch_judge_model" ? judgeWarning(status?.judge) : null;
            return (
              <TextField
                key={f.key}
                label={f.label}
                size="small"
                placeholder={f.placeholder}
                value={settingFieldValue(f.key)}
                disabled={resettingConfig}
                onChange={(e) => handleSettingFieldChange(f.key, e.target.value)}
                onBlur={() => commitSettingField(f.key, f.name)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    e.target.blur();
                  }
                }}
                helperText={savedKeys[f.key] ? t("autoresearch.settings.saved") : warning || " "}
                FormHelperTextProps={{
                  sx: {
                    color: savedKeys[f.key]
                      ? "success.main"
                      : warning
                        ? "warning.main"
                        : "inherit",
                  },
                }}
                sx={{ width: warning ? 320 : f.width }}
              />
            );
          })}
        </Box>

        <Box
          sx={{
            mt: 2,
            pt: 2,
            borderTop: 1,
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            gap: 2,
            flexWrap: "wrap",
          }}
        >
          <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>
            {t("autoresearch.settings.resetNote")}
          </Typography>
          <Button
            variant="outlined"
            color="error"
            size="small"
            startIcon={
              resettingConfig ? (
                <CircularProgress size={16} />
              ) : (
                <ResetDefaultsIcon />
              )
            }
            onClick={handleResetToDefaults}
            disabled={resettingConfig}
            // The active theme flattens every outlined button to grey; a
            // destructive action has to keep reading as destructive.
            sx={(t) => ({
              color: "error.main",
              borderColor: alpha(t.palette.error.main, 0.6),
              "&:hover": {
                borderColor: "error.main",
                backgroundColor: alpha(t.palette.error.main, 0.08),
              },
            })}
          >
            {t("autoresearch.settings.reset")}
          </Button>
        </Box>
      </Paper>

      {/* --- Code keeps (PendingFixes staged by the director) --- */}
      <CodeKeepsTable keeps={codeKeeps} onDecide={handleCodeKeep} />

      {/* --- Runs --- */}
      <Paper
        elevation={0}
        sx={{ p: 2, mb: 3, border: 1, borderColor: "divider" }}
      >
        <Typography variant="h6" sx={{ mb: 1 }}>
          {t("autoresearch.runs.title")}
        </Typography>
        {runs.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            {t("autoresearch.runs.empty")}
          </Typography>
        ) : (
          <TableContainer sx={{ maxHeight: 320 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>{t("autoresearch.runs.run")}</TableCell>
                  <TableCell>{t("autoresearch.runs.mode")}</TableCell>
                  <TableCell>{t("autoresearch.runs.status")}</TableCell>
                  <TableCell>{t("autoresearch.runs.started")}</TableCell>
                  <TableCell>{t("autoresearch.runs.ended")}</TableCell>
                  <TableCell align="right">{t("autoresearch.runs.experiments")}</TableCell>
                  <TableCell align="right">{t("autoresearch.runs.scores")}</TableCell>
                  <TableCell>{t("autoresearch.runs.haltReason")}</TableCell>
                  <TableCell align="right">{t("autoresearch.runs.ledger")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {runs.map((run) => {
                  const outcome = runOutcome(run);
                  return (
                  <TableRow
                    key={run.id}
                    hover
                    selected={selectedRun?.id === run.id || runMenu.payload?.id === run.id}
                    onClick={() => handleSelectRun(run.id)}
                    onContextMenu={(e) => runMenu.open(e, run)}
                    sx={{ cursor: "pointer" }}
                  >
                    <TableCell>{run.run_tag}</TableCell>
                    <TableCell>{run.mode}</TableCell>
                    <TableCell>
                      <Chip
                        label={runStatusLabel(run.status)}
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
                      <Tooltip title={outcome.detail}>
                        <Box
                          component="span"
                          sx={outcome.kind === "not_run" ? { color: "warning.main" } : undefined}
                        >
                          {outcome.text}
                        </Box>
                      </Tooltip>
                    </TableCell>
                    <TableCell>{run.halt_reason || "—"}</TableCell>
                    <TableCell align="right">
                      <Tooltip title={t("autoresearch.runs.ledgerTooltip")}>
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
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        <EntityContextMenu
          anchorPosition={runMenu.anchorPosition}
          onClose={runMenu.close}
          actions={runMenu.payload ? runMenuActions(runMenu.payload) : []}
        />

        {/* Selected run report */}
        {runDetailLoading && <LinearProgress sx={{ mt: 2, borderRadius: 1 }} />}
        {selectedRun && !runDetailLoading && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              {t("autoresearch.runs.reportHeading", { tag: selectedRun.run_tag })}
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
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    // The report quotes model-proposed values; never load a remote image from it.
                    img: (props) => <img {...props} src={guardedMediaSrc(props.src)} alt={props.alt || ""} />,
                  }}
                >
                  {selectedRun.report_md}
                </ReactMarkdown>
              </Box>
            ) : (
              <Typography variant="body2" color="text.secondary">
                {t("autoresearch.runs.noReport")}
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
          <Typography variant="h6">{t("autoresearch.promotions.title")}</Typography>
          <Button
            size="small"
            color="warning"
            startIcon={<RevertIcon />}
            onClick={() => setRevertConfirmOpen(true)}
          >
            {t("autoresearch.promotions.revert")}
          </Button>
        </Box>
        {promotions.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            {t("autoresearch.promotions.empty")}
          </Typography>
        ) : (
          <TableContainer sx={{ maxHeight: 320 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>{t("autoresearch.promotions.created")}</TableCell>
                  <TableCell>{t("autoresearch.promotions.source")}</TableCell>
                  <TableCell>{t("autoresearch.promotions.status")}</TableCell>
                  <TableCell>{t("autoresearch.promotions.active")}</TableCell>
                  <TableCell align="right">{t("autoresearch.promotions.score")}</TableCell>
                  <TableCell>{t("autoresearch.promotions.params")}</TableCell>
                  <TableCell align="right">{t("autoresearch.promotions.actions")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {promotions.map((p) => (
                  <TableRow key={p.id} hover>
                    <TableCell>{formatDate(p.created_at)}</TableCell>
                    <TableCell>{p.source || "—"}</TableCell>
                    <TableCell>
                      <Chip
                        label={p.status === "promoted" ? t("autoresearch.promotions.promoted") : p.status || "—"}
                        size="small"
                        color={p.status === "promoted" ? "primary" : "default"}
                        sx={{ height: 20, fontSize: "0.7rem" }}
                      />
                    </TableCell>
                    <TableCell>
                      {p.is_active && (
                        <Chip
                          label={t("autoresearch.promotions.activeChip")}
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
                          {t("autoresearch.promotions.activate")}
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
          {t("autoresearch.experiments.title")}
        </Typography>
        {metrics.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            {t("autoresearch.experiments.empty")}
          </Typography>
        ) : (
          <TableContainer sx={{ maxHeight: 400 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>{t("autoresearch.experiments.created")}</TableCell>
                  <TableCell>{t("autoresearch.experiments.parameter")}</TableCell>
                  <TableCell>{t("autoresearch.experiments.newValue")}</TableCell>
                  <TableCell>{t("autoresearch.experiments.status")}</TableCell>
                  <TableCell align="right">{t("autoresearch.experiments.delta")}</TableCell>
                  <TableCell>{t("autoresearch.experiments.source")}</TableCell>
                  <TableCell>{t("autoresearch.experiments.judge")}</TableCell>
                  <TableCell align="right">{t("autoresearch.experiments.hitRate")}</TableCell>
                  <TableCell align="right">MRR</TableCell>
                  <TableCell align="right">nDCG</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {metrics.map((exp) => {
                  const label = experimentLabel(exp);
                  return (
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
                        label={label.status}
                        size="small"
                        color={label.tone}
                        variant={label.kind === "health" ? "outlined" : "filled"}
                        sx={{ height: 20, fontSize: "0.7rem" }}
                      />
                    </TableCell>
                    <TableCell align="right">
                      {label.kind !== "health" && typeof exp.delta === "number"
                        ? `${exp.delta > 0 ? "+" : ""}${exp.delta.toFixed(3)}`
                        : "—"}
                    </TableCell>
                    <TableCell>
                      {exp.proposal_source && (
                        <Tooltip
                          title={
                            label.selfReported
                              ? t("autoresearch.experiments.selfReported")
                              : ""
                          }
                        >
                          <Chip
                            label={label.source}
                            size="small"
                            color={
                              exp.proposal_source === "llm"
                                ? "secondary"
                                : label.selfReported
                                  ? "warning"
                                  : "default"
                            }
                            variant={
                              exp.proposal_source === "llm"
                                ? "filled"
                                : "outlined"
                            }
                            sx={{ height: 20, fontSize: "0.7rem" }}
                          />
                        </Tooltip>
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
                  );
                })}
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
        <DialogTitle>{t("autoresearch.revertDialog.title")}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {t("autoresearch.revertDialog.body")}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRevertConfirmOpen(false)}>{t("common.cancel")}</Button>
          <Button color="warning" variant="contained" onClick={handleRevert}>
            {t("autoresearch.revertDialog.confirm")}
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
