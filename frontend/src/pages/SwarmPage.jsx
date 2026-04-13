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
  Edit as EditIcon,
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
  Add as AddIcon,
} from "@mui/icons-material";
import { useTheme } from "@mui/material/styles";

import PageLayout from "../components/layout/PageLayout";
import { useSnackbar } from "../components/common/SnackbarProvider";
import SwarmGraph from "../components/swarm/SwarmGraph";
import {
  getHealth,
  getAllStatus,
  launchSwarm,
  cancelSwarm,
  mergeSwarm,
  cleanupSwarm,
  getTemplates,
  getTemplateContent,
  saveTemplate,
  getTaskLogs,
  getConnectivity,
  getHistory,
  swarmService,
} from "../api/swarmService";

// poll interval in ms — fallback if Socket.IO fails or for initial sync
const POLL_INTERVAL = 10000;

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
  const [editorOpen, setEditorOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [logsData, setLogsData] = useState({ taskId: "", logs: "" });
  const [expandedSwarm, setExpandedSwarm] = useState(null);

  // editor state
  const [editorFilename, setEditorFilename] = useState("new_plan.md");
  const [editorContent, setEditorContent] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [isAiGenerating, setIsAiGenerating] = useState(false);

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

    // Socket.IO real-time updates
    swarmService.connect();
    swarmService.onEvent((event) => {
      console.log("Real-time swarm event:", event);
      // Refresh status when any event happens
      fetchStatus();
      
      // If it's a completion event, also refresh history
      if (event.event_type === "swarm_completed" || event.event_type === "swarm_cancelled") {
        fetchHistory();
      }
    });

    return () => {
      swarmService.disconnect();
    };
  }, [fetchStatus, fetchConnectivity, fetchTemplates, fetchHistory]);

  // polling as fallback/safety only
  useEffect(() => {
    const interval = setInterval(fetchStatus, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchStatus]);

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

  const handleOpenEditor = async (filename = "") => {
    if (filename) {
      try {
        const res = await getTemplateContent(filename);
        const data = res.data || res;
        setEditorFilename(filename);
        setEditorContent(data.content || "");
      } catch (err) {
        showMessage("Failed to load template", "error");
        return;
      }
    } else {
      setEditorFilename("new_plan.md");
      setEditorContent("# New Swarm Plan\n\n## Task 1\nAssign to: any\nFiles: []\nDeps: []\nDescription: Do something...");
    }
    setEditorOpen(true);
  };

  const handleSavePlan = async () => {
    if (!editorFilename.trim() || !editorContent.trim()) {
      showMessage("Filename and content are required", "warning");
      return;
    }
    
    setIsSaving(true);
    try {
      const res = await saveTemplate(editorFilename, editorContent);
      if (res.success !== false) {
        showMessage("Plan saved to templates", "success");
        await fetchTemplates();
        setPlanPath(`plugins/swarm/templates/${res.filename || editorFilename}`);
        setEditorOpen(false);
      } else {
        showMessage(res.message || "Save failed", "error");
      }
    } catch (err) {
      showMessage(err.message || "Save failed", "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleAiPlanBuilder = async () => {
    if (!aiPrompt.trim()) {
      showMessage("Enter what you want to achieve", "warning");
      return;
    }

    setIsAiGenerating(true);
    try {
      // Use the main chat API to generate a plan.md structure
      const sessionId = `swarm_builder_${Math.random().toString(36).substring(7)}`;
      const response = await fetch("/api/enhanced-chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          message: `Generate a swarm plan.md file for the following request: ${aiPrompt}. 
          The format must be markdown with ## headers for each task. 
          Each task should have:
          - Assign to: (research_agent, code_agent, or any)
          - Files: (list of files)
          - Deps: (IDs of tasks it depends on)
          - Description: (what to do)
          
          Provide ONLY the markdown content, no extra talk.`,
          chat_mode: "instinct"
        }),
      });

      const result = await response.json();
      // The enhanced-chat API returns success_response which wraps data in a 'data' field
      const responseData = result.data || result;
      
      if (responseData.response) {
        // Strip markdown code fences if present
        let content = responseData.response.trim();
        if (content.startsWith("```markdown")) content = content.split("```markdown")[1].split("```")[0];
        else if (content.startsWith("```")) content = content.split("```")[1].split("```")[0];
        
        setEditorContent(content);
        showMessage("Plan generated by AI!", "success");
      }
    } catch (err) {
      showMessage("AI generation failed", "error");
    } finally {
      setIsAiGenerating(false);
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
            variant="outlined"
            size="small"
            startIcon={<AddIcon />}
            onClick={() => handleOpenEditor()}
            disabled={!serviceOnline}
          >
            Create New Plan
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

      {/* ─── Plan Editor Dialog ──────────────────────────────────── */}
      <Dialog
        open={editorOpen}
        onClose={() => !isSaving && setEditorOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>Swarm Plan Editor</span>
          <Chip label={editorFilename} size="small" variant="outlined" />
        </DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2} sx={{ mb: 3 }}>
            <Typography variant="body2" color="text.secondary">
              Describe your task, and AI will generate the plan.md structure for you.
            </Typography>
            <Stack direction="row" spacing={1}>
              <TextField
                fullWidth
                size="small"
                placeholder="e.g., 'Refactor all API endpoints to use the new Response model'"
                value={aiPrompt}
                onChange={(e) => setAiPrompt(e.target.value)}
                disabled={isAiGenerating}
              />
              <Button 
                variant="outlined" 
                onClick={handleAiPlanBuilder}
                disabled={isAiGenerating || !aiPrompt.trim()}
                startIcon={isAiGenerating ? <CircularProgress size={16} /> : <SpeedIcon />}
                sx={{ whiteSpace: "nowrap" }}
              >
                AI Suggest Plan
              </Button>
            </Stack>
          </Stack>

          <TextField
            label="Filename (e.g. migrate_api.md)"
            value={editorFilename}
            onChange={(e) => setEditorFilename(e.target.value)}
            fullWidth
            size="small"
            sx={{ mb: 2 }}
          />
          
          <TextField
            label="Plan Content (Markdown)"
            value={editorContent}
            onChange={(e) => setEditorContent(e.target.value)}
            fullWidth
            multiline
            rows={15}
            placeholder="# Title\n\n## Task 1..."
            sx={{ 
              fontFamily: "monospace",
              "& .MuiInputBase-input": { fontFamily: "monospace", fontSize: "0.85rem" }
            }}
          />
          
          <Box sx={{ mt: 1 }}>
            <Typography variant="caption" color="text.secondary">
              Format: Use <b>## Task Name</b> for each task. Add <b>Deps: [1]</b> for dependencies.
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditorOpen(false)} disabled={isSaving}>
            Cancel
          </Button>
          <Button
            onClick={handleSavePlan}
            variant="contained"
            disabled={isSaving || !editorContent.trim()}
            startIcon={isSaving ? <CircularProgress size={18} /> : <EditIcon />}
          >
            Save to Templates
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
          {/* Visual DAG */}
          <Box sx={{ mb: 3 }}>
            <Typography variant="caption" color="text.secondary" fontWeight={600} sx={{ mb: 1, display: "block" }}>
              DEPENDENCY GRAPH
            </Typography>
            <SwarmGraph tasks={tasks} height={300} />
          </Box>

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
