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
  DeleteSweep 
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
import SystemMetricsBar from "../components/layout/SystemMetricsBar";
import { activateResourceManager } from "../utils/resource_manager";
import { io } from "socket.io-client";
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import { selfImprovementService } from "../api/selfImprovementService";
import { claudeAdvisorService } from "../api/claudeAdvisorService";
import { StatusChip, UNCLE_GOLD, FAMILY_BLUE } from "../utils/familyColors";
import {
  AutoFixHigh as FixIcon,
  Psychology as PsychologyIcon,
  Hub as HubIcon,
} from "@mui/icons-material";

// SECURITY DISABLED: Token authentication removed for single-user system
// const DEV_SECRET_KEY = import.meta.env.VITE_DEV_TOKEN || "dev-secret-key-change-in-production";

const DevToolsPage = () => {
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
  const [siRuns, setSiRuns] = useState([]);
  const [siRunsLoading, setSiRunsLoading] = useState(false);
  const [siRunsTotal, setSiRunsTotal] = useState(0);
  const [siRunsFilter, setSiRunsFilter] = useState("all");
  const [siExpandedRow, setSiExpandedRow] = useState(null);
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
          setCeleryError(data.details?.error || "Service down");
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

  const handleCloseSnackbar = () =>
    setSnackbar((prev) => ({ ...prev, open: false }));

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
        message: "Running progress jobs cleanup...",
        severity: "info",
      });

      // Call the new enhanced cleanup API
      const result = await cleanupStuckJobs();
      
      setSnackbar({
        open: true,
        message: `${result.message}: ${result.cleaned_count} stuck jobs cleaned up (${result.stuck_jobs_found} found)`,
        severity: result.cleaned_count > 0 ? "success" : "info",
      });

      // Refresh data to show updated job count
      fetchData();
      
    } catch (error) {
      console.error("Progress jobs cleanup failed:", error);
      setSnackbar({
        open: true,
        message: `Cleanup failed: ${error.message}`,
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
        message: "Indexing entities for universal RAG access...",
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
          message: `Entity indexing completed: ${total} entities indexed (${clients} clients, ${projects} projects, ${websites} websites, ${tasks} tasks)${errors > 0 ? `, ${errors} errors` : ''}`,
          severity: errors > 0 ? "warning" : "success"
        });
      } else {
        setSnackbar({
          open: true,
          message: result.error || 'Entity indexing failed',
          severity: "error"
        });
      }
    } catch (error) {
      console.error('Entity indexing error:', error);
      setSnackbar({
        open: true,
        message: `Entity indexing failed: ${error.message}`,
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
        message: "Initiating service restart...",
        severity: "info",
      });
      
      const result = await triggerReboot();
      
      setSnackbar({
        open: true,
        message: result.message || "Service restart initiated successfully",
        severity: "success",
      });
      
      // Give the server a moment to process the restart
      const timeoutId = setTimeout(() => {
        // Check if component is still mounted before updating state
        if (document.contains(document.querySelector('[data-component="devtools"]'))) {
          setSnackbar({
            open: true,
            message: "Services are restarting. The page may disconnect temporarily.",
            severity: "warning",
          });
        }
      }, 2000);
      
    } catch (error) {
      console.error("Service restart failed:", error);
      setSnackbar({
        open: true,
        message: `Service restart failed: ${error.message}`,
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
      setHealthError(hRes.reason?.message || "Error");
      setHealth(null);
    }

    if (dbRes.status === "fulfilled") {
      setDbHealth(dbRes.value);
      setDbError(null);
    } else {
      console.error("DB health fetch error:", dbRes.reason);
      setDbError(dbRes.reason?.message || "Error");
      setDbHealth(null);
    }

    if (celRes.status === "fulfilled") {
      setCeleryHealth(celRes.value);
      setCeleryError(null);
    } else {
      console.error("Celery health fetch error:", celRes.reason);
      setCeleryError(celRes.reason?.message || "Error");
      setCeleryHealth(null);
    }

    if (redisRes.status === "fulfilled") {
      setRedisHealth(redisRes.value);
      setRedisError(null);
    } else {
      console.error("Redis health fetch error:", redisRes.reason);
      setRedisError(redisRes.reason?.message || "Error");
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
        message: "Self-test complete",
        severity: "success",
      });
    } catch (err) {
      console.error("Self-test failed:", err);
      setSnackbar({
        open: true,
        message: `Self-test failed: ${err.message}`,
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
        throw new Error(`Failed to fetch logs (${resp.status})`);
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

  const fetchSiRuns = useCallback(async () => {
    setSiRunsLoading(true);
    try {
      const res = await selfImprovementService.getRuns(50, 0);
      const allRuns = res?.data?.runs || [];
      setSiRunsTotal(allRuns.length);
      setSiRuns(
        siRunsFilter === "all"
          ? allRuns
          : allRuns.filter((r) =>
              siRunsFilter === "success" || siRunsFilter === "failed"
                ? r.status === siRunsFilter
                : r.trigger === siRunsFilter
            )
      );
    } catch (err) {
      console.error("Failed to fetch SI runs:", err);
    } finally {
      setSiRunsLoading(false);
    }
  }, [siRunsFilter]);

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

  useEffect(() => {
    if (siRunsTotal > 0) fetchSiRuns();
  }, [siRunsFilter, fetchSiRuns]);

  // Calculate progress job statistics with enhanced stuck job detection
  const incompleteJobs = progressJobs.filter(job => !job.is_complete);
  const completedJobs = progressJobs.filter(job => job.is_complete);
  const stuckJobsCount = stuckJobs.length;
  const oldJobs = progressJobs.filter(job => {
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
      title="System Dashboard"
      variant="standard"
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel || "Default"}
    >
      <Box data-component="devtools">
      {/* System Status Summary */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            System Status Summary
          </Typography>
          <Box display="flex" gap={2} flexWrap="wrap">
            <Chip
              icon={health && !healthError ? <CheckCircle /> : <ErrorIcon />}
              label={`Backend: ${health && !healthError ? 'Healthy' : 'Error'}`}
              color={health && !healthError ? 'success' : 'error'}
              variant="outlined"
            />
            <Chip
              icon={dbHealth && !dbError ? <CheckCircle /> : <ErrorIcon />}
              label={`Database: ${dbHealth && !dbError ? 'Healthy' : 'Error'}`}
              color={dbHealth && !dbError ? 'success' : 'error'}
              variant="outlined"
            />
            <Chip
              icon={celeryHealth && !celeryError ? <CheckCircle /> : <ErrorIcon />}
              label={`Celery: ${celeryHealth && !celeryError ? 'Healthy' : 'Error'}`}
              color={celeryHealth && !celeryError ? 'success' : 'error'}
              variant="outlined"
            />
            <Chip
              icon={redisHealth && !redisError ? <CheckCircle /> : <ErrorIcon />}
              label={`Redis: ${redisHealth && !redisError ? 'Healthy' : 'Error'}`}
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
                  Backend
                </Typography>
                <Tooltip title="Refresh">
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
                  <Typography variant="body2">Version: {health.version}</Typography>
                  <Typography variant="body2">Uptime: {Math.round(health.uptime_seconds)}s</Typography>
                  {health.index_loaded && (
                    <Typography variant="body2" color="success.main">
                      ✓ Index loaded
                    </Typography>
                  )}
                </>
              ) : healthError ? (
                <Alert severity="error" action={
                  <Button color="inherit" size="small" onClick={handleRetry}>
                    Retry
                  </Button>
                }>
                  {healthError}
                </Alert>
              ) : (
                <Box display="flex" alignItems="center">
                  <CircularProgress size={20} sx={{ mr: 1 }} />
                  <Typography variant="body2">Checking...</Typography>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Database / Migrations
              </Typography>
              {dbHealth && !dbError ? (
                <>
                  <Box display="flex" alignItems="center" mb={1}>
                    {dbHealth.up_to_date ? (
                      <CheckCircle color="success" sx={{ mr: 1 }} />
                    ) : (
                      <Warning color="warning" sx={{ mr: 1 }} />
                    )}
                    <Chip
                      label={dbHealth.up_to_date ? "Up to date" : "Needs update"}
                      color={dbHealth.up_to_date ? "success" : "warning"}
                      size="small"
                    />
                  </Box>
                  <Typography variant="body2">Current: {dbHealth.current}</Typography>
                  <Typography variant="body2">
                    Heads: {dbHealth.heads && dbHealth.heads.join(", ")}
                  </Typography>
                  {dbHealth.multiple_heads && (
                    <Alert severity="error" sx={{ mt: 1 }}>
                      Multiple heads detected
                    </Alert>
                  )}
                </>
              ) : dbError ? (
                <Alert severity="error" action={
                  <Button color="inherit" size="small" onClick={handleRetry}>
                    Retry
                  </Button>
                }>
                  {dbError}
                </Alert>
              ) : (
                <Box display="flex" alignItems="center">
                  <CircularProgress size={20} sx={{ mr: 1 }} />
                  <Typography variant="body2">Checking...</Typography>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Celery Worker
              </Typography>
              {celeryHealth && !celeryError ? (
                <>
                  <Box display="flex" alignItems="center" mb={1}>
                    <CheckCircle color="success" sx={{ mr: 1 }} />
                    <Chip label={celeryHealth.status} color="success" size="small" />
                  </Box>
                  {celeryHealth.result && (
                    <Typography variant="body2" color="success.main">
                      ✓ Ping: {celeryHealth.result}
                    </Typography>
                  )}
                  {celeryTasks && (
                    <Box mt={1}>
                      <Typography variant="body2">
                        Active: {Object.keys(celeryTasks.active || {}).length}
                      </Typography>
                      <Typography variant="body2">
                        Queued: {Object.keys(celeryTasks.reserved || {}).length}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        Debug: {JSON.stringify(celeryTasks.active || {})}
                      </Typography>
                    </Box>
                  )}
                </>
              ) : celeryError ? (
                <Alert severity="error" action={
                  <Button color="inherit" size="small" onClick={handleRetry}>
                    Retry
                  </Button>
                }>
                  {celeryError}
                </Alert>
              ) : (
                <Box display="flex" alignItems="center">
                  <CircularProgress size={20} sx={{ mr: 1 }} />
                  <Typography variant="body2">Checking...</Typography>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Redis Cache
              </Typography>
              {redisHealth && !redisError ? (
                <>
                  <Box display="flex" alignItems="center" mb={1}>
                    <CheckCircle color="success" sx={{ mr: 1 }} />
                    <Chip label={redisHealth.status} color="success" size="small" />
                  </Box>
                  <Typography variant="body2" color="success.main">
                    ✓ Connection established
                  </Typography>
                </>
              ) : redisError ? (
                <Alert severity="error" action={
                  <Button color="inherit" size="small" onClick={handleRetry}>
                    Retry
                  </Button>
                }>
                  {redisError}
                </Alert>
              ) : (
                <Box display="flex" alignItems="center">
                  <CircularProgress size={20} sx={{ mr: 1 }} />
                  <Typography variant="body2">Checking...</Typography>
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Recent Failed Tasks
              </Typography>
              {failedTasks.length === 0 ? (
                <Typography>No recent failures.</Typography>
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
                System Controls
              </Typography>
              <Box display="flex" gap={1} flexWrap="wrap">
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleRetry}
                  startIcon={<Refresh />}
                >
                  Refresh All
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleSelfTest}
                  disabled={loadingSelfTest}
                >
                  {loadingSelfTest ? "Running..." : "Self Test"}
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleRestartServices}
                  color="warning"
                >
                  Restart Services
                </Button>
              </Box>
              <Box mt={2}>
                <Typography variant="body2" color="text.secondary">
                  Auto-refresh every 5 seconds
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
                  Progress Jobs Management
                </Typography>
                <Box display="flex" gap={1}>
                  <Chip 
                    label={`${incompleteJobs.length} Active`} 
                    color={incompleteJobs.length > 5 ? 'warning' : 'success'} 
                    size="small" 
                  />
                  <Chip 
                    label={`${completedJobs.length} Completed`} 
                    color="info" 
                    size="small" 
                  />
                  {stuckJobsCount > 0 && (
                    <Chip 
                      label={`${stuckJobsCount} Stuck`} 
                      color="error" 
                      size="small" 
                    />
                  )}
                  {!progressSystemHealthy && (
                    <Chip 
                      label="System Unhealthy" 
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
                  {loadingCleanup ? 'Cleaning...' : `Clean Stuck Jobs${stuckJobsCount > 0 ? ` (${stuckJobsCount})` : ''}`}
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleEntityIndexing}
                  disabled={loadingEntityIndexing}
                  startIcon={loadingEntityIndexing ? <CircularProgress size={16} /> : <CheckCircle />}
                  color="secondary"
                >
                  {loadingEntityIndexing ? 'Indexing...' : 'Index Entities'}
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={fetchData}
                  startIcon={<Refresh />}
                >
                  Refresh
                </Button>
              </Box>

              {stuckJobsCount > 0 && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                  {stuckJobsCount} jobs appear to be stuck (no active Celery tasks or stale timestamps). Consider running cleanup.
                </Alert>
              )}
              {!progressSystemHealthy && (
                <Alert severity="error" sx={{ mb: 2 }}>
                  Progress system detected issues. Some jobs may not be functioning properly.
                </Alert>
              )}

              <Accordion>
                <AccordionSummary expandIcon={<ExpandMore />}>
                  <Typography variant="subtitle1">
                    Job Details ({progressJobs.length} total)
                  </Typography>
                </AccordionSummary>
                <AccordionDetails>
                  {progressJobs.length === 0 ? (
                    <Typography color="text.secondary">No active jobs</Typography>
                  ) : (
                    <TableContainer component={Paper} variant="outlined">
                      <Table size="small">
                        <TableHead>
                          <TableRow>
                            <TableCell>Job ID</TableCell>
                            <TableCell>Type</TableCell>
                            <TableCell>Status</TableCell>
                            <TableCell>Progress</TableCell>
                            <TableCell>Last Update</TableCell>
                            <TableCell>Message</TableCell>
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
                                      <Tooltip title="Stale job (>1 hour without update)">
                                        <Warning color="warning" sx={{ ml: 1, fontSize: 16 }} />
                                      </Tooltip>
                                    )}
                                  </Box>
                                </TableCell>
                                <TableCell>
                                  <Chip 
                                    label={job.process_type || job.type || 'Unknown'} 
                                    size="small" 
                                    variant="outlined"
                                  />
                                </TableCell>
                                <TableCell>
                                  <Chip 
                                    label={job.is_complete ? 'Complete' : (job.status || 'Processing')}
                                    color={job.is_complete ? 'success' : (isStale ? 'warning' : 'info')}
                                    size="small"
                                  />
                                </TableCell>
                                <TableCell>{job.progress || 0}%</TableCell>
                                <TableCell>
                                  <Typography variant="body2" color="text.secondary">
                                    {isValidDate ? lastUpdate.toLocaleString() : (rawTimestamp ? 'Invalid Date' : 'No timestamp')}
                                    {isValidDate && hoursSinceUpdate > 1 && (
                                      <Typography 
                                        component="span" 
                                        variant="body2" 
                                        color="warning.main"
                                        sx={{ ml: 0.5 }}
                                      >
                                        {` (${Math.round(hoursSinceUpdate)}h ago)`}
                                      </Typography>
                                    )}
                                  </Typography>
                                </TableCell>
                                <TableCell>
                                  <Typography variant="body2" noWrap title={job.message || job.description}>
                                    {(job.message || job.description || 'No message').substring(0, 50)}
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
                      ... and {progressJobs.length - 10} more jobs
                    </Typography>
                  )}
                </AccordionDetails>
              </Accordion>

              {/* Self-Improvement Log */}
              <Accordion>
                <AccordionSummary expandIcon={<ExpandMore />} onClick={() => { if (siRuns.length === 0) fetchSiRuns(); }}>
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <FixIcon sx={{ color: "success.main" }} />
                    <Typography variant="subtitle1">Self-Improvement Log</Typography>
                    <Chip label={`${siRunsTotal} runs`} size="small" variant="outlined" />
                  </Box>
                </AccordionSummary>
                <AccordionDetails>
                  <Stack direction="row" spacing={0.5} sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
                    {["all", "scheduled", "reactive", "directed", "success", "failed"].map((f) => (
                      <Chip
                        key={f}
                        label={f.charAt(0).toUpperCase() + f.slice(1)}
                        size="small"
                        variant={siRunsFilter === f ? "filled" : "outlined"}
                        color={siRunsFilter === f ? "primary" : "default"}
                        onClick={() => { setSiRunsFilter(f); }}
                      />
                    ))}
                    <Button size="small" onClick={fetchSiRuns} startIcon={<Refresh />}>Refresh</Button>
                  </Stack>

                  {siRunsLoading ? (
                    <CircularProgress size={24} />
                  ) : siRuns.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">No self-improvement runs recorded yet.</Typography>
                  ) : (
                    <TableContainer component={Paper} variant="outlined">
                      <Table size="small">
                        <TableHead>
                          <TableRow>
                            <TableCell>Status</TableCell>
                            <TableCell>Trigger</TableCell>
                            <TableCell>Timestamp</TableCell>
                            <TableCell>Duration</TableCell>
                            <TableCell>Uncle Reviewed</TableCell>
                            <TableCell>Details</TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {siRuns.map((run) => (
                            <React.Fragment key={run.id}>
                              <TableRow
                                hover
                                sx={{ cursor: "pointer" }}
                                onClick={() => setSiExpandedRow(siExpandedRow === run.id ? null : run.id)}
                              >
                                <TableCell>
                                  <Chip
                                    label={run.status}
                                    size="small"
                                    color={run.status === "success" ? "success" : run.status === "failed" ? "error" : "warning"}
                                  />
                                </TableCell>
                                <TableCell>{run.trigger}</TableCell>
                                <TableCell>
                                  <Typography variant="caption">
                                    {run.timestamp ? new Date(run.timestamp).toLocaleString() : "—"}
                                  </Typography>
                                </TableCell>
                                <TableCell>
                                  {run.duration_seconds ? `${run.duration_seconds.toFixed(1)}s` : "—"}
                                </TableCell>
                                <TableCell>
                                  {run.uncle_reviewed ? (
                                    <Chip label="Reviewed" size="small" sx={{ bgcolor: UNCLE_GOLD, color: "#000", fontSize: "0.65rem" }} />
                                  ) : (
                                    <Typography variant="caption" color="text.secondary">No</Typography>
                                  )}
                                </TableCell>
                                <TableCell>
                                  <Typography variant="caption" color="primary" sx={{ cursor: "pointer" }}>
                                    {siExpandedRow === run.id ? "Collapse" : "Expand"}
                                  </Typography>
                                </TableCell>
                              </TableRow>
                              {siExpandedRow === run.id && (
                                <TableRow>
                                  <TableCell colSpan={6} sx={{ bgcolor: "action.hover" }}>
                                    <Box sx={{ p: 1 }}>
                                      {run.error_message && (
                                        <Alert severity="error" sx={{ mb: 1 }}>{run.error_message}</Alert>
                                      )}
                                      {run.uncle_feedback && (
                                        <Box sx={{ mb: 1 }}>
                                          <Typography variant="caption" fontWeight="bold" sx={{ color: UNCLE_GOLD }}>
                                            Uncle Claude Feedback:
                                          </Typography>
                                          <Typography variant="body2">{run.uncle_feedback}</Typography>
                                        </Box>
                                      )}
                                      {run.changes_made && (
                                        <Box sx={{ mb: 1 }}>
                                          <Typography variant="caption" fontWeight="bold">Changes Made:</Typography>
                                          <Box component="pre" sx={{ fontSize: "0.7rem", bgcolor: "background.default", p: 1, borderRadius: 1, overflow: "auto", maxHeight: 200 }}>
                                            {typeof run.changes_made === "string" ? run.changes_made : JSON.stringify(run.changes_made, null, 2)}
                                          </Box>
                                        </Box>
                                      )}
                                      {run.test_results_before && (
                                        <Box>
                                          <Typography variant="caption" fontWeight="bold">Test Results:</Typography>
                                          <Box component="pre" sx={{ fontSize: "0.7rem", bgcolor: "background.default", p: 1, borderRadius: 1, overflow: "auto", maxHeight: 150 }}>
                                            {typeof run.test_results_before === "string" ? run.test_results_before : JSON.stringify(run.test_results_before, null, 2)}
                                          </Box>
                                        </Box>
                                      )}
                                    </Box>
                                  </TableCell>
                                </TableRow>
                              )}
                            </React.Fragment>
                          ))}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  )}
                </AccordionDetails>
              </Accordion>

              {/* Family Network */}
              <Accordion>
                <AccordionSummary expandIcon={<ExpandMore />} onClick={() => { if (familyNodes.length === 0 && !uncleStatus) fetchFamilyNetwork(); }}>
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <HubIcon sx={{ color: FAMILY_BLUE }} />
                    <Typography variant="subtitle1">Family Network</Typography>
                  </Box>
                </AccordionSummary>
                <AccordionDetails>
                  <Button size="small" onClick={fetchFamilyNetwork} startIcon={<Refresh />} sx={{ mb: 2 }}>
                    Refresh
                  </Button>

                  {/* Uncle Claude Status Card */}
                  <Paper variant="outlined" sx={{ p: 2, mb: 2, borderColor: UNCLE_GOLD, borderWidth: 2 }}>
                    <Stack direction="row" spacing={2} alignItems="center">
                      <PsychologyIcon sx={{ color: UNCLE_GOLD, fontSize: 32 }} />
                      <Box sx={{ flex: 1 }}>
                        <Typography variant="subtitle2" sx={{ color: UNCLE_GOLD }}>Uncle Claude</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {uncleStatus?.model || "Not configured"} | {uncleStatus?.available ? "Online" : "Offline"}
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
                      No family nodes connected. Configure the Interconnector in Settings.
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
                                    VRAM: {node.vram_free || 0} / {node.vram_total} MB
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
                              label={node.status || "unknown"}
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
                  Backend Logs (last 100 lines)
                </Typography>
                <Button size="small" onClick={fetchLogs} disabled={logsLoading}>
                  {logsLoading ? "Loading..." : "Refresh"}
                </Button>
              </Box>
              {logsError ? (
                <Alert severity="error">Failed to load logs: {logsError}</Alert>
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
                  {logs || (logsLoading ? "Loading..." : "No log output.")}
                </Box>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Performance Overview
              </Typography>
              <Box>
                <Typography variant="body2">
                  Backend Uptime: {health ? Math.round(health.uptime_seconds) : 'N/A'}s
                </Typography>
                <Typography variant="body2">
                  Celery Tasks: {celeryTasks ? Object.keys(celeryTasks.active || {}).length : 'N/A'} active
                </Typography>
                <Typography variant="body2">
                  Active Jobs: {incompleteJobs.length}
                </Typography>
                <Typography variant="body2">
                  Failed Tasks: {failedTasks.length}
                </Typography>
              </Box>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                System Metrics
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
                Self-Test Results
              </Typography>
              {selfTestResults ? (
                <Box sx={{ mt: 2 }}>
                  <pre style={{ fontSize: "0.75rem", whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(selfTestResults, null, 2)}
                  </pre>
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  Run a self-test to see detailed system diagnostics
                </Typography>
              )}
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
