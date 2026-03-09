// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from Dean (user/owner).
import React, { useState, useEffect, useCallback } from "react";
import {
  Box,
  Typography,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Button,
  IconButton,
  Tooltip,
  CircularProgress,
  Snackbar,
  Alert as MuiAlert,
  Tabs,
  Tab,
  LinearProgress,
  Chip,
  Card,
  CardContent,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import EditIcon from "@mui/icons-material/Edit";
import DeleteIcon from "@mui/icons-material/Delete";
import CancelIcon from "@mui/icons-material/Cancel";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StorageIcon from "@mui/icons-material/Storage";
import WorkIcon from "@mui/icons-material/Work";
import ComputerIcon from "@mui/icons-material/Computer";

import {
  getTrainingDatasets,
  createTrainingDataset,
  updateTrainingDataset,
  deleteTrainingDataset,
  getTrainingJobs,
  createTrainingJob,
  cancelTrainingJob,
  resumeTrainingJob,
  deleteTrainingJob,
  getDeviceProfiles,
  createDeviceProfile,
  updateDeviceProfile,
  deleteDeviceProfile,
  getBaseModels,
  exportToOllama,
} from "../api";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import RefreshIcon from "@mui/icons-material/Refresh";
import TrainingDatasetModal from "../components/modals/TrainingDatasetModal";
import NewTrainingJobModal from "../components/modals/NewTrainingJobModal";
import DeviceProfileModal from "../components/modals/DeviceProfileModal";
import ParseJobModal from "../components/modals/ParseJobModal";
import ExportQuantizationModal from "../components/modals/ExportQuantizationModal";
import { useSnackbar } from "../components/common/SnackbarProvider";
import { useUnifiedProgress } from "../contexts/UnifiedProgressContext";
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

const TrainingPage = () => {
  const { showMessage } = useSnackbar();
  const { activeProcesses } = useUnifiedProgress();
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const [activeTab, setActiveTab] = useState(0);
  
  // Datasets state
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoading, setDatasetsLoading] = useState(true);
  
  // Jobs state
  const [jobs, setJobs] = useState([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  
  // Device profiles state
  const [deviceProfiles, setDeviceProfiles] = useState([]);
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [baseModels, setBaseModels] = useState([]);
  
  // Common state
  const [error, setError] = useState(null);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [jobModalOpen, setJobModalOpen] = useState(false);
  const [deviceModalOpen, setDeviceModalOpen] = useState(false);
  const [parseModalOpen, setParseModalOpen] = useState(false);
  const [currentItem, setCurrentItem] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const [feedback, setFeedback] = useState({
    open: false,
    message: "",
    severity: "info",
  });

  const fetchDatasets = useCallback(async () => {
    setDatasetsLoading(true);
    try {
      const data = await getTrainingDatasets();
      if (data?.error) throw new Error(data.error.message || data.error);
      setDatasets(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error("Error fetching training datasets:", err);
      showMessage(err.message || "Failed to fetch datasets.", "error");
      setDatasets([]);
    } finally {
      setDatasetsLoading(false);
    }
  }, [showMessage]);

  const fetchJobs = useCallback(async () => {
    setJobsLoading(true);
    try {
      const data = await getTrainingJobs();
      if (data?.error) throw new Error(data.error.message || data.error);
      setJobs(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error("Error fetching training jobs:", err);
      showMessage(err.message || "Failed to fetch jobs.", "error");
      setJobs([]);
    } finally {
      setJobsLoading(false);
    }
  }, [showMessage]);

  const fetchDeviceProfiles = useCallback(async () => {
    setProfilesLoading(true);
    try {
      const data = await getDeviceProfiles();
      if (data?.error) throw new Error(data.error.message || data.error);
      setDeviceProfiles(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error("Error fetching device profiles:", err);
      showMessage(err.message || "Failed to fetch device profiles.", "error");
      setDeviceProfiles([]);
    } finally {
      setProfilesLoading(false);
    }
  }, [showMessage]);

  const fetchBaseModels = useCallback(async () => {
    try {
      const data = await getBaseModels();
      if (data?.error) throw new Error(data.error.message || data.error);
      setBaseModels(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error("Error fetching base models:", err);
    }
  }, []);

  useEffect(() => {
    fetchDatasets();
    fetchJobs();
    fetchDeviceProfiles();
    fetchBaseModels();
  }, [fetchDatasets, fetchJobs, fetchDeviceProfiles, fetchBaseModels]);

  // Refresh jobs periodically when on jobs tab
  useEffect(() => {
    if (activeTab === 1) {
      const interval = setInterval(() => {
        fetchJobs();
      }, 5000); // Refresh every 5 seconds
      return () => clearInterval(interval);
    }
  }, [activeTab, fetchJobs]);

  const handleOpenEditModal = (item = null) => {
    setCurrentItem(item);
    setEditModalOpen(true);
  };

  const handleCloseEditModal = () => {
    if (isSaving) return;
    setCurrentItem(null);
    setEditModalOpen(false);
  };

  const handleSave = async (formData) => {
    setIsSaving(true);
    try {
      let result;
      if (currentItem) {
        result = await updateTrainingDataset(currentItem.id, formData);
      } else {
        result = await createTrainingDataset(formData);
      }
      if (result?.error) throw new Error(result.error.message || result.error);
      setFeedback({
        open: true,
        message: "Dataset saved successfully.",
        severity: "success",
      });
      await fetchDatasets();
      handleCloseEditModal();
    } catch (err) {
      console.error("Error saving dataset:", err);
      showMessage(`Save failed: ${err.message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCloseSnackbar = () => {
    setFeedback((prev) => ({ ...prev, open: false }));
  };

  const handleDeleteDataset = async (dataset) => {
    if (!window.confirm(`Are you sure you want to delete the dataset "${dataset.name}"? This action cannot be undone.`)) return;
    try {
      await deleteTrainingDataset(dataset.id);
      showMessage(`Dataset "${dataset.name}" deleted successfully`, "success");
      fetchDatasets();
    } catch (err) {
      showMessage(`Failed to delete dataset: ${err.message}`, "error");
    }
  };

  const handleCancelJob = async (jobId) => {
    try {
      await cancelTrainingJob(jobId);
      showMessage("Job cancelled", "success");
      fetchJobs();
    } catch (err) {
      showMessage(`Failed to cancel job: ${err.message}`, "error");
    }
  };

  const handleResumeJob = async (jobId) => {
    try {
      await resumeTrainingJob(jobId);
      showMessage("Job resumed - training continuing from checkpoint", "success");
      fetchJobs();
    } catch (err) {
      showMessage(`Failed to resume job: ${err.message}`, "error");
    }
  };

  const handleDeleteJob = async (jobId) => {
    if (!window.confirm("Are you sure you want to delete this job?")) return;
    try {
      await deleteTrainingJob(jobId);
      showMessage("Job deleted", "success");
      fetchJobs();
    } catch (err) {
      showMessage(`Failed to delete job: ${err.message}`, "error");
    }
  };

  const [exportingJobs, setExportingJobs] = useState(new Set());
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [selectedJobForExport, setSelectedJobForExport] = useState(null);
  const [isReQuantize, setIsReQuantize] = useState(false);

  const handleExportToOllama = (job, reQuantize = false) => {
    setSelectedJobForExport(job);
    setIsReQuantize(reQuantize);
    setExportModalOpen(true);
  };

  const handleExportConfirm = async (quantization, modelName) => {
    if (!selectedJobForExport) return;

    setExportingJobs((prev) => new Set([...prev, selectedJobForExport.id]));
    try {
      const result = await exportToOllama(selectedJobForExport.id, {
        model_name: modelName,
        quantization: quantization,
      });
      showMessage(
        result.message || `Export started for ${modelName} (${quantization.toUpperCase()})`,
        "info"
      );
      setExportModalOpen(false);
      fetchJobs(); // Refresh to show updated status
    } catch (err) {
      showMessage(`Failed to export: ${err.message}`, "error");
    } finally {
      setExportingJobs((prev) => {
        const next = new Set(prev);
        next.delete(selectedJobForExport.id);
        return next;
      });
    }
  };

  const handleSaveJob = async (jobData) => {
    setIsSaving(true);
    try {
      await createTrainingJob(jobData);
      showMessage("Training job created successfully", "success");
      setJobModalOpen(false);
      fetchJobs();
    } catch (err) {
      showMessage(`Failed to create job: ${err.message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleOpenDeviceModal = (profile = null) => {
    setCurrentItem(profile);
    setDeviceModalOpen(true);
  };

  const handleCloseDeviceModal = () => {
    if (isSaving) return;
    setCurrentItem(null);
    setDeviceModalOpen(false);
  };

  const handleSaveDeviceProfile = async (profileData) => {
    setIsSaving(true);
    try {
      if (currentItem) {
        await updateDeviceProfile(currentItem.id, profileData);
        showMessage("Device profile updated successfully", "success");
      } else {
        await createDeviceProfile(profileData);
        showMessage("Device profile created successfully", "success");
      }
      handleCloseDeviceModal();
      fetchDeviceProfiles();
    } catch (err) {
      showMessage(`Failed to save profile: ${err.message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteDeviceProfile = async (profileId) => {
    if (!window.confirm("Are you sure you want to delete this device profile?")) return;
    try {
      await deleteDeviceProfile(profileId);
      showMessage("Device profile deleted", "success");
      fetchDeviceProfiles();
    } catch (err) {
      showMessage(`Failed to delete profile: ${err.message}`, "error");
    }
  };

  const getJobProgress = (job) => {
    // Check if there's a progress event for this job
    const progressEvent = activeProcesses.get(job.job_id);
    if (progressEvent) {
      return progressEvent.progress || job.progress || 0;
    }
    return job.progress || 0;
  };

  const getStatusColor = (status) => {
    switch (status) {
      case "completed": return "success";
      case "running": return "info";
      case "failed": return "error";
      case "cancelled": return "default";
      default: return "warning";
    }
  };

  return (
    <PageLayout
      title="Training Manager"
      variant="standard"
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel || "Default"}
    >
      <Paper elevation={2}>
        <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
          <Tabs value={activeTab} onChange={(e, v) => setActiveTab(v)}>
            <Tab icon={<StorageIcon />} iconPosition="start" label="Datasets" />
            <Tab icon={<WorkIcon />} iconPosition="start" label="Jobs" />
            <Tab icon={<ComputerIcon />} iconPosition="start" label="Devices" />
          </Tabs>
        </Box>

        {/* Datasets Tab */}
        {activeTab === 0 && (
          <Box sx={{ p: 2 }}>
            <Box
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              mb={2}
            >
              <Typography variant="h6">Training Datasets</Typography>
              <Box display="flex" gap={1}>
                <Button
                  variant="outlined"
                  startIcon={<PlayArrowIcon />}
                  onClick={() => setParseModalOpen(true)}
                >
                  Parse Transcripts
                </Button>
                <Button
                  variant="contained"
                  startIcon={<AddIcon />}
                  onClick={() => handleOpenEditModal(null)}
                >
                  Add Dataset
                </Button>
              </Box>
            </Box>
            {datasetsLoading ? (
              <Box display="flex" justifyContent="center" my={3}>
                <CircularProgress />
              </Box>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Name</TableCell>
                      <TableCell>Description</TableCell>
                      <TableCell>Path</TableCell>
                      <TableCell align="right">Actions</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {datasets.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={4} align="center" sx={{ py: 4 }}>
                          <Typography variant="body2" color="text.secondary">
                            No datasets found. Click "Add Dataset" to create one.
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ) : (
                      datasets.map((ds) => (
                        <TableRow key={ds.id} hover>
                          <TableCell>
                            <Typography variant="body2" fontWeight="medium">
                              {ds.name}
                            </Typography>
                          </TableCell>
                          <TableCell>
                            <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {ds.description || "-"}
                            </Typography>
                          </TableCell>
                          <TableCell>
                            <Tooltip title={ds.path || "-"}>
                              <Typography variant="body2" sx={{ maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'monospace', fontSize: '0.8rem' }}>
                                {ds.path || "-"}
                              </Typography>
                            </Tooltip>
                          </TableCell>
                          <TableCell align="right">
                            <Box sx={{ display: 'flex', gap: 0.5, justifyContent: 'flex-end' }}>
                              <Tooltip title="Edit / Rename">
                                <IconButton
                                  size="small"
                                  onClick={() => handleOpenEditModal(ds)}
                                  color="primary"
                                >
                                  <EditIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                              <Tooltip title="Delete Dataset">
                                <IconButton
                                  size="small"
                                  onClick={() => handleDeleteDataset(ds)}
                                  color="error"
                                >
                                  <DeleteIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            </Box>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Box>
        )}

        {/* Jobs Tab */}
        {activeTab === 1 && (
          <Box sx={{ p: 2 }}>
            <Box
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              mb={2}
            >
              <Typography variant="h6">Training Jobs</Typography>
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={() => setJobModalOpen(true)}
              >
                New Training Job
              </Button>
            </Box>
            {jobsLoading ? (
              <Box display="flex" justifyContent="center" my={3}>
                <CircularProgress />
              </Box>
            ) : (
              <Box>
                {jobs.map((job) => {
                  const progress = getJobProgress(job);
                  return (
                    <Card key={job.id} sx={{ mb: 2 }}>
                      <CardContent>
                        <Box display="flex" justifyContent="space-between" alignItems="start" mb={1}>
                          <Box>
                            <Typography variant="h6">{job.name || job.job_id}</Typography>
                            <Typography variant="caption" color="text.secondary">
                              {job.base_model} → {job.output_model_name || "N/A"}
                            </Typography>
                          </Box>
                          <Box display="flex" gap={1} alignItems="center">
                            <Chip
                              label={job.status}
                              color={getStatusColor(job.status)}
                              size="small"
                            />
                            {job.pipeline_stage && job.pipeline_stage !== "pending" && (
                              <Chip
                                label={job.pipeline_stage}
                                variant="outlined"
                                size="small"
                              />
                            )}
                            {/* Export to Ollama button - show when completed with lora_path */}
                            {job.status === "completed" && job.lora_path && !job.ollama_model_name && (
                              <Tooltip title="Export to Ollama">
                                <IconButton
                                  size="small"
                                  color="primary"
                                  onClick={() => handleExportToOllama(job, false)}
                                  disabled={exportingJobs.has(job.id)}
                                >
                                  {exportingJobs.has(job.id) ? (
                                    <CircularProgress size={18} />
                                  ) : (
                                    <CloudUploadIcon fontSize="small" />
                                  )}
                                </IconButton>
                              </Tooltip>
                            )}
                            {/* Re-quantize button - show for completed exports */}
                            {job.status === "completed" && job.lora_path && job.ollama_model_name && (
                              <Tooltip title="Re-quantize (change quantization level)">
                                <IconButton
                                  size="small"
                                  color="secondary"
                                  onClick={() => handleExportToOllama(job, true)}
                                  disabled={exportingJobs.has(job.id)}
                                >
                                  {exportingJobs.has(job.id) ? (
                                    <CircularProgress size={18} />
                                  ) : (
                                    <RefreshIcon fontSize="small" />
                                  )}
                                </IconButton>
                              </Tooltip>
                            )}
                            {job.status === "running" && (
                              <Tooltip title="Cancel">
                                <IconButton size="small" onClick={() => handleCancelJob(job.id)}>
                                  <CancelIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                            {job.is_resumable && (job.status === "failed" || job.status === "cancelled") && (
                              <Tooltip title="Resume from checkpoint">
                                <IconButton size="small" color="success" onClick={() => handleResumeJob(job.id)}>
                                  <PlayArrowIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                            <Tooltip title="Delete">
                              <IconButton size="small" onClick={() => handleDeleteJob(job.id)}>
                                <DeleteIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          </Box>
                        </Box>
                        {job.status === "running" && (
                          <Box sx={{ mt: 1 }}>
                            <LinearProgress variant="determinate" value={progress} />
                            <Typography variant="caption" color="text.secondary">
                              {progress}% - {job.pipeline_stage || "processing"}
                            </Typography>
                          </Box>
                        )}
                        {job.error_message && (
                          <Typography variant="caption" color="error" sx={{ mt: 1, display: "block" }}>
                            Error: {job.error_message}
                          </Typography>
                        )}
                        {/* Show export results when available */}
                        {(job.gguf_path || job.ollama_model_name) && (
                          <Box sx={{ mt: 1, p: 1, bgcolor: "action.hover", borderRadius: 1 }}>
                            {job.gguf_path && (
                              <Typography variant="caption" display="block" sx={{ fontFamily: "monospace" }}>
                                GGUF: {job.gguf_path}
                              </Typography>
                            )}
                            {job.ollama_model_name && (
                              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                                <Typography variant="caption" display="block" color="success.main" sx={{ fontWeight: "medium" }}>
                                  Ollama: {job.ollama_model_name}
                                </Typography>
                                {job.quantization_level && (
                                  <Chip label={job.quantization_level.toUpperCase()} size="small" variant="outlined" />
                                )}
                              </Box>
                            )}
                          </Box>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
                {jobs.length === 0 && (
                  <Typography color="text.secondary" align="center" sx={{ py: 4 }}>
                    No training jobs found
                  </Typography>
                )}
              </Box>
            )}
          </Box>
        )}

        {/* Devices Tab */}
        {activeTab === 2 && (
          <Box sx={{ p: 2 }}>
            <Box
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              mb={2}
            >
              <Typography variant="h6">Device Profiles</Typography>
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={() => handleOpenDeviceModal(null)}
              >
                Add Profile
              </Button>
            </Box>
            {profilesLoading ? (
              <Box display="flex" justifyContent="center" my={3}>
                <CircularProgress />
              </Box>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Name</TableCell>
                      <TableCell>Type</TableCell>
                      <TableCell>VRAM</TableCell>
                      <TableCell>Batch Size</TableCell>
                      <TableCell>Seq Length</TableCell>
                      <TableCell>Default</TableCell>
                      <TableCell align="right">Actions</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {deviceProfiles.map((profile) => (
                      <TableRow key={profile.id} hover>
                        <TableCell>{profile.name}</TableCell>
                        <TableCell>{profile.device_type || "-"}</TableCell>
                        <TableCell>{profile.gpu_vram_mb ? `${profile.gpu_vram_mb / 1024}GB` : "N/A"}</TableCell>
                        <TableCell>{profile.max_batch_size}</TableCell>
                        <TableCell>{profile.max_seq_length}</TableCell>
                        <TableCell>{profile.is_default ? <Chip label="Default" size="small" color="primary" /> : "-"}</TableCell>
                        <TableCell align="right">
                          <Tooltip title="Edit">
                            <span>
                              <IconButton
                                size="small"
                                onClick={() => handleOpenDeviceModal(profile)}
                              >
                                <EditIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Tooltip title="Delete">
                            <span>
                              <IconButton
                                size="small"
                                onClick={() => handleDeleteDeviceProfile(profile.id)}
                                sx={{ ml: 1 }}
                              >
                                <DeleteIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Box>
        )}
      </Paper>

      {editModalOpen && (
        <TrainingDatasetModal
          open={editModalOpen}
          onClose={handleCloseEditModal}
          datasetData={currentItem}
          onSave={handleSave}
          isSaving={isSaving}
        />
      )}

      {jobModalOpen && (
        <NewTrainingJobModal
          open={jobModalOpen}
          onClose={() => setJobModalOpen(false)}
          onSave={handleSaveJob}
          isSaving={isSaving}
        />
      )}

      {deviceModalOpen && (
        <DeviceProfileModal
          open={deviceModalOpen}
          onClose={handleCloseDeviceModal}
          profileData={currentItem}
          onSave={handleSaveDeviceProfile}
          isSaving={isSaving}
        />
      )}

      {parseModalOpen && (
        <ParseJobModal
          open={parseModalOpen}
          onClose={() => setParseModalOpen(false)}
          onSuccess={(result) => {
            showMessage("Parse job started successfully", "success");
            fetchJobs();
          }}
        />
      )}

      {exportModalOpen && (
        <ExportQuantizationModal
          open={exportModalOpen}
          onClose={() => setExportModalOpen(false)}
          job={selectedJobForExport}
          onExport={handleExportConfirm}
          isExporting={exportingJobs.has(selectedJobForExport?.id)}
          isReQuantize={isReQuantize}
          currentQuantization={selectedJobForExport?.quantization_level}
        />
      )}

      <Snackbar
        open={feedback.open}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
      >
        <AlertSnackbar
          onClose={handleCloseSnackbar}
          severity={feedback.severity}
          sx={{ width: "100%" }}
        >
          {feedback.message}
        </AlertSnackbar>
      </Snackbar>
    </PageLayout>
  );
};

export default TrainingPage;
