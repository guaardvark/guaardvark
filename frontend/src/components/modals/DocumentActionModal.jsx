// frontend/src/components/modals/DocumentActionModal.jsx
// Version 1.4: Integrated file upload capability for creating new documents.

import React, { useState, useEffect, useCallback } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Grid,
  CircularProgress,
  Box,
  Alert,
  Autocomplete,
  Typography,
  IconButton,
  Tooltip,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import SyncIcon from "@mui/icons-material/Sync";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import LinkIcon from "@mui/icons-material/Link";
import * as apiService from "../../api";
import LinkingModal from "./LinkingModal";
import { translate, useUiTranslation } from "../../i18n";

// Allowed file types (should match backend ALLOWED_EXTENSIONS)
const ALLOWED_FILE_TYPES = [
  '.txt', '.pdf', '.md', '.docx', '.csv', '.json', '.py', '.js', '.jsx', 
  '.html', '.css', '.xml', '.htm', '.java', '.c', '.cpp', '.h', '.cs', 
  '.go', '.php', '.rb', '.swift', '.kt', '.rs', '.scala', '.zip'
];

// Removed file size limits to allow large files (e.g., 156MB+ CSVs)

const DocumentActionModal = ({
  open,
  onClose,
  documentData, // If null, modal is in "upload" mode. Otherwise, "edit" mode.
  onSave,
  isSaving,
  onReindex,
  onDeleteFromModal,
}) => {
  const { t } = useUiTranslation();
  const [formData, setFormData] = useState({
    id: null,
    filename: "",
    project_id: "",
    tags: "",
    notes: "",
  });
  const [selectedFile, setSelectedFile] = useState(null);
  const [formError, setFormError] = useState(null);
  const [projects, setProjects] = useState([]);
  const [isLoadingProjects, setIsLoadingProjects] = useState(false);
  const [formProjectValue, setFormProjectValue] = useState(null); // For Project Autocomplete
  const [isReindexing, setIsReindexing] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  // Enhanced Modal Linking System
  const [linkingModalOpen, setLinkingModalOpen] = useState(false);

  const isEditMode = !!documentData;

  const fetchProjectsForDropdown = useCallback(async () => {
    setIsLoadingProjects(true);
    try {
      const projectList = await apiService.getProjects();
      if (projectList.error) {
        throw new Error(projectList.error.message || projectList.error);
      }
      setProjects(Array.isArray(projectList) ? projectList : []);
    } catch (err) {
      console.error("Error fetching projects for modal:", err);
      setProjects([]);
      setFormError(translate("dialogs.documentAction.loadProjectsFailed", { error: err.message }));
    } finally {
      setIsLoadingProjects(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchProjectsForDropdown();
      if (isEditMode) {
        setFormData({
          id: documentData.id,
          filename: documentData.filename || "",
          project_id: documentData.project_id || "",
          tags: Array.isArray(documentData.tags)
            ? documentData.tags.join(", ")
            : typeof documentData.tags === "string"
              ? documentData.tags
              : "",
          notes: documentData.notes || documentData.metadata?.notes || "",
        });
      } else {
        // Reset for upload mode
        setFormData({ id: null, filename: "", project_id: "", tags: "", notes: "" });
      }
      // Reset other states
      setSelectedFile(null);
      setFormError(null);
      setIsReindexing(false);
      setIsDeleting(false);
    }
  }, [documentData, open, isEditMode, fetchProjectsForDropdown]);

  // When projects list or formData.project_id changes, sync formProjectValue
  useEffect(() => {
    if (open) {
      if (formData.project_id && projects.length > 0) {
        const projObj = projects.find(
          (p) => String(p.id) === String(formData.project_id),
        );
        if (
          projObj &&
          (!formProjectValue || formProjectValue.id !== projObj.id)
        ) {
          setFormProjectValue(projObj);
        }
      } else if (!formData.project_id) {
        setFormProjectValue(null);
      }
    }
  }, [open, projects, formData.project_id]);

  const handleInputChange = (event) => {
    const { name, value } = event.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handleProjectChange = (event, newValue) => {
    setFormProjectValue(newValue);
    if (newValue && typeof newValue === "object" && newValue.id) {
      setFormData((prev) => ({ ...prev, project_id: newValue.id }));
    } else if (!newValue) {
      setFormData((prev) => ({ ...prev, project_id: "" }));
    }
  };

  const validateFile = (file) => {
    // Check file type (file size limits removed to allow large files)
    const fileExtension = '.' + file.name.split('.').pop().toLowerCase();
    if (!ALLOWED_FILE_TYPES.includes(fileExtension)) {
      return t("dialogs.documentAction.unsupportedType", { extension: fileExtension, allowed: ALLOWED_FILE_TYPES.join(', ') });
    }
    
    return null; // No validation errors
  };

  const handleFileChange = (e) => {
    const file = e.target.files[0];
    if (file) {
      const validationError = validateFile(file);
      if (validationError) {
        setFormError(validationError);
        setSelectedFile(null);
        // Clear the file input
        e.target.value = '';
      } else {
        setSelectedFile(file);
        setFormError(null); // Clear previous file-related errors
      }
    }
  };

  const handlePrimaryAction = async () => {
    setFormError(null);

    let finalProjectId = null;
    if (formProjectValue) {
      if (typeof formProjectValue === "object" && formProjectValue.id) {
        finalProjectId = formProjectValue.id;
      } else if (
        typeof formProjectValue === "string" &&
        formProjectValue.trim() !== ""
      ) {
        try {
          const newProject = await apiService.createProject({
            name: formProjectValue.trim(),
          });
          if (newProject && newProject.id) {
            finalProjectId = newProject.id;
            // Await the dropdown refresh to ensure consistency
            await fetchProjectsForDropdown();
          } else {
            throw new Error(
              newProject.error || t("dialogs.documentAction.createProjectFailed"),
            );
          }
        } catch (err) {
          setFormError(t("dialogs.documentAction.projectCreationError", { error: err.message }));
          return;
        }
      }
    }

    if (isEditMode) {
      if (!formData.id) {
        setFormError(t("dialogs.documentAction.missingId"));
        return;
      }
      const payload = {
        project_id: finalProjectId || null,
        tags: formData.tags.trim()
          ? formData.tags
              .split(",")
              .map((tag) => tag.trim())
              .filter((tag) => tag)
          : [],
        notes: formData.notes || "",
      };
      onSave(formData.id, payload);
    } else {
      if (!selectedFile) {
        setFormError(t("dialogs.documentAction.selectFile"));
        return;
      }
      // Pass a plain object, not FormData
      onSave(null, {
        file: selectedFile,
        project_id: finalProjectId,
        tags: formData.tags.trim(),
        notes: formData.notes || "",
        metadata: {},
      });
    }
  };

  const handleInternalReindex = async () => {
    if (onReindex && documentData?.id) {
      setIsReindexing(true);
      await onReindex(documentData.id);
      setIsReindexing(false);
    }
  };

  const handleInternalDelete = async () => {
    if (onDeleteFromModal && documentData?.id) {
      if (
        window.confirm(
          t("dialogs.documentAction.deleteConfirm", { name: documentData.filename, id: documentData.id }),
        )
      ) {
        setIsDeleting(true);
        await onDeleteFromModal(documentData.id);
        setIsDeleting(false);
      }
    }
  };

  // Enhanced Modal Linking Configuration
  const linkableTypesConfig = isEditMode ? [
    {
      entityType: "project",
      singularLabel: t("dialogs.documentAction.project"),
      pluralLabel: t("dialogs.documentAction.projects"),
      apiServiceFunction: apiService.getProjects,
    },
    {
      entityType: "task",
      singularLabel: t("dialogs.documentAction.task"),
      pluralLabel: t("dialogs.documentAction.tasks"),
      apiServiceFunction: apiService.getTasks,
    },
    {
      entityType: "client",
      singularLabel: t("dialogs.documentAction.client"),
      pluralLabel: t("dialogs.documentAction.clients"),
      apiServiceFunction: apiService.getClients,
    }
  ] : [];

  const handleOpenLinkingModal = () => {
    if (isEditMode && documentData?.id) {
      setLinkingModalOpen(true);
    }
  };

  const handleCloseLinkingModal = () => {
    setLinkingModalOpen(false);
  };

  const handleLinksUpdated = () => {
    // Refresh any data that depends on links if needed
    // For now, just close the modal
    setLinkingModalOpen(false);
  };

  const getTitleText = () =>
    isEditMode ? formData.filename || t("dialogs.documentAction.fileDetails") : t("dialogs.documentAction.uploadTitle");
  const anyActionInProgress = isSaving || isReindexing || isDeleting;

  return (
    <>
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="document-action-modal-title"
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
        <Typography
          variant="h6"
          component="div"
          noWrap
          sx={{ maxWidth: "calc(100% - 48px)" }}
          title={getTitleText()}
        >
          {getTitleText()}
        </Typography>
        <IconButton
          onClick={onClose}
          size="small"
          sx={{ ml: 2 }}
          disabled={anyActionInProgress}
        >
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {formError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formError}
          </Alert>
        )}
        <Box component="form" noValidate autoComplete="off" sx={{ mt: 1 }}>
          <Grid container spacing={2}>
            {isEditMode ? (
              <Grid item xs={12}>
                <TextField
                  fullWidth
                  margin="dense"
                  id="doc-filename-modal"
                  label={t("dialogs.documentAction.filename")}
                  name="filename"
                  value={formData.filename}
                  disabled
                  variant="filled"
                />
              </Grid>
            ) : (
              <Grid item xs={12}>
                <Button
                  fullWidth
                  variant="outlined"
                  component="label"
                  startIcon={<UploadFileIcon />}
                  disabled={anyActionInProgress}
                >
                  {selectedFile
                    ? t("dialogs.documentAction.selectedFile", { name: selectedFile.name })
                    : t("dialogs.documentAction.selectFileButton")}
                  <input type="file" hidden onChange={handleFileChange} />
                </Button>
                {selectedFile && (
                  <Typography
                    variant="caption"
                    display="block"
                    sx={{ mt: 1, textAlign: "center" }}
                  >
                    {t("dialogs.documentAction.size", { size: (selectedFile.size / 1024).toFixed(2) })}
                  </Typography>
                )}
              </Grid>
            )}
            <Grid item xs={12}>
              <Autocomplete
                id="project-select-for-document-modal"
                options={projects}
                loading={isLoadingProjects}
                getOptionLabel={(option) => {
                  if (typeof option === "string") return option;
                  return option.name || t("dialogs.documentAction.idFallback", { id: option.id });
                }}
                value={formProjectValue}
                onChange={handleProjectChange}
                isOptionEqualToValue={(option, value) => {
                  if (!option || !value) return false;
                  if (typeof value === "string") return false;
                  return option.id === value.id;
                }}
                freeSolo
                selectOnFocus
                clearOnBlur
                handleHomeEndKeys
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={t("dialogs.documentAction.assignProject")}
                    variant="outlined"
                    margin="dense"
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <React.Fragment>
                          {isLoadingProjects ? (
                            <CircularProgress color="inherit" size={20} />
                          ) : null}
                          {params.InputProps.endAdornment}
                        </React.Fragment>
                      ),
                    }}
                  />
                )}
                disabled={anyActionInProgress || isLoadingProjects}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                margin="dense"
                id="document-tags-modal"
                label={t("dialogs.documentAction.tags")}
                name="tags"
                value={formData.tags}
                onChange={handleInputChange}
                disabled={anyActionInProgress}
                helperText={t("dialogs.documentAction.tagsHelp")}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                margin="dense"
                id="document-notes-modal"
                label={t("dialogs.documentAction.notes")}
                name="notes"
                value={formData.notes || ''}
                onChange={handleInputChange}
                disabled={anyActionInProgress}
                multiline
                rows={4}
                helperText={t("dialogs.documentAction.notesHelp")}
              />
            </Grid>
          </Grid>
        </Box>
      </DialogContent>
      <DialogActions
        sx={{ justifyContent: "space-between", px: 3, pb: 2, pt: 2 }}
      >
        <Box sx={{ display: "flex", gap: 1 }}>
          {isEditMode && onDeleteFromModal && (
            <Tooltip title={t("dialogs.documentAction.deleteTooltip")}>
              <span>
                <Button
                  variant="outlined"
                  color="inherit"
                  size="small"
                  onClick={handleInternalDelete}
                  disabled={anyActionInProgress}
                >
                  {t("dialogs.shared.delete")}
                </Button>
              </span>
            </Tooltip>
          )}
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          <Button
            onClick={onClose}
            disabled={anyActionInProgress}
            color="inherit"
          >
            {t("dialogs.shared.cancel")}
          </Button>
          {isEditMode && onReindex && (
            <Tooltip
              title={
                documentData.index_status === "INDEXED"
                  ? t("dialogs.documentAction.reindex")
                  : t("dialogs.documentAction.index")
              }
            >
              <span>
                <IconButton
                  color="inherit"
                  size="small"
                  onClick={handleInternalReindex}
                  disabled={
                    anyActionInProgress ||
                    documentData.index_status === "INDEXING"
                  }
                >
                  {isReindexing ? (
                    <CircularProgress size={20} color="inherit" />
                  ) : (
                    <SyncIcon fontSize="small" />
                  )}
                </IconButton>
              </span>
            </Tooltip>
          )}

          {/* Enhanced Entity Linking */}
          {isEditMode && (
            <Tooltip title={t("dialogs.documentAction.linkTooltip")}>
              <span>
                <IconButton
                  color="primary"
                  size="small"
                  onClick={handleOpenLinkingModal}
                  disabled={anyActionInProgress}
                >
                  <LinkIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          )}

          <Button
            onClick={handlePrimaryAction}
            variant="contained"
            disabled={anyActionInProgress}
          >
            {isSaving ? (
              <CircularProgress size={24} color="inherit" />
            ) : isEditMode ? (
              t("dialogs.documentAction.saveChanges")
            ) : (
              t("dialogs.documentAction.upload")
            )}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>

    {/* Enhanced Entity Linking Modal */}
    {isEditMode && documentData && (
      <LinkingModal
        open={linkingModalOpen}
        onClose={handleCloseLinkingModal}
        primaryEntityType="document"
        primaryEntityId={documentData.id}
        primaryEntityName={documentData.filename}
        linkableTypesConfig={linkableTypesConfig}
        apiGetLinkedItems={apiService.getCurrentlyLinkedItems}
        apiUpdateLinks={apiService.updateEntityLinks}
        onLinksUpdated={handleLinksUpdated}
      />
    )}
    </>
  );
};

export default DocumentActionModal;
