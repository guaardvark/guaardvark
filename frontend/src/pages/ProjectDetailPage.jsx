// frontend/src/pages/ProjectDetailPage.jsx
// Version 4.5: Aligned header and page layout styling with other main pages.
// - Standardized header with project title, client, actions (Edit/Delete), and active model.
// - Placed project description in a separate Paper below the header.
// - Ensured consistent padding and tab styling.
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from the project maintainer.

import React, { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Typography,
  CircularProgress,
  Paper,
  Button,
  Link,
  Tabs,
  Tab,
  Alert as MuiAlert,
  Snackbar,
  Chip,
  IconButton,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Tooltip,
  useTheme,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Autocomplete,
} from "@mui/material";
import {
  Add as AddIcon,
  Edit as EditIcon,
  Close as CloseIcon,
  Link as LinkIconMui,
  LinkOff as LinkOffIcon,
  Description as DescriptionIcon,
  Rule as RuleIcon,
  TaskAlt as TaskIcon,
  Language as WebsiteIcon,
} from "@mui/icons-material";

import * as apiService from "../api";
import { triggerIndexing } from "../api/indexingService"; // Import directly to avoid static bundling
import LinkingModal from "../components/modals/LinkingModal";
import WebsiteActionModal from "../components/modals/WebsiteActionModal";
import { useSnackbar } from "../components/common/SnackbarProvider";
import { useStatus } from "../contexts/StatusContext"; // For active model display
import { formatTimestamp } from "../utils/fileTypeUtils";
import PageLayout from "../components/layout/PageLayout";
import { ContextualLoader } from "../components/common/LoadingStates";
import { PROJECT_TABS } from "../utils/entityLinks";
import ProjectVideosPanel from "../components/projects/ProjectVideosPanel";
import { uiLocale, useUiTranslation, Trans } from "../i18n";

const tabIndexFor = (name) => Math.max(0, PROJECT_TABS.indexOf(name));

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

