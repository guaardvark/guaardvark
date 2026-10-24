// frontend/src/pages/ProjectsPage.jsx
// Version 3.5: Aligned UI with TaskPage/WebsitesPage, added view toggle, table view, and description excerpts.
// - Header style, padding, margins, and colors updated.
// - Implemented card and table views with a toggle.
// - Card/row click now navigates to project detail page.
// - Table view includes sorting and an actions column for edit/delete.
// - Project description is shown as an excerpt in both views.
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from the project maintainer.

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
import FolderOutlined from "@mui/icons-material/FolderOutlined";
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
import EntityContextMenu from "../components/common/EntityContextMenu";
import useContextMenu from "../hooks/useContextMenu";
import { entityLinkActions } from "../utils/entityLinks";
import EmptyState from "../components/common/EmptyState";
import { ContextualLoader } from "../components/common/LoadingStates";
import { uiLocale, useUiTranslation } from "../i18n";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

// Sorting functions
import { getComparator, stableSort } from "../utils/sortUtils";

const formatDate = (dateString) => {
  if (!dateString) return "-";
  try {
    return new Date(dateString).toLocaleDateString(uiLocale());
  } catch (e) {
    console.warn("Error formatting date:", dateString, e);
    return dateString;
  }
};

function ProjectsPage() {
  const { t } = useUiTranslation();
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

  // Right-click menu; fields and dialogs keep the browser's own menu.
  const pageMenu = useContextMenu();
  const contextItem = pageMenu.payload;
  const handleContextMenu = (e, project = null) => pageMenu.open(e, project);

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
          t("projects.list.loadErrors.projects", { error: projData.error.message || projData.error }),
        );
      if (clientData?.error)
        throw new Error(
          t("projects.list.loadErrors.clients", { error: clientData.error.message || clientData.error }),
        );

      setProjects(Array.isArray(projData) ? projData : []);
      setClients(
        Array.isArray(clientData)
          ? clientData.map((c) => ({ id: c.id, name: c.name }))
          : [],
      );
    } catch (err) {
      console.error("Error fetching projects or clients:", err);
      setError(t("projects.list.fetchFailed", { error: err.message }));
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
        message: t("projects.list.nameRequired"),
        severity: "warning",
      });
      return;
    }
    if (!formClientValue) {
      setFeedback({
        open: true,
        message: t("projects.list.clientRequired"),
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
              t("projects.list.createClientFailed", {
                error: createClientResponse.error.message || createClientResponse.error,
              }),
            );
          }
          if (!createClientResponse?.id) {
            throw new Error(
              t("projects.list.newClientNoId", { name: newClientName }),
            );
          }
          clientIdToSave = createClientResponse.id;
          fetchProjectsAndClients(); // Re-fetch clients to include the new one
        }
      } else {
        throw new Error(t("projects.list.invalidClient"));
      }

      if (!clientIdToSave) {
        throw new Error(t("projects.list.noClientId"));
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
          message: t("projects.list.updated"),
          severity: "success",
        });
      } else {
        result = await createProject(payload);
        if (result?.error)
          throw new Error(result.error.message || result.error);
        setFeedback({
          open: true,
          message: t("projects.list.created"),
          severity: "success",
        });
      }
      handleCloseEditDialog();
      fetchProjectsAndClients();
    } catch (err) {
      console.error("Save project error:", err);
      setFeedback({
        open: true,
        message: t("projects.list.saveFailed", { error: err.message }),
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
        message: t("projects.list.deleted"),
        severity: "info",
      });
      handleCloseDeleteDialog();
      fetchProjectsAndClients();
    } catch (err) {
      console.error("Delete project error:", err);
      setFeedback({
        open: true,
        message: t("projects.list.deleteFailed", { error: err.message }),
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
    return client ? client.name : t("projects.list.notAvailable");
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
    { id: "name", label: t("projects.list.columns.name"), sortable: true },
    { id: "client.name", label: t("projects.list.columns.client"), sortable: true },
    { id: "description", label: t("projects.list.columns.description"), sortable: false },
    { id: "document_count", label: t("projects.list.columns.docs"), sortable: true, align: "right" },
    { id: "website_count", label: t("projects.list.columns.sites"), sortable: true, align: "right" },
    { id: "task_count", label: t("projects.list.columns.tasks"), sortable: true, align: "right" },
    { id: "updated_at", label: t("projects.list.columns.updated"), sortable: true },
    { id: "actions", label: t("projects.list.columns.actions"), sortable: false, align: "right" },
  ];

  return (
    <PageLayout
      title={t("projects.list.title")}
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
          {t("projects.list.addProject")}
        </Button>
      }
      modelStatus
      activeModel={
        isLoadingModel
          ? t("projects.list.modelLoading")
          : modelError
            ? t("projects.list.modelError")
            : activeModel
      }
    >
      <Box
        onContextMenu={(e) => handleContextMenu(e, null)}
        sx={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
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
          <ContextualLoader loading message={t("projects.list.loading")} showProgress={false} inline />
        )}

        {!isLoading && sortedProjects.length === 0 && !error && (
          <EmptyState
            icon={<FolderOutlined />}
            title={t("projects.list.emptyTitle")}
            description={t("projects.list.emptyDescription")}
          />
        )}

        {!isLoading && sortedProjects.length > 0 && viewMode === "card" && (
          <Grid container spacing={2}>
            {sortedProjects.map((project) => (
              <Grid item xs={12} sm={6} md={4} lg={3} key={project.id}>
                <Card
                  onContextMenu={(e) => handleContextMenu(e, project)}
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
                            {t("projects.list.client", { name: getClientName(project.client_id) })}
                          </Typography>
                          <Tooltip
                            title={
                              project.description || t("projects.list.noDescriptionAvailable")
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
                              {project.description || t("projects.list.noDescription")}
                            </Typography>
                          </Tooltip>
                          <Box
                            sx={{
                              display: "flex",
                              justifyContent: "space-between",
                              flexWrap: "wrap",
                              gap: 0.5,
                              fontSize: "0.75rem",
                              color: "text.secondary",
                            }}
                          >
                            <Typography variant="caption">
                              {t("projects.list.docsCount", { count: project.document_count ?? 0 })}
                            </Typography>
                            <Typography variant="caption">
                              {t("projects.list.sitesCount", { count: project.website_count ?? 0 })}
                            </Typography>
                            <Typography variant="caption">
                              {t("projects.list.tasksCount", { count: project.task_count ?? 0 })}
                            </Typography>
                            <Typography variant="caption">
                              {t("projects.list.rulesCount", { count: project.rule_count ?? 0 })}
                            </Typography>
                            {project.video_count > 0 && (
                              <Typography variant="caption">
                                {t("projects.list.videosCount", { count: project.video_count })}
                              </Typography>
                            )}
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
                      onContextMenu={(e) => handleContextMenu(e, project)}
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
                        <Tooltip title={t("projects.list.editProject")}>
                          <span>
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
                          </span>
                        </Tooltip>
                        <Tooltip title={t("projects.list.deleteProject")}>
                          <span>
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
                          </span>
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
                {t("projects.list.total", { total: sortedProjects.length })}
              </Typography>
            )}
          </Paper>
        )}

      </Box>

      <EntityContextMenu
        anchorPosition={pageMenu.anchorPosition}
        onClose={pageMenu.close}
        actions={contextItem ? [
          { label: t("projects.list.menu.edit"), onClick: () => handleOpenEditDialog(contextItem) },
          {
            label: t("projects.list.menu.delete"),
            onClick: () => handleOpenDeleteDialog(contextItem),
            color: 'error.main',
          },
          ...entityLinkActions('project', contextItem.id, navigate),
        ] : [
          {
            label: t("projects.list.menu.newProject"),
            icon: <AddIcon fontSize="small" />,
            onClick: () => handleOpenEditDialog(null),
          },
        ]}
      />

      {/* Edit/Add Dialog (Modal) */}
      <Dialog
        open={editDialogOpen}
        onClose={handleCloseEditDialog}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>
          {currentItem?.id ? t("projects.list.editTitle") : t("projects.list.addTitle")}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              autoFocus
              required
              margin="dense"
              id="project-form-name"
              name="projectName"
              label={t("projects.list.nameLabel")}
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
                  label={t("projects.list.clientLabel")}
                  required
                  margin="dense"
                  variant="outlined"
                  helperText={t("projects.list.clientHelp")}
                  error={
                    !formClientValue &&
                    Boolean(formName.trim()) /* Only show error if trying to save */
                  }
                />
              )}
              disabled={isSaving || isLoading}
            />
            <TextField
              margin="dense"
              id="project-form-description"
              name="projectDescription"
              label={t("projects.list.descriptionLabel")}
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
            {t("projects.list.cancel")}
          </Button>
          <Button
            onClick={handleSave}
            variant="contained"
            disabled={
              isSaving || isLoading || !formName.trim() || !formClientValue
            }
          >
            {isSaving ? <CircularProgress size={24} /> : t("projects.list.save")}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onClose={handleCloseDeleteDialog}>
        <DialogTitle>{t("projects.list.deleteTitle")}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {t("projects.list.deleteConfirm", { name: currentItem?.name })}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={handleCloseDeleteDialog}
            disabled={isSaving}
            color="inherit"
          >
            {t("projects.list.cancel")}
          </Button>
          <Button
            onClick={handleDelete}
            color="error"
            variant="contained"
            disabled={isSaving}
          >
            {isSaving ? <CircularProgress size={24} /> : t("projects.list.delete")}
          </Button>
        </DialogActions>
      </Dialog>
    </PageLayout>
  );
}

export default ProjectsPage;
