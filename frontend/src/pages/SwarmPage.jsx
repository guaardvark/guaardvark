// frontend/src/pages/SwarmPage.jsx
// Swarm Orchestrator dashboard — real-time agent monitoring, launch, and management

import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Box,
  Typography,
  Paper,
  Grid,
  Card,
  CardContent,
  CardActions,
  Button,
  Chip,
  IconButton,
  Tooltip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  CircularProgress,
  Alert,
  Stack,
  Collapse,
  LinearProgress,
  Divider,
  Switch,
  FormControlLabel,
  Select,
  MenuItem,
  InputLabel,
  FormControl,
} from "@mui/material";
import {
  PlayArrow as LaunchIcon,
  Stop as CancelIcon,
  Refresh as RefreshIcon,
  MergeType as MergeIcon,
  Delete as CleanupIcon,
  Flight as FlightIcon,
  Cloud as OnlineIcon,
  CloudOff as OfflineIcon,
  Terminal as LogsIcon,
  Description as TemplateIcon,
  ExpandMore as ExpandIcon,
  ExpandLess as CollapseIcon,
  Speed as SpeedIcon,
  AttachMoney as CostIcon,
  Schedule as TimeIcon,
  AccountTree as BranchIcon,
  Warning as ConflictIcon,
  CheckCircle as DoneIcon,
  Error as FailedIcon,
  HourglassEmpty as PendingIcon,
  Sync as RunningIcon,
  RateReview as ReviewIcon,
  Close as CloseIcon,
} from "@mui/icons-material";
import { useTheme } from "@mui/material/styles";

import PageLayout from "../components/layout/PageLayout";
import { useSnackbar } from "../components/common/SnackbarProvider";
import {
  getHealth,
  getAllStatus,
  launchSwarm,
  cancelSwarm,
  mergeSwarm,
  cleanupSwarm,
  getTemplates,
  getTemplateContent,
  getTaskLogs,
  getConnectivity,
  getHistory,
} from "../api/swarmService";

// poll interval in ms — fast enough to feel real-time, slow enough to not hammer the API
const POLL_INTERVAL = 3000;

// status -> color/icon mapping
const STATUS_CONFIG = {
  pending: { color: "default", icon: <PendingIcon fontSize="small" />, label: "Pending" },
  blocked: { color: "default", icon: <PendingIcon fontSize="small" />, label: "Blocked" },
  queued: { color: "info", icon: <PendingIcon fontSize="small" />, label: "Queued" },
  running: { color: "primary", icon: <RunningIcon fontSize="small" />, label: "Running" },
  done: { color: "success", icon: <DoneIcon fontSize="small" />, label: "Done" },
  failed: { color: "error", icon: <FailedIcon fontSize="small" />, label: "Failed" },
  needs_review: { color: "warning", icon: <ReviewIcon fontSize="small" />, label: "Needs Review" },
  merged: { color: "success", icon: <MergeIcon fontSize="small" />, label: "Merged" },
  cancelled: { color: "default", icon: <CloseIcon fontSize="small" />, label: "Cancelled" },
};


// ─── Main Page ───────────────────────────────────────────────────────

