// frontend/src/components/modals/UnifiedUploadModal.jsx
// Unified file upload modal for both ChatPage and DocumentsPage

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
  LinearProgress,
} from "@mui/material";
import {
  Close as CloseIcon,
  UploadFile as UploadFileIcon,
  CloudUpload as CloudUploadIcon,
} from "@mui/icons-material";
import * as apiService from "../../api";
import { 
  managedApiCall,
} from "../../utils/resource_manager";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import { translate, useUiTranslation } from "../../i18n";

// Allowed file types (should match backend ALLOWED_EXTENSIONS)
const ALLOWED_FILE_TYPES = [
  '.txt', '.pdf', '.md', '.docx', '.csv', '.json', '.py', '.js', '.jsx', 
  '.html', '.css', '.xml', '.htm', '.java', '.c', '.cpp', '.h', '.cs', 
  '.go', '.php', '.rb', '.swift', '.kt', '.rs', '.scala', '.zip',
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.svg'
];

// Helper functions for progress display
const formatFileSize = (bytes) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

const formatDuration = (seconds) => {
  if (!seconds || seconds < 0) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return `${minutes}m ${remainingSeconds}s`;
};

const UnifiedUploadModal = ({
  open,
  onClose,
  onUploadComplete,
  sessionId = null, // For chat uploads
  projectId = null, // Pre-selected project
  mode = "document", // "document" or "chat"
}) => {
  const { t } = useUiTranslation();
  const [formData, setFormData] = useState({
    filename: "",
    project_id: projectId || "",
    tags: "",
  });
  const [selectedFile, setSelectedFile] = useState(null);
  const [formError, setFormError] = useState(null);
  const [projects, setProjects] = useState([]);
  const [isLoadingProjects, setIsLoadingProjects] = useState(false);
  const [formProjectValue, setFormProjectValue] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({ percentage: 0, speed: 0, eta: null });

  const { startProcess, updateProcess, completeProcess, errorProcess } = useUnifiedProgress();

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
      setFormError(translate("dialogs.unifiedUpload.loadProjectsFailed", { error: err.message }));
    } finally {
      setIsLoadingProjects(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchProjectsForDropdown();
      // Reset form data
      setFormData({ 
        filename: "", 
        project_id: projectId || "", 
        tags: mode === "chat" && sessionId ? `chat-upload,session_${sessionId}` : "" 
      });
      setSelectedFile(null);
      setFormError(null);
    }
  }, [open, projectId, sessionId, mode, fetchProjectsForDropdown]);

  // Sync project dropdown with formData.project_id
  useEffect(() => {
    if (open) {
      if (formData.project_id && projects.length > 0) {
        const projObj = projects.find(
          (p) => String(p.id) === String(formData.project_id),
        );
        if (projObj && (!formProjectValue || formProjectValue.id !== projObj.id)) {
          setFormProjectValue(projObj);
        }
      } else if (!formData.project_id) {
        setFormProjectValue(null);
      }
    }
  }, [open, projects, formData.project_id, formProjectValue]);

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
    const fileExtension = '.' + file.name.split('.').pop().toLowerCase();
    if (!ALLOWED_FILE_TYPES.includes(fileExtension)) {
      return t("dialogs.unifiedUpload.unsupportedType", { extension: fileExtension, allowed: ALLOWED_FILE_TYPES.join(', ') });
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
        e.target.value = '';
      } else {
        setSelectedFile(file);
        setFormError(null);
        // Auto-populate filename if not set
        if (!formData.filename) {
          setFormData(prev => ({ ...prev, filename: file.name }));
        }
      }
    }
  };

  const handleUpload = async () => {
    setFormError(null);
    
    if (!selectedFile) {
      setFormError(t("dialogs.unifiedUpload.selectFile"));
      return;
    }

    // Handle project creation if needed
    let finalProjectId = null;
    if (formProjectValue) {
      if (typeof formProjectValue === "object" && formProjectValue.id) {
        finalProjectId = formProjectValue.id;
      } else if (typeof formProjectValue === "string" && formProjectValue.trim() !== "") {
        try {
          const newProject = await apiService.createProject({
            name: formProjectValue.trim(),
          });
          if (newProject && newProject.id) {
            finalProjectId = newProject.id;
            await fetchProjectsForDropdown();
          } else {
            throw new Error(newProject.error || t("dialogs.unifiedUpload.createProjectFailed"));
          }
        } catch (err) {
          setFormError(t("dialogs.unifiedUpload.projectCreationError", { error: err.message }));
          return;
        }
      }
    }

    setIsUploading(true);
    setUploadProgress({ percentage: 0, speed: 0, eta: null });

    // Generate unique process ID for tracking
    const processId = `upload_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    try {
      // Start process in unified progress tracking
      startProcess(
        processId,
        t("dialogs.unifiedUpload.progressStart", { name: selectedFile.name }),
        "file_upload"
      );

      const uploadResult = await managedApiCall(
        processId,
        async (signal) => {
          // Use the unified upload API with progress callback
          return await apiService.uploadFile(
            selectedFile,
            finalProjectId,
            formData.tags,
            {},
            signal,
            (progressData) => {
              // Update local state for modal display
              setUploadProgress(progressData);
              
              // Update unified progress context
              updateProcess(
                processId,
                progressData.percentage,
                t("dialogs.unifiedUpload.progressUpdate", { name: selectedFile.name, percent: progressData.percentage })
              );
            }
          );
        },
        {
          timeout: 300000, // 5 minutes for large files
        }
      );

      if (uploadResult.error) {
        throw new Error(uploadResult.error);
      }

      // Mark upload as completed in progress context
      completeProcess(
        processId,
        t("dialogs.unifiedUpload.progressDone", { name: selectedFile.name })
      );

      // Success - call callback and close modal
      if (onUploadComplete) {
        onUploadComplete(uploadResult.data || uploadResult);
      }
      
      onClose();

      // Process will be automatically cleaned up by the context

    } catch (error) {
      console.error("Upload error:", error);
      setFormError(t("dialogs.unifiedUpload.importFailed", { error: error.message }));
      
      // Mark upload as failed in progress context
      errorProcess(
        processId,
        t("dialogs.unifiedUpload.progressFailed", { name: selectedFile.name, error: error.message })
      );
    } finally {
      setIsUploading(false);
    }
  };

  const handleClose = () => {
    if (!isUploading) {
      onClose();
    }
  };

  const modalTitle = mode === "chat" ? t("dialogs.unifiedUpload.chatTitle") : t("dialogs.unifiedUpload.documentTitle");

  return (
    <Dialog 
      open={open} 
      onClose={handleClose} 
      maxWidth="sm" 
      fullWidth
      disableEscapeKeyDown={isUploading}
    >
      <DialogTitle>
        <Box display="flex" alignItems="center" justifyContent="space-between">
          <Box display="flex" alignItems="center">
            <CloudUploadIcon sx={{ mr: 1 }} />
            {modalTitle}
          </Box>
          <IconButton 
            onClick={handleClose} 
            size="small" 
            disabled={isUploading}
          >
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent dividers>
        {formError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formError}
          </Alert>
        )}

        <Grid container spacing={2}>
          {/* File Selection */}
          <Grid item xs={12}>
            <Box
              border={2}
              borderColor={selectedFile ? "primary.main" : "grey.300"}
              borderRadius={2}
              p={2}
              textAlign="center"
              sx={{
                borderStyle: "dashed",
                cursor: "pointer",
                "&:hover": {
                  borderColor: "primary.main",
                  backgroundColor: "action.hover",
                },
              }}
              component="label"
            >
              <input
                type="file"
                hidden
                onChange={handleFileChange}
                accept={ALLOWED_FILE_TYPES.join(',')}
                disabled={isUploading}
              />
              <UploadFileIcon sx={{ fontSize: 48, color: "primary.main", mb: 1 }} />
              <Typography variant="body1" gutterBottom>
                {selectedFile ? selectedFile.name : t("dialogs.unifiedUpload.clickToSelect")}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {t("dialogs.unifiedUpload.supportedFormats", { formats: ALLOWED_FILE_TYPES.slice(0, 8).join(', ') })}
              </Typography>
            </Box>
          </Grid>

          {/* Upload Progress */}
          {isUploading && (
            <Grid item xs={12}>
              <Box sx={{ mb: 1 }}>
                <Typography variant="body2" color="text.secondary">
                  {uploadProgress.percentage > 0 
                    ? t("dialogs.unifiedUpload.importingPercent", { percent: uploadProgress.percentage })
                    : t("dialogs.unifiedUpload.importing")}
                </Typography>
                {uploadProgress.speed > 0 && (
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {t("dialogs.unifiedUpload.speed", { speed: formatFileSize(uploadProgress.speed) })}
                    {uploadProgress.eta && " • " + t("dialogs.unifiedUpload.eta", { time: formatDuration(uploadProgress.eta) })}
                  </Typography>
                )}
              </Box>
              <LinearProgress 
                variant={uploadProgress.percentage > 0 ? "determinate" : "indeterminate"}
                value={uploadProgress.percentage}
                sx={{ mb: 2 }} 
              />
            </Grid>
          )}

          {/* Project Selection */}
          <Grid item xs={12}>
            <Autocomplete
              value={formProjectValue}
              onChange={handleProjectChange}
              options={projects}
              getOptionLabel={(option) => {
                if (typeof option === "string") return option;
                return option?.name || "";
              }}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label={t("dialogs.unifiedUpload.project")}
                  placeholder={t("dialogs.unifiedUpload.projectPlaceholder")}
                  fullWidth
                  disabled={isUploading}
                />
              )}
              loading={isLoadingProjects}
              freeSolo
              disabled={isUploading}
            />
          </Grid>

          {/* Tags */}
          <Grid item xs={12}>
            <TextField
              name="tags"
              label={t("dialogs.unifiedUpload.tags")}
              value={formData.tags}
              onChange={handleInputChange}
              fullWidth
              placeholder={t("dialogs.unifiedUpload.tagsPlaceholder")}
              disabled={isUploading}
            />
          </Grid>
        </Grid>
      </DialogContent>

      <DialogActions>
        <Button 
          onClick={handleClose} 
          disabled={isUploading}
        >
          {t("dialogs.shared.cancel")}
        </Button>
        <Button
          variant="contained"
          onClick={handleUpload}
          disabled={!selectedFile || isUploading}
          startIcon={isUploading ? <CircularProgress size={16} /> : <CloudUploadIcon />}
        >
          {isUploading ? t("dialogs.unifiedUpload.uploading") : t("dialogs.unifiedUpload.upload")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default UnifiedUploadModal;