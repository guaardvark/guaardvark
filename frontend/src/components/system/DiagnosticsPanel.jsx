// frontend/src/components/system/DiagnosticsPanel.jsx
// Self-contained system diagnostics + test-suite panel for the System Dashboard.
// Owns its own state and API calls; `showMessage` is optional feedback plumbing.

import React, { useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Chip,
  Collapse,
  Divider,
  Paper,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import MuiAlert from "@mui/material/Alert";
import { alpha } from "@mui/material/styles";

import ApiIcon from "@mui/icons-material/Api";
import ChatIcon from "@mui/icons-material/Chat";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import SystemIcon from "@mui/icons-material/Computer";
import DnsIcon from "@mui/icons-material/Dns";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import FolderIcon from "@mui/icons-material/Folder";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import SecurityIcon from "@mui/icons-material/Security";
import SpeedIcon from "@mui/icons-material/Speed";
import StorageIcon from "@mui/icons-material/Storage";
import SyncProblemIcon from "@mui/icons-material/SyncProblem";
import TrendingUpIcon from "@mui/icons-material/TrendingUp";
import WarningIcon from "@mui/icons-material/Warning";

import { runAllTests, runSelfTest } from "../../api/settingsService";
import { translate, useUiTranslation } from "../../i18n";

// Diagnostics depth is one choice; "Full" is the API's "comprehensive" mode.
const depthOptions = () => [
  { value: "basic", label: translate("dashboardDiagnostics.depths.basic"), running: translate("dashboardDiagnostics.running.basic") },
  { value: "quick", label: translate("dashboardDiagnostics.depths.quick"), running: translate("dashboardDiagnostics.running.quick") },
  { value: "comprehensive", label: translate("dashboardDiagnostics.depths.comprehensive"), running: translate("dashboardDiagnostics.running.comprehensive") },
];

const depthLabel = (value) =>
  depthOptions().find((o) => o.value === value)?.label || value;

// Helper function to get category icon
const getCategoryIcon = (categoryKey) => {
  const iconMap = {
    core_system: <SystemIcon />,
    api_health: <ApiIcon />,
    file_processing: <FolderIcon />,
    chat_system: <ChatIcon />,
    security: <SecurityIcon />,
    performance: <TrendingUpIcon />
  };
  return iconMap[categoryKey] || <HelpOutlineIcon />;
};

// Helper function to get status color and icon
const getStatusDisplay = (status) => {
  const statusMap = {
    pass: { color: "success", icon: <CheckCircleOutlineIcon />, label: translate("dashboardDiagnostics.status.pass") },
    fail: { color: "error", icon: <ErrorOutlineIcon />, label: translate("dashboardDiagnostics.status.fail") },
    warning: { color: "warning", icon: <WarningIcon />, label: translate("dashboardDiagnostics.status.warning") },
    error: { color: "error", icon: <SyncProblemIcon />, label: translate("dashboardDiagnostics.status.error") },
    critical: { color: "error", icon: <ErrorOutlineIcon />, label: translate("dashboardDiagnostics.status.critical") },
    partial: { color: "warning", icon: <WarningIcon />, label: translate("dashboardDiagnostics.status.partial") },
    skip: { color: "default", icon: <InfoOutlinedIcon />, label: translate("dashboardDiagnostics.status.skip") },
  };
  const key = typeof status === "string" ? status.toLowerCase() : "";
  return statusMap[key] || { color: "default", icon: <InfoOutlinedIcon />, label: status || translate("dashboardDiagnostics.status.unknown") };
};

const renderStatusIcon = (value, detailsForKey = "") => {
  const details = String(detailsForKey).toLowerCase();

  if (typeof value === "boolean") {
    return value ? (
      <CheckCircleOutlineIcon
        sx={{ color: "success.main", verticalAlign: "middle" }}
      />
    ) : (
      <ErrorOutlineIcon
        sx={{ color: "error.main", verticalAlign: "middle" }}
      />
    );
  }
  if (typeof value === "string") {
    const lowerValue = value.toLowerCase();
    if (
      [
        "ok",
        "good",
        "healthy",
        "accessible",
        "loadable",
        "active",
        "true",
        "responsive",
        "idle / queue empty",
        "no recent indexing errors found in db",
        "no error/critical messages in last ~200 lines",
      ].some((s) => lowerValue.includes(s))
    ) {
      return (
        <CheckCircleOutlineIcon
          sx={{ color: "success.main", verticalAlign: "middle" }}
        />
      );
    }
    if (
      [
        "error",
        "failed",
        "unhealthy",
        "inaccessible",
        "critical",
        "false",
        "db error",
      ].some((s) => lowerValue.includes(s)) ||
      lowerValue.startsWith("error:") ||
      lowerValue.includes("error(s). examples:")
    ) {
      return (
        <ErrorOutlineIcon
          sx={{ color: "error.main", verticalAlign: "middle" }}
        />
      );
    }
    if (
      [
        "warning",
        "degraded",
        "not configured",
        "configured but not responsive/empty response",
        "items pending/indexing",
      ].some((s) => lowerValue.includes(s))
    ) {
      return (
        <SyncProblemIcon
          sx={{ color: "warning.main", verticalAlign: "middle" }}
        />
      );
    }
    if (lowerValue.includes("unknown") || lowerValue.includes("n/a")) {
      return (
        <HelpOutlineIcon
          sx={{ color: "text.secondary", verticalAlign: "middle" }}
        />
      );
    }
  }
  if (details) {
    if (
      ["ok", "found", "connected", "accessible", "responsive", "idle"].some(
        (s) => details.includes(s),
      )
    ) {
      return (
        <CheckCircleOutlineIcon
          sx={{ color: "success.main", verticalAlign: "middle" }}
        />
      );
    }
    if (
      [
        "failed",
        "error",
        "inaccessible",
        "not found/empty",
        "not found or not active for the current model",
      ].some((s) => details.includes(s))
    ) {
      return (
        <ErrorOutlineIcon
          sx={{ color: "error.main", verticalAlign: "middle" }}
        />
      );
    }
    if (
      details.includes("pending/indexing") ||
      details.includes("not configured")
    ) {
      return (
        <SyncProblemIcon
          sx={{ color: "warning.main", verticalAlign: "middle" }}
        />
      );
    }
  }
  return (
    <InfoOutlinedIcon
      sx={{ color: "text.secondary", verticalAlign: "middle" }}
    />
  );
};

// `t` is translate() for what is shown; the status icon reads the English
// wording (renderStatusIcon matches English keywords), so it passes englishT.
const systemCheckItems = (t = translate) => [
  {
    key: "ollama_reachable",
    label: t("dashboardDiagnostics.checks.ollama_reachable"),
    icon: <DnsIcon />,
    format: (v) => (v ? t("dashboardDiagnostics.values.ok") : t("dashboardDiagnostics.values.failed")),
  },
  {
    key: "active_model_name",
    label: t("dashboardDiagnostics.checks.active_model_name"),
    icon: <DnsIcon />,
    format: (v) => v || t("layout.notAvailable"),
  },
  {
    key: "active_model_status",
    label: t("dashboardDiagnostics.checks.active_model_status"),
    icon: <DnsIcon />,
    format: (v) => v || t("dashboardDiagnostics.values.unknown"),
  },
  {
    key: "active_model_health",
    label: t("dashboardDiagnostics.checks.active_model_health"),
    icon: <DnsIcon />,
    format: (v) => v || t("dashboardDiagnostics.values.unknown"),
  },
  {
    key: "llm_basic_response",
    label: t("dashboardDiagnostics.checks.llm_basic_response"),
    icon: <DnsIcon />,
    format: (v) => (v ? t("dashboardDiagnostics.values.ok") : t("dashboardDiagnostics.values.failedEmpty")),
  },
  {
    key: "model_count",
    label: t("dashboardDiagnostics.checks.model_count"),
    format: (v) => t("dashboardDiagnostics.values.modelsFound", { count: v ?? t("layout.notAvailable") }),
  },
  {
    key: "db_connection",
    label: t("dashboardDiagnostics.checks.db_connection"),
    icon: <StorageIcon />,
    format: (v) => (v ? t("dashboardDiagnostics.values.ok") : t("dashboardDiagnostics.values.failed")),
  },
  {
    key: "document_count_db",
    label: t("dashboardDiagnostics.checks.document_count_db"),
    format: (v) => t("dashboardDiagnostics.values.documentsInDb", { count: v ?? t("layout.notAvailable") }),
  },
  {
    key: "storage_dir_accessible",
    label: t("dashboardDiagnostics.checks.storage_dir_accessible"),
    icon: <StorageIcon />,
    format: (v, r) => {
      const path = r.storage_dir_path || t("layout.notAvailable");
      return v ? t("dashboardDiagnostics.values.pathAccessible", { path }) : t("dashboardDiagnostics.values.pathInaccessible", { path });
    },
  },
  {
    key: "upload_dir_accessible",
    label: t("dashboardDiagnostics.checks.upload_dir_accessible"),
    icon: <StorageIcon />,
    format: (v, r) => {
      const path = r.upload_dir_path || t("layout.notAvailable");
      return v ? t("dashboardDiagnostics.values.pathAccessible", { path }) : t("dashboardDiagnostics.values.pathInaccessible", { path });
    },
  },
  {
    key: "output_dir_accessible",
    label: t("dashboardDiagnostics.checks.output_dir_accessible"),
    icon: <StorageIcon />,
    format: (v, r) => {
      const path = r.output_dir_path || t("layout.notAvailable");
      return v ? t("dashboardDiagnostics.values.pathAccessible", { path }) : t("dashboardDiagnostics.values.pathInaccessible", { path });
    },
  },
  {
    key: "index_storage_exists",
    label: t("dashboardDiagnostics.checks.index_storage_exists"),
    icon: <SpeedIcon />,
    format: (v) => (v ? t("dashboardDiagnostics.values.ok") : t("dashboardDiagnostics.values.notFoundEmpty")),
  },
  {
    key: "qa_prompt_loadable",
    label: t("dashboardDiagnostics.checks.qa_prompt_loadable"),
    format: (v) => (v ? t("dashboardDiagnostics.values.loadable") : t("dashboardDiagnostics.values.notFoundError")),
  },
  {
    key: "indexing_queue_status",
    label: t("dashboardDiagnostics.checks.indexing_queue_status"),
    icon: <SpeedIcon />,
    format: (v) => v || t("layout.notAvailable"),
  },
  {
    key: "recent_indexing_errors",
    label: t("dashboardDiagnostics.checks.recent_indexing_errors"),
    icon: <ErrorOutlineIcon />,
    format: (v) => v || t("layout.notAvailable"),
  },
  {
    key: "backend_log_errors",
    label: t("dashboardDiagnostics.checks.backend_log_errors"),
    icon: <ErrorOutlineIcon />,
    format: (v) => v || t("layout.notAvailable"),
  },
  {
    key: "gpu_tools_available",
    label: t("dashboardDiagnostics.checks.gpu_tools_available"),
    icon: <SpeedIcon />,
    format: (v) => (v ? t("dashboardDiagnostics.values.available") : t("dashboardDiagnostics.values.unavailable")),
  },
  {
    key: "last_metrics_fetch_status",
    label: t("dashboardDiagnostics.checks.last_metrics_fetch_status"),
    icon: <SpeedIcon />,
    format: (v) => v || t("layout.notAvailable"),
  },
];

/** translate() pinned to English, for the keyword checks in renderStatusIcon. */
const englishT = (key, options) => translate(key, { ...options, lng: "en" });

const categoryStatusAccent = (statusColor) => {
  if (statusColor === "success") return "success.main";
  if (statusColor === "error") return "error.main";
  if (statusColor === "warning") return "warning.main";
  return "divider";
};

const categorySummaryBg = (theme, statusColor) => {
  if (statusColor === "success") return alpha(theme.palette.success.main, 0.08);
  if (statusColor === "error") return alpha(theme.palette.error.main, 0.1);
  if (statusColor === "warning") return alpha(theme.palette.warning.main, 0.1);
  return theme.palette.action.hover;
};

const renderLegacyDiagnosticsCards = (ds) => (
  <Stack spacing={1} sx={{ mt: 1.5 }}>
    {systemCheckItems().map((item, index) => {
      const val = ds[item.key];
      const details = val !== undefined ? item.format(val, ds) : translate("layout.notAvailable");
      // Only rows without an icon of their own read the wording, in English.
      const englishDetails =
        !item.icon && val !== undefined ? systemCheckItems(englishT)[index].format(val, ds) : "N/A";
      return (
        <Paper
          key={item.key}
          variant="outlined"
          sx={{
            p: 1.25,
            borderRadius: 1,
            bgcolor: "background.paper",
            borderColor: "divider",
          }}
        >
          <Box sx={{ display: "flex", gap: 1.25, alignItems: "flex-start", minWidth: 0 }}>
            <Box sx={{ color: "text.secondary", display: "flex", flexShrink: 0, pt: 0.25 }}>
              {item.icon ? React.cloneElement(item.icon, { fontSize: "small" }) : renderStatusIcon(val, englishDetails)}
            </Box>
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography variant="body2" fontWeight={600} sx={{ wordBreak: "break-word" }}>
                {item.label}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, wordBreak: "break-word", overflowWrap: "anywhere" }}>
                {details}
              </Typography>
            </Box>
          </Box>
        </Paper>
      );
    })}
  </Stack>
);

