// frontend/src/pages/ProjectsPage.jsx
// Version 3.5: Aligned UI with TaskPage/WebsitesPage, added view toggle, table view, and description excerpts.
// - Header style, padding, margins, and colors updated.
// - Implemented card and table views with a toggle.
// - Card/row click now navigates to project detail page.
// - Table view includes sorting and an actions column for edit/delete.
// - Project description is shown as an excerpt in both views.
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from Dean (user/owner).

import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  Box,
  Typography,
  Button,
  CircularProgress,
  Alert as MuiAlert,
  Snackbar,
  Grid,
  Card,
  CardActionArea,
  CardContent,
  Tooltip,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  DialogContentText,
  TextField,
  Autocomplete,
  Stack,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import EditIcon from "@mui/icons-material/Edit";
import CloseIcon from "@mui/icons-material/Close";
import ViewModuleIcon from "@mui/icons-material/ViewModule";
import ViewListIcon from "@mui/icons-material/ViewList";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTheme } from "@mui/material/styles";

import {
  getProjects,
  createProject,
  updateProject,
  deleteProject,
  getClients,
  createClient as createClientApiService,
} from "../api";
import { useStatus } from "../contexts/StatusContext";
import { getLogoUrl } from "../config/logoConfig";
import PageLayout from "../components/layout/PageLayout";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

// Sorting functions
import { descendingComparator, getComparator, stableSort } from "../utils/sortUtils";

const formatDate = (dateString) => {
  if (!dateString) return "-";
  try {
    return new Date(dateString).toLocaleDateString();
  } catch (e) {
    console.warn("Error formatting date:", dateString, e);
    return dateString;
  }
};

