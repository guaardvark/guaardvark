// frontend/src/components/cards/TaskCard.jsx
// TaskCard component for the unified task dashboard

import React from "react";
import {
  Card,
  CardContent,
  Typography,
  Box,
  Chip,
  IconButton,
  Tooltip,
  Grid,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import CloseIcon from "@mui/icons-material/Close";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import { processTaskQueue } from "../../api/taskService";
import { useUiTranslation } from "../../i18n";

const TaskStatusChip = ({ status, retryCount }) => {
  const { t } = useUiTranslation();
  const getStatusConfig = (status) => {
    const cleanStatus = status?.replace('CSV Gen', '').trim().toLowerCase() || "unknown";

    switch (cleanStatus) {
      case "pending":
        return { color: "info", label: t("layoutCards.task.status.pending"), variant: "outlined" };
      case "queued":
        return { color: "info", label: t("layoutCards.task.status.queued"), variant: "filled" };
      case "in-progress":
      case "running":
        return { color: "warning", label: t("layoutCards.task.status.running"), variant: "filled" };
      case "paused":
        return { color: "secondary", label: t("layoutCards.task.status.paused"), variant: "outlined" };
      case "completed":
        return { color: "success", label: t("layoutCards.task.status.completed"), variant: "filled" };
      case "failed":
      case "error":
        return { color: "error", label: t("layoutCards.task.status.failed"), variant: "filled" };
      case "cancelled":
        return { color: "warning", label: t("layoutCards.task.status.cancelled"), variant: "outlined" };
      default:
        return { color: "default", label: status || t("layoutCards.task.status.unknown"), variant: "outlined" };
    }
  };

  const config = getStatusConfig(status);

  // Show retry count if task has retried
  const label = retryCount && retryCount > 0
    ? t("layoutCards.task.statusWithRetries", { status: config.label, retries: retryCount })
    : config.label;

  return (
    <Chip
      label={label}
      color={config.color}
      variant={config.variant}
      size="small"
      sx={{ fontWeight: "medium", minWidth: 80 }}
    />
  );
};

const TaskCard = ({ 
  task, 
  onEdit, 
  _onDuplicate, 
  onDelete, 
  onStartJob,
  availableProjects = [] 
}) => {
  const { t } = useUiTranslation();
  // Guard clause for null/undefined task
  if (!task) {
    return null;
  }
  
  const theme = useTheme();
  const { _activeProcesses, getProcess } = useUnifiedProgress();

  // BUG FIX #7: Safely get process data with null checks
  const processData = task?.job_id && getProcess && typeof getProcess === 'function'
    ? getProcess(task.job_id)
    : null;
  const realTimeProgress = processData?.progress || 0;
  const realTimeStatus = processData?.status || task?.status || 'unknown';
  
  const isQueued = realTimeStatus === "queued";
  const isRunning = realTimeStatus === "in-progress" || realTimeStatus === "running";
  const isCompleted = realTimeStatus === "completed" || realTimeStatus === "complete";
  const isFailed = realTimeStatus === "failed" || realTimeStatus === "error";

  const handleCardClick = () => {
    onEdit(task);
  };

  const handleJobAction = async (action, event) => {
    if (event && typeof event.stopPropagation === 'function') {
      event.stopPropagation();
    }

    // BUG FIX #8: Improved error handling for dynamic imports with proper fallbacks
    try {
      if (action === 'start') {
        // Call the onStartJob prop if provided
        if (onStartJob && typeof onStartJob === 'function') {
          await onStartJob(task.id, task);
        } else {
          // Use taskService processTaskQueue with static import
          let taskStarted = false;
          try {
            await processTaskQueue();
            taskStarted = true;
          } catch (importError) {
            console.warn('Failed to run processTaskQueue, trying fallback:', importError);
          }

          // Fallback to basic task start API if taskService failed
          if (!taskStarted) {
            try {
              const apiModule = await import('../../api');
              if (apiModule.startTask && typeof apiModule.startTask === 'function') {
                await apiModule.startTask(task.id);
              } else {
                throw new Error('startTask function not found in API module');
              }
            } catch (fallbackError) {
              console.error('Failed to start task with fallback:', fallbackError);
              throw new Error(`Unable to start task: ${fallbackError.message}`);
            }
          }
        }
      } else if (action === 'pause' || action === 'stop') {
        // Import and use cancelJob from API
        if (!task.job_id) {
          console.warn('No job_id found for task:', task.id, '- Cannot cancel job without job_id');
          throw new Error('Cannot stop task: No active job found. The task may not have been started yet.');
        }

        try {
          const apiModule = await import('../../api');
          if (apiModule.cancelJob && typeof apiModule.cancelJob === 'function') {
            await apiModule.cancelJob(task.job_id);
          } else {
            throw new Error('cancelJob function not available');
          }
        } catch (cancelError) {
          console.error('Failed to cancel job:', cancelError);
          throw new Error(`Unable to cancel job: ${cancelError.message}`);
        }
      }
    } catch (error) {
      console.error(`Failed to ${action} job for task ${task.id}:`, error);
      // You might want to show user feedback here
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return "-";
    try {
      return new Date(dateString).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (error) {
      return "-";
    }
  };

  const getProjectName = () => {
    if (task.project?.name) return task.project.name;
    if (task.project_id && availableProjects.length > 0) {
      const project = availableProjects.find(p => p.id === task.project_id);
      return project?.name || t("layoutCards.task.projectNumber", { id: task.project_id });
    }
    return task.project_id ? t("layoutCards.task.projectNumber", { id: task.project_id }) : t("layoutCards.task.noProject");
  };

  return (
    <Card
      sx={{
        position: "relative",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        border: "2px solid",
        borderColor: isQueued ? theme.palette.info.light :
                    isRunning ? theme.palette.info.main :
                    isCompleted ? theme.palette.success.main :
                    isFailed ? theme.palette.error.main : "divider",
        borderRadius: 3,
        transition: "all 0.2s ease-in-out",
        "&:hover": {
          boxShadow: theme.shadows[6],
          transform: "translateY(-2px)",
        },
      }}
    >
      <Box
        onClick={handleCardClick}
        sx={{
          flexGrow: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "stretch",
          p: 1.5,
          cursor: "pointer",
          "&:hover": {
            backgroundColor: theme.palette.action.hover,
          },
        }}
      >
        <CardContent sx={{ flexGrow: 1, p: 0 }}>
          <Grid container spacing={1} alignItems="flex-start">
            <Grid item xs={9}>
              <Typography
                variant="h6"
                component="div"
                gutterBottom
                noWrap
                title={task.name || t("layoutCards.task.untitled")}
                sx={{ fontSize: "1rem", fontWeight: "medium" }}
              >
                {task.name || t("layoutCards.task.untitled")}
              </Typography>
              
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mb: 1 }}
                noWrap
                title={`${getProjectName()} • ${task?.client_name || t("layoutCards.task.noClient")}`}
              >
                {getProjectName()} • {task?.client_name || t("layoutCards.task.noClient")}
              </Typography>

              {/* Progress info for running/queued tasks - progress bar moved to ProgressFooterBar */}
              {isQueued && (
                <Box sx={{ mb: 1 }}>
                  <Typography variant="caption" color="info.main" noWrap>
                    {t("layoutCards.task.queuedForExecution")}
                  </Typography>
                </Box>
              )}
              {isRunning && (
                <Box sx={{ mb: 1 }}>
                  <Typography variant="caption" color="info.main" noWrap>
                    {processData?.message || t("layoutCards.task.processing")} ({realTimeProgress}%)
                  </Typography>
                </Box>
              )}

              <Tooltip title={task.description || t("layoutCards.task.noDescriptionAvailable")}>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{
                    mb: 1,
                    fontStyle: task.description ? "normal" : "italic",
                    height: "3em",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                  }}
                >
                  {task.description || t("layoutCards.task.noDescription")}
                </Typography>
              </Tooltip>

              <Box sx={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", alignItems: "flex-end" }}>
                <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
                  {task.type && (
                    <Chip 
                      label={task.type.replace('_', ' ')} 
                      size="small" 
                      variant="outlined"
                      sx={{ textTransform: "capitalize", fontSize: "0.75rem" }}
                    />
                  )}
                  {task.output_filename && (
                    <Chip 
                      label={task.output_filename} 
                      size="small" 
                      variant="outlined"
                      color="secondary"
                      sx={{ fontSize: "0.75rem" }}
                    />
                  )}
                </Box>
                
                <Typography variant="caption" color="text.secondary">
                  {formatDate(task.created_at)}
                </Typography>
              </Box>
            </Grid>

            <Grid item xs={3} sx={{ position: "relative" }}>
              {/* Quick Delete Button - Top Right */}
              <Tooltip title={t("layoutCards.task.deleteTask")}>
                <IconButton
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(task.id, task.name, e);
                  }}
                  sx={{
                    position: "absolute",
                    top: -8,
                    right: -8,
                    backgroundColor: "transparent",
                    "&:hover": {
                      backgroundColor: "action.hover",
                      color: "text.primary",
                    },
                    zIndex: 1,
                  }}
                >
                  <CloseIcon fontSize="small" sx={{ color: "text.secondary" }} />
                </IconButton>
              </Tooltip>
            </Grid>
          </Grid>
        </CardContent>
      </Box>

      {/* Enhanced Job Control Buttons - Middle Right (positioned relative to Card) */}
      {!isCompleted && (
        <Box sx={{
          position: "absolute",
          top: "50%",
          right: 8,
          transform: "translateY(-50%)",
          zIndex: 2
        }}>
          {!isRunning && !isQueued ? (
            <Tooltip title={task.status === 'pending' ? t("layoutCards.task.runTask") : t("layoutCards.task.startJob")}>
              <IconButton
                size="small"
                color="success"
                onClick={(e) => handleJobAction('start', e)}
                sx={{
                  backgroundColor: "success.light",
                  color: "success.contrastText",
                  "&:hover": {
                    backgroundColor: "success.main",
                  },
                }}
              >
                <PlayArrowIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          ) : (
            <Tooltip title={isQueued ? t("layoutCards.task.cancelQueuedTask") : t("layoutCards.task.stopJob")}>
              <IconButton
                size="small"
                onClick={(e) => handleJobAction('stop', e)}
                sx={{
                  backgroundColor: "warning.light",
                  color: "warning.contrastText",
                  "&:hover": {
                    backgroundColor: "warning.main",
                  },
                }}
              >
                <StopIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
        </Box>
      )}

      {/* Status Chip - Bottom Right */}
      <Box sx={{ position: "absolute", bottom: 8, right: 8 }}>
        <TaskStatusChip status={realTimeStatus} retryCount={task.retry_count} />
      </Box>

    </Card>
  );
};

export default TaskCard;