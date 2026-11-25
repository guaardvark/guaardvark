// A diagnostics or test-suite run while it is going and after it ends: the
// steps with their state, the check or test in hand, live counts, the test
// files as they finish and each failure as it arrives. Draws the run snapshot
// served by /api/meta/runs/<id>.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  LinearProgress,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlineOutlined";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import WarningIcon from "@mui/icons-material/Warning";
import CheckCircleOutlineIcon from "../common/CheckCircleOutlineIcon";
import {
  cancelDiagnosticRun,
  getDiagnosticRun,
  getLatestDiagnosticRun,
} from "../../api/settingsService";
import { formatNumber, useUiTranslation } from "../../i18n";

const MONO = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";
const POLL_MS = 1000;

/** Seconds as 0:07, 12:40 or 1:02:03. */
export const formatElapsed = (seconds) => {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
};

/** Seconds a step or file took: 0.4 s, 12 s, 3:05. */
export const formatDuration = (seconds) => {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "";
  if (value < 10) return `${value.toFixed(1)} s`;
  if (value < 60) return `${Math.round(value)} s`;
  return formatElapsed(value);
};

/** The test file and the test name of a pytest id. */
export const splitTestId = (id) => {
  const text = String(id || "");
  const at = text.indexOf("::");
  return at < 0 ? { file: text, test: "" } : { file: text.slice(0, at), test: text.slice(at + 2) };
};

/**
 * Follow the run of one kind ("diagnostics" or "tests"): pick up the newest
 * one when the page opens, poll while it is going, and call onFinished once
 * when a run this page watched reaches its end.
 */
