// frontend/src/components/modals/JobDetailsModal.jsx
// Job Details Modal - Comprehensive job monitoring and management
// Leverages existing unified progress system and job management APIs

import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Typography,
  LinearProgress,
  Chip,
  Grid,
  Card,
  CardContent,
  IconButton,
  Tooltip,
  Alert,
  CircularProgress,
  Divider,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
} from "@mui/material";
import {
  Close as CloseIcon,
  PlayArrow as PlayIcon,
  Pause as PauseIcon,
  Stop as StopIcon,
  Delete as DeleteIcon,
  Refresh as RefreshIcon,
  Schedule as ScheduleIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Warning as WarningIcon,
  Info as InfoIcon,
} from "@mui/icons-material";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import { useSnackbar } from "../common/SnackbarProvider";
import * as progressService from "../../api/progressService";
// taskService now dynamically imported

const JobDetailsModal = ({
  open,
  onClose,
  jobId,
  taskData = null, // Optional task data for context
}) => {
  const [jobData, setJobData] = useState(null);
  const [jobHistory, setJobHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [actionInProgress, setActionInProgress] = useState(false);
  
  // Race condition protection
  const isClosingRef = useRef(false);
  
  const { activeProcesses } = useUnifiedProgress();
  const { showMessage } = useSnackbar();

  // Get real-time job data from unified progress context
  const realTimeJobData = activeProcesses.get(jobId);

  // Fetch job details and history
  const fetchJobDetails = useCallback(async () => {
    if (!jobId) return;
    
    setLoading(true);
    setError(null);
    
    try {
      // Get job metadata from API
      const response = await fetch(`/api/meta/active_jobs`);
      const data = await response.json();
      
      // Try to find job by exact ID first
      let job = data.active_jobs?.find(j => j.id === jobId) || 
                data.stuck_jobs?.find(j => j.id === jobId);
      
      // If not found, try to find by process_type and job_id pattern
      if (!job && jobId.startsWith('task_')) {
        const taskId = jobId.replace('task_', '');
        job = data.active_jobs?.find(j => 
          j.process_type === 'file_generation' && 
          j.additional_data?.task_id === taskId
        ) || data.stuck_jobs?.find(j => 
          j.process_type === 'file_generation' && 
          j.additional_data?.task_id === taskId
        );
      }
      
      if (job) {
        setJobData(job);
      } else {
        // Create a mock job data for completed tasks
        if (taskData && (taskData.status === 'completed' || taskData.status === 'completedCSV Gen')) {
          const mockJob = {
            id: jobId,
            status: 'complete',
            progress: 100,
            message: 'Task completed successfully',
            process_type: taskData.type || 'file_generation',
            start_time: taskData.created_at,
            last_update: taskData.updated_at || taskData.created_at,
            additional_data: {
              task_id: taskData.id,
              task_name: taskData.name
            }
          };
          console.log('Creating mock job data:', mockJob);
          setJobData(mockJob);
        } else {
          console.log('No task data or task not completed:', taskData);
          setError("Job not found");
        }
      }
    } catch (err) {
      console.error("Failed to fetch job details:", err);
      setError("Failed to load job details");
    } finally {
      setLoading(false);
    }
  }, [jobId, taskData]);

  // Single data fetch when modal opens
  useEffect(() => {
    if (!open || !jobId || isClosingRef.current) {
      return;
    }
    
    // Reset closing flag when modal opens
    isClosingRef.current = false;
    
    // Single fetch - no auto-refresh
    fetchJobDetails();
  }, [open, jobId]); // Only fetch when modal opens or jobId changes

  // Job action handlers
  const handleCancelJob = async () => {
    if (!jobId) return;
    
    setActionInProgress(true);
    try {
      await progressService.cancelJob(jobId);
      showMessage("Job cancelled successfully", "success");
    } catch (err) {
      console.error(`Failed to cancel job: ${err.message}`);
      showMessage(`Failed to cancel job: ${err.message}`, "error");
    } finally {
      setActionInProgress(false);
    }
  };

  const handleDeleteJob = async () => {
    if (!jobId) return;
    
    if (!window.confirm("Are you sure you want to delete this job? This action cannot be undone.")) {
      return;
    }
    
    setActionInProgress(true);
    try {
      await progressService.deleteJob(jobId);
      showMessage("Job deleted successfully", "success");
      onClose();
    } catch (err) {
      console.error(`Failed to delete job: ${err.message}`);
      showMessage(`Failed to delete job: ${err.message}`, "error");
    } finally {
      setActionInProgress(false);
    }
  };

  const handleRetryJob = async () => {
    if (!jobId || !taskData) return;
    
    setActionInProgress(true);
    try {
      await progressService.retryJob(jobId);
      
      showMessage("Job retry initiated", "success");
    } catch (err) {
      console.error(`Failed to retry job: ${err.message}`);
      showMessage(`Failed to retry job: ${err.message}`, "error");
    } finally {
      setActionInProgress(false);
    }
  };

  const handleDuplicateTask = async () => {
    if (!taskData?.id) return;
    
    setActionInProgress(true);
    try {
      const taskService = await import("../../api/taskService");
      const duplicatedTask = await taskService.duplicateTask(taskData.id);
      
      showMessage("Task duplicated successfully", "success");
      
      // Close modal and let parent refresh task list
      onClose();
    } catch (err) {
      console.error(`Failed to duplicate task: ${err.message}`);
      showMessage(`Failed to duplicate task: ${err.message}`, "error");
    } finally {
      setActionInProgress(false);
    }
  };

  const handleStopTask = async () => {
    if (!taskData?.id) return;
    
    if (!window.confirm("Are you sure you want to stop this task? This will cancel any ongoing processing.")) {
      return;
    }
    
    setActionInProgress(true);
    try {
      // First try to cancel the job if it exists
      if (jobId) {
        await progressService.cancelJob(jobId);
      }
      
      // Then update the task status to cancelled
      const taskService = await import("../../api/taskService");
      await taskService.updateTask(taskData.id, { status: "cancelled" });
      
      showMessage("Task stopped successfully", "success");
      
      // Refresh job details
      fetchJobDetails();
    } catch (err) {
      console.error(`Failed to stop task: ${err.message}`);
      showMessage(`Failed to stop task: ${err.message}`, "error");
    } finally {
      setActionInProgress(false);
    }
  };

  const handleDeleteTask = async () => {
    if (!taskData?.id) return;
    
    if (!window.confirm("Are you sure you want to delete this task? This action cannot be undone and will also delete any associated job data.")) {
      return;
    }
    
    setActionInProgress(true);
    try {
      // First try to delete the job if it exists
      if (jobId) {
        await progressService.deleteJob(jobId);
      }
      
      // Then delete the task
      const taskService = await import("../../api/taskService");
      await taskService.deleteTask(taskData.id);
      
      showMessage("Task deleted successfully", "success");
      
      // Close modal and let parent refresh task list
      onClose();
    } catch (err) {
      console.error(`Failed to delete task: ${err.message}`);
      showMessage(`Failed to delete task: ${err.message}`, "error");
    } finally {
      setActionInProgress(false);
    }
  };

  // Helper functions
  const getStatusIcon = (status) => {
    switch (status) {
      case "complete":
        return <CheckCircleIcon color="success" />;
      case "error":
        return <ErrorIcon color="error" />;
      case "cancelled":
        return <StopIcon color="warning" />;
      case "processing":
        return <CircularProgress size={20} />;
      default:
        return <InfoIcon color="info" />;
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case "complete":
        return "success";
      case "error":
        return "error";
      case "cancelled":
        return "warning";
      case "processing":
        return "primary";
      default:
        return "default";
    }
  };

  const formatDuration = (startTime, endTime = null) => {
    const start = new Date(startTime);
    const end = endTime ? new Date(endTime) : new Date();
    const duration = end - start;
    
    const seconds = Math.floor(duration / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    
    if (hours > 0) {
      return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
    } else if (minutes > 0) {
      return `${minutes}m ${seconds % 60}s`;
    } else {
      return `${seconds}s`;
    }
  };

  const formatTimestamp = (timestamp) => {
    return new Date(timestamp).toLocaleString();
  };

  // Use real-time data if available, otherwise use fetched data
  const displayJobData = realTimeJobData || jobData;

  // Debug logging
  console.log('Modal render state:', {
    open,
    loading,
    hasRealTimeData: !!realTimeJobData,
    hasJobData: !!jobData,
    displayJobData: !!displayJobData,
    error
  });

  // Don't render anything if modal is not open
  if (!open) {
    return null;
  }

  if (!displayJobData && !loading) {
    console.log('Modal returning null - no display data and not loading');
    return null;
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="md"
      aria-labelledby="job-details-modal-title"
    >
      <DialogTitle
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          pt: 1.5,
          pb: 1,
          m: 0,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          {displayJobData && getStatusIcon(displayJobData.status)}
          <Typography variant="h6" component="div" noWrap>
            Job Details: {displayJobData?.id || jobId}
          </Typography>
        </Box>
        <IconButton
          onClick={onClose}
          size="small"
          sx={{ ml: 2 }}
          disabled={actionInProgress}
        >
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
            <CircularProgress />
          </Box>
        ) : displayJobData ? (
          <Box sx={{ mt: 1 }}>
            {/* Job Status and Progress */}
            <Card sx={{ mb: 2 }}>
              <CardContent>
                <Grid container spacing={2} alignItems="center">
                  <Grid item xs={12} md={6}>
                    <Typography variant="h6" gutterBottom>
                      Status
                    </Typography>
                    <Chip
                      label={displayJobData.status.toUpperCase()}
                      color={getStatusColor(displayJobData.status)}
                      icon={getStatusIcon(displayJobData.status)}
                      sx={{ mb: 1 }}
                    />
                    <Typography variant="body2" color="text.secondary">
                      {displayJobData.message}
                    </Typography>
                  </Grid>
                  <Grid item xs={12} md={6}>
                    <Typography variant="h6" gutterBottom>
                      Progress
                    </Typography>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <LinearProgress
                        variant="determinate"
                        value={displayJobData.progress || 0}
                        sx={{ flexGrow: 1 }}
                      />
                      <Typography variant="body2">
                        {displayJobData.progress || 0}%
                      </Typography>
                    </Box>
                  </Grid>
                </Grid>
              </CardContent>
            </Card>

            {/* Job Information */}
            <Card sx={{ mb: 2 }}>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Job Information
                </Typography>
                <Grid container spacing={2}>
                  <Grid item xs={12} md={6}>
                    <Typography variant="body2" color="text.secondary">
                      Process Type
                    </Typography>
                    <Typography variant="body1">
                      {displayJobData.process_type}
                    </Typography>
                  </Grid>
                  <Grid item xs={12} md={6}>
                    <Typography variant="body2" color="text.secondary">
                      Job ID
                    </Typography>
                    <Typography variant="body1" fontFamily="monospace">
                      {displayJobData.id}
                    </Typography>
                  </Grid>
                  <Grid item xs={12} md={6}>
                    <Typography variant="body2" color="text.secondary">
                      Start Time
                    </Typography>
                    <Typography variant="body1">
                      {formatTimestamp(displayJobData.start_time)}
                    </Typography>
                  </Grid>
                  <Grid item xs={12} md={6}>
                    <Typography variant="body2" color="text.secondary">
                      Last Update
                    </Typography>
                    <Typography variant="body1">
                      {formatTimestamp(displayJobData.last_update)}
                    </Typography>
                  </Grid>
                  <Grid item xs={12}>
                    <Typography variant="body2" color="text.secondary">
                      Duration
                    </Typography>
                    <Typography variant="body1">
                      {formatDuration(displayJobData.start_time, displayJobData.last_update)}
                    </Typography>
                  </Grid>
                </Grid>
              </CardContent>
            </Card>

            {/* Additional Data */}
            {displayJobData.additional_data && Object.keys(displayJobData.additional_data).length > 0 && (
              <Card sx={{ mb: 2 }}>
                <CardContent>
                  <Typography variant="h6" gutterBottom>
                    Additional Information
                  </Typography>
                  <Grid container spacing={2}>
                    {Object.entries(displayJobData.additional_data).map(([key, value]) => (
                      <Grid item xs={12} md={6} key={key}>
                        <Typography variant="body2" color="text.secondary">
                          {key.replace(/_/g, ' ').toUpperCase()}
                        </Typography>
                        <Typography variant="body1">
                          {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                        </Typography>
                      </Grid>
                    ))}
                  </Grid>
                </CardContent>
              </Card>
            )}

            {/* Task Context (if available) */}
            {taskData && (
              <Card sx={{ mb: 2 }}>
                <CardContent>
                  <Typography variant="h6" gutterBottom>
                    Associated Task
                  </Typography>
                  <Grid container spacing={2}>
                    <Grid item xs={12} md={6}>
                      <Typography variant="body2" color="text.secondary">
                        Task Name
                      </Typography>
                      <Typography variant="body1">
                        {taskData.name}
                      </Typography>
                    </Grid>
                    <Grid item xs={12} md={6}>
                      <Typography variant="body2" color="text.secondary">
                        Task Type
                      </Typography>
                      <Typography variant="body1">
                        {taskData.type}
                      </Typography>
                    </Grid>
                  </Grid>
                </CardContent>
              </Card>
            )}
          </Box>
        ) : null}
      </DialogContent>

      <DialogActions sx={{ justifyContent: "space-between", px: 3, pb: 2, pt: 2 }}>
        <Box sx={{ display: "flex", gap: 1 }}>
          {/* Task Actions */}
          {taskData && (
            <>
              <Tooltip title="Duplicate Task">
                <span>
                  <IconButton
                    color="primary"
                    onClick={handleDuplicateTask}
                    disabled={actionInProgress}
                  >
                    <RefreshIcon />
                  </IconButton>
                </span>
              </Tooltip>
              
              <Tooltip title="Stop Task">
                <span>
                  <IconButton
                    color="warning"
                    onClick={handleStopTask}
                    disabled={actionInProgress}
                  >
                    <StopIcon />
                  </IconButton>
                </span>
              </Tooltip>
              
              <Tooltip title="Delete Task">
                <span>
                  <IconButton
                    color="error"
                    onClick={handleDeleteTask}
                    disabled={actionInProgress}
                  >
                    <DeleteIcon />
                  </IconButton>
                </span>
              </Tooltip>
            </>
          )}
          
          {/* Job Actions */}
          {displayJobData && !taskData && (
            <>
              {displayJobData.status === "processing" && (
                <Tooltip title="Cancel Job">
                  <span>
                    <IconButton
                      color="warning"
                      onClick={handleCancelJob}
                      disabled={actionInProgress}
                    >
                      <StopIcon />
                    </IconButton>
                  </span>
                </Tooltip>
              )}
              
              {displayJobData.status === "error" && (
                <Tooltip title="Retry Job">
                  <span>
                    <IconButton
                      color="primary"
                      onClick={handleRetryJob}
                      disabled={actionInProgress}
                    >
                      <RefreshIcon />
                    </IconButton>
                  </span>
                </Tooltip>
              )}
              
              <Tooltip title="Delete Job">
                <span>
                  <IconButton
                    color="error"
                    onClick={handleDeleteJob}
                    disabled={actionInProgress}
                  >
                    <DeleteIcon />
                  </IconButton>
                </span>
              </Tooltip>
            </>
          )}
        </Box>

        <Box sx={{ display: "flex", gap: 1 }}>
          <Button
            onClick={() => {
              isClosingRef.current = true;
              onClose();
            }}
            disabled={actionInProgress}
            color="inherit"
          >
            Close
          </Button>
          
          <Button
            onClick={fetchJobDetails}
            disabled={actionInProgress}
            startIcon={<RefreshIcon />}
          >
            Refresh
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default JobDetailsModal;
