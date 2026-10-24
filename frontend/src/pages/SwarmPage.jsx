// frontend/src/pages/SwarmPage.jsx
// Swarm Orchestrator dashboard — real-time agent monitoring, launch, and management

import React, { useState, useEffect, useCallback } from "react";
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
  Tabs,
  Tab,
} from "@mui/material";
import {
  PlayArrow as LaunchIcon,
  Stop as CancelIcon,
  Refresh as RefreshIcon,
  MergeType as MergeIcon,
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
  CheckCircle as DoneIcon,
  Error as FailedIcon,
  HourglassEmpty as PendingIcon,
  Sync as RunningIcon,
  RateReview as ReviewIcon,
  Close as CloseIcon,
  Add as AddIcon,
} from "@mui/icons-material";
import { useTheme } from "@mui/material/styles";

import { useUiTranslation, translate, Trans } from "../i18n";
import PageLayout from "../components/layout/PageLayout";
import { useSnackbar } from "../components/common/SnackbarProvider";
import EntityContextMenu from "../components/common/EntityContextMenu";
import useContextMenu from "../hooks/useContextMenu";
import copyText from "../utils/copyText";
import SwarmGraph from "../components/swarm/SwarmGraph";
import {
  getAllStatus,
  launchSwarm,
  cancelSwarm,
  mergeSwarm,
  cleanupSwarm,
  getTemplates,
  getTemplateContent,
  saveTemplate,
  getTaskLogs,
  getTaskDiff,
  getConnectivity,
  getHistory,
  swarmService,
} from "../api/swarmService";

// poll interval in ms — fallback if Socket.IO fails or for initial sync
const POLL_INTERVAL = 10000;

// status -> color/icon mapping; a function so the labels follow the language
const statusConfig = () => ({
  pending: { color: "default", icon: <PendingIcon fontSize="small" />, label: translate("swarm.taskStatus.pending") },
  blocked: { color: "default", icon: <PendingIcon fontSize="small" />, label: translate("swarm.taskStatus.blocked") },
  queued: { color: "info", icon: <PendingIcon fontSize="small" />, label: translate("swarm.taskStatus.queued") },
  running: { color: "primary", icon: <RunningIcon fontSize="small" />, label: translate("swarm.taskStatus.running") },
  done: { color: "success", icon: <DoneIcon fontSize="small" />, label: translate("swarm.taskStatus.done") },
  failed: { color: "error", icon: <FailedIcon fontSize="small" />, label: translate("swarm.taskStatus.failed") },
  needs_review: { color: "warning", icon: <ReviewIcon fontSize="small" />, label: translate("swarm.taskStatus.needsReview") },
  merged: { color: "success", icon: <MergeIcon fontSize="small" />, label: translate("swarm.taskStatus.merged") },
  cancelled: { color: "default", icon: <CloseIcon fontSize="small" />, label: translate("swarm.taskStatus.cancelled") },
  // The agent exited cleanly but committed and changed nothing.
  no_changes: { color: "warning", icon: <ReviewIcon fontSize="small" />, label: translate("swarm.taskStatus.noChanges") },
  // The agent ran past agent_timeout_minutes and was stopped.
  timed_out: { color: "error", icon: <FailedIcon fontSize="small" />, label: translate("swarm.taskStatus.timedOut") },
});


// Task states whose logs and diff are worth opening.
const VIEWABLE_TASK_STATUSES = ["running", "done", "failed", "needs_review", "no_changes", "timed_out"];

// ─── Main Page ───────────────────────────────────────────────────────

