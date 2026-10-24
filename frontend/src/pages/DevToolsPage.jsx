// v1.19.6 - System Dashboard (formerly Dev Tools)
// v1.19.6 - Added progress jobs management and cleanup functionality
import React, { useEffect, useState, useCallback, useRef } from "react";
import { SOCKET_URL } from "../api/apiClient";
import {
  Box,
  Typography,
  Grid,
  Button,
  CircularProgress,
  Card,
  CardContent,
  IconButton,
  Tooltip,
  Chip,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Stack,
  LinearProgress,
} from "@mui/material";
import { 
  Refresh, 
  Warning, 
  CheckCircle, 
  Error as ErrorIcon, 
  ExpandMore, 
  CleaningServices,
} from "@mui/icons-material";
import {
  getBackendHealth,
  getDbHealth,
  getCeleryHealth,
  getRedisHealth,
  getCeleryTasks,
  getTasks,
  runSelfTest,
} from "../api";
import { getProgressJobs, cleanupStuckJobs } from "../api/progressService";
import { triggerReboot } from "../api/settingsService";
import AlertSnackbar from "../components/common/AlertSnackbar";
import DiagnosticsPanel from "../components/system/DiagnosticsPanel";
import RAGDebugSection from "../components/settings/RAGDebugSection";
import SystemMetricsBar from "../components/layout/SystemMetricsBar";
import { activateResourceManager } from "../utils/resource_manager";
import { io } from "socket.io-client";
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import SelfImprovementRuns from "../components/selfImprovement/SelfImprovementRuns";
import { claudeAdvisorService } from "../api/claudeAdvisorService";
import { StatusChip, UNCLE_GOLD, FAMILY_BLUE } from "../utils/familyColors";
import {
  AutoFixHigh as FixIcon,
  Psychology as PsychologyIcon,
  Hub as HubIcon,
} from "@mui/icons-material";
import { useUiTranslation, uiLocale } from "../i18n";

// A failed check that brought no message of its own; worded where it renders.
const UNKNOWN_ERROR = "unknown_error";
const SERVICE_DOWN = "service_down";

// SECURITY DISABLED: Token authentication removed for single-user system
// const DEV_SECRET_KEY = import.meta.env.VITE_DEV_TOKEN || "dev-secret-key-change-in-production";