export function useDiagnosticRun(kind, onFinished) {
  const [run, setRun] = useState(null);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;
  const watchedRef = useRef(null);

  useEffect(() => {
    let alive = true;
    getLatestDiagnosticRun(kind)
      .then((latest) => {
        if (!alive || !latest) return;
        if (latest.running) watchedRef.current = latest.id;
        setRun((current) => current || latest);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [kind]);

  const running = Boolean(run?.running);
  const runId = run?.id;
  useEffect(() => {
    if (!running || !runId) return undefined;
    let alive = true;
    const timer = setInterval(async () => {
      try {
        const next = await getDiagnosticRun(runId);
        if (!alive || !next) return;
        setRun(next);
        if (!next.running && watchedRef.current === next.id) {
          watchedRef.current = null;
          finishedRef.current?.(next);
        }
      } catch (error) {
        if (!alive) return;
        // The backend forgets runs when it restarts.
        setRun((current) =>
          current && current.id === runId
            ? { ...current, running: false, status: "error", error: error.message }
            : current,
        );
      }
    }, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [running, runId]);

  const begin = useCallback(async (startCall) => {
    const started = await startCall();
    if (started) {
      watchedRef.current = started.id;
      setRun(started);
    }
    return started;
  }, []);

  const stop = useCallback(async () => {
    if (!runId) return;
    try {
      await cancelDiagnosticRun(runId);
    } catch {
      // The next poll shows where the run stands.
    }
  }, [runId]);

  return { run, running, begin, stop };
}

const STATUS_COLOR = {
  passed: "success.main",
  failed: "error.main",
  error: "error.main",
  warning: "warning.main",
};

const StepIcon = ({ status }) => {
  const sx = { fontSize: 18, color: STATUS_COLOR[status] || "text.disabled" };
  if (status === "running") return <CircularProgress size={14} thickness={5} />;
  if (status === "passed") return <CheckCircleOutlineIcon sx={sx} />;
  if (status === "failed" || status === "error") return <ErrorOutlineIcon sx={sx} />;
  if (status === "warning") return <WarningIcon sx={sx} />;
  if (status === "skipped") return <InfoOutlinedIcon sx={sx} />;
  return (
    <Box sx={{ width: 12, height: 12, borderRadius: "50%", border: 1.5, borderColor: "text.disabled" }} />
  );
};

const StepRow = ({ step, index, count, t }) => {
  const idle = step.status === "pending";
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: 1,
        py: 0.5,
        minWidth: 0,
        opacity: idle ? 0.6 : 1,
      }}
    >
      <Box sx={{ width: 20, height: 20, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
        <StepIcon status={step.status} />
      </Box>
      <Typography variant="caption" sx={{ color: "text.secondary", fontFamily: MONO, flexShrink: 0, pt: "2px" }}>
        {index + 1}/{count}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" sx={{ fontWeight: step.status === "running" ? 600 : 400 }}>
          {t(`dashboardDiagnostics.steps.${step.key}`, { defaultValue: step.label })}
        </Typography>
        {step.detail && (
          <Typography
            variant="caption"
            component="div"
            sx={{ color: "text.secondary", wordBreak: "break-word", overflowWrap: "anywhere" }}
          >
            {step.detail}
          </Typography>
        )}
      </Box>
      {step.duration != null && (
        <Typography variant="caption" sx={{ color: "text.secondary", whiteSpace: "nowrap", pt: "2px" }}>
          {formatDuration(step.duration)}
        </Typography>
      )}
    </Box>
  );
};

const FailureRow = ({ failure }) => {
  const [open, setOpen] = useState(false);
  const { file, test } = splitTestId(failure.id);
  return (
    <Paper variant="outlined" sx={{ p: 1, borderRadius: 1, borderColor: "error.dark", minWidth: 0 }}>
      <Box
        onClick={() => setOpen((o) => !o)}
        sx={{ cursor: failure.message ? "pointer" : "default", minWidth: 0 }}
      >
        <Typography variant="caption" component="div" sx={{ fontFamily: MONO, wordBreak: "break-word", overflowWrap: "anywhere" }}>
          {test || file}
        </Typography>
        {test && (
          <Typography variant="caption" component="div" sx={{ color: "text.secondary", fontFamily: MONO, wordBreak: "break-all" }}>
            {file}
          </Typography>
        )}
      </Box>
      <Collapse in={open && Boolean(failure.message)} unmountOnExit>
        <Typography
          component="pre"
          variant="caption"
          sx={{
            m: 0,
            mt: 1,
            p: 1,
            borderRadius: 1,
            bgcolor: "action.hover",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontFamily: MONO,
            fontSize: "0.7rem",
            maxHeight: 260,
            overflow: "auto",
          }}
        >
          {failure.message}
        </Typography>
      </Collapse>
    </Paper>
  );
};

const RUN_CHIP = {
  passed: "success",
  failed: "error",
  error: "error",
  warning: "warning",
  cancelled: "default",
  running: "info",
};

const DiagnosticRunProgress = ({ run, onStop }) => {
  const { t } = useUiTranslation();
  const [outputOpen, setOutputOpen] = useState(false);
  if (!run) return null;

  const isTests = run.kind === "tests";
  const steps = Array.isArray(run.steps) ? run.steps : [];
  const counts = run.counts || {};
  const failures = Array.isArray(run.failures) ? run.failures : [];
  const recentFiles = Array.isArray(run.recent_files) ? [...run.recent_files].reverse() : [];
  const finishedSteps = steps.filter((s) => !["pending", "running"].includes(s.status)).length;
  const current = splitTestId(run.current);
  const output = run.result || {};
  const hasOutput = isTests && (output.stdout || output.stderr);

  const headline = isTests && run.total
    ? t("dashboardDiagnostics.progress.testsOf", { done: formatNumber(run.done), total: formatNumber(run.total) })
    : t("dashboardDiagnostics.progress.stepsOf", { done: finishedSteps, total: steps.length });

  return (
    <Box
      sx={{
        width: "100%",
        minWidth: 0,
        p: 1.5,
        borderRadius: 1,
        border: 1,
        borderColor: "divider",
        bgcolor: (theme) => alpha(theme.palette.action.hover, theme.palette.mode === "dark" ? 0.25 : 0.5),
      }}
    >
      <Box sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1 }}>
        <Chip
          size="small"
          variant="outlined"
          color={RUN_CHIP[run.status] || "default"}
          label={t(`dashboardDiagnostics.runStatus.${run.status}`, { defaultValue: run.status })}
        />
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          {headline}
        </Typography>
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          {t("dashboardDiagnostics.progress.elapsed", { time: formatElapsed(run.elapsed) })}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {run.running && onStop && (
          <Button size="small" color="error" variant="outlined" onClick={onStop}>
            {t("dashboardDiagnostics.progress.stop")}
          </Button>
        )}
      </Box>

      <LinearProgress
        variant={run.running && !run.percent ? "indeterminate" : "determinate"}
        value={run.running ? run.percent || 0 : 100}
        color={RUN_CHIP[run.status] === "default" ? "inherit" : RUN_CHIP[run.status] || "primary"}
        sx={{ mt: 1.25, height: 6, borderRadius: 3 }}
      />

      {run.running && run.current && (
        <Box sx={{ mt: 1, minWidth: 0 }}>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            {t("dashboardDiagnostics.progress.now")}
          </Typography>
          <Typography variant="caption" component="div" sx={{ fontFamily: MONO, wordBreak: "break-all" }}>
            {current.file}
          </Typography>
          {current.test && (
            <Typography variant="caption" component="div" sx={{ fontFamily: MONO, color: "text.secondary", wordBreak: "break-all" }}>
              {current.test}
            </Typography>
          )}
        </Box>
      )}

      {run.error && !run.running && (
        <Typography variant="body2" color="error" sx={{ mt: 1, wordBreak: "break-word" }}>
          {run.error}
        </Typography>
      )}

      {isTests && (run.total > 0 || run.done > 0) && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, mt: 1.25 }}>
          <Chip size="small" variant="outlined" color="success" label={t("dashboardDiagnostics.passedCount", { count: counts.passed || 0 })} />
          <Chip size="small" variant="outlined" color={counts.failed ? "error" : "default"} label={t("dashboardDiagnostics.failedCount", { count: counts.failed || 0 })} />
          <Chip size="small" variant="outlined" color={counts.errors ? "warning" : "default"} label={t("dashboardDiagnostics.errorCount", { count: counts.errors || 0 })} />
          <Chip size="small" variant="outlined" label={t("dashboardDiagnostics.skippedCount", { count: counts.skipped || 0 })} />
          <Chip
            size="small"
            variant="outlined"
            label={t("dashboardDiagnostics.progress.filesOf", {
              done: formatNumber(run.files_done || 0),
              total: formatNumber(run.files_total || 0),
            })}
          />
        </Box>
      )}

      <Box sx={{ mt: 1.25 }}>
        {steps.map((step, index) => (
          <React.Fragment key={step.key}>
            {step.group && step.group !== steps[index - 1]?.group && (
              <Typography
                variant="caption"
                component="div"
                sx={{ color: "text.secondary", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5, mt: index ? 1 : 0 }}
              >
                {t(`dashboardDiagnostics.groups.${step.group}`, { defaultValue: step.group })}
              </Typography>
            )}
            <StepRow step={step} index={index} count={steps.length} t={t} />
          </React.Fragment>
        ))}
      </Box>

      {isTests && recentFiles.length > 0 && (
        <Box sx={{ mt: 1.25 }}>
          <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>
            {t("dashboardDiagnostics.progress.recentFiles")}
          </Typography>
          {recentFiles.map((file) => (
            <Box key={file.path} sx={{ display: "flex", alignItems: "center", gap: 1, py: 0.25, minWidth: 0 }}>
              <StepIcon status={file.failed ? "failed" : "passed"} />
              <Typography variant="caption" sx={{ fontFamily: MONO, flex: 1, minWidth: 0, wordBreak: "break-all" }}>
                {file.path}
              </Typography>
              <Typography variant="caption" sx={{ color: file.failed ? "error.main" : "text.secondary", whiteSpace: "nowrap" }}>
                {file.failed
                  ? t("dashboardDiagnostics.progress.fileFailed", { failed: file.failed, total: file.total })
                  : t("dashboardDiagnostics.progress.filePassed", { total: file.total })}
                {" · "}
                {formatDuration(file.duration)}
              </Typography>
            </Box>
          ))}
        </Box>
      )}

      {failures.length > 0 && (
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>
            {t("dashboardDiagnostics.progress.failures", { count: failures.length })}
          </Typography>
          <Stack spacing={0.75} sx={{ mt: 0.75, maxHeight: 420, overflow: "auto" }}>
            {failures.map((failure, i) => (
              <FailureRow key={`${failure.id}-${i}`} failure={failure} />
            ))}
          </Stack>
          {run.failures_truncated && (
            <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 0.5 }}>
              {t("dashboardDiagnostics.progress.failuresTruncated", { shown: failures.length })}
            </Typography>
          )}
        </Box>
      )}

      {!run.running && output.log_path && (
        <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 1, wordBreak: "break-all" }}>
          {t("dashboardDiagnostics.log", { path: output.log_path })}
        </Typography>
      )}

      {!run.running && hasOutput && (
        <Box sx={{ mt: 1 }}>
          <Button size="small" variant="text" onClick={() => setOutputOpen((o) => !o)} sx={{ textTransform: "none", p: 0, minWidth: 0 }}>
            {outputOpen ? t("dashboardDiagnostics.hideRawOutput") : t("dashboardDiagnostics.showRawOutput")}
          </Button>
          <Collapse in={outputOpen} unmountOnExit>
            {["stderr", "stdout"].map((name) =>
              output[name] ? (
                <Box key={name} sx={{ mt: 1 }}>
                  <Typography variant="caption" sx={{ color: name === "stderr" ? "error.main" : "text.secondary", fontWeight: 600 }}>
                    {name}
                  </Typography>
                  <Typography
                    component="pre"
                    variant="caption"
                    sx={{
                      display: "block",
                      mt: 0.5,
                      p: 1,
                      borderRadius: 1,
                      bgcolor: "action.hover",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      overflowWrap: "anywhere",
                      fontFamily: MONO,
                      fontSize: "0.7rem",
                      maxHeight: 280,
                      overflow: "auto",
                    }}
                  >
                    {output[name]}
                  </Typography>
                </Box>
              ) : null,
            )}
          </Collapse>
        </Box>
      )}
    </Box>
  );
};

export default DiagnosticRunProgress;
