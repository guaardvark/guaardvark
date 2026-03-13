// frontend/src/pages/TaskPage.jsx
// Version 3.0: Enhanced GUI with default task model, cleaner interface, and progress integration
// - Added Default Task Model dropdown to top right
// - Removed progress bars from tasks, added percentage completion text
// - Integrated with Progress Footer Bar for processing tasks
// - Cleaner, more useful interface

import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem as MuiMenuItem,
  Alert as MuiAlert,
  Paper,
  Select,
  Snackbar,
  Tooltip,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";

import AddIcon from "@mui/icons-material/Add";
import RefreshIcon from "@mui/icons-material/Refresh";
import FileCopyIcon from "@mui/icons-material/FileCopy";
import CloseIcon from "@mui/icons-material/Close";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import CodeIcon from "@mui/icons-material/Code";
import TableChartIcon from "@mui/icons-material/TableChart";
import AnalyticsIcon from "@mui/icons-material/Analytics";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import ClearAllIcon from "@mui/icons-material/ClearAll";
import AssignmentOutlined from "@mui/icons-material/AssignmentOutlined";
import DescriptionIcon from "@mui/icons-material/Description";
import {
  cancelJob,
  createTask,
  deleteTask,
  getAvailableModels,
  getProjects,
  getTasks,
  updateTask,
  duplicateTask,
} from "../api";
import { processTaskQueue } from "../api/taskService";
import { useStatus } from "../contexts/StatusContext";
import { useUnifiedProgress } from "../contexts/UnifiedProgressContext";
import TaskCard from "../components/cards/TaskCard";
import TaskActionModal from "../components/modals/TaskActionModal";
import PageLayout from "../components/layout/PageLayout";
import EntityContextMenu from "../components/common/EntityContextMenu";
import EmptyState from "../components/common/EmptyState";
import { ContextualLoader } from "../components/common/LoadingStates";

// Removed TaskStatusChip - now handled in TaskCard component

const formatDate = (dateString) => {
  if (!dateString) return "-";
  try {
    const date = new Date(dateString);
    // Return timestamp format: YYYY-MM-DD HH:MM:SS
    return date.toLocaleString("en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone
    }).replace(/(\d+)\/(\d+)\/(\d+),?\s*(.+)/, "$3-$1-$2 $4");
  } catch (error) {
    return "-";
  }
};

// Removed table-related functions and constants - now using card-based layout