const DiagnosticsPanel = ({ showMessage }) => {
  const { t } = useUiTranslation();
  const [depth, setDepth] = useState("basic");
  const [isTesting, setIsTesting] = useState(false);
  const [testResults, setTestResults] = useState(null);
  const [testMode, setTestMode] = useState(null); // depth that produced testResults
  const [expandedCategories, setExpandedCategories] = useState({});
  const [isRunningTests, setIsRunningTests] = useState(false);
  const [testSuiteResults, setTestSuiteResults] = useState(null);
  const [testSuiteOutputOpen, setTestSuiteOutputOpen] = useState(false);

  const notify = (message, severity) => {
    if (typeof showMessage === "function") showMessage(message, severity);
  };

  // Enhanced test runner with mode selection
  const handleRunSystemCheck = async (mode = "basic") => {
    // Validate mode parameter
    const validModes = ["basic", "quick", "comprehensive"];
    const validatedMode = validModes.includes(mode) ? mode : "basic";

    setIsTesting(true);
    setTestResults(null);
    setTestMode(validatedMode);
    setExpandedCategories({}); // Reset expanded categories

    const running = depthOptions().find((o) => o.value === validatedMode)?.running;
    notify(running || t("dashboardDiagnostics.running.default"), "info");

    try {
      // Call enhanced API with mode parameter
      const response = await runSelfTest({
        mode: validatedMode,
        include_legacy: true
      });

      if (response?.error && typeof response.error === "string")
        throw new Error(response.error);
      if (response?.results && typeof response.results === "object") {
        setTestResults(response.results);

        // Enhanced success message with status
        const overallStatus = response.results.overall_status || "UNKNOWN";
        const statusDisplay = getStatusDisplay(overallStatus);
        let msg = t("dashboardDiagnostics.checkComplete", { status: statusDisplay.label });

        if (response.results.categories) {
          const categoryCount = Object.keys(response.results.categories).length;
          msg = t("dashboardDiagnostics.checkCompleteCategories", { status: statusDisplay.label, count: categoryCount });
        }

        const severity = overallStatus === "PASS" ? "success" :
          overallStatus === "WARNING" ? "warning" : "error";
        notify(msg, severity);
      } else {
        throw new Error(t("dashboardDiagnostics.invalidResults"));
      }
    } catch (error) {
      notify(
        t("dashboardDiagnostics.checkError", { message: error.message || t("dashboardDiagnostics.couldNotRun") }),
        "error",
      );
      setTestResults({
        error: t("dashboardDiagnostics.runFailed", { message: error.message }),
      });
    } finally {
      setIsTesting(false);
    }
  };

  const handleRunAllTests = async () => {
    setIsRunningTests(true);
    setTestSuiteResults(null);
    setTestSuiteOutputOpen(false);
    notify(t("dashboardDiagnostics.runningSuite"), "info");
    try {
      const response = await runAllTests();
      if (response?.results) {
        setTestSuiteResults(response.results);
        const rc = response.results.returncode;
        const sev = rc === 0 || rc === 4 || rc === 5 ? "success" : "error";
        notify(t("dashboardDiagnostics.suiteFinished"), sev);
      } else {
        throw new Error(t("dashboardDiagnostics.invalidResponse"));
      }
    } catch (err) {
      notify(t("dashboardDiagnostics.suiteError", { message: err.message }), "error");
      setTestSuiteResults({ error: err.message });
    } finally {
      setIsRunningTests(false);
    }
  };

  // Categorized self-test results: card list + muted accordions (no cramped tables)
  const renderCategorizedResults = (results) => {
    if (!results.categories) return null;

    return (
      <Box mt={2} sx={{ width: "100%", minWidth: 0 }}>
        {results.overall_status && (
          <Box
            mb={2}
            sx={{
              display: "flex",
              flexWrap: "wrap",
              gap: 1,
              alignItems: "center",
            }}
          >
            <Chip
              icon={getStatusDisplay(results.overall_status).icon}
              label={t("dashboardDiagnostics.overall", { status: getStatusDisplay(results.overall_status).label })}
              color={getStatusDisplay(results.overall_status).color}
              variant="outlined"
              size="small"
            />
            {results.execution_time != null && (
              <Chip
                icon={<SpeedIcon sx={{ fontSize: "1rem !important" }} />}
                label={`${Number(results.execution_time).toFixed(2)}s`}
                variant="outlined"
                size="small"
              />
            )}
          </Box>
        )}

        {Object.entries(results.categories).map(([categoryKey, categoryData]) => {
          const isExpanded = expandedCategories[categoryKey] || false;
          const statusDisplay = getStatusDisplay(categoryData.status);

          return (
            <Accordion
              key={categoryKey}
              expanded={isExpanded}
              disableGutters
              elevation={0}
              onChange={() =>
                setExpandedCategories((prev) => ({
                  ...prev,
                  [categoryKey]: !prev[categoryKey],
                }))
              }
              sx={{
                mb: 1,
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
                overflow: "hidden",
                "&:before": { display: "none" },
              }}
            >
              <AccordionSummary
                expandIcon={<ExpandMoreIcon />}
                sx={(theme) => ({
                  minHeight: 48,
                  px: 1.5,
                  borderLeft: "3px solid",
                  borderLeftColor: categoryStatusAccent(statusDisplay.color),
                  bgcolor: categorySummaryBg(theme, statusDisplay.color),
                  "&.Mui-expanded": { minHeight: 48 },
                })}
              >
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    width: "100%",
                    minWidth: 0,
                    flexWrap: "wrap",
                  }}
                >
                  <Box sx={{ color: "text.secondary", display: "flex" }}>{getCategoryIcon(categoryKey)}</Box>
                  <Typography variant="subtitle2" sx={{ flex: "1 1 140px", minWidth: 0, fontWeight: 600 }}>
                    {categoryData.name || categoryKey}
                  </Typography>
                  <Chip
                    icon={statusDisplay.icon}
                    label={statusDisplay.label}
                    color={statusDisplay.color}
                    size="small"
                    variant="outlined"
                  />
                  {categoryData.duration != null && (
                    <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>
                      {categoryData.duration.toFixed(2)}s
                    </Typography>
                  )}
                </Box>
              </AccordionSummary>
              <AccordionDetails sx={{ pt: 0, px: 1.5, pb: 1.5, bgcolor: "background.default" }}>
                {categoryData.summary && (
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, wordBreak: "break-word" }}>
                    {categoryData.summary}
                  </Typography>
                )}

                {categoryData.tests && categoryData.tests.length > 0 && (
                  <Stack spacing={1} sx={{ mt: 0.5 }}>
                    {categoryData.tests.map((test, index) => {
                      const testStatus = getStatusDisplay(test.status);
                      const shortName = (test.name || "").replace(/^.*\//, "");
                      return (
                        <Paper
                          key={index}
                          variant="outlined"
                          sx={{
                            p: 1.25,
                            borderRadius: 1,
                            bgcolor: "background.paper",
                            borderColor: "divider",
                          }}
                        >
                          <Box
                            sx={{
                              display: "flex",
                              flexWrap: "wrap",
                              alignItems: "flex-start",
                              gap: 1,
                              minWidth: 0,
                            }}
                          >
                            <Chip
                              icon={testStatus.icon}
                              label={testStatus.label}
                              color={testStatus.color}
                              size="small"
                              variant="outlined"
                              sx={{ flexShrink: 0 }}
                            />
                            <Box sx={{ flex: "1 1 200px", minWidth: 0 }}>
                              <Typography
                                variant="body2"
                                component="div"
                                sx={{
                                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                                  fontSize: "0.8rem",
                                  wordBreak: "break-word",
                                  overflowWrap: "anywhere",
                                }}
                              >
                                {shortName || test.name || t("dashboardDiagnostics.unnamedTest")}
                              </Typography>
                              {test.duration != null && (
                                <Typography variant="caption" color="text.secondary">
                                  {test.duration.toFixed(2)}s
                                </Typography>
                              )}
                            </Box>
                          </Box>
                          {(test.details || test.error_message) && (
                            <Box sx={{ mt: 1, pt: 1, borderTop: 1, borderColor: "divider" }}>
                              {test.details && (
                                <Typography variant="body2" color="text.secondary" sx={{ wordBreak: "break-word" }}>
                                  {test.details}
                                </Typography>
                              )}
                              {test.error_message && (
                                <Typography variant="caption" color="error" component="div" sx={{ mt: 0.5, wordBreak: "break-word" }}>
                                  {test.error_message}
                                </Typography>
                              )}
                            </Box>
                          )}
                        </Paper>
                      );
                    })}
                  </Stack>
                )}
              </AccordionDetails>
            </Accordion>
          );
        })}
      </Box>
    );
  };

  const renderTestSuitePanel = (suite) => {
    if (!suite || typeof suite !== "object") return null;
    const rc = suite.returncode;
    const passed = rc === 0 || rc === 4 || rc === 5;
    const summary = suite.summary || {};
    const counts = summary.counts || {};
    const failures = Array.isArray(summary.failures) ? summary.failures : [];
    const stdout = typeof suite.stdout === "string" ? suite.stdout : "";
    const stderr = typeof suite.stderr === "string" ? suite.stderr : "";

    return (
      <Box
        mt={2}
        sx={{
          width: "100%",
          minWidth: 0,
          p: 1.5,
          borderRadius: 1,
          border: 1,
          borderColor: "divider",
          bgcolor: (theme) => alpha(theme.palette.action.hover, theme.palette.mode === "dark" ? 0.35 : 0.6),
        }}
      >
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, alignItems: "center" }}>
          <Chip label={passed ? t("dashboardDiagnostics.suitePassed") : t("dashboardDiagnostics.suiteFailed")} color={passed ? "success" : "error"} size="small" variant="outlined" />
          <Chip label={t("dashboardDiagnostics.exitCode", { code: rc ?? "?" })} size="small" variant="outlined" />
          {typeof counts.passed === "number" && (
            <Chip label={t("dashboardDiagnostics.passedCount", { count: counts.passed })} size="small" variant="outlined" />
          )}
          {typeof counts.failed === "number" && counts.failed > 0 && (
            <Chip label={t("dashboardDiagnostics.failedCount", { count: counts.failed })} size="small" color="error" variant="outlined" />
          )}
          {typeof counts.errors === "number" && counts.errors > 0 && (
            <Chip label={t("dashboardDiagnostics.errorCount", { count: counts.errors })} size="small" color="warning" variant="outlined" />
          )}
          {typeof counts.skipped === "number" && counts.skipped > 0 && (
            <Chip label={t("dashboardDiagnostics.skippedCount", { count: counts.skipped })} size="small" variant="outlined" />
          )}
        </Box>

        {suite.log_path && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1, wordBreak: "break-all" }}>
            {t("dashboardDiagnostics.log", { path: suite.log_path })}
          </Typography>
        )}

        {failures.length > 0 && (
          <Box sx={{ mt: 1.5 }}>
            <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>
              {t("dashboardDiagnostics.failureDetails")}
            </Typography>
            <Stack spacing={1} sx={{ mt: 1 }}>
              {failures.map((block, i) => (
                <Paper
                  key={i}
                  variant="outlined"
                  sx={{
                    p: 1,
                    borderRadius: 1,
                    bgcolor: "background.paper",
                    borderColor: "error.dark",
                    maxHeight: 220,
                    overflow: "auto",
                  }}
                >
                  <Typography
                    component="pre"
                    variant="caption"
                    sx={{
                      m: 0,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                      fontSize: "0.7rem",
                    }}
                  >
                    {block}
                  </Typography>
                </Paper>
              ))}
            </Stack>
          </Box>
        )}

        {(stdout || stderr) && (
          <>
            <Divider sx={{ my: 1.5 }} />
            <Button size="small" variant="text" onClick={() => setTestSuiteOutputOpen((o) => !o)} sx={{ textTransform: "none", p: 0, minWidth: 0 }}>
              {testSuiteOutputOpen ? t("dashboardDiagnostics.hideRawOutput") : t("dashboardDiagnostics.showRawOutput")}
            </Button>
            <Collapse in={testSuiteOutputOpen}>
              {stderr ? (
                <Box sx={{ mt: 1 }}>
                  <Typography variant="caption" color="error" sx={{ fontWeight: 600 }}>
                    stderr
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
                      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                      fontSize: "0.7rem",
                      maxHeight: 240,
                      overflow: "auto",
                    }}
                  >
                    {stderr}
                  </Typography>
                </Box>
              ) : null}
              {stdout ? (
                <Box sx={{ mt: stderr ? 1.5 : 1 }}>
                  <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                    stdout
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
                      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                      fontSize: "0.7rem",
                      maxHeight: 280,
                      overflow: "auto",
                    }}
                  >
                    {stdout}
                  </Typography>
                </Box>
              ) : null}
            </Collapse>
          </>
        )}
      </Box>
    );
  };

  return (
    <Stack spacing={3} sx={{ width: "100%", minWidth: 0 }}>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1, width: "100%", minWidth: 0 }}>
        <Typography variant="subtitle2" color="text.secondary">
          {t("dashboardDiagnostics.depth")}
        </Typography>
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", alignItems: "center" }}>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={depth}
            onChange={(_e, value) => {
              if (value) setDepth(value);
            }}
            disabled={isTesting}
            aria-label={t("dashboardDiagnostics.depthAria")}
          >
            {depthOptions().map((option) => (
              <ToggleButton key={option.value} value={option.value}>
                {option.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <Button
            variant="contained"
            size="small"
            onClick={() => handleRunSystemCheck(depth)}
            disabled={isTesting}
          >
            {isTesting ? t("dashboardDiagnostics.runningButton") : t("dashboardDiagnostics.runDiagnostics")}
          </Button>
          {testMode && !isTesting && (
            <Chip label={t("dashboardDiagnostics.depthChip", { depth: depthLabel(testMode) })} size="small" variant="outlined" />
          )}
        </Box>
        {testResults && (
          <Box
            sx={{
              mt: 1,
              p: 1.5,
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
              width: "100%",
              minWidth: 0,
              bgcolor: (theme) => alpha(theme.palette.action.hover, theme.palette.mode === "dark" ? 0.25 : 0.5),
            }}
          >
            {testResults.error && typeof testResults.error === "string" ? (
              <MuiAlert severity="error">{testResults.error}</MuiAlert>
            ) : (
              <>
                {testResults.categories && renderCategorizedResults(testResults)}
                {(testResults.legacy_diagnostics || (!testResults.categories && testResults)) && (
                  renderLegacyDiagnosticsCards(testResults.legacy_diagnostics || testResults)
                )}
              </>
            )}
          </Box>
        )}
      </Box>

      <Divider />

      <Box sx={{ display: "flex", flexDirection: "column", gap: 1, width: "100%", minWidth: 0 }}>
        <Typography variant="subtitle2" color="text.secondary">
          {t("dashboardDiagnostics.testSuite")}
        </Typography>
        <Button variant="outlined" size="small" onClick={handleRunAllTests} disabled={isRunningTests} sx={{ alignSelf: "flex-start" }}>
          {isRunningTests ? t("dashboardDiagnostics.runningButton") : t("dashboardDiagnostics.runTests")}
        </Button>
        {testSuiteResults && (
          <Box sx={{ width: "100%", minWidth: 0 }}>
            {testSuiteResults.error ? (
              <MuiAlert severity="error">{testSuiteResults.error}</MuiAlert>
            ) : (
              renderTestSuitePanel(testSuiteResults)
            )}
          </Box>
        )}
      </Box>
    </Stack>
  );
};

export default DiagnosticsPanel;