function ProjectsPage() {
  const theme = useTheme();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { activeModel, isLoadingModel, modelError } = useStatus();

  const [projects, setProjects] = useState([]);
  const [clients, setClients] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState({
    open: false,
    message: "",
    severity: "info",
  });

  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [currentItem, setCurrentItem] = useState(null);

  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formClientValue, setFormClientValue] = useState(null); // Can be object or string for new client

  const [viewMode, setViewMode] = useState("card"); // 'card' or 'table'
  const [order, setOrder] = useState("asc");
  const [orderBy, setOrderBy] = useState("name");

  const fetchProjectsAndClients = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [projData, clientData] = await Promise.all([
        getProjects(),
        getClients(),
      ]);

      if (projData?.error)
        throw new Error(
          `Projects: ${projData.error.message || projData.error}`,
        );
      if (clientData?.error)
        throw new Error(
          `Clients: ${clientData.error.message || clientData.error}`,
        );

      setProjects(Array.isArray(projData) ? projData : []);
      setClients(
        Array.isArray(clientData)
          ? clientData.map((c) => ({ id: c.id, name: c.name }))
          : [],
      );
    } catch (err) {
      console.error("Error fetching projects or clients:", err);
      setError(`Failed to fetch data: ${err.message}`);
      setProjects([]);
      setClients([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProjectsAndClients();
  }, [fetchProjectsAndClients]);

  useEffect(() => {
    const idParam = searchParams.get("projectId");
    if (idParam && projects.length > 0) {
      const project = projects.find((p) => String(p.id) === idParam);
      if (project) {
        handleOpenEditDialog(project);
        const params = new URLSearchParams(searchParams);
        params.delete("projectId");
        setSearchParams(params, { replace: true });
      }
    }
  }, [projects, searchParams, setSearchParams]);

  const handleOpenEditDialog = (project = null) => {
    setCurrentItem(project);
    if (project) {
      setFormName(project.name);
      setFormDescription(project.description || "");
      const clientObj = clients.find((c) => c.id === project.client_id) || null;
      setFormClientValue(clientObj);
    } else {
      setFormName("");
      setFormDescription("");
      setFormClientValue(null);
    }
    setEditDialogOpen(true);
  };

  const handleCloseEditDialog = () => {
    if (isSaving) return;
    setEditDialogOpen(false);
    setCurrentItem(null);
    const params = new URLSearchParams(searchParams);
    params.delete("projectId");
    setSearchParams(params, { replace: true });
  };

  const handleOpenDeleteDialog = (project, event) => {
    if (event) event.stopPropagation(); // Prevent card/row click if delete icon is clicked
    setCurrentItem(project);
    setDeleteDialogOpen(true);
  };

  const handleCloseDeleteDialog = () => {
    setDeleteDialogOpen(false);
    setCurrentItem(null);
  };

  const handleSave = async () => {
    if (!formName.trim()) {
      setFeedback({
        open: true,
        message: "Project Name is required.",
        severity: "warning",
      });
      return;
    }
    if (!formClientValue) {
      setFeedback({
        open: true,
        message: "Client is required.",
        severity: "warning",
      });
      return;
    }

    setIsSaving(true);
    let clientIdToSave = null;

    try {
      if (
        typeof formClientValue === "object" &&
        formClientValue !== null &&
        formClientValue.id
      ) {
        clientIdToSave = formClientValue.id;
      } else if (
        typeof formClientValue === "string" &&
        formClientValue.trim() !== ""
      ) {
        const newClientName = formClientValue.trim();
        const existingClient = clients.find(
          (c) => c.name.toLowerCase() === newClientName.toLowerCase(),
        );
        if (existingClient) {
          clientIdToSave = existingClient.id;
        } else {
          const createClientResponse = await createClientApiService({
            name: newClientName,
          });
          if (createClientResponse?.error) {
            throw new Error(
              `Failed to create client: ${createClientResponse.error.message || createClientResponse.error}`,
            );
          }
          if (!createClientResponse?.id) {
            throw new Error(
              `Failed to get ID for newly created client: ${newClientName}`,
            );
          }
          clientIdToSave = createClientResponse.id;
          fetchProjectsAndClients(); // Re-fetch clients to include the new one
        }
      } else {
        throw new Error("Invalid client selection or input.");
      }

      if (!clientIdToSave) {
        throw new Error("Could not determine Client ID to save.");
      }

      const payload = {
        name: formName.trim(),
        description: formDescription.trim(),
        client_id: clientIdToSave,
      };

      let result;
      if (currentItem?.id) {
        result = await updateProject(currentItem.id, payload);
        if (result?.error)
          throw new Error(result.error.message || result.error);
        setFeedback({
          open: true,
          message: "Project updated successfully!",
          severity: "success",
        });
      } else {
        result = await createProject(payload);
        if (result?.error)
          throw new Error(result.error.message || result.error);
        setFeedback({
          open: true,
          message: "Project created successfully!",
          severity: "success",
        });
      }
      handleCloseEditDialog();
      fetchProjectsAndClients();
    } catch (err) {
      console.error("Save project error:", err);
      setFeedback({
        open: true,
        message: `Failed to save project: ${err.message}`,
        severity: "error",
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!currentItem?.id) return;
    setIsSaving(true); // Use general saving flag or a specific deleting flag
    try {
      const result = await deleteProject(currentItem.id);
      if (result?.error) throw new Error(result.error.message || result.error);
      setFeedback({
        open: true,
        message: "Project deleted successfully!",
        severity: "info",
      });
      handleCloseDeleteDialog();
      fetchProjectsAndClients();
    } catch (err) {
      console.error("Delete project error:", err);
      setFeedback({
        open: true,
        message: `Failed to delete project: ${err.message}`,
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

  const getClientName = (clientId) => {
    const client = clients.find((c) => c.id === clientId);
    return client ? client.name : "N/A";
  };

  const handleSortRequest = (property) => {
    const isAsc = orderBy === property && order === "asc";
    setOrder(isAsc ? "desc" : "asc");
    setOrderBy(property);
  };

  const sortedProjects = useMemo(() => {
    return stableSort(projects, getComparator(order, orderBy));
  }, [projects, order, orderBy]);

  const headCells = [
    { id: "name", label: "Project Name", sortable: true },
    { id: "client.name", label: "Client", sortable: true },
    { id: "description", label: "Description", sortable: false },
    { id: "document_count", label: "Docs", sortable: true, align: "right" },
    { id: "website_count", label: "Sites", sortable: true, align: "right" },
    { id: "task_count", label: "Tasks", sortable: true, align: "right" },
    { id: "updated_at", label: "Last Updated", sortable: true },
    { id: "actions", label: "Actions", sortable: false, align: "right" },
  ];

  return (
    <PageLayout
      title="Projects"
      variant="standard"
      viewToggle={{
        mode: viewMode,
        onToggle: (val) => setViewMode(val),
      }}
      actions={
        <Button
          variant="contained"
          size="small"
          startIcon={<AddIcon />}
          onClick={() => handleOpenEditDialog(null)}
          disabled={isSaving}
        >
          Add Project
        </Button>
      }
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel}
    >
        <Snackbar
          open={feedback.open}
          autoHideDuration={6000}
          onClose={handleCloseFeedback}
        >
          <AlertSnackbar
            onClose={handleCloseFeedback}
            severity={feedback.severity}
            variant="filled"
            sx={{ width: "100%" }}
          >
            {feedback.message}
          </AlertSnackbar>
        </Snackbar>

        {error && (
          <MuiAlert severity="error" sx={{ mb: 2 }}>
            {error}
          </MuiAlert>
        )}
        {isLoading && (
          <CircularProgress sx={{ display: "block", margin: "auto", my: 2 }} />
        )}

        {!isLoading && sortedProjects.length === 0 && !error && (
          <Typography sx={{ mt: 2, textAlign: "center", fontStyle: "italic" }}>
            No projects found. Add one to get started!
          </Typography>
        )}

        {!isLoading && sortedProjects.length > 0 && viewMode === "card" && (
          <Grid container spacing={2}>
            {sortedProjects.map((project) => (
              <Grid item xs={12} sm={6} md={4} lg={3} key={project.id}>
                <Card
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    height: "100%",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 2,
                    "&:hover": { boxShadow: theme.shadows[3] },
                  }}
                >
                  <CardActionArea
                    onClick={() => navigate(`/projects/${project.id}`)}
                    sx={{
                      flexGrow: 1,
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "stretch",
                      p: 1.5,
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
                            title={project.name}
                            sx={{ fontSize: "1rem", fontWeight: "medium" }}
                          >
                            {project.name}
                          </Typography>
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ mb: 1 }}
                            noWrap
                            title={getClientName(project.client_id)}
                          >
                            Client: {getClientName(project.client_id)}
                          </Typography>
                          <Tooltip
                            title={
                              project.description || "No description available."
                            }
                          >
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              sx={{
                                mb: 1,
                                fontStyle: project.description
                                  ? "normal"
                                  : "italic",
                                height: "3em",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                display: "-webkit-box",
                                WebkitLineClamp: 2,
                                WebkitBoxOrient: "vertical",
                              }}
                            >
                              {project.description || "No description"}
                            </Typography>
                          </Tooltip>
                          <Box
                            sx={{
                              display: "flex",
                              justifyContent: "space-between",
                              flexWrap: "wrap",
                              gap: 0.5,
                              fontSize: "0.75rem",
                              color: "text.disabled",
                            }}
                          >
                            <Typography variant="caption">
                              Docs: {project.document_count ?? 0}
                            </Typography>
                            <Typography variant="caption">
                              Sites: {project.website_count ?? 0}
                            </Typography>
                            <Typography variant="caption">
                              Tasks: {project.task_count ?? 0}
                            </Typography>
                            <Typography variant="caption">
                              Rules: {project.rule_count ?? 0}
                            </Typography>
                          </Box>
                        </Grid>
                        <Grid item xs={3} sx={{ textAlign: "right" }}>
                          {project.client?.logo_path && (
                            <img
                              src={getLogoUrl(project.client.logo_path)}
                              alt={project.client.name}
                              style={{ maxHeight: 60, maxWidth: "100%" }}
                            />
                          )}
                        </Grid>
                      </Grid>
                    </CardContent>
                  </CardActionArea>
                </Card>
              </Grid>
            ))}
          </Grid>
        )}

        {!isLoading && sortedProjects.length > 0 && viewMode === "table" && (
          <Paper elevation={2} sx={{ mb: 1, overflow: "hidden" }}>
            <TableContainer sx={{ maxHeight: "calc(100vh - 200px)" }}>
              <Table stickyHeader size="small">
                <TableHead>
                  <TableRow>
                    {headCells.map((headCell) => (
                      <TableCell
                        key={headCell.id}
                        align={headCell.align || "left"}
                        sortDirection={orderBy === headCell.id ? order : false}
                        sx={{ fontWeight: "bold" }}
                      >
                        {headCell.sortable ? (
                          <TableSortLabel
                            active={orderBy === headCell.id}
                            direction={orderBy === headCell.id ? order : "asc"}
                            onClick={() => handleSortRequest(headCell.id)}
                          >
                            {headCell.label}
                          </TableSortLabel>
                        ) : (
                          headCell.label
                        )}
                      </TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {sortedProjects.map((project) => (
                    <TableRow
                      key={project.id}
                      hover
                      onClick={() => navigate(`/projects/${project.id}`)}
                      sx={{
                        "&:hover": {
                          backgroundColor: theme.palette.action.hover,
                        },
                      }}
                    >
                      <TableCell>
                        <Typography
                          variant="body2"
                          noWrap
                          sx={{ maxWidth: 200 }}
                          title={project.name}
                        >
                          {project.name}
                        </Typography>
                      </TableCell>
                      <TableCell>{getClientName(project.client_id)}</TableCell>
                      <TableCell sx={{ maxWidth: 250 }}>
                        <Tooltip title={project.description || ""}>
                          <Typography variant="body2" noWrap>
                            {project.description || "-"}
                          </Typography>
                        </Tooltip>
                      </TableCell>
                      <TableCell align="right">
                        {project.document_count ?? 0}
                      </TableCell>
                      <TableCell align="right">
                        {project.website_count ?? 0}
                      </TableCell>
                      <TableCell align="right">
                        {project.task_count ?? 0}
                      </TableCell>
                      <TableCell>{formatDate(project.updated_at)}</TableCell>
                      <TableCell align="right" sx={{ pr: 2 }}>
                        <Tooltip title="Edit Project">
                          <IconButton
                            size="small"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleOpenEditDialog(project);
                            }}
                            disabled={isSaving}
                          >
                            <EditIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Delete Project">
                          <IconButton
                            size="small"
                            onClick={(e) => handleOpenDeleteDialog(project, e)}
                            disabled={isSaving}
                          >
                            <CloseIcon
                              fontSize="small"
                              sx={{ color: "text.secondary" }}
                            />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
            {sortedProjects.length > 0 && (
              <Typography
                variant="caption"
                display="block"
                sx={{
                  textAlign: "right",
                  p: 1,
                  color: "text.secondary",
                  borderTop: 1,
                  borderColor: "divider",
                }}
              >
                Total Projects: {sortedProjects.length}
              </Typography>
            )}
          </Paper>
        )}

      {/* Edit/Add Dialog (Modal) */}
      <Dialog
        open={editDialogOpen}
        onClose={handleCloseEditDialog}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>
          {currentItem?.id ? "Edit Project" : "Add New Project"}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              autoFocus
              required
              margin="dense"
              id="project-form-name"
              name="projectName"
              label="Project Name"
              type="text"
              fullWidth
              variant="outlined"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              disabled={isSaving}
            />
            <Autocomplete
              value={formClientValue}
              onChange={(event, newValue) => {
                setFormClientValue(newValue);
              }}
              options={clients} // Ensure 'clients' state is populated with {id, name} objects
              getOptionLabel={(option) => {
                if (typeof option === "string") return option; // For freeSolo new entries
                return option?.name || "";
              }}
              isOptionEqualToValue={(option, value) => {
                if (!option || !value) return false;
                if (typeof value === "string") return option.name === value; // Compare names for freeSolo
                if (typeof option === "string") return option === value.name; // Handle string option case
                return option.id === value?.id;
              }}
              freeSolo
              selectOnFocus
              clearOnBlur
              handleHomeEndKeys
              renderOption={(props, option) => (
                <Box component="li" {...props} key={option.id || option}>
                  {option.name || option}
                </Box>
              )}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Client"
                  required
                  margin="dense"
                  variant="outlined"
                  helperText="Select existing client or type a new name to create one."
                  error={
                    !formClientValue &&
                    formName.trim() /* Only show error if trying to save */
                  }
                />
              )}
              disabled={isSaving || isLoading}
            />
            <TextField
              margin="dense"
              id="project-form-description"
              name="projectDescription"
              label="Description (Optional)"
              type="text"
              fullWidth
              multiline
              rows={4}
              variant="outlined"
              value={formDescription}
              onChange={(e) => setFormDescription(e.target.value)}
              disabled={isSaving}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={handleCloseEditDialog}
            disabled={isSaving}
            color="inherit"
          >
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            variant="contained"
            disabled={
              isSaving || isLoading || !formName.trim() || !formClientValue
            }
          >
            {isSaving ? <CircularProgress size={24} /> : "Save Project"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onClose={handleCloseDeleteDialog}>
        <DialogTitle>Confirm Deletion</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to delete the project &quot;{currentItem?.name}&quot;?
            This action cannot be undone. Associated items may need to be
            reassigned or handled separately.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={handleCloseDeleteDialog}
            disabled={isSaving}
            color="inherit"
          >
            Cancel
          </Button>
          <Button
            onClick={handleDelete}
            color="error"
            variant="contained"
            disabled={isSaving}
          >
            {isSaving ? <CircularProgress size={24} /> : "Delete Project"}
          </Button>
        </DialogActions>
      </Dialog>
    </PageLayout>
  );
}

export default ProjectsPage;