const DevToolsPage = () => {
  const { t } = useUiTranslation();
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const [health, setHealth] = useState(null);
  const [dbHealth, setDbHealth] = useState(null);
  const [celeryHealth, setCeleryHealth] = useState(null);
  const [redisHealth, setRedisHealth] = useState(null);
  const [celeryTasks, setCeleryTasks] = useState(null);
  const [failedTasks, setFailedTasks] = useState([]);
  const [progressJobs, setProgressJobs] = useState([]);
  const [stuckJobs, setStuckJobs] = useState([]);
  const [progressSystemHealthy, setProgressSystemHealthy] = useState(true);
  const [selfTestResults, setSelfTestResults] = useState(null);
  const [loadingSelfTest, setLoadingSelfTest] = useState(false);
  const [loadingCleanup, setLoadingCleanup] = useState(false);
  const [loadingEntityIndexing, setLoadingEntityIndexing] = useState(false);
  const [healthError, setHealthError] = useState(null);
  const [dbError, setDbError] = useState(null);
  const [celeryError, setCeleryError] = useState(null);
  const [redisError, setRedisError] = useState(null);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: "",
    severity: "error",
  });
  const [logs, setLogs] = useState("");
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsError, setLogsError] = useState(null);
  const socketRef = useRef(null);
  const [siRunsTotal, setSiRunsTotal] = useState(0);
  const [familyNodes, setFamilyNodes] = useState([]);
  const [familyLoading, setFamilyLoading] = useState(false);
  const [uncleStatus, setUncleStatus] = useState(null);

  // WEBSOCKET CONNECTION: Set up health monitoring via WebSocket
  useEffect(() => {
    // Activate ResourceManager when devtools page is opened
    activateResourceManager();
    
    // Initialize WebSocket connection for health updates
    socketRef.current = io(SOCKET_URL, {
      reconnection: true,
      reconnectionAttempts: 5,
    });

    socketRef.current.on("connect", () => {
      console.log("DevTools: Connected to health monitoring WebSocket");
      // Subscribe to health updates
      socketRef.current.emit("subscribe_health");
    });

    socketRef.current.on("health_status_change", (data) => {
      console.log("DevTools: Received health status change:", data);
      if (data.service === "celery") {
        if (data.status === "up") {
          setCeleryHealth(data.details);
          setCeleryError(null);
        } else {
          setCeleryHealth(null);
          setCeleryError(data.details?.error || SERVICE_DOWN);
        }
      }
    });

    socketRef.current.on("disconnect", () => {
      console.log("DevTools: Disconnected from health monitoring WebSocket");
    });
    
    return () => {
      if (socketRef.current) {
        socketRef.current.disconnect();
      }
    };
  }, []);

  const errorText = (error) =>
    error === UNKNOWN_ERROR
      ? t("devTools.errors.unknown")
      : error === SERVICE_DOWN
        ? t("devTools.errors.serviceDown")
        : error;

  const handleCloseSnackbar = () =>
    setSnackbar((prev) => ({ ...prev, open: false }));

  const showMessage = useCallback((message, severity = "info") => {
    setSnackbar({ open: true, message, severity });
  }, []);

  const handleRetry = () => {
    setHealthError(null);
    setDbError(null);
    setCeleryError(null);
    setRedisError(null);
    fetchData();
  };

  const handleProgressJobsCleanup = async () => {
    setLoadingCleanup(true);
    try {
      setSnackbar({
        open: true,
        message: t("devTools.cleanup.running"),
        severity: "info",
      });

      // Call the new enhanced cleanup API
      const result = await cleanupStuckJobs();
      
      setSnackbar({
        open: true,
        message: t("devTools.cleanup.result", {
          message: result.message,
          cleaned: result.cleaned_count,
          found: result.stuck_jobs_found,
        }),
        severity: result.cleaned_count > 0 ? "success" : "info",
      });

      // Refresh data to show updated job count
      fetchData();
      
    } catch (error) {
      console.error("Progress jobs cleanup failed:", error);
      setSnackbar({
        open: true,
        message: t("devTools.cleanup.failed", { error: error.message }),
        severity: "error",
      });
    } finally {
      setLoadingCleanup(false);
    }
  };

  const handleEntityIndexing = async () => {
    setLoadingEntityIndexing(true);
    try {
      setSnackbar({
        open: true,
        message: t("devTools.entityIndexing.running"),
        severity: "info",
      });

      const response = await fetch('/api/entity-indexing/index-all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const result = await response.json();
      
      if (result.success) {
        const { clients, projects, websites, tasks, errors } = result.results;
        const total = clients + projects + websites + tasks;
        setSnackbar({
          open: true,
          message: errors > 0
            ? t("devTools.entityIndexing.completedWithErrors", { total, clients, projects, websites, tasks, errors })
            : t("devTools.entityIndexing.completed", { total, clients, projects, websites, tasks }),
          severity: errors > 0 ? "warning" : "success"
        });
      } else {
        setSnackbar({
          open: true,
          message: result.error || t("devTools.entityIndexing.failed"),
          severity: "error"
        });
      }
    } catch (error) {
      console.error('Entity indexing error:', error);
      setSnackbar({
        open: true,
        message: t("devTools.entityIndexing.failedWithError", { error: error.message }),
        severity: "error"
      });
    } finally {
      setLoadingEntityIndexing(false);
    }
  };

  const handleRestartServices = async () => {
    try {
      setSnackbar({
        open: true,
        message: t("devTools.restart.starting"),
        severity: "info",
      });
      
      const result = await triggerReboot();
      
      setSnackbar({
        open: true,
        message: result.message || t("devTools.restart.started"),
        severity: "success",
      });
      
      // Give the server a moment to process the restart
      const _timeoutId = setTimeout(() => {
        // Check if component is still mounted before updating state
        if (document.contains(document.querySelector('[data-component="devtools"]'))) {
          setSnackbar({
            open: true,
            message: t("devTools.restart.restarting"),
            severity: "warning",
          });
        }
      }, 2000);
      
    } catch (error) {
      console.error("Service restart failed:", error);
      setSnackbar({
        open: true,
        message: t("devTools.restart.failed", { error: error.message }),
        severity: "error",
      });
    }
  };

  const fetchData = async () => {
    const results = await Promise.allSettled([
      getBackendHealth(),
      getDbHealth(),
      getCeleryHealth(),
      getRedisHealth(),
      getTasks(null, "failed"),
      getCeleryTasks(),
      getProgressJobs(),
    ]);

    const [hRes, dbRes, celRes, redisRes, tasksRes, celeryInfoRes, progressRes] = results;

    if (hRes.status === "fulfilled") {
      setHealth(hRes.value);
      setHealthError(null);
    } else {
      console.error("Backend health fetch error:", hRes.reason);
      setHealthError(hRes.reason?.message || UNKNOWN_ERROR);
      setHealth(null);
    }

    if (dbRes.status === "fulfilled") {
      setDbHealth(dbRes.value);
      setDbError(null);
    } else {
      console.error("DB health fetch error:", dbRes.reason);
      setDbError(dbRes.reason?.message || UNKNOWN_ERROR);
      setDbHealth(null);
    }

    if (celRes.status === "fulfilled") {
      setCeleryHealth(celRes.value);
      setCeleryError(null);
    } else {
      console.error("Celery health fetch error:", celRes.reason);
      setCeleryError(celRes.reason?.message || UNKNOWN_ERROR);
      setCeleryHealth(null);
    }

    if (redisRes.status === "fulfilled") {
      setRedisHealth(redisRes.value);
      setRedisError(null);
    } else {
      console.error("Redis health fetch error:", redisRes.reason);
      setRedisError(redisRes.reason?.message || UNKNOWN_ERROR);
      setRedisHealth(null);
    }

    if (celeryInfoRes.status === "fulfilled") {
      console.log("DevTools: Celery tasks data received:", celeryInfoRes.value);
      setCeleryTasks(celeryInfoRes.value);
    } else {
      console.log("DevTools: Celery tasks fetch failed:", celeryInfoRes.reason);
      setCeleryTasks(null);
    }

    if (tasksRes.status === "fulfilled") {
      const tasksData = tasksRes.value;
      setFailedTasks(Array.isArray(tasksData) ? tasksData.slice(0, 5) : []);
    } else {
      setFailedTasks([]);
    }

    if (progressRes.status === "fulfilled") {
      const progressData = progressRes.value;
      setProgressJobs(progressData.active_jobs || []);
      setStuckJobs(progressData.stuck_jobs || []);
      setProgressSystemHealthy(progressData.system_healthy !== false);
    } else {
      setProgressJobs([]);
      setStuckJobs([]);
      setProgressSystemHealthy(true);
    }
  };

  // INITIAL DATA LOAD: Load once on mount, then rely on WebSocket for real-time updates
  useEffect(() => {
    fetchData(); // Initial load
    // Reduced polling to 5 minutes for non-WebSocket data (tasks, progress jobs, etc.)
    const id = setInterval(() => {
      // Only refresh non-health data to avoid ping spam
      fetchNonHealthData();
    }, 300000); // 5 minutes
    return () => clearInterval(id);
  }, []);

  const fetchNonHealthData = async () => {
    const results = await Promise.allSettled([
      getTasks(null, "failed"),
      getCeleryTasks(),
      getProgressJobs(),
    ]);

    const [tasksRes, celeryInfoRes, progressRes] = results;

    if (celeryInfoRes.status === "fulfilled") {
      setCeleryTasks(celeryInfoRes.value);
    } else {
      setCeleryTasks(null);
    }

    if (tasksRes.status === "fulfilled") {
      const tasksData = tasksRes.value;
      setFailedTasks(Array.isArray(tasksData) ? tasksData.slice(0, 5) : []);
    } else {
      setFailedTasks([]);
    }

    if (progressRes.status === "fulfilled") {
      const progressData = progressRes.value;
      // Handle both old array format and new enhanced object format
      if (Array.isArray(progressData)) {
        setProgressJobs(progressData);
        setStuckJobs([]);
        setProgressSystemHealthy(true);
      } else {
        setProgressJobs(progressData.active_jobs || []);
        setStuckJobs(progressData.stuck_jobs || []);
        setProgressSystemHealthy(progressData.system_healthy !== false);
      }
    } else {
      setProgressJobs([]);
      setStuckJobs([]);
      setProgressSystemHealthy(true);
    }
  };

  const handleSelfTest = async () => {
    setLoadingSelfTest(true);
    setSelfTestResults(null);
    try {
      const res = await runSelfTest();
      setSelfTestResults(res.results || res);
      setSnackbar({
        open: true,
        message: t("devTools.selfTest.complete"),
        severity: "success",
      });
    } catch (err) {
      console.error("Self-test failed:", err);
      setSnackbar({
        open: true,
        message: t("devTools.selfTest.failed", { error: err.message }),
        severity: "error",
      });
    } finally {
      setLoadingSelfTest(false);
    }
  };

  // Log viewer logic - simplified without authentication
  const fetchLogs = useCallback(async () => {
    setLogsLoading(true);
    setLogsError(null);
    try {
      // Simple fetch without authentication
      const resp = await fetch(`/api/logs/tail?lines=100`);
      
      if (!resp.ok) {
        throw new Error(t("devTools.logs.fetchFailed", { status: resp.status }));
      }
      
      const text = await resp.text();
      setLogs(text);
    } catch (err) {
      setLogsError(err.message);
      setLogs("");
    } finally {
      setLogsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLogs();
    const id = setInterval(fetchLogs, 5000);
    return () => clearInterval(id);
  }, [fetchLogs]);

  const fetchFamilyNetwork = useCallback(async () => {
    setFamilyLoading(true);
    try {
      const [nodesRes, uncleRes] = await Promise.all([
        fetch(`${SOCKET_URL.replace(/\/$/, "")}/api/interconnector/nodes`).then((r) => r.json()).catch(() => ({ data: [] })),
        claudeAdvisorService.getStatus(),
      ]);
      setFamilyNodes(nodesRes?.data || nodesRes?.nodes || []);
      setUncleStatus(uncleRes?.data);
    } catch (err) {
      console.error("Failed to fetch family network:", err);
    } finally {
      setFamilyLoading(false);
    }
  }, []);


  // Calculate progress job statistics with enhanced stuck job detection
  const incompleteJobs = progressJobs.filter(job => !job.is_complete);
  const completedJobs = progressJobs.filter(job => job.is_complete);
  const stuckJobsCount = stuckJobs.length;
  const _oldJobs = progressJobs.filter(job => {
    const timestampValue = job.last_update_utc || job.updated_at;
    if (!timestampValue) return false; // Skip jobs with no timestamp
    
    try {
      const lastUpdate = new Date(timestampValue);
      if (isNaN(lastUpdate.getTime())) return false; // Skip invalid dates
      
      const hoursSinceUpdate = (Date.now() - lastUpdate.getTime()) / (1000 * 60 * 60);
      return hoursSinceUpdate > 1 && !job.is_complete;
    } catch (e) {
      console.warn('Invalid timestamp in progress job:', timestampValue);
      return false;
    }
  });

  return (
    <PageLayout
      title={t("devTools.title")}
      variant="standard"
      modelStatus
      activeModel={isLoadingModel ? t("devTools.loading") : modelError ? t("devTools.status.error") : activeModel || t("devTools.model.default")}
    >
      <Box data-component="devtools">
      {/* System Status Summary */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t("devTools.summary.heading")}
          </Typography>
          <Box display="flex" gap={2} flexWrap="wrap">
            <Chip
              icon={health && !healthError ? <CheckCircle /> : <ErrorIcon />}
              label={t("devTools.summary.backend", { state: health && !healthError ? t("devTools.status.healthy") : t("devTools.status.error") })}
              color={health && !healthError ? 'success' : 'error'}
              variant="outlined"
            />
            <Chip
              icon={dbHealth && !dbError ? <CheckCircle /> : <ErrorIcon />}
              label={t("devTools.summary.database", { state: dbHealth && !dbError ? t("devTools.status.healthy") : t("devTools.status.error") })}
              color={dbHealth && !dbError ? 'success' : 'error'}
              variant="outlined"
            />
            <Chip
              icon={celeryHealth && !celeryError ? <CheckCircle /> : <ErrorIcon />}
              label={t("devTools.summary.celery", { state: celeryHealth && !celeryError ? t("devTools.status.healthy") : t("devTools.status.error") })}
              color={celeryHealth && !celeryError ? 'success' : 'error'}
              variant="outlined"
            />
            <Chip
              icon={redisHealth && !redisError ? <CheckCircle /> : <ErrorIcon />}
              label={t("devTools.summary.redis", { state: redisHealth && !redisError ? t("devTools.status.healthy") : t("devTools.status.error") })}
              color={redisHealth && !redisError ? 'success' : 'error'}
              variant="outlined"
            />
          </Box>
        </CardContent>
      </Card>

      <Grid container spacing={2}>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
                <Typography variant="h6">
                  {t("devTools.backend.heading")}
                </Typography>
                <Tooltip title={t("devTools.actions.refresh")}>
                  <IconButton size="small" onClick={handleRetry}>
                    <Refresh />
                  </IconButton>
                </Tooltip>
              </Box>
              {health && !healthError ? (
                <>
                  <Box display="flex" alignItems="center" mb={1}>
                    <CheckCircle color="success" sx={{ mr: 1 }} />
                    <Chip label={health.status} color="success" size="small" />
                  </Box>
                  <Typography variant="body2">{t("devTools.backend.version", { version: health.version })}</Typography>
                  <Typography variant="body2">{t("devTools.backend.uptime", { seconds: Math.round(health.uptime_seconds) })}</Typography>
                  {health.index_loaded && (
                    <Typography variant="body2" color="success.main">
                      {t("devTools.backend.indexLoaded")}
                    </Typography>
                  )}
                </>
              ) : healthError ? (
                <Alert severity="error" action={
                  <Button color="inherit" size="small" onClick={handleRetry}>
                    {t("devTools.actions.retry")}
                  </Button>
                }>
                  {errorText(healthError)}
                </Alert>
              ) : (
                <Box display="flex" alignItems="center">
                  <CircularProgress size={20} sx={{ mr: 1 }} />
                  <Typography variant="body2">{t("devTools.checking")}</Typography>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {t("devTools.database.heading")}
              </Typography>
              {dbHealth && !dbError ? (
                <>
                  <Box display="flex" alignItems="center" mb={1}>
                    {dbHealth.status === "ok" ? (
                      <CheckCircle color="success" sx={{ mr: 1 }} />
                    ) : (
                      <Warning color="warning" sx={{ mr: 1 }} />
                    )}
                    <Chip
                      label={dbHealth.status === "ok" ? t("devTools.database.upToDate") : t("devTools.database.needsUpdate")}
                      color={dbHealth.status === "ok" ? "success" : "warning"}
                      size="small"
                    />
                  </Box>
                  <Typography variant="body2">{t("devTools.database.current", { revision: dbHealth.db_revision || t("devTools.database.noRevision") })}</Typography>
                  <Typography variant="body2">
                    {t("devTools.database.heads", { heads: (dbHealth.heads || [dbHealth.head]).filter(Boolean).join(", ") })}
                  </Typography>
                  {dbHealth.status === "multiple_heads" && (
                    <Alert severity="error" sx={{ mt: 1 }}>
                      {t("devTools.database.multipleHeads")}
                    </Alert>
                  )}
                </>
              ) : dbError ? (
                <Alert severity="error" action={
                  <Button color="inherit" size="small" onClick={handleRetry}>
                    {t("devTools.actions.retry")}
                  </Button>
                }>
                  {errorText(dbError)}
                </Alert>
              ) : (
                <Box display="flex" alignItems="center">
                  <CircularProgress size={20} sx={{ mr: 1 }} />
                  <Typography variant="body2">{t("devTools.checking")}</Typography>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {t("devTools.celery.heading")}
              </Typography>
              {celeryHealth && !celeryError ? (
                <>
                  <Box display="flex" alignItems="center" mb={1}>
                    <CheckCircle color="success" sx={{ mr: 1 }} />
                    <Chip label={celeryHealth.status} color="success" size="small" />
                  </Box>
                  {celeryHealth.result && (
                    <Typography variant="body2" color="success.main">
                      {t("devTools.celery.ping", { result: celeryHealth.result })}
                    </Typography>
                  )}
                  {celeryTasks && (
                    <Box mt={1}>
                      <Typography variant="body2">
                        {t("devTools.celery.active", { count: Object.keys(celeryTasks.active || {}).length })}
                      </Typography>
                      <Typography variant="body2">
                        {t("devTools.celery.queued", { count: Object.keys(celeryTasks.reserved || {}).length })}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {t("devTools.celery.debug", { tasks: JSON.stringify(celeryTasks.active || {}) })}
                      </Typography>
                    </Box>
                  )}
                </>
              ) : celeryError ? (
                <Alert severity="error" action={
                  <Button color="inherit" size="small" onClick={handleRetry}>
                    {t("devTools.actions.retry")}
                  </Button>
                }>
                  {errorText(celeryError)}
                </Alert>
              ) : (
                <Box display="flex" alignItems="center">
                  <CircularProgress size={20} sx={{ mr: 1 }} />
                  <Typography variant="body2">{t("devTools.checking")}</Typography>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {t("devTools.redis.heading")}
              </Typography>
              {redisHealth && !redisError ? (
                <>
                  <Box display="flex" alignItems="center" mb={1}>
                    <CheckCircle color="success" sx={{ mr: 1 }} />
                    <Chip label={redisHealth.status} color="success" size="small" />
                  </Box>
                  <Typography variant="body2" color="success.main">
                    {t("devTools.redis.connected")}
                  </Typography>
                </>
              ) : redisError ? (
                <Alert severity="error" action={
                  <Button color="inherit" size="small" onClick={handleRetry}>
                    {t("devTools.actions.retry")}
                  </Button>
                }>
                  {errorText(redisError)}
                </Alert>
              ) : (
                <Box display="flex" alignItems="center">
                  <CircularProgress size={20} sx={{ mr: 1 }} />
                  <Typography variant="body2">{t("devTools.checking")}</Typography>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {t("devTools.failedTasks.heading")}
              </Typography>
              {failedTasks.length === 0 ? (
                <Typography>{t("devTools.failedTasks.none")}</Typography>
              ) : (
                failedTasks.map((t) => (
                  <Typography key={t.id}>
                    {t.name} - {t.status}
                  </Typography>
                ))
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {t("devTools.controls.heading")}
              </Typography>
              <Box display="flex" gap={1} flexWrap="wrap">
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleRetry}
                  startIcon={<Refresh />}
                >
                  {t("devTools.controls.refreshAll")}
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleSelfTest}
                  disabled={loadingSelfTest}
                >
                  {loadingSelfTest ? t("devTools.controls.running") : t("devTools.controls.selfTest")}
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleRestartServices}
                  color="warning"
                >
                  {t("devTools.controls.restartServices")}
                </Button>
              </Box>
              <Box mt={2}>
                <Typography variant="body2" color="text.secondary">
                  {t("devTools.controls.autoRefresh")}
                </Typography>
              </Box>
            </CardContent>
          </Card>
        </Grid>
        {/* NEW: Progress Jobs Management Section */}
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" justifyContent="space-between" mb={2}>
                <Typography variant="h6">
                  {t("devTools.jobs.heading")}
                </Typography>
                <Box display="flex" gap={1}>
                  <Chip 
                    label={t("devTools.jobs.activeCount", { count: incompleteJobs.length })} 
                    color={incompleteJobs.length > 5 ? 'warning' : 'success'} 
                    size="small" 
                  />
                  <Chip 
                    label={t("devTools.jobs.completedCount", { count: completedJobs.length })} 
                    color="info" 
                    size="small" 
                  />
                  {stuckJobsCount > 0 && (
                    <Chip 
                      label={t("devTools.jobs.stuckCount", { count: stuckJobsCount })} 
                      color="error" 
                      size="small" 
                    />
                  )}
                  {!progressSystemHealthy && (
                    <Chip 
                      label={t("devTools.jobs.systemUnhealthy")} 
                      color="error" 
                      size="small" 
                    />
                  )}
                </Box>
              </Box>
              
              <Box display="flex" gap={1} mb={2}>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleProgressJobsCleanup}
                  disabled={loadingCleanup}
                  startIcon={loadingCleanup ? <CircularProgress size={16} /> : <CleaningServices />}
                  color={stuckJobsCount > 0 ? 'warning' : 'primary'}
                >
                  {loadingCleanup
                    ? t("devTools.jobs.cleaning")
                    : stuckJobsCount > 0
                      ? t("devTools.jobs.cleanStuckCount", { count: stuckJobsCount })
                      : t("devTools.jobs.cleanStuck")}
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleEntityIndexing}
                  disabled={loadingEntityIndexing}
                  startIcon={loadingEntityIndexing ? <CircularProgress size={16} /> : <CheckCircle />}
                  color="secondary"
                >
                  {loadingEntityIndexing ? t("devTools.jobs.indexing") : t("devTools.jobs.indexEntities")}
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={fetchData}
                  startIcon={<Refresh />}
                >
                  {t("devTools.actions.refresh")}
                </Button>
              </Box>

              {stuckJobsCount > 0 && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                  {t("devTools.jobs.stuckWarning", { count: stuckJobsCount })}
                </Alert>
              )}
              {!progressSystemHealthy && (
                <Alert severity="error" sx={{ mb: 2 }}>
                  {t("devTools.jobs.systemIssues")}
                </Alert>
              )}

              <Accordion>
                <AccordionSummary expandIcon={<ExpandMore />}>
                  <Typography variant="subtitle1">
                    {t("devTools.jobs.details", { count: progressJobs.length })}
                  </Typography>
                </AccordionSummary>
                <AccordionDetails>
                  {progressJobs.length === 0 ? (
                    <Typography color="text.secondary">{t("devTools.jobs.noActive")}</Typography>
                  ) : (
                    <TableContainer component={Paper} variant="outlined">
                      <Table size="small">
                        <TableHead>
                          <TableRow>
                            <TableCell>{t("devTools.jobs.columns.jobId")}</TableCell>
                            <TableCell>{t("devTools.jobs.columns.type")}</TableCell>
                            <TableCell>{t("devTools.jobs.columns.status")}</TableCell>
                            <TableCell>{t("devTools.jobs.columns.progress")}</TableCell>
                            <TableCell>{t("devTools.jobs.columns.lastUpdate")}</TableCell>
                            <TableCell>{t("devTools.jobs.columns.message")}</TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {progressJobs.slice(0, 10).map((job, index) => {
                            const rawTimestamp = job.last_update_utc || job.updated_at || job.timestamp;
                            const lastUpdate = rawTimestamp ? new Date(rawTimestamp) : null;
                            const isValidDate = lastUpdate && !isNaN(lastUpdate.getTime());
                            const hoursSinceUpdate = isValidDate ? (Date.now() - lastUpdate.getTime()) / (1000 * 60 * 60) : 999;
                            const isStale = hoursSinceUpdate > 1 && !job.is_complete;
                            
                            return (
                              <TableRow 
                                key={index} 
                                sx={{ 
                                  backgroundColor: isStale ? 'warning.light' : 'inherit',
                                  '&:hover': {
                                    backgroundColor: 'action.hover'
                                  }
                                }}
                              >
                                <TableCell>
                                  <Box display="flex" alignItems="center">
                                    {job.id || job.job_id || `job_${index + 1}`}
                                    {isStale && (
                                      <Tooltip title={t("devTools.jobs.staleTooltip")}>
                                        <Warning color="warning" sx={{ ml: 1, fontSize: 16 }} />
                                      </Tooltip>
                                    )}
                                  </Box>
                                </TableCell>
                                <TableCell>
                                  <Chip 
                                    label={job.process_type || job.type || t("devTools.jobs.unknownType")} 
                                    size="small" 
                                    variant="outlined"
                                  />
                                </TableCell>
                                <TableCell>
                                  <Chip 
                                    label={job.is_complete ? t("devTools.jobs.complete") : (job.status || t("devTools.jobs.processing"))}
                                    color={job.is_complete ? 'success' : (isStale ? 'warning' : 'info')}
                                    size="small"
                                  />
                                </TableCell>
                                <TableCell>{job.progress || 0}%</TableCell>
                                <TableCell>
                                  <Typography variant="body2" color="text.secondary">
                                    {isValidDate ? lastUpdate.toLocaleString(uiLocale()) : (rawTimestamp ? t("devTools.jobs.invalidDate") : t("devTools.jobs.noTimestamp"))}
                                    {isValidDate && hoursSinceUpdate > 1 && (
                                      <Typography 
                                        component="span" 
                                        variant="body2" 
                                        color="warning.main"
                                        sx={{ ml: 0.5 }}
                                      >
                                        {" "}{t("devTools.jobs.hoursAgo", { hours: Math.round(hoursSinceUpdate) })}
                                      </Typography>
                                    )}
                                  </Typography>
                                </TableCell>
                                <TableCell>
                                  <Typography variant="body2" noWrap title={job.message || job.description}>
                                    {(job.message || job.description || t("devTools.jobs.noMessage")).substring(0, 50)}
                                    {(job.message || job.description || '').length > 50 ? '...' : ''}
                                  </Typography>
                                </TableCell>
                              </TableRow>
                            );
                          })}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  )}
                  {progressJobs.length > 10 && (
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                      {t("devTools.jobs.moreJobs", { count: progressJobs.length - 10 })}
                    </Typography>
                  )}
                </AccordionDetails>
              </Accordion>

              {/* Self-improvement log */}
              <Accordion TransitionProps={{ unmountOnExit: true }}>
                <AccordionSummary expandIcon={<ExpandMore />}>
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <FixIcon sx={{ color: "success.main" }} />
                    <Typography variant="subtitle1">{t("devTools.selfImprovement.heading")}</Typography>
                    <Chip label={t("devTools.selfImprovement.runs", { count: siRunsTotal })} size="small" variant="outlined" />
                  </Box>
                </AccordionSummary>
                <AccordionDetails>
                  <SelfImprovementRuns onTotal={setSiRunsTotal} />
                </AccordionDetails>
              </Accordion>

              {/* Family Network */}
              <Accordion>
                <AccordionSummary expandIcon={<ExpandMore />} onClick={() => { if (familyNodes.length === 0 && !uncleStatus) fetchFamilyNetwork(); }}>
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <HubIcon sx={{ color: FAMILY_BLUE }} />
                    <Typography variant="subtitle1">{t("devTools.family.heading")}</Typography>
                  </Box>
                </AccordionSummary>
                <AccordionDetails>
                  <Button size="small" onClick={fetchFamilyNetwork} startIcon={<Refresh />} sx={{ mb: 2 }}>
                    {t("devTools.actions.refresh")}
                  </Button>

                  {/* Uncle Claude Status Card */}
                  <Paper variant="outlined" sx={{ p: 2, mb: 2, borderColor: UNCLE_GOLD, borderWidth: 2 }}>
                    <Stack direction="row" spacing={2} alignItems="center">
                      <PsychologyIcon sx={{ color: UNCLE_GOLD, fontSize: 32 }} />
                      <Box sx={{ flex: 1 }}>
                        <Typography variant="subtitle2" sx={{ color: UNCLE_GOLD }}>Uncle Claude</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {uncleStatus?.model || t("devTools.family.notConfigured")} | {uncleStatus?.available ? t("devTools.family.online") : t("devTools.family.offline")}
                        </Typography>
                        {uncleStatus?.usage && (
                          <LinearProgress
                            variant="determinate"
                            value={Math.min(uncleStatus.usage.budget_used_percent || 0, 100)}
                            sx={{
                              mt: 0.5,
                              height: 4,
                              borderRadius: 2,
                              bgcolor: "action.hover",
                              "& .MuiLinearProgress-bar": { bgcolor: UNCLE_GOLD },
                            }}
                          />
                        )}
                      </Box>
                      <StatusChip
                        source="uncle_claude"
                        status={uncleStatus?.available ? "connected" : "offline"}
                      />
                    </Stack>
                  </Paper>

                  {/* Family Nodes */}
                  {familyLoading ? (
                    <CircularProgress size={24} />
                  ) : familyNodes.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      {t("devTools.family.noNodes")}
                    </Typography>
                  ) : (
                    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
                      {familyNodes.map((node) => (
                        <Paper key={node.node_id} variant="outlined" sx={{ p: 1.5 }}>
                          <Stack direction="row" spacing={2} alignItems="center">
                            <HubIcon sx={{ color: FAMILY_BLUE }} />
                            <Box sx={{ flex: 1 }}>
                              <Typography variant="subtitle2">{node.node_name || node.node_id}</Typography>
                              <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 0.5 }}>
                                {node.model_name && <Chip label={node.model_name} size="small" variant="outlined" />}
                                {node.specialties && JSON.parse(node.specialties || "[]").map((s, i) => (
                                  <Chip key={i} label={s} size="small" variant="outlined" sx={{ fontSize: "0.6rem" }} />
                                ))}
                              </Stack>
                              {node.vram_total > 0 && (
                                <Box sx={{ mt: 0.5 }}>
                                  <Typography variant="caption" color="text.secondary">
                                    {t("devTools.family.vram", { free: node.vram_free || 0, total: node.vram_total })}
                                  </Typography>
                                  <LinearProgress
                                    variant="determinate"
                                    value={node.vram_total > 0 ? Math.round(((node.vram_total - (node.vram_free || 0)) / node.vram_total) * 100) : 0}
                                    sx={{ height: 3, borderRadius: 2 }}
                                  />
                                </Box>
                              )}
                            </Box>
                            <Chip
                              label={node.status || t("devTools.family.unknownStatus")}
                              size="small"
                              color={node.status === "active" ? "success" : "default"}
                            />
                          </Stack>
                        </Paper>
                      ))}
                    </Box>
                  )}
                </AccordionDetails>
              </Accordion>
            </CardContent>
          </Card>
        </Grid>
        {/* Log Viewer Section */}
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Box display="flex" alignItems="center" justifyContent="space-between" mb={1}>
                <Typography variant="h6" gutterBottom>
                  {t("devTools.logs.heading")}
                </Typography>
                <Button size="small" onClick={fetchLogs} disabled={logsLoading}>
                  {logsLoading ? t("devTools.loading") : t("devTools.actions.refresh")}
                </Button>
              </Box>
              {logsError ? (
                <Alert severity="error">{t("devTools.logs.loadFailed", { error: logsError })}</Alert>
              ) : (
                <Box
                  sx={{
                    background: "#111",
                    color: "#0f0",
                    fontFamily: "monospace",
                    fontSize: "0.85rem",
                    borderRadius: 1,
                    p: 2,
                    maxHeight: 350,
                    overflow: "auto",
                  }}
                  component="pre"
                >
                  {logs || (logsLoading ? t("devTools.loading") : t("devTools.logs.empty"))}
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {t("devTools.performance.heading")}
              </Typography>
              <Box>
                <Typography variant="body2">
                  {t("devTools.performance.backendUptime", { seconds: health ? Math.round(health.uptime_seconds) : t("devTools.performance.notAvailable") })}
                </Typography>
                <Typography variant="body2">
                  {t("devTools.performance.celeryTasks", { tasks: celeryTasks ? Object.keys(celeryTasks.active || {}).length : t("devTools.performance.notAvailable") })}
                </Typography>
                <Typography variant="body2">
                  {t("devTools.performance.activeJobs", { count: incompleteJobs.length })}
                </Typography>
                <Typography variant="body2">
                  {t("devTools.performance.failedTasks", { count: failedTasks.length })}
                </Typography>
              </Box>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {t("devTools.metrics.heading")}
              </Typography>
              <Box sx={{ maxWidth: 300 }}>
                <SystemMetricsBar />
              </Box>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {t("devTools.selfTest.resultsHeading")}
              </Typography>
              {selfTestResults ? (
                <Box sx={{ mt: 2 }}>
                  <pre style={{ fontSize: "0.75rem", whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(selfTestResults, null, 2)}
                  </pre>
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  {t("devTools.selfTest.resultsEmpty")}
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {t("devTools.diagnostics.heading")}
              </Typography>
              <DiagnosticsPanel showMessage={showMessage} />
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {t("devTools.retrievalDebug.heading")}
              </Typography>
              <RAGDebugSection />
            </CardContent>
          </Card>
        </Grid>
      </Grid>
      <AlertSnackbar
        open={snackbar.open}
        onClose={handleCloseSnackbar}
        severity={snackbar.severity}
        message={snackbar.message}
      />
      </Box>
    </PageLayout>
  );
};

export default DevToolsPage;
