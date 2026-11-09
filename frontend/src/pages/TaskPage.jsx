// frontend/src/pages/TaskPage.jsx
// Jobs: scheduler tasks the person creates, plus the video batches already running.

import { formatUiError } from "../utils/uiError";
import ActionButton from "../components/settings/ui/ActionButton";
import ChoiceChips from "../components/settings/ui/ChoiceChips";
import ConfirmActionDialog from "../components/settings/ui/ConfirmActionDialog";
import DashboardStrip, { DashboardTile } from "../components/settings/ui/DashboardStrip";
import SettingsPanel from "../components/settings/ui/SettingsPanel";
import StatusPill from "../components/settings/ui/StatusPill";
import {
  Alert,
  Box,
  Chip,
  Divider,
  IconButton,
  LinearProgress,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem as MuiMenuItem,
  Alert as MuiAlert,
  Paper,
  Snackbar,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import AddIcon from "@mui/icons-material/Add";
import CancelIcon from "@mui/icons-material/Cancel";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RefreshIcon from "@mui/icons-material/Refresh";
import CodeIcon from "@mui/icons-material/Code";
import TableChartIcon from "@mui/icons-material/TableChart";
import AnalyticsIcon from "@mui/icons-material/Analytics";
import ClearAllIcon from "@mui/icons-material/ClearAll";
import AssignmentOutlined from "@mui/icons-material/AssignmentOutlined";
import DescriptionIcon from "@mui/icons-material/Description";
import {
  cancelJob,
  createTask,
  deleteTask,
  getProjects,
  getTasks,
  updateTask,
  duplicateTask,
  getClients,
  getWebsite,
} from "../api";
import { TASK_PARAMS } from "../utils/entityLinks";
import { listJobs, cancelJob as cancelUnifiedJob, JOB_KINDS } from "../api/jobsService";
import { processTaskQueue } from "../api/taskService";
import { useStatus } from "../contexts/StatusContext";
import { useUnifiedProgress } from "../contexts/UnifiedProgressContext";
import TaskCard from "../components/cards/TaskCard";
import TaskActionModal from "../components/modals/TaskActionModal";
import PageLayout from "../components/layout/PageLayout";
import EntityContextMenu from "../components/common/EntityContextMenu";
import useContextMenu from "../hooks/useContextMenu";
import EmptyState from "../components/common/EmptyState";
import { ContextualLoader } from "../components/common/LoadingStates";
import { translate, useUiTranslation } from "../i18n";

// A video batch job's status as shown; the value from the API stays the one compared.
const jobStatusLabel = (status) =>
  ({
    pending: translate("jobs.status.pending"),
    running: translate("jobs.status.running"),
    paused: translate("jobs.status.paused"),
    completed: translate("jobs.status.completed"),
    failed: translate("jobs.status.failed"),
    cancelled: translate("jobs.status.cancelled"),
  })[status] ?? status;

// The status filter chips; the ids stay the ones filtered on.
const statusFilterLabel = (filter) =>
  ({
    all: translate("tasks.filter.all"),
    active: translate("tasks.filter.active"),
    completed: translate("tasks.filter.completed"),
    failed: translate("tasks.filter.failed"),
  })[filter];

// Removed TaskStatusChip - now handled in TaskCard component

// Removed table-related functions and constants - now using card-based layout

const TaskPage = () => {
  const { t } = useUiTranslation();
  // Links from elsewhere (utils/entityLinks.js): show one project's, client's
  // or website's tasks, open one task, or start a new task for that entity.
  const [searchParams, setSearchParams] = useSearchParams();
  const projectFilter = searchParams.get(TASK_PARAMS.project);
  const clientFilter = searchParams.get(TASK_PARAMS.client);
  const websiteFilter = searchParams.get(TASK_PARAMS.website);
  const taskIdParam = searchParams.get(TASK_PARAMS.taskId);
  const wantsNewTask = searchParams.get(TASK_PARAMS.create) === "1";
  const dropParams = useCallback(
    (...names) =>
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          names.forEach((name) => next.delete(name));
          return next;
        },
        { replace: true },
      ),
    [setSearchParams],
  );
  const { activeModel, isLoadingModel, modelError } = useStatus();
  useUnifiedProgress();

  // State management
  const [tasks, setTasks] = useState([]);
  const [availableProjects, setAvailableProjects] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState(null);
  // The delete waiting for ConfirmActionDialog: { title, description, facts?, confirmLabel, run }.
  const [pendingConfirm, setPendingConfirm] = useState(null);
  // Projects or models that could not be loaded: the project filter and the
  // task form's model list are empty then, so say why.
  const [lookupErrors, setLookupErrors] = useState({});
  const noteLookup = (key, err) =>
    setLookupErrors((prev) => {
      const next = { ...prev };
      if (err) next[key] = err;
      else delete next[key];
      return next;
    });
  const [feedback, setFeedback] = useState({
    open: false,
    message: "",
    severity: "info",
  });
  const [queueOrder, setQueueOrder] = useState([]);
  const [isProcessingQueue, setIsProcessingQueue] = useState(false);

  // Task creation state
  const [showTaskForm, setShowTaskForm] = useState(false);
  const [editingTask, setEditingTask] = useState(null);
  const [createDefaults, setCreateDefaults] = useState(null);
  const [tasksLoaded, setTasksLoaded] = useState(false);

  // The linked client and website: undefined while loading, null if not found.
  const [linkedClient, setLinkedClient] = useState(undefined);
  const [linkedWebsite, setLinkedWebsite] = useState(undefined);

  // New Job menu state
  const [newTaskMenuAnchor, setNewTaskMenuAnchor] = useState(null);

  // Filter state
  const [statusFilter, setStatusFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');

  // Right-click menu; fields and dialogs keep the browser's own menu.
  const pageMenu = useContextMenu();
  const contextItem = pageMenu.payload;
  const handleContextMenu = (e, task = null) => pageMenu.open(e, task);

  const [videoGenJobs, setVideoGenJobs] = useState([]);
  const [videoGenLoading, setVideoGenLoading] = useState(false);
  const [videoGenError, setVideoGenError] = useState(null);

  // Fetch tasks
  const fetchTasks = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await getTasks(projectFilter);
      if (result.error) {
        setError(result.error);
      } else {
        setTasks(result);
        // Extract queue order from tasks - include pending and queued tasks
        const pendingTasks = result
          .filter((task) => task.status === "pending" || task.status === "queued")
          .sort(
            (a, b) =>
              a.priority - b.priority ||
              new Date(a.created_at) - new Date(b.created_at)
          );
        setQueueOrder(pendingTasks.map((task) => task.id));
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
      setTasksLoaded(true);
    }
  }, [projectFilter]);

  // Fetch projects
  const fetchProjects = useCallback(async () => {
    try {
      const result = await getProjects();
      if (!result.error) {
        setAvailableProjects(result);
        noteLookup("projects", null);
      } else {
        noteLookup("projects", t("tasks.lookup.projectsFailed", { error: formatUiError(result.error) }));
      }
    } catch (err) {
      console.error("Failed to fetch projects:", err);
      noteLookup(
        "projects",
        t("tasks.lookup.projectsFailed", { error: formatUiError(err) || t("tasks.lookup.requestFailed") }),
      );
    }
  }, []);

  const fetchVideoGenJobs = useCallback(async () => {
    setVideoGenLoading(true);
    setVideoGenError(null);
    try {
      const data = await listJobs({
        kinds: [JOB_KINDS.VIDEO_GEN],
        statuses: ["pending", "running", "paused"],
        limit: 50,
      });
      setVideoGenJobs(data?.jobs || []);
    } catch (err) {
      setVideoGenError(formatUiError(err.response?.data?.error) || err.message || t("tasks.video.loadFailed"));
    } finally {
      setVideoGenLoading(false);
    }
  }, []);

  const requestCancelVideoGenJob = (job) => {
    setPendingConfirm({
      title: t("tasks.video.cancelTitle", { label: job.label || job.id }),
      description: t("tasks.video.cancelDescription"),
      keeps: t("tasks.video.cancelKeeps"),
      confirmLabel: t("tasks.video.cancel"),
      run: () => handleCancelVideoGenJob(job.id),
    });
  };

  const handleCancelVideoGenJob = async (jobId) => {
    try {
      const res = await cancelUnifiedJob(jobId);
      if (res?.cancelled) {
        setFeedback({
          open: true,
          message: t("tasks.video.cancelled"),
          severity: "success",
        });
        fetchVideoGenJobs();
      } else {
        setFeedback({
          open: true,
          message: res?.reason || t("tasks.video.cancelRefused"),
          severity: "warning",
        });
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: formatUiError(err.response?.data?.error) || err.message || t("tasks.video.cancelFailed"),
        severity: "error",
      });
    }
  };

  // Load data on component mount
  useEffect(() => {
    fetchProjects();
    fetchVideoGenJobs();
  }, [fetchVideoGenJobs]);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  useEffect(() => {
    if (!clientFilter) {
      setLinkedClient(undefined);
      return undefined;
    }
    let live = true;
    setLinkedClient(undefined);
    getClients()
      .then((list) => {
        if (!live) return;
        const found = Array.isArray(list) ? list.find((c) => String(c.id) === String(clientFilter)) : null;
        setLinkedClient(found || null);
      })
      .catch(() => live && setLinkedClient(null));
    return () => {
      live = false;
    };
  }, [clientFilter]);

  useEffect(() => {
    if (!websiteFilter) {
      setLinkedWebsite(undefined);
      return undefined;
    }
    let live = true;
    setLinkedWebsite(undefined);
    getWebsite(websiteFilter)
      .then((site) => live && setLinkedWebsite(site?.id ? site : null))
      .catch(() => live && setLinkedWebsite(null));
    return () => {
      live = false;
    };
  }, [websiteFilter]);

  // ?new=1: a new task for the linked project, client or website, once their names are known.
  useEffect(() => {
    if (!wantsNewTask) return;
    if ((clientFilter && linkedClient === undefined) || (websiteFilter && linkedWebsite === undefined)) return;
    setEditingTask(null);
    setCreateDefaults({
      project_id: projectFilter ? Number(projectFilter) : linkedWebsite?.project?.id ?? undefined,
      client_id: clientFilter ? Number(clientFilter) : linkedWebsite?.client_id ?? undefined,
      client_name: linkedClient?.name ?? linkedWebsite?.client?.name ?? undefined,
      website_id: websiteFilter ? Number(websiteFilter) : undefined,
    });
    setShowTaskForm(true);
    dropParams(TASK_PARAMS.create);
  }, [wantsNewTask, clientFilter, websiteFilter, projectFilter, linkedClient, linkedWebsite, dropParams]);

  // ?taskId=: open that task once the list is in.
  useEffect(() => {
    if (!taskIdParam || !tasksLoaded) return;
    const task = tasks.find((t) => String(t.id) === String(taskIdParam));
    if (task) {
      setCreateDefaults(null);
      setEditingTask(task);
      setShowTaskForm(true);
    } else {
      setFeedback({ open: true, message: t("tasks.notFound", { id: taskIdParam }), severity: "warning" });
    }
    dropParams(TASK_PARAMS.taskId);
  }, [taskIdParam, tasksLoaded, tasks, dropParams]);

  // Auto-refresh when tasks are running
  useEffect(() => {
    const hasRunningTasks = tasks.some(t =>
      ['queued', 'in-progress', 'running', 'processing'].includes(t.status?.toLowerCase())
    );
    if (!hasRunningTasks) return;
    const interval = setInterval(() => { fetchTasks(); }, 10000);
    return () => clearInterval(interval);
  }, [tasks, fetchTasks]);

  useEffect(() => {
    if (videoGenJobs.length === 0) return;
    const interval = setInterval(() => { fetchVideoGenJobs(); }, 10000);
    return () => clearInterval(interval);
  }, [videoGenJobs.length, fetchVideoGenJobs]);

  // Process queue
  const processQueue = async () => {
    setIsProcessingQueue(true);
    try {
      await processTaskQueue();
      setFeedback({
        open: true,
        message: t("tasks.queue.started"),
        severity: "success",
      });
      // Refresh tasks to show updated statuses
      setTimeout(() => {
        fetchTasks();
      }, 1000);
    } catch (err) {
      setFeedback({
        open: true,
        message: t("tasks.queue.failed", { error: err.message }),
        severity: "error",
      });
    } finally {
      setIsProcessingQueue(false);
    }
  };

  // Task form handlers
  const handleOpenTaskForm = () => {
    setCreateDefaults(null);
    setEditingTask(null);
    setShowTaskForm(true);
  };

  const handleCloseTaskForm = () => {
    setShowTaskForm(false);
    setEditingTask(null);
    setCreateDefaults(null);
  };

  const handleEditTask = (task) => {
    setCreateDefaults(null);
    setEditingTask(task);
    setShowTaskForm(true);
  };

  const handleTaskCreated = (task) => {
    // Task created and job started successfully
    setShowTaskForm(false);
    setEditingTask(null);
    setFeedback({
      open: true,
      message: task.message || t("tasks.created"),
      severity: "success",
    });
    fetchTasks();
  };

  const handleTaskSave = async (taskId, taskData) => {
    setIsSaving(true);
    let result = null;
    try {
      if (taskId) {
        // Update existing task
        result = await updateTask(taskId, taskData);
        if (result?.error) {
          throw new Error(result.error);
        }
        setFeedback({
          open: true,
          message: t("tasks.updated"),
          severity: "success",
        });
      } else {
        // Create new task
        result = await createTask(taskData);
        if (result?.error) {
          throw new Error(result.error);
        }
        setFeedback({
          open: true,
          message: t("tasks.created"),
          severity: "success",
        });
      }
      fetchTasks();
      // BUG FIX #2: Always return a valid result object
      return result || { success: true, id: taskId };
    } catch (err) {
      setFeedback({
        open: true,
        message: t("tasks.saveFailed", { error: err.message }),
        severity: "error",
      });
      throw err;
    } finally {
      setIsSaving(false);
    }
  };

  const handleTaskDeleted = () => {
    setShowTaskForm(false);
    setEditingTask(null);
    setFeedback({
      open: true,
      message: t("tasks.deleted"),
      severity: "success",
    });
    fetchTasks();
  };

  const handleTaskDuplicated = (result) => {
    setShowTaskForm(false);
    setEditingTask(null);
    setFeedback({
      open: true,
      message: t("tasks.duplicatedWithId", { id: result?.data?.new_task_id || result?.new_task_id }),
      severity: "success",
    });
    fetchTasks();
  };

  const handleQuickTaskAction = (actionId) => {
    const actionMap = {
      "csv_generation": { type: "file_generation", name: "CSV Generation" },
      "code_task": { type: "code_generation", name: "Code Generation" },
      "content_task": { type: "content_generation", name: "Content Generation" },
      "analysis_task": { type: "data_analysis", name: "Analysis Task" },
    };

    const action = actionMap[actionId];
    if (action) {
      // Set up the editing task with pre-filled data
      setCreateDefaults(null);
      setEditingTask({
        name: action.name,
        type: action.type,
        description: action.type === "code_generation" ? "Generate code files (.jsx, .py, .js, etc.)" : "",
        output_filename: action.type === "code_generation" ? "generated_file.jsx" : "",
        auto_start_job: actionId === "csv_generation",
      });
      setShowTaskForm(true);
    }
  };

  // Action handlers for task management
  const handleDuplicateTask = async (taskId, event) => {
    // BUG FIX #3: Safely check if event exists and has stopPropagation method
    if (event && typeof event.stopPropagation === 'function') {
      event.stopPropagation(); // Prevent row click
    }

    try {
      const result = await duplicateTask(taskId);
      if (result?.error) {
        throw new Error(result.error);
      }
      setFeedback({
        open: true,
        message: t("tasks.duplicated"),
        severity: "success",
      });
      fetchTasks(); // Refresh task list
    } catch (err) {
      setFeedback({
        open: true,
        message: t("tasks.duplicateFailed", { error: err.message }),
        severity: "error",
      });
    }
  };

  // Asks with ConfirmActionDialog; deleteTaskConfirmed runs on confirm.
  const handleDeleteTask = (taskId, taskName, event) => {
    // BUG FIX #4: Safely check if event exists and has stopPropagation method
    if (event && typeof event.stopPropagation === 'function') {
      event.stopPropagation(); // Prevent row click
    }
    setPendingConfirm({
      title: t("tasks.delete.title"),
      description: t("tasks.delete.description", { name: taskName || t("tasks.delete.thisTask") }),
      keeps: t("tasks.delete.keeps"),
      confirmLabel: t("tasks.delete.confirm"),
      run: () => deleteTaskConfirmed(taskId),
    });
  };

  const deleteTaskConfirmed = async (taskId) => {
    try {
      const result = await deleteTask(taskId);
      if (result?.error) {
        throw new Error(result.error);
      }
      setFeedback({
        open: true,
        message: t("tasks.deleted"),
        severity: "success",
      });
      fetchTasks(); // Refresh task list
    } catch (err) {
      setFeedback({
        open: true,
        message: t("tasks.deleteFailed", { error: err.message }),
        severity: "error",
      });
    }
  };

  const handleStartSingleTask = async (taskId, task) => {
    if (!taskId || !task) {
      setFeedback({
        open: true,
        message: t("tasks.start.invalid"),
        severity: "error",
      });
      return;
    }

    try {
      setFeedback({
        open: true,
        message: t("tasks.start.starting", { name: task.name || t("tasks.start.unnamed") }),
        severity: "info",
      });

      // BUG FIX #5: Better error handling for dynamic import and API call
      try {
        const { startTask } = await import('../api');
        if (typeof startTask !== 'function') {
          throw new Error('startTask is not a function');
        }
        const result = await startTask(taskId);
        if (result?.error) {
          throw new Error(result.error);
        }
      } catch (importError) {
        console.error('Failed to start task:', importError);
        throw new Error(t("tasks.start.failed", { error: importError.message }));
      }

      setFeedback({
        open: true,
        message: t("tasks.start.started", { name: task.name || t("tasks.start.unnamed") }),
        severity: "success",
      });

      // Refresh tasks to show updated statuses
      setTimeout(() => {
        fetchTasks();
      }, 1000);
    } catch (err) {
      setFeedback({
        open: true,
        message: t("tasks.start.failed", { error: err.message }),
        severity: "error",
      });
    }
  };

  const handleStopTask = async (task) => {
    if (!task?.job_id) {
      setFeedback({
        open: true,
        message: t("tasks.stop.noJob"),
        severity: "warning",
      });
      return;
    }
    try {
      await cancelJob(task.job_id);
      setFeedback({
        open: true,
        message: t("tasks.stop.stopped", { name: task.name || t("tasks.start.unnamed") }),
        severity: "success",
      });
      setTimeout(fetchTasks, 1000);
    } catch (err) {
      setFeedback({
        open: true,
        message: t("tasks.stop.failed", { error: formatUiError(err) || err.message }),
        severity: "error",
      });
    }
  };

  const handleClearAllTasks = async () => {
    if (tasks.length === 0) {
      setFeedback({
        open: true,
        message: t("tasks.clear.none"),
        severity: "info",
      });
      return;
    }

    const facts = [{ label: t("tasks.clear.tasksFact"), value: tasks.length }];
    if (sortedTasks.length !== tasks.length) {
      facts.push({ label: t("tasks.clear.onScreen"), value: sortedTasks.length });
    }
    setPendingConfirm({
      title: t("tasks.clear.title"),
      description: t("tasks.clear.description"),
      facts,
      keeps: t("tasks.clear.keeps"),
      confirmLabel: t("tasks.clear.confirm"),
      run: clearAllTasksConfirmed,
    });
  };

  const clearAllTasksConfirmed = async () => {
    try {
      setIsSaving(true);
      setFeedback({
        open: true,
        message: t("tasks.clear.clearing"),
        severity: "info",
      });

      // Delete all tasks
      const deletePromises = tasks.map(task => deleteTask(task.id));
      await Promise.all(deletePromises);

      setFeedback({
        open: true,
        message: t("tasks.clear.done", { count: tasks.length }),
        severity: "success",
      });
      
      // Refresh tasks to show empty state
      fetchTasks();
    } catch (err) {
      setFeedback({
        open: true,
        message: t("tasks.clear.failed", { error: err.message }),
        severity: "error",
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleCloseFeedback = (event, reason) => {
    if (reason === "clickaway") return;
    setFeedback((prev) => ({ ...prev, open: false }));
  };

  // Sort and filter tasks for card view
  const sortedTasks = useMemo(() => {
    let filtered = [...tasks];

    // Status filter
    if (statusFilter === 'active') {
      filtered = filtered.filter(t => ['pending', 'queued', 'in-progress', 'running', 'processing'].includes(t.status?.toLowerCase()));
    } else if (statusFilter === 'completed') {
      filtered = filtered.filter(t => ['completed', 'complete'].includes(t.status?.toLowerCase()));
    } else if (statusFilter === 'failed') {
      filtered = filtered.filter(t => ['failed', 'error', 'cancelled', 'canceled'].includes(t.status?.toLowerCase()));
    }

    // Type filter
    if (typeFilter !== 'all') {
      filtered = filtered.filter(t => t.type === typeFilter);
    }

    // Client / website from a link. Older tasks carry only the name or URL.
    if (clientFilter) {
      filtered = filtered.filter((t) =>
        String(t.client_id ?? t.client_info?.id ?? "") === String(clientFilter) ||
        (linkedClient?.name && t.client_name === linkedClient.name));
    }
    if (websiteFilter) {
      filtered = filtered.filter((t) =>
        String(t.website_id ?? t.website_info?.id ?? "") === String(websiteFilter) ||
        (linkedWebsite?.url && t.target_website === linkedWebsite.url));
    }

    return filtered.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  }, [tasks, statusFilter, typeFilter, clientFilter, websiteFilter, linkedClient, linkedWebsite]);

  const entityFilterLabel = [
    projectFilter &&
      t("tasks.filter.project", {
        name: availableProjects.find((p) => String(p.id) === String(projectFilter))?.name || `#${projectFilter}`,
      }),
    clientFilter && t("tasks.filter.client", { name: linkedClient?.name || `#${clientFilter}` }),
    websiteFilter && t("tasks.filter.website", { url: linkedWebsite?.url || `#${websiteFilter}` }),
  ]
    .filter(Boolean)
    .join(" · ");

  const statusOf = (task) => task.status?.toLowerCase();
  const pendingCount = tasks.filter((task) => statusOf(task) === "pending").length;
  const queuedCount = tasks.filter((task) => statusOf(task) === "queued").length;
  const runningCount = tasks.filter((task) =>
    ["in-progress", "running", "processing"].includes(statusOf(task)),
  ).length;
  const statusOptions = ["all", "active", "completed", "failed"].map((value) => ({
    value,
    label: statusFilterLabel(value),
  }));
  const typeOptions = [
    ["all", "tasks.typeFilter.all"],
    ["code_generation", "tasks.types.code_generation"],
    ["file_generation", "tasks.types.file_generation"],
    ["content_generation", "tasks.types.content_generation"],
    ["bulk_content_generation", "tasks.types.bulk_content_generation"],
    ["data_analysis", "tasks.types.data_analysis"],
    ["website_analysis", "tasks.types.website_analysis"],
    ["custom", "tasks.types.custom"],
  ].map(([value, key]) => ({ value, label: t(key) }));
  const videoTone = (status) => ({
    running: "info",
    paused: "warn",
    failed: "error",
    completed: "ok",
    cancelled: "warn",
  })[status] || "neutral";

  return (
    <PageLayout
      title={t("tasks.title")}
      variant="standard"
      actions={
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          {queueOrder.length > 0 && (
            <ActionButton startIcon={<PlayArrowIcon />} onClick={processQueue} loading={isProcessingQueue} disabled={isLoading}>
              {t("tasks.runQueue", { count: queueOrder.length })}
            </ActionButton>
          )}
          <ActionButton
            startIcon={<AddIcon />}
            onClick={(e) => setNewTaskMenuAnchor(e.currentTarget)}
            disabled={isLoading || isSaving}
          >
            {t("tasks.newJob")}
          </ActionButton>
          <Menu
            anchorEl={newTaskMenuAnchor}
            open={Boolean(newTaskMenuAnchor)}
            onClose={() => setNewTaskMenuAnchor(null)}
          >
            <MuiMenuItem onClick={() => { setNewTaskMenuAnchor(null); handleQuickTaskAction("code_task"); }}>
              <ListItemIcon><CodeIcon fontSize="small" /></ListItemIcon>
              <ListItemText>{t("tasks.newMenu.code")}</ListItemText>
            </MuiMenuItem>
            <MuiMenuItem onClick={() => { setNewTaskMenuAnchor(null); handleQuickTaskAction("csv_generation"); }}>
              <ListItemIcon><TableChartIcon fontSize="small" /></ListItemIcon>
              <ListItemText>{t("tasks.newMenu.csv")}</ListItemText>
            </MuiMenuItem>
            <MuiMenuItem onClick={() => { setNewTaskMenuAnchor(null); handleQuickTaskAction("content_task"); }}>
              <ListItemIcon><DescriptionIcon fontSize="small" /></ListItemIcon>
              <ListItemText>{t("tasks.newMenu.content")}</ListItemText>
            </MuiMenuItem>
            <MuiMenuItem onClick={() => { setNewTaskMenuAnchor(null); handleQuickTaskAction("analysis_task"); }}>
              <ListItemIcon><AnalyticsIcon fontSize="small" /></ListItemIcon>
              <ListItemText>{t("tasks.newMenu.analysis")}</ListItemText>
            </MuiMenuItem>
            <Divider />
            <MuiMenuItem onClick={() => { setNewTaskMenuAnchor(null); handleOpenTaskForm(); }}>
              <ListItemIcon><AddIcon fontSize="small" /></ListItemIcon>
              <ListItemText>{t("tasks.newMenu.custom")}</ListItemText>
            </MuiMenuItem>
          </Menu>
        </Box>
      }
      modelStatus
      activeModel={isLoadingModel ? t("common.loading") : modelError ? t("common.error") : activeModel}
    >
      <Box
        onContextMenu={(e) => handleContextMenu(e, null)}
        sx={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
      >
        <Snackbar
          open={feedback.open}
          autoHideDuration={4000}
          onClose={handleCloseFeedback}
          anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        >
          <MuiAlert
            onClose={handleCloseFeedback}
            severity={feedback.severity || "info"}
            sx={{ width: "100%" }}
            variant="filled"
          >
            {feedback.message}
          </MuiAlert>
        </Snackbar>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        {Object.keys(lookupErrors).length > 0 && (
          <Alert
            severity="warning"
            sx={{ mb: 2 }}
            action={
              <ActionButton kind="link" onClick={() => fetchProjects()}>
                {t("common.retry")}
              </ActionButton>
            }
          >
            {Object.values(lookupErrors).join(". ")}.
          </Alert>
        )}

        <ConfirmActionDialog
          open={Boolean(pendingConfirm)}
          title={pendingConfirm?.title || ""}
          description={pendingConfirm?.description}
          facts={pendingConfirm?.facts || []}
          keeps={pendingConfirm?.keeps}
          confirmLabel={pendingConfirm?.confirmLabel}
          onConfirm={() => {
            const run = pendingConfirm?.run;
            setPendingConfirm(null);
            run?.();
          }}
          onClose={() => setPendingConfirm(null)}
        />

        {/* Enhanced Task Action Modal */}
        <TaskActionModal
          open={showTaskForm}
          onClose={handleCloseTaskForm}
          taskData={editingTask}
          defaults={editingTask ? null : createDefaults}
          onSave={handleTaskSave}
          isSaving={isSaving}
          onTaskCreated={handleTaskCreated}
          onTaskDeleted={handleTaskDeleted}
          onTaskDuplicated={handleTaskDuplicated}
        />

        <DashboardStrip>
          <DashboardTile
            label={t("layoutCards.task.status.pending")}
            value={pendingCount}
            tone={pendingCount > 0 ? "ok" : "off"}
          />
          <DashboardTile
            label={t("layoutCards.task.status.queued")}
            value={queuedCount}
            tone={queuedCount > 0 ? "ok" : "off"}
          />
          <DashboardTile
            label={t("layoutCards.task.status.running")}
            value={runningCount}
            tone={runningCount > 0 ? "ok" : "off"}
          />
        </DashboardStrip>

        <Paper sx={{ p: 2, mb: 2, mt: 2, borderRadius: 2 }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1.5 }}>
            <Box>
              <Typography variant="subtitle1" fontWeight={600}>
                {t("tasks.video.title")}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {t("tasks.video.intro")}
              </Typography>
            </Box>
            <IconButton size="small" onClick={fetchVideoGenJobs} title={t("tasks.video.refresh")}>
              <RefreshIcon fontSize="small" />
            </IconButton>
          </Stack>

          {videoGenError && (
            <Alert severity="error" sx={{ mb: 1.5 }}>{videoGenError}</Alert>
          )}
          {videoGenLoading && <LinearProgress sx={{ mb: 1 }} />}

          {videoGenJobs.length === 0 && !videoGenLoading ? (
            <Typography variant="body2" color="text.secondary">
              {t("tasks.video.empty")}
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontWeight: 600 }}>{t("tasks.video.label")}</TableCell>
                  <TableCell sx={{ fontWeight: 600 }}>{t("tasks.video.status")}</TableCell>
                  <TableCell sx={{ fontWeight: 600, width: 180 }}>{t("tasks.video.progress")}</TableCell>
                  <TableCell sx={{ fontWeight: 600, width: 120 }} align="right">{t("tasks.video.actions")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {videoGenJobs.map((job) => (
                  <TableRow key={job.id} hover>
                    <TableCell>{job.label}</TableCell>
                    <TableCell>
                      <StatusPill label={jobStatusLabel(job.status)} tone={videoTone(job.status)} />
                    </TableCell>
                    <TableCell>
                      {job.progress != null ? (
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Box sx={{ width: 80 }}>
                            <LinearProgress
                              variant="determinate"
                              value={Math.max(0, Math.min(100, job.progress))}
                              sx={{ height: 6, borderRadius: 3 }}
                            />
                          </Box>
                          <Typography variant="caption">{Math.round(job.progress)}%</Typography>
                        </Stack>
                      ) : (
                        <Typography variant="caption" color="text.secondary">—</Typography>
                      )}
                    </TableCell>
                    <TableCell align="right">
                      {job.cancellable && (
                        <ActionButton
                          startIcon={<CancelIcon />}
                          onClick={() => requestCancelVideoGenJob(job)}
                        >
                          {t("tasks.video.cancel")}
                        </ActionButton>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Paper>

        <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap", alignItems: "center" }}>
          {entityFilterLabel && (
            <Chip
              label={entityFilterLabel}
              size="small"
              color="primary"
              onDelete={() => dropParams(TASK_PARAMS.project, TASK_PARAMS.client, TASK_PARAMS.website)}
            />
          )}
          <Box data-native-contextmenu data-testid="task-status-filter">
            <ChoiceChips
              ariaLabel={t("tasks.filter.status")}
              options={statusOptions}
              value={statusFilter}
              onChange={setStatusFilter}
            />
          </Box>
          <Box data-native-contextmenu data-testid="task-type-filter">
            <ChoiceChips
              ariaLabel={t("tasks.typeFilter.label")}
              options={typeOptions}
              value={typeFilter}
              onChange={setTypeFilter}
            />
          </Box>
        </Box>

        {/* Tasks Display */}
        {isLoading ? (
          <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", py: 6 }}>
            <ContextualLoader loading message={t("tasks.loading")} showProgress={false} inline />
          </Box>
        ) : sortedTasks.length === 0 ? (
          <Paper sx={{ p: 4, textAlign: "center", borderRadius: 3 }}>
            <EmptyState
              icon={<AssignmentOutlined />}
              title={tasks.length === 0 ? t("tasks.emptyTitle") : t("tasks.emptyFilteredTitle")}
              description={tasks.length === 0 ? t("tasks.emptyDescription") : t("tasks.emptyFilteredDescription")}
            />
            {tasks.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <ActionButton onClick={() => { setStatusFilter("all"); setTypeFilter("all"); }}>
                  {t("tasks.clearFilters")}
                </ActionButton>
              </Box>
            )}
          </Paper>
        ) : (
          <>
            {/* Task Cards Grid */}
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "1fr",
                  sm: "repeat(2, 1fr)",
                  md: "repeat(3, 1fr)",
                  lg: "repeat(4, 1fr)",
                },
                gap: 2,
                mb: 2,
              }}
            >
              {sortedTasks.map((task) => (
                <div key={task.id} onContextMenu={(e) => handleContextMenu(e, task)}>
                  <TaskCard
                    task={task}
                    onEdit={handleEditTask}
                    onStartJob={handleStartSingleTask}
                    onStopJob={handleStopTask}
                    availableProjects={availableProjects}
                  />
                </div>
              ))}
            </Box>
          </>
        )}

        {tasks.length > 0 && !isLoading && (
          <Box sx={{ mt: 2 }}>
            <SettingsPanel
              danger
              title={t("tasks.clear.title")}
              description={t("tasks.clear.description")}
            >
              <Box>
                <ActionButton
                  kind="destructive"
                  startIcon={<ClearAllIcon />}
                  onClick={handleClearAllTasks}
                  disabled={isSaving}
                >
                  {t("tasks.clearAll")}
                </ActionButton>
              </Box>
            </SettingsPanel>
          </Box>
        )}

      </Box>

        <EntityContextMenu
          anchorPosition={pageMenu.anchorPosition}
          onClose={pageMenu.close}
          actions={contextItem ? [
            {
              label: t("tasks.menu.start"),
              onClick: () => handleStartSingleTask(contextItem.id, contextItem),
              disabled: ['in-progress', 'running', 'processing', 'completed', 'complete', 'queued'].includes(contextItem.status?.toLowerCase()),
            },
            {
              label: t("tasks.menu.cancel"),
              onClick: () => handleStopTask(contextItem),
              disabled: !['in-progress', 'running', 'processing', 'queued'].includes(contextItem.status?.toLowerCase()) || !contextItem.job_id,
            },
            { label: t("tasks.menu.edit"), onClick: () => handleEditTask(contextItem), dividerBefore: true },
            { label: t("tasks.menu.duplicate"), onClick: () => handleDuplicateTask(contextItem.id) },
            { label: t("common.delete"), onClick: () => handleDeleteTask(contextItem.id, contextItem.name), dividerBefore: true, color: 'error.main' },
          ] : [
            { label: t("tasks.menu.newCode"), icon: <CodeIcon fontSize="small" />, onClick: () => handleQuickTaskAction("code_task") },
            { label: t("tasks.menu.newCsv"), icon: <TableChartIcon fontSize="small" />, onClick: () => handleQuickTaskAction("csv_generation") },
            { label: t("tasks.menu.newContent"), icon: <DescriptionIcon fontSize="small" />, onClick: () => handleQuickTaskAction("content_task") },
            { label: t("tasks.menu.newAnalysis"), icon: <AnalyticsIcon fontSize="small" />, onClick: () => handleQuickTaskAction("analysis_task") },
            { label: t("tasks.menu.newCustom"), icon: <AddIcon fontSize="small" />, onClick: () => handleOpenTaskForm(), dividerBefore: true },
          ]}
        />
    </PageLayout>
  );
};

export default TaskPage;