const SwarmPage = () => {
  const theme = useTheme();
  const { showMessage } = useSnackbar();

  // state
  const [serviceOnline, setServiceOnline] = useState(false);
  const [connectivity, setConnectivity] = useState(null);
  const [swarms, setSwarms] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // dialogs
  const [launchOpen, setLaunchOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [logsData, setLogsData] = useState({ taskId: "", logs: "" });
  const [expandedSwarm, setExpandedSwarm] = useState(null);

  // launch form
  const [planPath, setPlanPath] = useState("");
  const [flightMode, setFlightMode] = useState(false);
  const [maxAgents, setMaxAgents] = useState(5);
  const [autoMerge, setAutoMerge] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [launching, setLaunching] = useState(false);

  // ─── Data Fetching ─────────────────────────────────────────────

  const fetchStatus = useCallback(async () => {
    try {
      const res = await getAllStatus();
      if (res.success !== false) {
        const data = res.data || res;
        setSwarms(data.swarms || []);
        setServiceOnline(true);
        setError(null);
      }
    } catch {
      setServiceOnline(false);
    }
  }, []);

  const fetchConnectivity = useCallback(async () => {
    try {
      const res = await getConnectivity();
      if (res.success !== false) {
        setConnectivity(res.data || res);
      }
    } catch {
      // service offline
    }
  }, []);

  const fetchTemplates = useCallback(async () => {
    try {
      const res = await getTemplates();
      if (res.success !== false) {
        const data = res.data || res;
        setTemplates(data.templates || []);
      }
    } catch {
      // silent
    }
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await getHistory(10);
      if (res.success !== false) {
        const data = res.data || res;
        setHistory(data.swarms || []);
      }
    } catch {
      // silent
    }
  }, []);

  // initial load
  useEffect(() => {
    const init = async () => {
      setLoading(true);
      await Promise.all([fetchStatus(), fetchConnectivity(), fetchTemplates(), fetchHistory()]);
      setLoading(false);
    };
    init();
  }, [fetchStatus, fetchConnectivity, fetchTemplates, fetchHistory]);

  // polling for active swarms
  useEffect(() => {
    const hasActive = swarms.some((s) => s.status === "running");
    if (!hasActive && !loading) return;

    const interval = setInterval(fetchStatus, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [swarms, loading, fetchStatus]);

  // ─── Actions ───────────────────────────────────────────────────

  const handleLaunch = async () => {
    const path = planPath.trim();
    if (!path) {
      showMessage("Enter a plan file path", "warning");
      return;
    }

    setLaunching(true);
    try {
      const res = await launchSwarm({
        planPath: path,
        flightMode,
        maxAgents,
        autoMerge,
      });
      if (res.success !== false) {
        const data = res.data || res;
        showMessage(`Swarm launched: ${data.swarm_id}`, "success");
        setLaunchOpen(false);
        setPlanPath("");
        setSelectedTemplate("");
        await fetchStatus();
      } else {
        showMessage(res.message || "Launch failed", "error");
      }
    } catch (err) {
      showMessage(err.message || "Launch failed", "error");
    } finally {
      setLaunching(false);
    }
  };

  const handleCancel = async (swarmId) => {
    if (!swarmId) { showMessage("No swarm ID", "warning"); return; }
    try {
      const res = await cancelSwarm(swarmId);
      showMessage(res.message || "Cancelled", "success");
      await fetchStatus();
    } catch (err) {
      showMessage(err.message || "Cancel failed", "error");
    }
  };

  const handleMerge = async (swarmId) => {
    if (!swarmId) { showMessage("No swarm ID", "warning"); return; }
    try {
      const res = await mergeSwarm(swarmId);
      const data = res.data || res;
      showMessage(
        `Merged ${data.merged || 0} branches, ${data.conflicts || 0} conflicts`,
        data.conflicts > 0 ? "warning" : "success"
      );
      await fetchStatus();
    } catch (err) {
      showMessage(err.message || "Merge failed", "error");
    }
  };

  const handleCleanup = async (swarmId) => {
    if (!swarmId) { showMessage("No swarm ID", "warning"); return; }
    try {
      const res = await cleanupSwarm(swarmId, { deleteBranches: true });
      showMessage(res.message || "Cleaned up", "success");
      await fetchStatus();
      await fetchHistory();
    } catch (err) {
      showMessage(err.message || "Cleanup failed", "error");
    }
  };

  const handleViewLogs = async (swarmId, taskId) => {
    try {
      const res = await getTaskLogs(swarmId, taskId);
      const data = res.data || res;
      setLogsData({ taskId, logs: data.logs || "(no logs)" });
      setLogsOpen(true);
    } catch (err) {
      showMessage("Could not fetch logs", "error");
    }
  };

  const handleTemplateSelect = async (filename) => {
    setSelectedTemplate(filename);
    try {
      const res = await getTemplateContent(filename);
      const data = res.data || res;
      // set the full path for launching
      setPlanPath(data.filename ? `plugins/swarm/templates/${data.filename}` : "");
    } catch {
      // silent
    }
  };

  // ─── Render ────────────────────────────────────────────────────

  const isOnline = connectivity?.online ?? false;

  return (
    <PageLayout
      title="Swarm"
      variant="standard"
      actions={
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip
            icon={isOnline ? <OnlineIcon /> : <OfflineIcon />}
            label={isOnline ? "Online" : "Offline"}
            color={isOnline ? "success" : "default"}
            size="small"
            variant="outlined"
          />
          {!serviceOnline && (
            <Chip label="Service Offline" color="error" size="small" variant="outlined" />
          )}
          <Button
            variant="outlined"
            size="small"
            startIcon={<RefreshIcon />}
            onClick={() => {
              fetchStatus();
              fetchConnectivity();
              fetchHistory();
            }}
          >
            Refresh
          </Button>
          <Button
            variant="contained"
            startIcon={<LaunchIcon />}
            onClick={() => setLaunchOpen(true)}
            disabled={!serviceOnline}
          >
            Launch Swarm
          </Button>
        </Stack>
      }
    >
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {/* Active Swarms */}
      {swarms.length > 0 && (
        <Box sx={{ mb: 4 }}>
          <Typography variant="subtitle1" fontWeight={600} sx={{ mb: 2 }}>
            Active Swarms
          </Typography>
          <Stack spacing={2}>
            {swarms.map((swarm) => (
              <SwarmCard
                key={swarm.swarm_id}
                swarm={swarm}
                expanded={expandedSwarm === swarm.swarm_id}
                onToggleExpand={() =>
                  setExpandedSwarm(
                    expandedSwarm === swarm.swarm_id ? null : swarm.swarm_id
                  )
                }
                onCancel={handleCancel}
                onMerge={handleMerge}
                onCleanup={handleCleanup}
                onViewLogs={handleViewLogs}
                theme={theme}
              />
            ))}
          </Stack>
        </Box>
      )}

      {/* Templates */}
      {templates.length > 0 && swarms.length === 0 && (
        <Box sx={{ mb: 4 }}>
          <Typography variant="subtitle1" fontWeight={600} sx={{ mb: 2 }}>
            Quick Launch Templates
          </Typography>
          <Grid container spacing={2}>
            {templates.map((tmpl) => (
              <Grid item xs={12} sm={6} md={3} key={tmpl.filename}>
                <Card
                  sx={{
                    cursor: "pointer",
                    border: "1px solid",
                    borderColor: "divider",
                    transition: "all 0.2s",
                    "&:hover": {
                      borderColor: "primary.main",
                      boxShadow: theme.shadows[2],
                    },
                  }}
                  onClick={() => {
                    handleTemplateSelect(tmpl.filename);
                    setLaunchOpen(true);
                  }}
                >
                  <CardContent>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                      <TemplateIcon fontSize="small" color="primary" />
                      <Typography variant="subtitle2" fontWeight={600}>
                        {tmpl.title}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                      {tmpl.description}
                    </Typography>
                    <Chip
                      label={`${tmpl.task_count} tasks`}
                      size="small"
                      variant="outlined"
                    />
                  </CardContent>
                </Card>
              </Grid>
            ))}
          </Grid>
        </Box>
      )}

      {/* History */}
      {history.length > 0 && (
        <Box sx={{ mb: 4 }}>
          <Typography variant="subtitle1" fontWeight={600} sx={{ mb: 2 }}>
            Recent Swarms
          </Typography>
          <Stack spacing={1}>
            {history.map((h) => (
              <Paper
                key={h.swarm_id}
                sx={{
                  p: 2,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                >
                  <Box>
                    <Typography variant="body2" fontWeight={600}>
                      {h.swarm_id}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {h.task_count} tasks
                      {h.flight_mode ? " | Flight Mode" : ""}
                      {h.total_cost_usd > 0
                        ? ` | $${h.total_cost_usd.toFixed(2)}`
                        : " | Free (local)"}
                    </Typography>
                  </Box>
                  <IconButton
                    size="small"
                    onClick={() => handleCleanup(h.swarm_id)}
                  >
                    <Tooltip title="Clean up">
                      <CloseIcon fontSize="small" />
                    </Tooltip>
                  </IconButton>
                </Stack>
              </Paper>
            ))}
          </Stack>
        </Box>
      )}

      {/* Empty state */}
      {!loading && swarms.length === 0 && history.length === 0 && (
        <Paper
          sx={{
            p: 6,
            textAlign: "center",
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <FlightIcon
            sx={{ fontSize: 64, color: "text.secondary", mb: 2 }}
          />
          <Typography variant="h6" sx={{ mb: 1 }}>
            No Swarms Running
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Launch a swarm of AI agents to work on your codebase in parallel.
            {!serviceOnline && " Start the swarm plugin first."}
          </Typography>
          <Button
            variant="contained"
            startIcon={<LaunchIcon />}
            onClick={() => setLaunchOpen(true)}
            disabled={!serviceOnline}
          >
            Launch Your First Swarm
          </Button>
        </Paper>
      )}

      {/* ─── Launch Dialog ──────────────────────────────────────── */}
      <Dialog
        open={launchOpen}
        onClose={() => setLaunchOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Launch Swarm</DialogTitle>
        <DialogContent>
          <Stack spacing={2.5} sx={{ mt: 1 }}>
            {templates.length > 0 && (
              <FormControl fullWidth size="small">
                <InputLabel>Template (optional)</InputLabel>
                <Select
                  value={selectedTemplate}
                  label="Template (optional)"
                  onChange={(e) => handleTemplateSelect(e.target.value)}
                >
                  <MenuItem value="">
                    <em>Custom plan file</em>
                  </MenuItem>
                  {templates.map((t) => (
                    <MenuItem key={t.filename} value={t.filename}>
                      {t.title} ({t.task_count} tasks)
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}

            <TextField
              label="Plan file path"
              value={planPath}
              onChange={(e) => setPlanPath(e.target.value)}
              fullWidth
              size="small"
              placeholder="path/to/plan.md or select a template above"
              helperText="Relative to GUAARDVARK_ROOT or absolute path"
            />

            <TextField
              label="Max concurrent agents"
              type="number"
              value={maxAgents}
              onChange={(e) =>
                setMaxAgents(Math.max(1, parseInt(e.target.value) || 1))
              }
              fullWidth
              size="small"
              inputProps={{ min: 1, max: 20 }}
            />

            <Stack direction="row" spacing={2}>
              <FormControlLabel
                control={
                  <Switch
                    checked={flightMode}
                    onChange={(e) => setFlightMode(e.target.checked)}
                  />
                }
                label={
                  <Stack direction="row" spacing={0.5} alignItems="center">
                    <FlightIcon fontSize="small" />
                    <span>Flight Mode</span>
                  </Stack>
                }
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={autoMerge}
                    onChange={(e) => setAutoMerge(e.target.checked)}
                  />
                }
                label="Auto-merge"
              />
            </Stack>

            {flightMode && (
              <Alert severity="info" variant="outlined">
                Flight Mode: offline backends only (Ollama). Conflicts will be
                auto-serialized.
              </Alert>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setLaunchOpen(false)} disabled={launching}>
            Cancel
          </Button>
          <Button
            onClick={handleLaunch}
            variant="contained"
            disabled={launching || !planPath.trim()}
            startIcon={
              launching ? (
                <CircularProgress size={18} />
              ) : flightMode ? (
                <FlightIcon />
              ) : (
                <LaunchIcon />
              )
            }
          >
            {flightMode ? "Launch (Flight Mode)" : "Launch"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ─── Logs Dialog ────────────────────────────────────────── */}
      <Dialog
        open={logsOpen}
        onClose={() => setLogsOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          Agent Logs: {logsData.taskId}
          <IconButton
            onClick={() => setLogsOpen(false)}
            sx={{ position: "absolute", right: 8, top: 8 }}
          >
            <CloseIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent>
          <Box
            sx={{
              fontFamily: "monospace",
              fontSize: "0.8rem",
              whiteSpace: "pre-wrap",
              bgcolor: "background.default",
              p: 2,
              borderRadius: 1,
              maxHeight: 500,
              overflow: "auto",
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            {logsData.logs || "(no output yet)"}
          </Box>
        </DialogContent>
      </Dialog>
    </PageLayout>
  );
};


// ─── Swarm Card Component ────────────────────────────────────────────

const SwarmCard = ({
  swarm,
  expanded,
  onToggleExpand,
  onCancel,
  onMerge,
  onCleanup,
  onViewLogs,
  theme,
}) => {
  const tasks = swarm.tasks || [];
  const isRunning = swarm.status === "running";
  const statusCounts = swarm.tasks_by_status || {};
  const elapsed = swarm.elapsed_seconds
    ? formatElapsed(swarm.elapsed_seconds)
    : "-";

  const doneCount =
    (statusCounts.done || 0) +
    (statusCounts.merged || 0);
  const totalCount = tasks.length;
  const progress = totalCount > 0 ? (doneCount / totalCount) * 100 : 0;

  return (
    <Paper
      sx={{
        border: "1px solid",
        borderColor: isRunning ? "primary.main" : "divider",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          p: 2,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          cursor: "pointer",
        }}
        onClick={onToggleExpand}
      >
        <Box sx={{ flex: 1 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="subtitle2" fontWeight={600}>
              {swarm.swarm_id}
            </Typography>
            {swarm.flight_mode && (
              <Chip
                icon={<FlightIcon />}
                label="Flight Mode"
                size="small"
                color="info"
                variant="outlined"
              />
            )}
            <Chip
              label={
                swarm.status === "failed" ? "Failed" :
                isRunning ? "Running" : "Completed"
              }
              size="small"
              color={
                swarm.status === "failed" ? "error" :
                isRunning ? "primary" : "default"
              }
              variant={isRunning ? "filled" : "outlined"}
            />
          </Stack>

          {/* Error message for failed swarms */}
          {swarm.error && (
            <Alert severity="error" variant="outlined" sx={{ mt: 1, py: 0 }}>
              <Typography variant="caption">{swarm.error}</Typography>
            </Alert>
          )}

          {/* Progress bar */}
          <Box sx={{ mt: 1, display: "flex", alignItems: "center", gap: 2 }}>
            <LinearProgress
              variant="determinate"
              value={progress}
              sx={{ flex: 1, height: 6, borderRadius: 3 }}
            />
            <Typography variant="caption" color="text.secondary" sx={{ minWidth: 80 }}>
              {doneCount}/{totalCount} tasks
            </Typography>
          </Box>

          {/* Stats row */}
          <Stack direction="row" spacing={2} sx={{ mt: 1 }}>
            <StatChip icon={<TimeIcon />} label={elapsed} />
            <StatChip
              icon={<CostIcon />}
              label={
                swarm.total_cost_usd > 0
                  ? `$${swarm.total_cost_usd.toFixed(2)}`
                  : "Free"
              }
            />
            <StatChip
              icon={<SpeedIcon />}
              label={`${swarm.running_count || 0} active`}
            />
            {swarm.disk_usage_mb > 0 && (
              <StatChip
                icon={<BranchIcon />}
                label={`${swarm.disk_usage_mb} MB`}
              />
            )}
          </Stack>
        </Box>

        <IconButton>
          {expanded ? <CollapseIcon /> : <ExpandIcon />}
        </IconButton>
      </Box>

      {/* Expanded task list */}
      <Collapse in={expanded}>
        <Divider />
        <Box sx={{ p: 2 }}>
          {/* Actions */}
          <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
            {isRunning && (
              <Button
                size="small"
                color="error"
                variant="outlined"
                startIcon={<CancelIcon />}
                onClick={() => onCancel(swarm.swarm_id)}
              >
                Cancel
              </Button>
            )}
            {!isRunning && tasks.length > 0 && (
              <Button
                size="small"
                variant="outlined"
                startIcon={<MergeIcon />}
                onClick={() => onMerge(swarm.swarm_id)}
              >
                Merge All
              </Button>
            )}
            {!isRunning && (
              <Button
                size="small"
                variant="outlined"
                startIcon={<CloseIcon />}
                onClick={() => onCleanup(swarm.swarm_id)}
              >
                Clean Up
              </Button>
            )}
          </Stack>

          {/* Task grid */}
          <Grid container spacing={1.5}>
            {tasks.map((task) => (
              <Grid item xs={12} sm={6} md={4} key={task.id}>
                <TaskCard
                  task={task}
                  swarmId={swarm.swarm_id}
                  onViewLogs={onViewLogs}
                  theme={theme}
                />
              </Grid>
            ))}
          </Grid>
        </Box>
      </Collapse>
    </Paper>
  );
};


// ─── Task Card Component ─────────────────────────────────────────────

const TaskCard = ({ task, swarmId, onViewLogs, theme }) => {
  const cfg = STATUS_CONFIG[task.status] || STATUS_CONFIG.pending;

  return (
    <Card
      variant="outlined"
      sx={{
        height: "100%",
        borderColor:
          task.status === "running"
            ? "primary.main"
            : task.status === "failed"
              ? "error.main"
              : "divider",
      }}
    >
      <CardContent sx={{ pb: 1, "&:last-child": { pb: 1 } }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
          sx={{ mb: 0.5 }}
        >
          <Typography variant="body2" fontWeight={600} sx={{ flex: 1 }}>
            {task.title}
          </Typography>
          <Chip
            icon={cfg.icon}
            label={cfg.label}
            size="small"
            color={cfg.color}
            variant="outlined"
          />
        </Stack>

        {task.backend_name && (
          <Typography variant="caption" color="text.secondary">
            {task.backend_name} | {task.elapsed || "-"}
            {task.estimated_cost_usd > 0 &&
              ` | $${task.estimated_cost_usd.toFixed(2)}`}
          </Typography>
        )}

        {task.error && (
          <Alert severity="error" variant="outlined" sx={{ mt: 1, py: 0 }}>
            <Typography variant="caption">{task.error}</Typography>
          </Alert>
        )}
      </CardContent>
      <CardActions sx={{ pt: 0 }}>
        {(task.status === "running" || task.status === "done" || task.status === "failed") && (
          <Tooltip title="View logs">
            <IconButton
              size="small"
              onClick={() => onViewLogs(swarmId, task.id)}
            >
              <LogsIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        {task.branch_name && (
          <Tooltip title={task.branch_name}>
            <Chip
              icon={<BranchIcon />}
              label={task.id}
              size="small"
              variant="outlined"
              sx={{ maxWidth: 150 }}
            />
          </Tooltip>
        )}
      </CardActions>
    </Card>
  );
};


// ─── Helpers ─────────────────────────────────────────────────────────

const StatChip = ({ icon, label }) => (
  <Stack direction="row" spacing={0.5} alignItems="center">
    {React.cloneElement(icon, {
      sx: { fontSize: 14, color: "text.secondary" },
    })}
    <Typography variant="caption" color="text.secondary">
      {label}
    </Typography>
  </Stack>
);

const formatElapsed = (seconds) => {
  if (!seconds || seconds < 0) return "-";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
};


export default SwarmPage;
