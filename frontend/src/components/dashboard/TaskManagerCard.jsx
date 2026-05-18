// frontend/src/components/dashboard/TaskManagerCard.jsx
// Version 3.4: Fix Add button handler and restore dialog logic.

import React, { useState, useEffect, useCallback } from "react";
import {
  CircularProgress,
  Alert,
  Box,
  Chip,
  Typography,
  List,
  ListItem,
  ListItemText,
  Dialog, // Added for Add Task
  DialogActions, // Added
  DialogContent, // Added
  DialogTitle, // Added
  TextField, // Added
  Button, // Added
  Snackbar, // Added for feedback
  Alert as MuiAlert, // Alias Alert to avoid conflict if needed inside Snackbar
} from "@mui/material";
import { Link } from "@mui/material";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
// Import API functions - getTasks (functional), createTask (now functional)
import { getTasks, createTask } from "../../api";

// Helper component to render task status chip
const TaskStatusChip = ({ status }) => {
  let color = "default";
  let label = status || "Unknown";
  switch (status?.toLowerCase()) {
    case "pending":
      color = "info";
      break;
    case "in-progress":
    case "running":
      color = "warning";
      break;
    case "completed":
      color = "success";
      break;
    case "failed":
    case "error":
      color = "error";
      break;
    default:
      label = status
        ? status.charAt(0).toUpperCase() + status.slice(1)
        : "Unknown";
      break;
  }
  return (
    <Chip
      label={label}
      color={color}
      size="small"
      sx={{
        textTransform: "capitalize",
        height: "auto",
        lineHeight: "1.4",
        minWidth: "80px",
        textAlign: "center",
      }}
    />
  );
};

// Initial state for the Add Task form
const initialNewTaskState = {
  name: "",
  type: "",
  output: "", // Corresponds to Task model 'output' field
};

const TaskManagerCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      ...props
    },
    ref,
  ) => {
    const [tasks, setTasks] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState(null);
    const navigate = useNavigate();
    // State for Add Task Dialog
    const [addDialogOpen, setAddDialogOpen] = useState(false);
    const [newTaskData, setNewTaskData] = useState(initialNewTaskState);
    const [isSaving, setIsSaving] = useState(false);
    const [feedback, setFeedback] = useState({
      open: false,
      message: "",
      severity: "info",
    });

    // Fetch tasks
    const fetchTasksAndCounts = useCallback(async () => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await getTasks();
        if (data && data.error) {
          throw new Error(data.error);
        }
        const taskList = Array.isArray(data) ? data : [];
        taskList.sort(
          (a, b) => new Date(b.created_at) - new Date(a.created_at),
        );
        setTasks(taskList);
      } catch (err) {
        setError("Failed to load tasks.");
        console.error("Error fetching tasks:", err);
        setTasks([]);
      } finally {
        setIsLoading(false);
      }
    }, []);

    useEffect(() => {
      fetchTasksAndCounts();
    }, [fetchTasksAndCounts]);

    // --- Handlers for Add Task Dialog ---
    const handleOpenAddDialog = () => {
      setNewTaskData(initialNewTaskState); // Reset form
      setError(null); // Clear previous errors
      setAddDialogOpen(true); // *** This should open the dialog ***
    };

    const handleCloseAddDialog = () => {
      if (isSaving) return; // Prevent closing while saving
      setAddDialogOpen(false);
    };

    const handleNewTaskChange = (event) => {
      const { name, value } = event.target;
      setNewTaskData((prev) => ({ ...prev, [name]: value }));
    };

    const handleCreateTask = async () => {
      setIsSaving(true);
      setFeedback({ open: false, message: "" });
      setError(null);

      if (!newTaskData.name || !newTaskData.type) {
        setFeedback({
          open: true,
          message: "Task Name and Type are required.",
          severity: "warning",
        });
        setIsSaving(false);
        return;
      }

      try {
        // Call functional API function
        const result = await createTask(newTaskData);
        if (result && result.error) throw new Error(result.error);

        // Use the name from the result if available, otherwise from input
        const taskName = result?.name || newTaskData.name;
        setFeedback({
          open: true,
          message: `Task '${taskName}' created successfully!`,
          severity: "success",
        });
        handleCloseAddDialog();
        fetchTasksAndCounts(); // Re-fetch tasks list
      } catch (err) {
        console.error("Error creating task:", err);
        setFeedback({
          open: true,
          message: `Error creating task: ${err.message}`,
          severity: "error",
        });
      } finally {
        setIsSaving(false);
      }
    };
    // --- End Add Task Handlers ---

    // Close feedback snackbar
    const handleCloseFeedback = (event, reason) => {
      if (reason === "clickaway") return;
      setFeedback({ open: false, message: "" });
    };

    return (
      <>
        {" "}
        {/* Fragment */}
        <DashboardCardWrapper
          title="Tasks"
          ref={ref}
          style={style}
          isMinimized={isMinimized}
          onToggleMinimize={onToggleMinimize}
          cardColor={cardColor}
          onCardColorChange={onCardColorChange}
          {...props}
        >
          {isLoading ? (
            <Box
              sx={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: "100px",
              }}
            >
              <CircularProgress size={24} />
            </Box>
          ) : error ? (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          ) : (
            <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {/* Add Task Button */}
              <Box sx={{ display: "flex", justifyContent: "flex-end", mb: 1 }}>
                <Button
                  variant="contained"
                  size="small"
                  onClick={handleOpenAddDialog}
                  sx={{
                    minWidth: "90px",
                    textTransform: "none",
                    fontSize: "0.75rem",
                    padding: "4px 8px",
                  }}
                >
                  + Add Task
                </Button>
              </Box>

              {tasks.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No tasks found.
                </Typography>
              ) : (
                <List dense sx={{ p: 0 }}>
                  {tasks.slice(0, 5).map((task, index) => (
                    <ListItem
                      key={task.id || index}
                      sx={{
                        p: 0.5,
                        mb: 0.5,
                        border: 1,
                        borderColor: "divider",
                        borderRadius: 1,
                        cursor: "pointer",
                        "&:hover": {
                          backgroundColor: "action.hover",
                        },
                      }}
                      onClick={() => {
                        // Navigate to Tasks page with the task ID
                        navigate(`/tasks?taskId=${task.id || task.task_id}`);
                      }}
                    >
                      <ListItemText
                        primary={
                          <Typography
                            variant="body2"
                            sx={{
                              fontWeight: "medium",
                              fontSize: "0.8rem",
                              mb: 0.5,
                            }}
                          >
                            {task.name || task.task_name || "Unnamed Task"}
                          </Typography>
                        }
                        secondary={
                          <Box
                            sx={{
                              display: "flex",
                              alignItems: "center",
                              gap: 0.5,
                            }}
                          >
                            <TaskStatusChip status={task.status} />
                            <Typography
                              variant="caption"
                              sx={{
                                color: "text.secondary",
                                fontSize: "0.6rem",
                              }}
                            >
                              {task.created_at
                                ? new Date(task.created_at).toLocaleDateString()
                                : ""}
                            </Typography>
                          </Box>
                        }
                        secondaryTypographyProps={{
                          component: "div",
                          sx: { mt: 0.5 }
                        }}
                      />
                    </ListItem>
                  ))}
                </List>
              )}

              {tasks.length > 5 && (
                <Box sx={{ textAlign: "center", mt: 1 }}>
                  <Link
                    component={RouterLink}
                    to="/tasks"
                    variant="body2"
                    color="primary"
                    sx={{
                      textDecoration: "none",
                      fontSize: "0.75rem",
                      "&:hover": {
                        textDecoration: "underline",
                      },
                    }}
                  >
                    View All Tasks ({tasks.length})
                  </Link>
                </Box>
              )}
            </Box>
          )}
        </DashboardCardWrapper>
        {/* Add Task Dialog */}
        <Dialog
          open={addDialogOpen}
          onClose={handleCloseAddDialog}
          maxWidth="sm"
          fullWidth
        >
          <DialogTitle>Add New Task</DialogTitle>
          <DialogContent>
            <TextField
              autoFocus
              margin="dense"
              name="name"
              label="Task Name"
              type="text"
              fullWidth
              variant="outlined"
              value={newTaskData.name}
              onChange={handleNewTaskChange}
              required
            />
            <TextField
              margin="dense"
              name="type"
              label="Task Type"
              type="text"
              fullWidth
              variant="outlined"
              value={newTaskData.type}
              onChange={handleNewTaskChange}
              required
            />
            <TextField
              margin="dense"
              name="output"
              label="Output/Description"
              type="text"
              fullWidth
              variant="outlined"
              multiline
              rows={3}
              value={newTaskData.output}
              onChange={handleNewTaskChange}
              placeholder="Optional: Describe the expected output or additional details..."
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={handleCloseAddDialog} disabled={isSaving}>
              Cancel
            </Button>
            <Button
              onClick={handleCreateTask}
              variant="contained"
              disabled={isSaving}
            >
              {isSaving ? "Creating..." : "Create Task"}
            </Button>
          </DialogActions>
        </Dialog>
        {/* Feedback Snackbar */}
        <Snackbar
          open={feedback.open}
          autoHideDuration={6000}
          onClose={handleCloseFeedback}
          anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        >
          <MuiAlert
            elevation={6}
            variant="filled"
            onClose={handleCloseFeedback}
            severity={feedback.severity}
          >
            {feedback.message}
          </MuiAlert>
        </Snackbar>
      </>
    );
  },
);

TaskManagerCard.displayName = "TaskManagerCard";
export default TaskManagerCard;