const TaskPage = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { activeModel, isLoadingModel, modelError } = useStatus();
  useUnifiedProgress();

  // State management
  const [tasks, setTasks] = useState([]);
  const [availableProjects, setAvailableProjects] = useState([]);
  const [availableModels, setAvailableModels] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState(null);
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
  const [viewMode, setViewMode] = useState("cards"); // "cards" or "table"

  // Sorting state (simplified for card view)
  const [sortBy, setSortBy] = useState("created_at");

  // New Job menu state
  const [newTaskMenuAnchor, setNewTaskMenuAnchor] = useState(null);

  // Filter state
  const [statusFilter, setStatusFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');

  // Context menu state
  const [contextMenu, setContextMenu] = useState(null);
  const [contextItem, setContextItem] = useState(null);

  const handleContextMenu = (e, task = null) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ top: e.clientY, left: e.clientX });
    setContextItem(task);
  };

  // Fetch tasks
  const fetchTasks = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const projectId = searchParams.get("project_id");
      const result = await getTasks(projectId);
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
    }
  }, [searchParams]);

  // Fetch projects
  const fetchProjects = useCallback(async () => {
    try {
      const result = await getProjects();
      if (!result.error) {
        setAvailableProjects(result);
      }
    } catch (err) {
      console.error("Failed to fetch projects:", err);
    }
  }, []);

  // Fetch available models
  const fetchAvailableModels = useCallback(async () => {
    try {
      const result = await getAvailableModels();
      if (!result.error) {
        setAvailableModels(result);
      }
    } catch (err) {
      console.error("Failed to fetch models:", err);
    }
  }, []);

  // Load data on component mount
  useEffect(() => {
    fetchTasks();
    fetchProjects();
    fetchAvailableModels();
  }, []);

  // Auto-refresh when tasks are running
  useEffect(() => {
    const hasRunningTasks = tasks.some(t =>
      ['queued', 'in-progress', 'running', 'processing'].includes(t.status?.toLowerCase())
    );
    if (!hasRunningTasks) return;
    const interval = setInterval(() => { fetchTasks(); }, 10000);
    return () => clearInterval(interval);
  }, [tasks, fetchTasks]);

  // Removed sorting and progress handlers - now handled in TaskCard component

  // Process queue
  const processQueue = async () => {
    setIsProcessingQueue(true);
    try {
      await processTaskQueue();
      setFeedback({
        open: true,
        message: "Task queue processing started",
        severity: "success",
      });
      // Refresh tasks to show updated statuses
      setTimeout(() => {
        fetchTasks();
      }, 1000);
    } catch (err) {
      setFeedback({
        open: true,
        message: `Failed to process queue: ${err.message}`,
        severity: "error",
      });
    } finally {
      setIsProcessingQueue(false);
    }
  };

  // Task form handlers
  const handleOpenTaskForm = () => {
    setEditingTask(null);
    setShowTaskForm(true);
  };

  const handleCloseTaskForm = () => {
    setShowTaskForm(false);
    setEditingTask(null);
  };

  const handleEditTask = (task) => {
    setEditingTask(task);
    setShowTaskForm(true);
  };

  const handleTaskCreated = (task) => {
    // Task created and job started successfully
    setShowTaskForm(false);
    setEditingTask(null);
    setFeedback({
      open: true,
      message: task.message || "Task created successfully",
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
          message: "Task updated successfully",
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
          message: "Task created successfully",
          severity: "success",
        });
      }
      fetchTasks();
      // BUG FIX #2: Always return a valid result object
      return result || { success: true, id: taskId };
    } catch (err) {
      setFeedback({
        open: true,
        message: `Failed to save task: ${err.message}`,
        severity: "error",
      });
      throw err;
    } finally {
      setIsSaving(false);
    }
  };

  const handleTaskDeleted = (taskId) => {
    setShowTaskForm(false);
    setEditingTask(null);
    setFeedback({
      open: true,
      message: "Task deleted successfully",
      severity: "success",
    });
    fetchTasks();
  };

  const handleTaskDuplicated = (result) => {
    setShowTaskForm(false);
    setEditingTask(null);
    setFeedback({
      open: true,
      message: `Task duplicated successfully (ID: ${result?.data?.new_task_id || result?.new_task_id})`,
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
        message: `Task duplicated successfully`,
        severity: "success",
      });
      fetchTasks(); // Refresh task list
    } catch (err) {
      setFeedback({
        open: true,
        message: `Failed to duplicate task: ${err.message}`,
        severity: "error",
      });
    }
  };

  const handleDeleteTask = async (taskId, taskName, event) => {
    // BUG FIX #4: Safely check if event exists and has stopPropagation method
    if (event && typeof event.stopPropagation === 'function') {
      event.stopPropagation(); // Prevent row click
    }

    if (!window.confirm(`Are you sure you want to delete task "${taskName || 'this task'}"?`)) {
      return;
    }

    try {
      const result = await deleteTask(taskId);
      if (result?.error) {
        throw new Error(result.error);
      }
      setFeedback({
        open: true,
        message: "Task deleted successfully",
        severity: "success",
      });
      fetchTasks(); // Refresh task list
    } catch (err) {
      setFeedback({
        open: true,
        message: `Failed to delete task: ${err.message}`,
        severity: "error",
      });
    }
  };

  const handleStartSingleTask = async (taskId, task) => {
    if (!taskId || !task) {
      setFeedback({
        open: true,
        message: "Invalid task data",
        severity: "error",
      });
      return;
    }

    try {
      setFeedback({
        open: true,
        message: `Starting task: ${task.name || 'Unnamed Task'}`,
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
        throw new Error(`Failed to start task: ${importError.message}`);
      }

      setFeedback({
        open: true,
        message: `Task "${task.name || 'Unnamed Task'}" started successfully`,
        severity: "success",
      });

      // Refresh tasks to show updated statuses
      setTimeout(() => {
        fetchTasks();
      }, 1000);
    } catch (err) {
      setFeedback({
        open: true,
        message: `Failed to start task: ${err.message}`,
        severity: "error",
      });
    }
  };

  const handleClearAllTasks = async () => {
    if (tasks.length === 0) {
      setFeedback({
        open: true,
        message: "No tasks to clear",
        severity: "info",
      });
      return;
    }

    if (!window.confirm(`Are you sure you want to delete ALL ${tasks.length} tasks? This action cannot be undone.`)) {
      return;
    }

    try {
      setIsSaving(true);
      setFeedback({
        open: true,
        message: "Clearing all tasks...",
        severity: "info",
      });

      // Delete all tasks
      const deletePromises = tasks.map(task => deleteTask(task.id));
      await Promise.all(deletePromises);

      setFeedback({
        open: true,
        message: `Successfully deleted ${tasks.length} tasks`,
        severity: "success",
      });
      
      // Refresh tasks to show empty state
      fetchTasks();
    } catch (err) {
      setFeedback({
        open: true,
        message: `Failed to clear tasks: ${err.message}`,
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
      filtered = filtered.filter(t => t.status?.toLowerCase() === 'completed');
    } else if (statusFilter === 'failed') {
      filtered = filtered.filter(t => ['failed', 'error', 'cancelled', 'canceled'].includes(t.status?.toLowerCase()));
    }

    // Type filter
    if (typeFilter !== 'all') {
      filtered = filtered.filter(t => t.type === typeFilter);
    }

    return filtered.sort((a, b) => {
      if (sortBy === "created_at") {
        return new Date(b.created_at) - new Date(a.created_at);
      }
      if (sortBy === "name") {
        return (a.name || "").localeCompare(b.name || "");
      }
      if (sortBy === "status") {
        // Complete status order mapping including all possible states
        const statusOrder = {
          "in-progress": 0,
          "running": 1,
          "processing": 2,
          "queued": 3,
          "pending": 4,
          "paused": 5,
          "completed": 6,
          "complete": 7,
          "failed": 8,
          "error": 9,
          "cancelled": 10,
          "canceled": 11
        };
        const aOrder = statusOrder[a.status?.toLowerCase()] ?? 99;
        const bOrder = statusOrder[b.status?.toLowerCase()] ?? 99;
        return aOrder - bOrder;
      }
      return 0;
    });
  }, [tasks, sortBy, statusFilter, typeFilter]);

  return (
    <PageLayout
      title="Job Scheduler"
      variant="standard"
      actions={
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Chip label={`${tasks.filter(t => t.status === "pending").length} Pending`} size="small" variant="outlined" />
          <Chip label={`${tasks.filter(t => t.status === "queued").length} Queued`} size="small" variant="outlined" color="info" />
          <Chip label={`${tasks.filter(t => t.status === "in-progress").length} Running`} size="small" variant="outlined" />
          {queueOrder.length > 0 && (
            <Button variant="contained" size="small" startIcon={<PlayArrowIcon />} onClick={processQueue} disabled={isProcessingQueue || isLoading}>
              Run {queueOrder.length} Task{queueOrder.length !== 1 ? 's' : ''}
            </Button>
          )}
          {tasks.length > 0 && (
            <Button variant="outlined" size="small" startIcon={<ClearAllIcon />} onClick={handleClearAllTasks} disabled={isLoading || isSaving}>Clear All</Button>
          )}
          <Button
            variant="contained"
            size="small"
            startIcon={<AddIcon />}
            onClick={(e) => setNewTaskMenuAnchor(e.currentTarget)}
            disabled={isLoading || isSaving}
          >
            New Job
          </Button>
          <Menu
            anchorEl={newTaskMenuAnchor}
            open={Boolean(newTaskMenuAnchor)}
            onClose={() => setNewTaskMenuAnchor(null)}
          >
            <MuiMenuItem onClick={() => { setNewTaskMenuAnchor(null); handleQuickTaskAction("code_task"); }}>
              <ListItemIcon><CodeIcon fontSize="small" /></ListItemIcon>
              <ListItemText>Code Generation</ListItemText>
            </MuiMenuItem>
            <MuiMenuItem onClick={() => { setNewTaskMenuAnchor(null); handleQuickTaskAction("csv_generation"); }}>
              <ListItemIcon><TableChartIcon fontSize="small" /></ListItemIcon>
              <ListItemText>CSV / Bulk Content</ListItemText>
            </MuiMenuItem>
            <MuiMenuItem onClick={() => { setNewTaskMenuAnchor(null); handleQuickTaskAction("content_task"); }}>
              <ListItemIcon><DescriptionIcon fontSize="small" /></ListItemIcon>
              <ListItemText>Content Generation</ListItemText>
            </MuiMenuItem>
            <MuiMenuItem onClick={() => { setNewTaskMenuAnchor(null); handleQuickTaskAction("analysis_task"); }}>
              <ListItemIcon><AnalyticsIcon fontSize="small" /></ListItemIcon>
              <ListItemText>Data Analysis</ListItemText>
            </MuiMenuItem>
            <Divider />
            <MuiMenuItem onClick={() => { setNewTaskMenuAnchor(null); handleOpenTaskForm(); }}>
              <ListItemIcon><AddIcon fontSize="small" /></ListItemIcon>
              <ListItemText>Custom Job</ListItemText>
            </MuiMenuItem>
          </Menu>
        </Box>
      }
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel}
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

        {/* Enhanced Task Action Modal */}
        <TaskActionModal
          open={showTaskForm}
          onClose={handleCloseTaskForm}
          taskData={editingTask}
          onSave={handleTaskSave}
          isSaving={isSaving}
          onTaskCreated={handleTaskCreated}
          onTaskDeleted={handleTaskDeleted}
          onTaskDuplicated={handleTaskDuplicated}
        />

        {/* Filter bar */}
        <Box sx={{ display: 'flex', gap: 1, mb: 2, flexWrap: 'wrap', alignItems: 'center' }}>
          {['all', 'active', 'completed', 'failed'].map(f => (
            <Chip
              key={f}
              label={f.charAt(0).toUpperCase() + f.slice(1)}
              size="small"
              variant={statusFilter === f ? 'filled' : 'outlined'}
              color={statusFilter === f ? 'primary' : 'default'}
              onClick={() => setStatusFilter(f)}
            />
          ))}
          <Divider orientation="vertical" flexItem sx={{ mx: 1 }} />
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <InputLabel>Type</InputLabel>
            <Select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} label="Type">
              <MuiMenuItem value="all">All Types</MuiMenuItem>
              <MuiMenuItem value="code_generation">Code Gen</MuiMenuItem>
              <MuiMenuItem value="file_generation">CSV / Bulk</MuiMenuItem>
              <MuiMenuItem value="content_generation">Content</MuiMenuItem>
              <MuiMenuItem value="data_analysis">Analysis</MuiMenuItem>
            </Select>
          </FormControl>
        </Box>

        {/* Tasks Display */}
        {isLoading ? (
          <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", py: 6 }}>
            <ContextualLoader loading message="Loading tasks..." showProgress={false} inline />
          </Box>
        ) : sortedTasks.length === 0 ? (
          <Paper sx={{ p: 4, textAlign: "center", borderRadius: 3 }}>
            <EmptyState
              icon={<AssignmentOutlined />}
              title="No tasks found"
              description="Use the quick actions above to create your first task"
            />
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
                    onDuplicate={handleDuplicateTask}
                    onDelete={handleDeleteTask}
                    onStartJob={handleStartSingleTask}
                    availableProjects={availableProjects}
                  />
                </div>
              ))}
            </Box>

          </>
        )}

      </Box>

        <EntityContextMenu
          anchorPosition={contextMenu}
          onClose={() => { setContextMenu(null); setContextItem(null); }}
          actions={contextItem ? [
            {
              label: 'Start',
              onClick: () => handleStartSingleTask(contextItem.id, contextItem),
              disabled: ['in-progress', 'running', 'processing', 'completed', 'complete', 'queued'].includes(contextItem.status?.toLowerCase()),
            },
            {
              label: 'Cancel',
              onClick: () => {
                if (contextItem.job_id) {
                  cancelJob(contextItem.job_id);
                  setTimeout(fetchTasks, 1000);
                }
              },
              disabled: !['in-progress', 'running', 'processing', 'queued'].includes(contextItem.status?.toLowerCase()),
            },
            { label: 'Edit', onClick: () => handleEditTask(contextItem), dividerBefore: true },
            { label: 'Duplicate', onClick: () => handleDuplicateTask(contextItem.id) },
            { label: 'Delete', onClick: () => handleDeleteTask(contextItem.id, contextItem.name), dividerBefore: true, color: 'error.main' },
          ] : [
            { label: 'New Code Generation', icon: <CodeIcon fontSize="small" />, onClick: () => handleQuickTaskAction("code_task") },
            { label: 'New CSV / Bulk Content', icon: <TableChartIcon fontSize="small" />, onClick: () => handleQuickTaskAction("csv_generation") },
            { label: 'New Content Generation', icon: <DescriptionIcon fontSize="small" />, onClick: () => handleQuickTaskAction("content_task") },
            { label: 'New Data Analysis', icon: <AnalyticsIcon fontSize="small" />, onClick: () => handleQuickTaskAction("analysis_task") },
            { label: 'New Custom Job', icon: <AddIcon fontSize="small" />, onClick: () => handleOpenTaskForm(), dividerBefore: true },
          ]}
        />
    </PageLayout>
  );
};

export default TaskPage;