// Simplified ProjectActionModal (inline for edit) - assumes client selection is not part of this modal for now.
const ProjectEditModal = ({
  open,
  onClose,
  project,
  onSave,
  isSaving,
  clients,
}) => {
  const { t } = useUiTranslation();
  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formRootPath, setFormRootPath] = useState("");
  const [formClientValue, setFormClientValue] = useState(null);
  const [modalError, setModalError] = useState("");

  useEffect(() => {
    if (project) {
      setFormName(project.name || "");
      setFormDescription(project.description || "");
      setFormRootPath(project.root_path || "");
      const clientObj = clients.find((c) => c.id === project.client_id) || null;
      setFormClientValue(clientObj);
    } else {
      setFormName("");
      setFormDescription("");
      setFormRootPath("");
      setFormClientValue(null);
    }
    setModalError("");
  }, [project, clients, open]);

  const handleSave = () => {
    if (!formName.trim()) {
      setModalError(t("projects.detail.editModal.nameRequired"));
      return;
    }
    if (!formClientValue || !formClientValue.id) {
      setModalError(t("projects.detail.editModal.clientRequired"));
      return;
    }
    setModalError("");
    onSave({
      id: project.id,
      name: formName,
      description: formDescription,
      client_id: formClientValue.id,
      root_path: formRootPath.trim(),
    });
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{t("projects.detail.editModal.title", { name: project?.name })}</DialogTitle>
      <DialogContent>
        {modalError && (
          <MuiAlert severity="error" sx={{ mb: 2 }}>
            {modalError}
          </MuiAlert>
        )}
        <TextField
          autoFocus
          margin="dense"
          label={t("projects.detail.editModal.name")}
          type="text"
          fullWidth
          variant="outlined"
          value={formName}
          onChange={(e) => setFormName(e.target.value)}
          sx={{ mb: 2, mt: 1 }}
          disabled={isSaving}
        />
        <Autocomplete
          value={formClientValue}
          onChange={(event, newValue) => setFormClientValue(newValue)}
          options={clients}
          getOptionLabel={(option) => option?.name || ""}
          isOptionEqualToValue={(option, value) => {
            if (!option || !value) return false;
            return option.id === value.id;
          }}
          renderInput={(params) => (
            <TextField
              {...params}
              label={t("projects.detail.editModal.client")}
              variant="outlined"
              required
              disabled={isSaving}
            />
          )}
          sx={{ mb: 2 }}
        />
        <TextField
          margin="dense"
          label={t("projects.detail.editModal.description")}
          type="text"
          fullWidth
          multiline
          rows={4}
          variant="outlined"
          value={formDescription}
          onChange={(e) => setFormDescription(e.target.value)}
          disabled={isSaving}
        />
        <TextField
          margin="dense"
          label={t("projects.detail.editModal.folder")}
          type="text"
          fullWidth
          variant="outlined"
          placeholder="~/projects/my-app"
          value={formRootPath}
          onChange={(e) => setFormRootPath(e.target.value)}
          helperText={t("projects.detail.editModal.folderHelp")}
          sx={{ mt: 2 }}
          disabled={isSaving}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSaving} color="inherit">
          {t("projects.detail.editModal.cancel")}
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          disabled={isSaving || !formName.trim() || !formClientValue?.id}
        >
          {isSaving ? <CircularProgress size={24} /> : t("projects.detail.editModal.save")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

const ProjectDetailPage = () => {
  const { t } = useUiTranslation();
  const { projectId } = useParams();
  const navigate = useNavigate();
  const _theme = useTheme();
  const { showMessage } = useSnackbar();
  const { activeModel, isLoadingModel, modelError } = useStatus();

  const [project, setProject] = useState(null);
  const [isLoadingProject, setIsLoadingProject] = useState(true);
  const [projectError, setProjectError] = useState(null);
  // ?tab=documents (and the other PROJECT_TABS names) opens that tab.
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const [tabValue, setTabValue] = useState(() => tabIndexFor(tabParam));
  useEffect(() => {
    if (tabParam) setTabValue(tabIndexFor(tabParam));
  }, [tabParam]);
  const [feedback, setFeedback] = useState({
    message: "",
    severity: "info",
    open: false,
  });
  const [isActionLoading, setIsActionLoading] = useState(false); // For delete, linking

  const [tasks, setTasks] = useState([]);
  const [isLoadingTasks, setIsLoadingTasks] = useState(false);
  const [tasksError, setTasksError] = useState(null);

  const [isAddTaskDialogOpen, setIsAddTaskDialogOpen] = useState(false);
  const [newTaskName, setNewTaskName] = useState("");
  const [newTaskDescription, setNewTaskDescription] = useState("");
  const [isSavingTask, setIsSavingTask] = useState(false);

  const [documents, setDocuments] = useState([]);
  const [isLoadingDocs, setIsLoadingDocs] = useState(false);
  const [docsError, setDocsError] = useState(null);

  const [websites, setWebsites] = useState([]);
  const [isLoadingWebsites, setIsLoadingWebsites] = useState(false);
  const [websitesError, setWebsitesError] = useState(null);
  const [isWebsiteModalOpen, setIsWebsiteModalOpen] = useState(false);
  const [websiteModalData, setWebsiteModalData] = useState(null);
  const [isSavingWebsite, setIsSavingWebsite] = useState(false);

  const [linkedRules, setLinkedRules] = useState([]);
  const [isLoadingRules, setIsLoadingRules] = useState(false);
  const [rulesError, setRulesError] = useState(null);

  const [isLinkRuleModalOpen, setIsLinkRuleModalOpen] = useState(false);
  const [linkRuleModalKey, setLinkRuleModalKey] = useState(0);

  const [isProjectEditModalOpen, setIsProjectEditModalOpen] = useState(false);
  const [isSavingProject, setIsSavingProject] = useState(false);
  const [clientsForProjectModal, setClientsForProjectModal] = useState([]);

  const fetchProjectDetails = useCallback(async () => {
    setIsLoadingProject(true);
    setProjectError(null);
    try {
      const data = await apiService.getProject(projectId);
      if (data?.error) throw new Error(data.error.message || data.error);
      setProject(data);
      const clientList = await apiService.getClients(); // Fetch clients for edit modal
      if (clientList.error)
        throw new Error(clientList.error.message || clientList.error);
      setClientsForProjectModal(
        Array.isArray(clientList)
          ? clientList.map((c) => ({ id: c.id, name: c.name }))
          : [],
      );
    } catch (err) {
      console.error("Error fetching project %s:", projectId, err);
      const errMsg =
        err.message || t("projects.detail.loadFailed", { id: projectId });
      setProjectError(errMsg);
      showMessage(t("projects.detail.loadError", { error: errMsg }), "error");
    } finally {
      setIsLoadingProject(false);
    }
  }, [projectId]);

  const fetchTasks = useCallback(async () => {
    /* ... (same as before) ... */
    if (!projectId) return;
    setIsLoadingTasks(true);
    setTasksError(null);
    try {
      const tasksResult = await apiService.getTasks(projectId);
      if (tasksResult?.error)
        throw new Error(tasksResult.error.message || tasksResult.error);
      setTasks(Array.isArray(tasksResult) ? tasksResult : []);
    } catch (err) {
      const msg = err.message || t("projects.detail.tasksLoadFailed");
      setTasksError(msg);
      setTasks([]);
      showMessage(msg, "error");
    } finally {
      setIsLoadingTasks(false);
    }
  }, [projectId]);
  const fetchDocuments = useCallback(async () => {
    /* ... (same as before) ... */
    if (!projectId) return;
    setIsLoadingDocs(true);
    setDocsError(null);
    try {
      const docsResult = await apiService.getDocuments({
        projectId: projectId,
        page: 1,
        perPage: 100,
      });
      if (docsResult?.error)
        throw new Error(docsResult.error.message || docsResult.error);
      setDocuments(docsResult?.documents || docsResult?.items || []);
    } catch (err) {
      const msg = err.message || t("projects.detail.docsLoadFailed");
      setDocsError(msg);
      setDocuments([]);
      showMessage(msg, "error");
    } finally {
      setIsLoadingDocs(false);
    }
  }, [projectId]);
  const fetchWebsites = useCallback(async () => {
    /* ... (same as before) ... */
    if (!projectId) return;
    setIsLoadingWebsites(true);
    setWebsitesError(null);
    try {
      const websitesResult = await apiService.getWebsites({
        project_id: projectId,
      });
      if (websitesResult?.error)
        throw new Error(websitesResult.error.message || websitesResult.error);
      setWebsites(Array.isArray(websitesResult) ? websitesResult : []);
    } catch (err) {
      const msg = err.message || t("projects.detail.websitesLoadFailed");
      setWebsitesError(msg);
      setWebsites([]);
      showMessage(msg, "error");
    } finally {
      setIsLoadingWebsites(false);
    }
  }, [projectId]);
  const fetchRules = useCallback(async () => {
    /* ... (same as before) ... */
    if (!projectId) return;
    setIsLoadingRules(true);
    setRulesError(null);
    try {
      const rulesResult = await apiService.getRules({ project_id: projectId }); // Assuming API supports this
      if (rulesResult?.error)
        throw new Error(rulesResult.error.message || rulesResult.error);
      setLinkedRules(Array.isArray(rulesResult) ? rulesResult : []);
    } catch (err) {
      setRulesError(err.message || t("projects.detail.rulesLoadFailed"));
      setLinkedRules([]);
    } finally {
      setIsLoadingRules(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchProjectDetails();
    fetchTasks();
    fetchDocuments();
    fetchWebsites();
    fetchRules();
  }, [
    projectId,
    fetchProjectDetails,
    fetchTasks,
    fetchDocuments,
    fetchWebsites,
    fetchRules,
  ]);

  const handleTabChange = (event, newValue) => setTabValue(newValue);
  const handleCloseFeedback = (event, reason) => {
    if (reason === "clickaway") return;
    setFeedback((prev) => ({ ...prev, open: false }));
  };

  const handleOpenAddTaskDialog = () => {
    setNewTaskName("");
    setNewTaskDescription("");
    setIsAddTaskDialogOpen(true);
  };
  const handleCloseAddTaskDialog = () => {
    if (!isSavingTask) setIsAddTaskDialogOpen(false);
  };
  const handleSaveNewTask = async () => {
    /* ... (same as before, ensure projectId is Number) ... */
    if (!newTaskName.trim()) {
      setFeedback({
        message: t("projects.detail.taskNameEmpty"),
        severity: "warning",
        open: true,
      });
      return;
    }
    setIsSavingTask(true);
    try {
      const taskData = {
        name: newTaskName.trim(),
        description: newTaskDescription.trim(),
        project_id: Number(projectId),
        status: "PENDING",
      };
      await apiService.createTask(taskData);
      setFeedback({
        message: t("projects.detail.taskAdded"),
        severity: "success",
        open: true,
      });
      handleCloseAddTaskDialog();
      fetchTasks();
    } catch (err) {
      showMessage(t("projects.detail.taskAddFailed", { error: err.message }), "error");
    } finally {
      setIsSavingTask(false);
    }
  };

  const _handleOpenProjectEditModal = () => setIsProjectEditModalOpen(true);
  const handleCloseProjectEditModal = () => {
    if (!isSavingProject) setIsProjectEditModalOpen(false);
  };
  const handleSaveProject = async (projectDataToSave) => {
    setIsSavingProject(true);
    setFeedback({ open: false, message: "" });
    try {
      const result = await apiService.updateProject(
        projectDataToSave.id,
        projectDataToSave,
      );
      if (result?.error) throw new Error(result.error.message || result.error);
      setFeedback({
        open: true,
        message: t("projects.detail.projectUpdated"),
        severity: "success",
      });
      fetchProjectDetails(); // Refresh project details
      handleCloseProjectEditModal();
    } catch (err) {
      showMessage(t("projects.detail.projectUpdateFailed", { error: err.message }), "error");
    } finally {
      setIsSavingProject(false);
    }
  };

  const handleDeleteProject = async () => {
    /* ... (same as before) ... */
    if (
      window.confirm(
        t("projects.detail.deleteConfirm", { name: project?.name, id: projectId }),
      )
    ) {
      setIsActionLoading(true); // Use general action loading or specific
      setFeedback({
        message: t("projects.detail.deleting"),
        severity: "info",
        open: true,
      });
      try {
        await apiService.deleteProject(projectId);
        setFeedback({
          message: t("projects.detail.deleted"),
          severity: "success",
          open: true,
        });
        setTimeout(() => navigate("/projects"), 1500);
      } catch (err) {
        showMessage(t("projects.detail.deleteError", { error: err.message }), "error");
      } finally {
        setIsActionLoading(false);
      }
    }
  };

  const handleOpenLinkRuleModal = () => {
    /* ... (same as before) ... */
    setLinkRuleModalKey((prevKey) => prevKey + 1);
    setIsLinkRuleModalOpen(true);
  };
  const handleCloseLinkRuleModal = () => setIsLinkRuleModalOpen(false);
  const getRulesForLinking = useCallback(async (filters = {}) => {
    /* ... (same as before) ... */
    try {
      const rules = await apiService.getRules(filters);
      if (rules?.error) throw new Error(rules.error.message || rules.error);
      return Array.isArray(rules)
        ? rules.map((r) => ({ id: r.id, name: r.name, description: r.type }))
        : [];
    } catch (error) {
      showMessage(t("projects.detail.rulesFetchError", { error: error.message }), "error");
      return [];
    }
  }, []);
  const getLinkedRulesForProjectCallback = useCallback(
    async (pEntityType, pEntityId, lEntityType) => {
      /* ... (same as before) ... */
      if (pEntityType === "project" && lEntityType === "rule" && pEntityId) {
        try {
          // This fetches rules already linked to *this* project.
          // The `WorkspaceRules` function on this page already does this and stores in `linkedRules`.
          // For the modal, we might need to ensure it gets the most up-to-date list or re-fetch.
          // For now, assume it re-fetches or uses a dedicated API if `getRules({ project_id: pEntityId })` works.
          const rules = await apiService.getRules({ project_id: pEntityId });
          if (rules?.error) throw new Error(rules.error.message || rules.error);
          return Array.isArray(rules)
            ? rules.map((r) => ({ id: r.id, name: r.name }))
            : [];
        } catch (error) {
          showMessage(t("projects.detail.linkedRulesFetchError", { error: error.message }), "error");
          return [];
        }
      }
      return [];
    },
    [],
  );
  const updateProjectRuleLinksCallback = useCallback(
    async (pEntityType, pEntityId, lEntityType, targetRuleIds) => {
      /* ... (same as before) ... */
      if (pEntityType === "project" && lEntityType === "rule" && pEntityId) {
        setIsActionLoading(true);
        try {
          const currentRulesResult = await apiService.getRules({
            project_id: pEntityId,
          });
          if (currentRulesResult?.error)
            throw new Error(
              currentRulesResult.error.message || currentRulesResult.error,
            );
          const currentRuleIds = Array.isArray(currentRulesResult)
            ? currentRulesResult.map((r) => r.id)
            : [];

          const toLinkPromises = targetRuleIds
            .filter((id) => !currentRuleIds.includes(id))
            .map((ruleId) => apiService.linkRuleToProject(pEntityId, ruleId));

          const toUnlinkPromises = currentRuleIds
            .filter((id) => !targetRuleIds.includes(id))
            .map((ruleId) =>
              apiService.unlinkRuleFromProject(pEntityId, ruleId),
            );

          await Promise.all([...toLinkPromises, ...toUnlinkPromises]);
          // Success feedback handled by onLinksUpdated
        } catch (error) {
          showMessage(t("projects.detail.ruleLinksError", { error: error.message }), "error");
          throw error;
        } finally {
          setIsActionLoading(false);
        }
      }
    },
    [],
  );
  const handleRuleLinksUpdated = () => {
    fetchRules();
    setFeedback({
      message: t("projects.detail.ruleLinksUpdated"),
      severity: "success",
      open: true,
    });
    setIsLinkRuleModalOpen(false);
  };

  const handleTriggerDocIndex = async (docId) => {
    /* ... (same as before) ... */
    if (!docId) return;
    setFeedback({
      message: t("projects.detail.indexRequesting", { id: docId }),
      severity: "info",
      open: true,
    });
    setIsActionLoading(true);
    try {
      const result = await triggerIndexing(docId);
      if (result?.error) throw new Error(result.error.message || result.error);
      setFeedback({
        message:
          result?.message || t("projects.detail.indexTriggered", { id: docId }),
        severity: "success",
        open: true,
      });
      setTimeout(fetchDocuments, 3000); // Give some time for status to potentially update
    } catch (err) {
      setFeedback({
        message: t("projects.detail.indexError", { error: err.message }),
        severity: "error",
        open: true,
      });
    } finally {
      setIsActionLoading(false);
    }
  };
  const handleDeleteDoc = async (docId, filename) => {
    /* ... (same as before) ... */
    if (!docId) return;
    if (
      window.confirm(
        t("projects.detail.docDeleteConfirm", { filename, id: docId }),
      )
    ) {
      setIsActionLoading(true);
      setFeedback({
        message: t("projects.detail.docDeleting", { id: docId }),
        severity: "info",
        open: true,
      });
      try {
        const result = await apiService.deleteDocument(docId);
        if (result?.error)
          throw new Error(result.error.message || result.error);
        setFeedback({
          message: result?.message || t("projects.detail.docDeleted", { id: docId }),
          severity: "success",
          open: true,
        });
        fetchDocuments();
      } catch (err) {
        setFeedback({
          message: t("projects.detail.docDeleteError", { error: err.message }),
          severity: "error",
          open: true,
        });
      } finally {
        setIsActionLoading(false);
      }
    }
  };
  const handleAddWebsite = () => {
    setWebsiteModalData({ project_id: Number(projectId) });
    setIsWebsiteModalOpen(true);
  };

  const handleCloseWebsiteModal = () => {
    if (isSavingWebsite) return;
    setIsWebsiteModalOpen(false);
    setWebsiteModalData(null);
  };

  const handleSaveWebsite = async (data) => {
    setIsSavingWebsite(true);
    setFeedback({ open: false, message: "" });
    try {
      const result = await apiService.createWebsite(data);
      if (result?.error) throw new Error(result.error.message || result.error);
      setFeedback({
        open: true,
        message: t("projects.detail.websiteCreated"),
        severity: "success",
      });
      handleCloseWebsiteModal();
      fetchWebsites();
    } catch (err) {
      setFeedback({
        open: true,
        message: t("projects.detail.websiteCreateFailed", { error: err.message }),
        severity: "error",
      });
    } finally {
      setIsSavingWebsite(false);
    }
  };

  if (isLoadingProject && !project) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", p: 5 }}>
        <ContextualLoader loading message={t("projects.detail.loading")} showProgress={false} inline />
      </Box>
    );
  }
  if (projectError) {
    return (
      <MuiAlert severity="error" sx={{ m: 2 }}>
        {t("projects.detail.loadError", { error: projectError })}
      </MuiAlert>
    );
  }
  if (!project) {
    return (
      <Typography sx={{ p: 3, textAlign: "center" }}>
        {t("projects.detail.notFound")}
      </Typography>
    );
  }

  return (
    <PageLayout
      title={t("projects.detail.title", { name: project?.name || "" })}
      variant="standard"
      actions={
        <Button
          variant="outlined"
          size="small"
          color="inherit"
          startIcon={<CloseIcon sx={{ color: "text.secondary" }} />}
          onClick={handleDeleteProject}
          disabled={isActionLoading || isSavingProject}
        >
          {t("projects.detail.delete")}
        </Button>
      }
      modelStatus
      activeModel={
        isLoadingModel
          ? t("projects.detail.modelLoading")
          : modelError
            ? t("projects.detail.modelError")
            : activeModel
      }
      headerContent={
        <Box sx={{ px: 2, pb: 0.5 }}>
          <Typography variant="caption" color="text.secondary">
            {t("projects.detail.header", {
              client: project?.client?.name || t("projects.detail.notAvailable"),
              id: project?.id,
              created: project?.created_at
                ? new Date(project.created_at).toLocaleDateString(uiLocale())
                : t("projects.detail.notAvailable"),
            })}
          </Typography>
        </Box>
      }
    >
      <Snackbar
        open={feedback.open}
        autoHideDuration={6000}
        onClose={handleCloseFeedback}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <AlertSnackbar
          onClose={handleCloseFeedback}
          severity={feedback.severity || "info"}
          sx={{ width: "100%" }}
        >
          {feedback.message}
        </AlertSnackbar>
      </Snackbar>

      {project.effective_root && (
        <Typography
          variant="body2"
          sx={{ mx: 1.5, mt: 1.5, color: "text.secondary", fontFamily: "monospace" }}
          title={t("projects.detail.folderTitle")}
        >
          {t("projects.detail.folder", { path: project.effective_root })}
        </Typography>
      )}

      {project.description && (
        <Paper
          elevation={0}
          sx={{
            p: 1.5,
            mx: 1.5,
            mt: 1.5,
            mb: 0,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          <Typography
            variant="subtitle2"
            gutterBottom
            sx={{ fontWeight: "medium" }}
          >
            {t("projects.detail.description")}
          </Typography>
          <Typography
            variant="body2"
            sx={{ whiteSpace: "pre-wrap", color: "text.secondary" }}
          >
            {project.description}
          </Typography>
        </Paper>
      )}

      <Box
        sx={{
          borderBottom: 1,
          borderColor: "divider",
          mt: project.description ? 1.5 : 0,
          mx: 1.5,
        }}
      >
        <Tabs
          value={tabValue}
          onChange={handleTabChange}
          aria-label={t("projects.detail.tabsLabel")}
          variant="scrollable"
          scrollButtons="auto"
          sx={{
            minHeight: "40px", // Adjust tab height
            "& .MuiTab-root": {
              minHeight: "40px",
              fontSize: "0.875rem",
              py: 1,
            },
          }}
        >
          <Tab
            label={t("projects.detail.tabs.tasks")}
            id="project-tab-tasks"
            aria-controls="project-panel-tasks"
          />
          <Tab
            label={t("projects.detail.tabs.documents")}
            id="project-tab-documents"
            aria-controls="project-panel-documents"
          />
          <Tab
            label={t("projects.detail.tabs.websites")}
            id="project-tab-websites"
            aria-controls="project-panel-websites"
          />
          <Tab
            label={t("projects.detail.tabs.rules")}
            id="project-tab-rules"
            aria-controls="project-panel-rules"
          />
          <Tab
            label={t("projects.detail.tabs.videos")}
            id="project-tab-videos"
            aria-controls="project-panel-videos"
          />
        </Tabs>
      </Box>

      <Box sx={{ flexGrow: 1, overflowY: "auto", p: 1.5, pt: 2 }}>
        {tabValue === 0 /* Tasks Panel */ && (
          <Paper
            elevation={2}
            sx={{ p: 2 }}
            role="tabpanel"
            id="project-panel-tasks"
            aria-labelledby="project-tab-tasks"
          >
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                mb: 2,
              }}
            >
              <Typography variant="h6" sx={{ fontSize: "1.1rem" }}>
                {t("projects.detail.tasksFor", { name: project.name })}
              </Typography>
              <Button
                variant="contained"
                size="small"
                startIcon={<AddIcon />}
                onClick={handleOpenAddTaskDialog}
                disabled={isActionLoading || isSavingTask}
              >
                {t("projects.detail.addTask")}
              </Button>
            </Box>
            {isLoadingTasks && (
              <Box sx={{ display: "flex", justifyContent: "center", my: 2 }}>
                <CircularProgress size={24} />
                <Typography sx={{ ml: 1 }}>{t("projects.detail.loadingTasks")}</Typography>
              </Box>
            )}
            {tasksError && (
              <MuiAlert severity="error" sx={{ my: 1 }}>
                {t("projects.detail.tasksError", { error: tasksError })}
              </MuiAlert>
            )}
            {!isLoadingTasks && !tasksError && tasks.length === 0 && (
              <Typography
                sx={{ my: 2, textAlign: "center", fontStyle: "italic" }}
              >
                {t("projects.detail.noTasks")}
              </Typography>
            )}
            {!isLoadingTasks && !tasksError && tasks.length > 0 && (
              <List dense>
                {tasks.map((task) => (
                  <ListItem
                    key={task.id}
                    divider
                    secondaryAction={
                      <Tooltip title={t("projects.detail.editTaskTooltip")}>
                        <span>
                          <IconButton
                            edge="end"
                            aria-label={t("projects.detail.editTaskLabel")}
                            size="small"
                            disabled
                          >
                            <EditIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                    }
                  >
                    <ListItemIcon sx={{ minWidth: "36px" }}>
                      <TaskIcon color="action" fontSize="small" />
                    </ListItemIcon>
                    <ListItemText
                      primaryTypographyProps={{ variant: "body2" }}
                      secondaryTypographyProps={{ variant: "caption" }}
                      primary={task.name || t("projects.detail.taskFallback", { id: task.id })}
                      secondary={t("projects.detail.taskDetails", {
                        status: task.status || t("projects.detail.notAvailable"),
                        type: task.type || t("projects.detail.notAvailable"),
                        created: new Date(task.created_at).toLocaleDateString(uiLocale()),
                      })}
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </Paper>
        )}
        {tabValue === 1 /* Documents Panel */ && (
          <Paper
            elevation={2}
            sx={{ p: 2 }}
            role="tabpanel"
            id="project-panel-documents"
            aria-labelledby="project-tab-documents"
          >
            <Typography variant="h6" sx={{ fontSize: "1.1rem", mb: 2 }}>
              {t("projects.detail.documentsIn", { name: project.name })}
            </Typography>
            {isLoadingDocs && (
              <Box sx={{ display: "flex", justifyContent: "center", my: 2 }}>
                <CircularProgress size={24} />
                <Typography sx={{ ml: 1 }}>{t("projects.detail.loadingDocs")}</Typography>
              </Box>
            )}
            {docsError && (
              <MuiAlert severity="error" sx={{ my: 1 }}>
                {t("projects.detail.docsError", { error: docsError })}
              </MuiAlert>
            )}
            {!isLoadingDocs && !docsError && documents.length === 0 && (
              <Typography
                sx={{ my: 2, textAlign: "center", fontStyle: "italic" }}
              >
                {t("projects.detail.noDocs")}
              </Typography>
            )}
            {!isLoadingDocs && !docsError && documents.length > 0 && (
              <List dense>
                {documents.map((doc) => (
                  <ListItem
                    key={doc.id}
                    divider
                    secondaryAction={
                      <Box sx={{ display: "flex", gap: 0.5 }}>
                        <Tooltip
                          title={
                            doc.index_status === "INDEXED"
                              ? t("projects.detail.reindexDoc")
                              : t("projects.detail.indexDoc")
                          }
                        >
                          <span>
                            <Button
                              variant="outlined"
                              size="small"
                              onClick={() => handleTriggerDocIndex(doc.id)}
                              disabled={
                                isActionLoading ||
                                doc.index_status === "INDEXING"
                              }
                              sx={{
                                minWidth: "70px",
                                fontSize: "0.75rem",
                                p: "2px 8px",
                              }}
                            >
                              {doc.index_status === "INDEXING" ? (
                                <CircularProgress size={16} />
                              ) : (
                                t("projects.detail.index")
                              )}
                            </Button>
                          </span>
                        </Tooltip>
                        <Tooltip title={t("projects.detail.deleteDoc")}>
                          <span>
                            <IconButton
                              edge="end"
                              aria-label={t("projects.detail.deleteDocLabel")}
                              size="small"
                              onClick={() =>
                                handleDeleteDoc(doc.id, doc.filename)
                              }
                              disabled={isActionLoading}
                            >
                              <CloseIcon
                                fontSize="small"
                                sx={{ color: "text.secondary" }}
                              />
                            </IconButton>
                          </span>
                        </Tooltip>
                      </Box>
                    }
                  >
                    <ListItemIcon sx={{ minWidth: "36px" }}>
                      <DescriptionIcon
                        color={
                          doc.index_status === "INDEXED"
                            ? "success"
                            : doc.index_status === "ERROR"
                              ? "error"
                              : "action"
                        }
                        fontSize="small"
                      />
                    </ListItemIcon>
                    <ListItemText
                      primaryTypographyProps={{ variant: "body2" }}
                      secondaryTypographyProps={{ variant: "caption" }}
                      primary={doc.filename}
                      secondary={
                        <Trans
                          i18nKey="projects.detail.docDetails"
                          values={{ uploaded: formatTimestamp(doc.created_at || doc.uploaded_at) }}
                          components={{
                            status: (
                              <Chip
                                label={doc.index_status || t("projects.detail.notAvailable")}
                                size="small"
                                color={
                                  doc.index_status === "INDEXED"
                                    ? "success"
                                    : doc.index_status === "ERROR"
                                      ? "error"
                                      : "default"
                                }
                                sx={{ mr: 1, height: "auto", lineHeight: 1.2 }}
                              />
                            ),
                          }}
                        />
                      }
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </Paper>
        )}
        {tabValue === 2 /* Websites Panel */ && (
          <Paper
            elevation={2}
            sx={{ p: 2 }}
            role="tabpanel"
            id="project-panel-websites"
            aria-labelledby="project-tab-websites"
          >
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                mb: 2,
              }}
            >
              <Typography variant="h6" sx={{ fontSize: "1.1rem" }}>
                {t("projects.detail.websitesFor", { name: project.name })}
              </Typography>
              <Button
                variant="contained"
                size="small"
                startIcon={<AddIcon />}
                onClick={handleAddWebsite}
                disabled={isActionLoading}
              >
                {t("projects.detail.addWebsite")}
              </Button>
            </Box>
            {isLoadingWebsites && (
              <Box sx={{ display: "flex", justifyContent: "center", my: 2 }}>
                <CircularProgress size={24} />
                <Typography sx={{ ml: 1 }}>{t("projects.detail.loadingWebsites")}</Typography>
              </Box>
            )}
            {websitesError && (
              <MuiAlert severity="error" sx={{ my: 1 }}>
                {t("projects.detail.websitesError", { error: websitesError })}
              </MuiAlert>
            )}
            {!isLoadingWebsites && !websitesError && websites.length === 0 && (
              <Typography
                sx={{ my: 2, textAlign: "center", fontStyle: "italic" }}
              >
                {t("projects.detail.noWebsites")}
              </Typography>
            )}
            {!isLoadingWebsites && !websitesError && websites.length > 0 && (
              <List dense>
                {websites.map((site) => (
                  <ListItem
                    key={site.id}
                    divider
                    secondaryAction={
                      <Tooltip title={t("projects.detail.editWebsiteTooltip")}>
                        <span>
                          <IconButton
                            edge="end"
                            aria-label={t("projects.detail.editWebsiteLabel")}
                            size="small"
                            disabled
                          >
                            <EditIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                    }
                  >
                    <ListItemIcon sx={{ minWidth: "36px" }}>
                      <WebsiteIcon color="action" fontSize="small" />
                    </ListItemIcon>
                    <ListItemText
                      primaryTypographyProps={{ variant: "body2" }}
                      secondaryTypographyProps={{ variant: "caption" }}
                      primary={
                        <Link
                          href={site.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          sx={{ wordBreak: "break-all" }}
                        >
                          {site.url}
                        </Link>
                      }
                      secondary={t("projects.detail.websiteDetails", {
                        status: site.status || t("projects.detail.notAvailable"),
                        added: new Date(site.created_at).toLocaleDateString(uiLocale()),
                      })}
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </Paper>
        )}
        {tabValue === 4 /* Videos Panel */ && project?.id && (
          <Paper elevation={2} sx={{ p: 2 }} id="project-panel-videos">
            <ProjectVideosPanel projectId={project.id} />
          </Paper>
        )}
        {tabValue === 3 /* Rules Panel */ && (
          <Paper
            elevation={2}
            sx={{ p: 2 }}
            role="tabpanel"
            id="project-panel-rules"
            aria-labelledby="project-tab-rules"
          >
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                mb: 2,
              }}
            >
              <Typography variant="h6" sx={{ fontSize: "1.1rem" }}>
                {t("projects.detail.rulesFor", { name: project.name })}
              </Typography>
              <Button
                variant="contained"
                size="small"
                startIcon={<LinkIconMui />}
                onClick={handleOpenLinkRuleModal}
                disabled={isActionLoading}
              >
                {t("projects.detail.linkRule")}
              </Button>
            </Box>
            {isLoadingRules && (
              <Box sx={{ display: "flex", justifyContent: "center", my: 2 }}>
                <CircularProgress size={24} />
                <Typography sx={{ ml: 1 }}>{t("projects.detail.loadingRules")}</Typography>
              </Box>
            )}
            {rulesError && (
              <MuiAlert severity="error" sx={{ my: 1 }}>
                {t("projects.detail.rulesError", { error: rulesError })}
              </MuiAlert>
            )}
            {!isLoadingRules && !rulesError && linkedRules.length === 0 && (
              <Typography
                sx={{ my: 2, textAlign: "center", fontStyle: "italic" }}
              >
                {t("projects.detail.noRules")}
              </Typography>
            )}
            {!isLoadingRules && !rulesError && linkedRules.length > 0 && (
              <List dense>
                {linkedRules.map((rule) => (
                  <ListItem
                    key={rule.id}
                    divider
                    secondaryAction={
                      <Tooltip title={t("projects.detail.unlinkTooltip")}>
                        <span>
                          <IconButton
                            edge="end"
                            aria-label={t("projects.detail.unlinkLabel")}
                            size="small"
                            onClick={async () => {
                              setIsActionLoading(true);
                              try {
                                await apiService.unlinkRuleFromProject(
                                  projectId,
                                  rule.id,
                                );
                                fetchRules();
                                setFeedback({
                                  message: t("projects.detail.ruleUnlinked"),
                                  severity: "success",
                                  open: true,
                                });
                              } catch (e) {
                                setFeedback({
                                  message: t("projects.detail.failed", { error: e.message }),
                                  severity: "error",
                                  open: true,
                                });
                              } finally {
                                setIsActionLoading(false);
                              }
                            }}
                            disabled={isActionLoading}
                            color="warning"
                          >
                            <LinkOffIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                    }
                  >
                    <ListItemIcon sx={{ minWidth: "36px" }}>
                      {rule.level === "PROMPT" ||
                      rule.type?.includes("TEMPLATE") ? (
                        <DescriptionIcon color="primary" fontSize="small" />
                      ) : (
                        <RuleIcon color="secondary" fontSize="small" />
                      )}
                    </ListItemIcon>
                    <ListItemText
                      primaryTypographyProps={{ variant: "body2" }}
                      secondaryTypographyProps={{ variant: "caption" }}
                      primary={rule.name || t("projects.detail.ruleFallback", { id: rule.id })}
                      secondary={t("projects.detail.ruleDetails", {
                        type: rule.type || "RULE",
                        level: rule.level || t("projects.detail.notAvailable"),
                        active: rule.is_active ? t("projects.detail.yes") : t("projects.detail.no"),
                      })}
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </Paper>
        )}
      </Box>

      <Dialog
        open={isAddTaskDialogOpen}
        onClose={handleCloseAddTaskDialog}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>{t("projects.detail.addTaskTitle", { name: project?.name })}</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            id="new-task-name"
            label={t("projects.detail.taskName")}
            type="text"
            fullWidth
            variant="outlined"
            value={newTaskName}
            onChange={(e) => setNewTaskName(e.target.value)}
            disabled={isSavingTask}
            sx={{ mb: 2, mt: 1 }}
          />
          <TextField
            margin="dense"
            id="new-task-description"
            label={t("projects.detail.taskDescription")}
            type="text"
            fullWidth
            multiline
            rows={3}
            variant="outlined"
            value={newTaskDescription}
            onChange={(e) => setNewTaskDescription(e.target.value)}
            disabled={isSavingTask}
          />
        </DialogContent>
        <DialogActions>
          <Button
            onClick={handleCloseAddTaskDialog}
            disabled={isSavingTask}
            color="inherit"
          >
            {t("projects.detail.cancel")}
          </Button>
          <Button
            onClick={handleSaveNewTask}
            variant="contained"
            disabled={isSavingTask || !newTaskName.trim()}
          >
            {isSavingTask ? <CircularProgress size={24} /> : t("projects.detail.saveTask")}
          </Button>
        </DialogActions>
      </Dialog>

      {isLinkRuleModalOpen && project && (
        <LinkingModal
          key={linkRuleModalKey}
          open={isLinkRuleModalOpen}
          onClose={handleCloseLinkRuleModal}
          primaryEntityType="project"
          primaryEntityId={Number(projectId)}
          primaryEntityName={project.name}
          linkableTypesConfig={[
            {
              entityType: "rule",
              singularLabel: t("projects.detail.linkRuleSingular"),
              pluralLabel: t("projects.detail.linkRulePlural"),
              apiServiceFunction: getRulesForLinking,
            },
          ]}
          apiGetLinkedItems={getLinkedRulesForProjectCallback}
          apiUpdateLinks={updateProjectRuleLinksCallback}
          onLinksUpdated={handleRuleLinksUpdated}
        />
      )}
      {isWebsiteModalOpen && (
        <WebsiteActionModal
          open={isWebsiteModalOpen}
          onClose={handleCloseWebsiteModal}
          websiteData={websiteModalData}
          onSave={handleSaveWebsite}
          isSaving={isSavingWebsite}
        />
      )}
      {isProjectEditModalOpen && project && (
        <ProjectEditModal
          open={isProjectEditModalOpen}
          onClose={handleCloseProjectEditModal}
          project={project}
          onSave={handleSaveProject}
          isSaving={isSavingProject}
          clients={clientsForProjectModal}
        />
      )}
    </PageLayout>
  );
};

export default ProjectDetailPage;