const SwarmPage = () => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const { showMessage } = useSnackbar();
  const itemMenu = useContextMenu();

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
  const [taskViewOpen, setTaskViewOpen] = useState(false);
  // logsNote/diffNote ("loading", "none", "failed") pick the placeholder shown
  // instead of logs or a diff; its text is translated at render.
  const [taskViewData, setTaskViewData] = useState({ 
    taskId: "", 
    swarmId: "", 
    logs: "", 
    logsNote: null, 
    diff: "", 
    diffNote: null, 
    tab: 0 
  });
  const [isDiffLoading, setIsDiffLoading] = useState(false);
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

  // Template dropdown + template card share this handler. Picking a template
  // updates the plan-path field so the user can see exactly what will be
  // launched; picking the empty "Custom plan file" option clears the
  // selection but leaves any manually typed path alone.
  const handleTemplateSelect = (filename) => {
    setSelectedTemplate(filename);
    if (filename) {
      setPlanPath(`plugins/swarm/templates/${filename}`);
    }
  };

  const handleLaunch = async () => {
    const path = planPath.trim();
    if (!path) {
      showMessage(t("swarm.messages.enterPlanPath"), "warning");
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
        showMessage(t("swarm.messages.launched", { id: data.swarm_id }), "success");
        setLaunchOpen(false);
        setPlanPath("");
        setSelectedTemplate("");
        await fetchStatus();
      } else {
        showMessage(res.message || t("swarm.messages.launchFailed"), "error");
      }
    } catch (err) {
      showMessage(err.message || t("swarm.messages.launchFailed"), "error");
    } finally {
      setLaunching(false);
    }
  };

  const handleCancel = async (swarmId) => {
    if (!swarmId) { showMessage(t("swarm.messages.noSwarmId"), "warning"); return; }
    try {
      const res = await cancelSwarm(swarmId);
      showMessage(res.message || t("swarm.messages.cancelled"), "success");
      await fetchStatus();
    } catch (err) {
      showMessage(err.message || t("swarm.messages.cancelFailed"), "error");
    }
  };

  const handleMerge = async (swarmId) => {
    if (!swarmId) { showMessage(t("swarm.messages.noSwarmId"), "warning"); return; }
    try {
      const res = await mergeSwarm(swarmId);
      const data = res.data || res;
      showMessage(
        t("swarm.messages.merged", { merged: data.merged || 0, conflicts: data.conflicts || 0 }),
        data.conflicts > 0 ? "warning" : "success"
      );
      await fetchStatus();
    } catch (err) {
      showMessage(err.message || t("swarm.messages.mergeFailed"), "error");
    }
  };

  const handleCleanup = async (swarmId) => {
    if (!swarmId) { showMessage(t("swarm.messages.noSwarmId"), "warning"); return; }
    try {
      const res = await cleanupSwarm(swarmId, { deleteBranches: true });
      showMessage(res.message || t("swarm.messages.cleanedUp"), "success");
      await fetchStatus();
      await fetchHistory();
    } catch (err) {
      showMessage(err.message || t("swarm.messages.cleanupFailed"), "error");
    }
  };

  const handleViewTask = async (swarmId, taskId, initialTab = 0) => {
    setTaskViewData({ taskId, swarmId, logs: "", logsNote: "loading", diff: "", diffNote: null, tab: initialTab });
    setTaskViewOpen(true);
    
    try {
      // Always fetch logs first
      const logsRes = await getTaskLogs(swarmId, taskId);
      const logsData = logsRes.data || logsRes;
      
      setTaskViewData(prev => ({ 
        ...prev, 
        logs: logsData.logs || "", 
        logsNote: logsData.logs ? null : "none" 
      }));

      // If opening diff tab, fetch it immediately
      if (initialTab === 1) {
        fetchDiff(swarmId, taskId);
      }
    } catch (err) {
      showMessage(t("swarm.messages.logsFailed"), "error");
    }
  };

  const fetchDiff = async (swarmId, taskId) => {
    setIsDiffLoading(true);
    try {
      const res = await getTaskDiff(swarmId, taskId);
      const data = res.data || res;
      setTaskViewData(prev => ({ 
        ...prev, 
        diff: data.diff || "", 
        diffNote: data.diff ? null : "none" 
      }));
    } catch (err) {
      setTaskViewData(prev => ({ ...prev, diff: "", diffNote: "failed" }));
    } finally {
      setIsDiffLoading(false);
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
        showMessage(t("swarm.messages.templateLoadFailed"), "error");
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
      showMessage(t("swarm.messages.filenameAndContentRequired"), "warning");
      return;
    }
    
    setIsSaving(true);
    try {
      const res = await saveTemplate(editorFilename, editorContent);
      if (res.success !== false) {
        showMessage(t("swarm.messages.planSaved"), "success");
        await fetchTemplates();
        setPlanPath(`plugins/swarm/templates/${res.filename || editorFilename}`);
        setEditorOpen(false);
      } else {
        showMessage(res.message || t("swarm.messages.saveFailed"), "error");
      }
    } catch (err) {
      showMessage(err.message || t("swarm.messages.saveFailed"), "error");
    } finally {
      setIsSaving(false);
    }
  };

  // Right-click items for a swarm, a task, a past swarm or a template; each
  // mirrors the buttons and conditions shown on that item.
  const itemMenuActions = () => {
    const p = itemMenu.payload;
    if (!p) return [];
    if (p.kind === "swarm") {
      const { swarm } = p;
      const isRunning = swarm.status === "running";
      const isExpanded = expandedSwarm === swarm.swarm_id;
      const actions = [
        isRunning && { label: t("swarm.actions.cancel"), onClick: () => handleCancel(swarm.swarm_id), color: "error.main" },
        !isRunning && (swarm.tasks || []).length > 0 && { label: t("swarm.actions.mergeAll"), onClick: () => handleMerge(swarm.swarm_id) },
        !isRunning && { label: t("swarm.actions.cleanUp"), onClick: () => handleCleanup(swarm.swarm_id) },
      ].filter(Boolean);
      if (actions.length) actions[0] = { ...actions[0], dividerBefore: true };
      return [
        {
          label: isExpanded ? t("swarm.menu.collapse") : t("swarm.menu.expand"),
          onClick: () => setExpandedSwarm(isExpanded ? null : swarm.swarm_id),
        },
        ...actions,
        { label: t("swarm.menu.copySwarmId"), onClick: () => copyText(swarm.swarm_id), dividerBefore: true },
      ];
    }
    if (p.kind === "task") {
      const { swarmId, task } = p;
      const canView = VIEWABLE_TASK_STATUSES.includes(task.status);
      return [
        canView && { label: t("swarm.menu.viewLogs"), onClick: () => handleViewTask(swarmId, task.id) },
        canView && { label: t("swarm.menu.viewDiff"), onClick: () => handleViewTask(swarmId, task.id, 1) },
        task.branch_name && {
          label: t("swarm.menu.copyBranchName"),
          onClick: () => copyText(task.branch_name),
          dividerBefore: canView,
        },
        { label: t("swarm.menu.copyTaskId"), onClick: () => copyText(task.id), dividerBefore: canView && !task.branch_name },
      ];
    }
    if (p.kind === "history") {
      return [
        { label: t("swarm.actions.cleanUp"), onClick: () => handleCleanup(p.h.swarm_id) },
        { label: t("swarm.menu.copySwarmId"), onClick: () => copyText(p.h.swarm_id), dividerBefore: true },
      ];
    }
    if (p.kind === "template") {
      return [
        {
          label: t("swarm.menu.launch"),
          onClick: () => {
            handleTemplateSelect(p.tmpl.filename);
            setLaunchOpen(true);
          },
        },
      ];
    }
    return [];
  };

  const handleAiPlanBuilder = async () => {
    if (!aiPrompt.trim()) {
      showMessage(t("swarm.messages.enterGoal"), "warning");
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
          - Assign to: any
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
        showMessage(t("swarm.messages.planGenerated"), "success");
      }
    } catch (err) {
      showMessage(t("swarm.messages.aiGenerationFailed"), "error");
    } finally {
      setIsAiGenerating(false);
    }
  };

  // ─── Render ────────────────────────────────────────────────────

  const isOnline = connectivity?.online ?? false;

  return (
    <PageLayout
      title={t("swarm.title")}
      variant="standard"
      actions={
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip
            icon={isOnline ? <OnlineIcon /> : <OfflineIcon />}
            label={isOnline ? t("swarm.header.online") : t("swarm.header.offline")}
            color={isOnline ? "success" : "default"}
            size="small"
            variant="outlined"
          />
          {!serviceOnline && (
            <Chip label={t("swarm.header.serviceOffline")} color="error" size="small" variant="outlined" />
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
            {t("swarm.header.refresh")}
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<AddIcon />}
            onClick={() => handleOpenEditor()}
            disabled={!serviceOnline}
          >
            {t("swarm.header.createPlan")}
          </Button>
          <Button
            variant="contained"
            startIcon={<LaunchIcon />}
            onClick={() => setLaunchOpen(true)}
            disabled={!serviceOnline}
          >
            {t("swarm.header.launchSwarm")}
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
            {t("swarm.sections.activeSwarms")}
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
                onViewTask={handleViewTask}
                onItemContextMenu={itemMenu.open}
                theme={theme}
                />            ))}
          </Stack>
        </Box>
      )}

      {/* Templates */}
      {templates.length > 0 && swarms.length === 0 && (
        <Box sx={{ mb: 4 }}>
          <Typography variant="subtitle1" fontWeight={600} sx={{ mb: 2 }}>
            {t("swarm.sections.quickLaunchTemplates")}
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
                  onContextMenu={(e) => itemMenu.open(e, { kind: "template", tmpl })}
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
                      label={t("swarm.taskCount", { count: tmpl.task_count })}
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
            {t("swarm.sections.recentSwarms")}
          </Typography>
          <Stack spacing={1}>
            {history.map((h) => (
              <Paper
                key={h.swarm_id}
                onContextMenu={(e) => itemMenu.open(e, { kind: "history", h })}
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
                      {t("swarm.taskCount", { count: h.task_count })}
                      {h.flight_mode ? ` | ${t("swarm.flightMode")}` : ""}
                      {h.total_cost_usd > 0
                        ? ` | $${h.total_cost_usd.toFixed(2)}`
                        : ` | ${t("swarm.history.freeLocal")}`}
                    </Typography>
                  </Box>
                  <IconButton
                    size="small"
                    onClick={() => handleCleanup(h.swarm_id)}
                  >
                    <Tooltip title={t("swarm.history.cleanUp")}>
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
            {t("swarm.empty.title")}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            {t("swarm.empty.description")}
            {!serviceOnline && ` ${t("swarm.empty.startPlugin")}`}
          </Typography>
          <Button
            variant="contained"
            startIcon={<LaunchIcon />}
            onClick={() => setLaunchOpen(true)}
            disabled={!serviceOnline}
          >
            {t("swarm.empty.launchFirst")}
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
        <DialogTitle>{t("swarm.launchDialog.title")}</DialogTitle>
        <DialogContent>
          <Stack spacing={2.5} sx={{ mt: 1 }}>
            {templates.length > 0 && (
              <FormControl fullWidth size="small">
                <InputLabel>{t("swarm.launchDialog.template")}</InputLabel>
                <Select
                  value={selectedTemplate}
                  label={t("swarm.launchDialog.template")}
                  onChange={(e) => handleTemplateSelect(e.target.value)}
                >
                  <MenuItem value="">
                    <em>{t("swarm.launchDialog.customPlanFile")}</em>
                  </MenuItem>
                  {templates.map((tmpl) => (
                    <MenuItem key={tmpl.filename} value={tmpl.filename}>
                      {t("swarm.launchDialog.templateOption", { title: tmpl.title, count: tmpl.task_count })}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}

            <TextField
              label={t("swarm.launchDialog.planPath")}
              value={planPath}
              onChange={(e) => setPlanPath(e.target.value)}
              fullWidth
              size="small"
              placeholder={t("swarm.launchDialog.planPathPlaceholder")}
              helperText={t("swarm.launchDialog.planPathHelp")}
            />

            <TextField
              label={t("swarm.launchDialog.maxAgents")}
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
                    <span>{t("swarm.flightMode")}</span>
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
                label={t("swarm.launchDialog.autoMerge")}
              />
            </Stack>

            {flightMode && (
              <Alert severity="info" variant="outlined">
                {t("swarm.launchDialog.flightModeInfo")}
              </Alert>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setLaunchOpen(false)} disabled={launching}>
            {t("swarm.launchDialog.cancel")}
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
            {flightMode ? t("swarm.launchDialog.launchFlightMode") : t("swarm.launchDialog.launch")}
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
          <span>{t("swarm.editor.title")}</span>
          <Chip label={editorFilename} size="small" variant="outlined" />
        </DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2} sx={{ mb: 3 }}>
            <Typography variant="body2" color="text.secondary">
              {t("swarm.editor.aiHint")}
            </Typography>
            <Stack direction="row" spacing={1}>
              <TextField
                fullWidth
                size="small"
                placeholder={t("swarm.editor.aiPlaceholder")}
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
                {t("swarm.editor.aiSuggest")}
              </Button>
            </Stack>
          </Stack>

          <TextField
            label={t("swarm.editor.filename")}
            value={editorFilename}
            onChange={(e) => setEditorFilename(e.target.value)}
            fullWidth
            size="small"
            sx={{ mb: 2 }}
          />
          
          <TextField
            label={t("swarm.editor.content")}
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
              <Trans i18nKey="swarm.editor.formatHint" components={{ bold: <b /> }} />
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditorOpen(false)} disabled={isSaving}>
            {t("swarm.editor.cancel")}
          </Button>
          <Button
            onClick={handleSavePlan}
            variant="contained"
            disabled={isSaving || !editorContent.trim()}
            startIcon={isSaving ? <CircularProgress size={18} /> : <EditIcon />}
          >
            {t("swarm.editor.saveToTemplates")}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ─── Task View Dialog ───────────────────────────────────── */}
      <Dialog
        open={taskViewOpen}
        onClose={() => setTaskViewOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ pb: 0 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Typography variant="h6">{t("swarm.taskView.title", { id: taskViewData.taskId })}</Typography>
            <IconButton onClick={() => setTaskViewOpen(false)}>
              <CloseIcon />
            </IconButton>
          </Stack>
          <Tabs 
            value={taskViewData.tab} 
            onChange={(e, v) => {
              setTaskViewData(prev => ({ ...prev, tab: v }));
              if (v === 1 && !taskViewData.diff && !taskViewData.diffNote) {
                fetchDiff(taskViewData.swarmId, taskViewData.taskId);
              }
            }}
            sx={{ mt: 1 }}
          >
            <Tab label={t("swarm.taskView.logsTab")} icon={<LogsIcon />} iconPosition="start" />
            <Tab label={t("swarm.taskView.liveDiffTab")} icon={<BranchIcon />} iconPosition="start" />
          </Tabs>
        </DialogTitle>
        <DialogContent dividers>
          {taskViewData.tab === 0 && (
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
              {taskViewData.logsNote === "loading"
                ? t("swarm.taskView.loadingLogs")
                : taskViewData.logsNote === "none"
                  ? t("swarm.taskView.noLogs")
                  : taskViewData.logs || t("swarm.taskView.noOutputYet")}
            </Box>
          )}

          {taskViewData.tab === 1 && (
            <Box>
              {isDiffLoading ? (
                <Stack alignItems="center" sx={{ py: 4 }}>
                  <CircularProgress size={24} sx={{ mb: 1 }} />
                  <Typography variant="caption">{t("swarm.taskView.fetchingDiff")}</Typography>
                </Stack>
              ) : (
                <Box
                  sx={{
                    fontFamily: "monospace",
                    fontSize: "0.8rem",
                    whiteSpace: "pre-wrap",
                    bgcolor: "background.default",
                    color: "text.primary",
                    p: 2,
                    borderRadius: 1,
                    maxHeight: 500,
                    overflow: "auto",
                    border: "1px solid",
                    borderColor: "divider",
                    "& .diff-add": { color: "success.main" },
                    "& .diff-del": { color: "error.main" },
                    "& .diff-meta": { color: "info.main", fontWeight: "bold" },
                  }}
                >
                  {taskViewData.diffNote ? (
                    <div>
                      {taskViewData.diffNote === "failed"
                        ? t("swarm.taskView.diffFailed")
                        : t("swarm.taskView.noChanges")}
                    </div>
                  ) : taskViewData.diff.split("\n").map((line, i) => {
                    let className = "";
                    if (line.startsWith("+") && !line.startsWith("+++")) className = "diff-add";
                    else if (line.startsWith("-") && !line.startsWith("---")) className = "diff-del";
                    else if (line.startsWith("@@") || line.startsWith("diff --git")) className = "diff-meta";
                    
                    return (
                      <div key={i} className={className}>
                        {line || " "}
                      </div>
                    );
                  })}
                </Box>
              )}
              <Box sx={{ mt: 1, textAlign: "right" }}>
                <Button 
                  size="small" 
                  startIcon={<RefreshIcon />} 
                  onClick={() => fetchDiff(taskViewData.swarmId, taskViewData.taskId)}
                  disabled={isDiffLoading}
                >
                  {t("swarm.taskView.refreshDiff")}
                </Button>
              </Box>
            </Box>
          )}
        </DialogContent>
      </Dialog>

      <EntityContextMenu
        anchorPosition={itemMenu.anchorPosition}
        onClose={itemMenu.close}
        actions={itemMenu.isOpen ? itemMenuActions() : []}
      />
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
  onViewTask,
  onItemContextMenu,
  theme,
}) => {
  const { t } = useUiTranslation();
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
      onContextMenu={(e) => onItemContextMenu(e, { kind: "swarm", swarm })}
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
                label={t("swarm.flightMode")}
                size="small"
                color="info"
                variant="outlined"
              />
            )}
            <Chip
              label={
                swarm.status === "failed" ? t("swarm.swarmStatus.failed") :
                isRunning ? t("swarm.swarmStatus.running") : t("swarm.swarmStatus.completed")
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
              {t("swarm.card.progress", { done: doneCount, total: totalCount })}
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
                  : t("swarm.card.free")
              }
            />
            <StatChip
              icon={<SpeedIcon />}
              label={t("swarm.card.activeAgents", { count: swarm.running_count || 0 })}
            />
            {swarm.disk_usage_mb > 0 && (
              <StatChip
                icon={<BranchIcon />}
                label={t("swarm.card.diskUsage", { size: swarm.disk_usage_mb })}
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
          {/* Visual DAG: the graph keeps the browser's own right-click */}
          <Box sx={{ mb: 3 }} data-native-contextmenu>
            <Typography variant="caption" color="text.secondary" fontWeight={600} sx={{ mb: 1, display: "block" }}>
              {t("swarm.card.dependencyGraph")}
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
                {t("swarm.actions.cancel")}
              </Button>
            )}
            {!isRunning && tasks.length > 0 && (
              <Button
                size="small"
                variant="outlined"
                startIcon={<MergeIcon />}
                onClick={() => onMerge(swarm.swarm_id)}
              >
                {t("swarm.actions.mergeAll")}
              </Button>
            )}
            {!isRunning && (
              <Button
                size="small"
                variant="outlined"
                startIcon={<CloseIcon />}
                onClick={() => onCleanup(swarm.swarm_id)}
              >
                {t("swarm.actions.cleanUp")}
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
                  onViewTask={onViewTask}
                  onItemContextMenu={onItemContextMenu}
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

const TaskCard = ({ task, swarmId, onViewTask, onItemContextMenu, _theme }) => {
  const { t } = useUiTranslation();
  const statuses = statusConfig();
  const cfg = statuses[task.status] || statuses.pending;

  return (
    <Card
      variant="outlined"
      onContextMenu={(e) => onItemContextMenu(e, { kind: "task", swarmId, task })}
      sx={{
        height: "100%",
        borderColor:
          task.status === "running"
            ? "primary.main"
            : task.status === "failed" || task.status === "timed_out"
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
        {VIEWABLE_TASK_STATUSES.includes(task.status) && (
          <Tooltip title={t("swarm.card.viewTaskDetail")}>
            <IconButton
              size="small"
              onClick={() => onViewTask(swarmId, task.id)}
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
  if (mins > 0) return translate("swarm.elapsed.minutesSeconds", { minutes: mins, seconds: secs });
  return translate("swarm.elapsed.seconds", { seconds: secs });
};


export default SwarmPage;
