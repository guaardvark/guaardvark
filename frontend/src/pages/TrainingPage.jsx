// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from the project maintainer.
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
  Alert,
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
import CloseIcon from "@mui/icons-material/Close";
import CancelIcon from "@mui/icons-material/Cancel";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StorageIcon from "@mui/icons-material/Storage";
import WorkIcon from "@mui/icons-material/Work";
import ComputerIcon from "@mui/icons-material/Computer";
import SchoolIcon from "@mui/icons-material/School";
import SaveIcon from "@mui/icons-material/Save";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import KeyboardArrowUpIcon from "@mui/icons-material/KeyboardArrowUp";
import Collapse from "@mui/material/Collapse";

import {
  getTrainingDatasets,
  createTrainingDataset,
  updateTrainingDataset,
  deleteTrainingDataset,
  getTrainingJobs,
  createTrainingJob,
  cancelTrainingJob,
  resumeTrainingJob,
  startTrainingJob,
  deleteTrainingJob,
  getDeviceProfiles,
  createDeviceProfile,
  updateDeviceProfile,
  deleteDeviceProfile,
  getHardwareCapabilities,
  getBaseModels,
  exportToOllama,
  exportTrainingJobAnyway,
} from "../api";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import RefreshIcon from "@mui/icons-material/Refresh";
import Editor from "../utils/monaco";
import { useSearchParams, Link as RouterLink } from "react-router-dom";
import { useTheme } from "@mui/material/styles";
import axios from "axios";
import TrainingDatasetModal from "../components/modals/TrainingDatasetModal";
import NewTrainingJobModal from "../components/modals/NewTrainingJobModal";
import DeviceProfileModal, { hardwareNote, profileFromHardware } from "../components/modals/DeviceProfileModal";
import ParseJobModal from "../components/modals/ParseJobModal";
import BuildFromFolderModal from "../components/modals/BuildFromFolderModal";
import ExportQuantizationModal from "../components/modals/ExportQuantizationModal";
import TrainingLibrariesModal from "../components/modals/TrainingLibrariesModal";
import { useSnackbar } from "../components/common/SnackbarProvider";
import EntityContextMenu from "../components/common/EntityContextMenu";
import useContextMenu from "../hooks/useContextMenu";
import { useUnifiedProgress } from "../contexts/UnifiedProgressContext";
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import { useAppStore } from "../stores/useAppStore";
import {
  createFormSync,
  dirtyFields,
  markFieldsSaved,
  mergeServerValues,
  setFieldValue,
} from "../utils/serverFormSync";
import { uiLocale, useUiTranslation, Trans } from "../i18n";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

const LEARN_API = "/api/agent-control/learn";

// ?tab= values the page opens on (TrainingFloater and other links use them).
const TAB_INDEX = { demonstrations: 0, datasets: 1, jobs: 2, devices: 3 };

/** The steps editor's text for a demonstration as the server holds it. */
const stepsJson = (steps) =>
  JSON.stringify((steps || []).map(({ _id, ...rest }) => rest), null, 2);

// `draft` is this row's unsaved editor text as a synced form
// (utils/serverFormSync), held by the page so that collapsing the row or
// reloading the list does not drop it; null when there is no edit.
// updateDraft(id, fn) replaces it with fn(current form).
const DemoRow = ({ demo, expanded, onToggle, onDelete, onAttempt, draft, updateDraft, onSaved, showMessage, theme }) => {
  const { t } = useUiTranslation();
  const [saving, setSaving] = useState(false);
  const serverSteps = stepsJson(demo.steps);
  const editorValue = draft ? draft.values.steps : serverSteps;
  const dirty = Boolean(draft) && dirtyFields(draft).length > 0;

  // Steps saved elsewhere while this row holds an edit: the edit is kept (the
  // next save writes over them) and the person is told, through the page's
  // existing snackbar.
  const conflict = draft?.conflicts?.steps;
  useEffect(() => {
    if (conflict !== undefined) {
      showMessage(
        t("training.demos.stepsConflict", { name: demo.name || t("training.demos.untitled") }),
        "warning",
      );
    }
  }, [conflict]);

  const handleEdit = (val) =>
    updateDraft(demo.id, (form) => setFieldValue(form || createFormSync({ steps: serverSteps }), "steps", val || ""));

  const handleSaveSteps = async () => {
    const sent = editorValue;
    setSaving(true);
    try {
      const parsed = JSON.parse(sent);
      if (!Array.isArray(parsed)) throw new Error(t("training.demos.stepsNotArray"));
      const res = await axios.put(`${LEARN_API}/demonstrations/${demo.id}/steps`, { steps: parsed });
      updateDraft(demo.id, (form) => (form ? markFieldsSaved(form, { steps: sent }) : form));
      // The list's copy now matches what was saved, so the step count is right
      // and re-opening the row shows the saved steps.
      onSaved(res.data?.demonstration || { ...demo, steps: parsed });
      showMessage(t("training.demos.stepsSaved"), "success");
    } catch (err) {
      showMessage(t("training.common.saveFailed", { error: err.message }), "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <TableRow hover onClick={onToggle} sx={{ cursor: "pointer", "& > *": { borderBottom: expanded ? 0 : undefined } }}>
        <TableCell sx={{ width: 40, p: 0.5 }}>
          <IconButton size="small">
            {expanded ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
          </IconButton>
        </TableCell>
        <TableCell>
          <Typography variant="body2" fontWeight="medium">
            {demo.name || t("training.demos.untitled")}
          </Typography>
        </TableCell>
        <TableCell align="center">{demo.steps?.length ?? 0}</TableCell>
        <TableCell>
          <Chip
            label={demo.autonomy_level}
            size="small"
            color={
              demo.autonomy_level === "guided" ? "warning" :
              demo.autonomy_level === "supervised" ? "info" :
              demo.autonomy_level === "autonomous" ? "success" : "default"
            }
            sx={{ height: 22 }}
          />
        </TableCell>
        <TableCell align="center">
          {demo.success_count}/{demo.attempt_count}
        </TableCell>
        <TableCell>
          <Typography variant="body2" color="text.secondary" sx={{ fontSize: "0.8rem" }}>
            {demo.created_at ? new Date(demo.created_at).toLocaleDateString(uiLocale()) : "-"}
          </Typography>
        </TableCell>
        <TableCell align="right">
          <Box sx={{ display: "flex", gap: 0.5, justifyContent: "flex-end" }}>
            <Tooltip title={t("training.demos.attempt")}>
              <IconButton size="small" onClick={(e) => { e.stopPropagation(); onAttempt(demo); }} color="primary">
                <PlayArrowIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title={t("training.demos.delete")}>
              <IconButton size="small" onClick={(e) => { e.stopPropagation(); onDelete(demo); }} color="error">
                <CloseIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={7} sx={{ py: 0, px: 0 }}>
          <Collapse in={expanded} timeout="auto" unmountOnExit>
            <Box sx={{ p: 2, bgcolor: "action.hover" }}>
              <Typography variant="subtitle2" gutterBottom>
                {t("training.demos.stepsEditor")}
              </Typography>
              <Editor
                height="300px"
                language="json"
                theme={theme.palette.mode === "dark" ? "vs-dark" : "vs-light"}
                value={editorValue}
                onChange={handleEdit}
                options={{
                  minimap: { enabled: false },
                  fontSize: 13,
                  lineNumbers: "on",
                  scrollBeyondLastLine: false,
                  wordWrap: "on",
                  tabSize: 2,
                }}
              />
              <Box sx={{ display: "flex", justifyContent: "flex-end", mt: 1 }}>
                <Button
                  variant="contained"
                  size="small"
                  startIcon={<SaveIcon />}
                  onClick={handleSaveSteps}
                  disabled={saving || !dirty}
                >
                  {saving ? t("training.demos.saving") : t("training.demos.saveSteps")}
                </Button>
              </Box>
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
};

const TrainingPage = () => {
  const { t } = useUiTranslation();
  const { showMessage } = useSnackbar();
  const { activeProcesses } = useUnifiedProgress();
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const theme = useTheme();
  // The Interactive Trainer is the app-wide floating window that records demonstrations.
  const setTrainerOpen = useAppStore((state) => state.setTrainerOpen);
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeTab, setActiveTab] = useState(() => TAB_INDEX[searchParams.get("tab")] ?? 0);

  // Demonstrations state
  const [demonstrations, setDemonstrations] = useState([]);
  const [demosLoading, setDemosLoading] = useState(true);
  const [expandedDemoId, setExpandedDemoId] = useState(() => {
    const demoParam = searchParams.get("demo");
    return demoParam ? parseInt(demoParam, 10) : null;
  });
  // Unsaved steps edits by demonstration id (see DemoRow). Kept here because
  // the rows unmount while the list reloads.
  const [stepDrafts, setStepDrafts] = useState({});
  const updateStepDraft = useCallback((id, fn) => {
    setStepDrafts((prev) => {
      const next = fn(prev[id] || null);
      if (next === (prev[id] || null)) return prev;
      return { ...prev, [id]: next };
    });
  }, []);
  // Each reload folds the server's steps into the drafts: a draft with no
  // edit left is dropped, an edited one keeps its text (and notes a conflict
  // when the server's steps moved on), and a deleted demonstration's goes.
  useEffect(() => {
    setStepDrafts((prev) => {
      const next = {};
      Object.entries(prev).forEach(([id, form]) => {
        const demo = demonstrations.find((d) => String(d.id) === id);
        if (!demo || !form) return;
        const merged = mergeServerValues(form, { steps: stepsJson(demo.steps) });
        if (dirtyFields(merged).length) next[id] = merged;
      });
      const same = Object.keys(next).length === Object.keys(prev).length
        && Object.keys(next).every((id) => next[id] === prev[id]);
      return same ? prev : next;
    });
  }, [demonstrations]);
  const handleDemoSaved = useCallback((saved) => {
    if (!saved?.id) return;
    setDemonstrations((prev) => prev.map((d) => (d.id === saved.id ? { ...d, ...saved } : d)));
  }, []);

  // Datasets state
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoading, setDatasetsLoading] = useState(true);
  
  // Jobs state
  const [jobs, setJobs] = useState([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  
  // Device profiles state
  const [deviceProfiles, setDeviceProfiles] = useState([]);
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [_baseModels, setBaseModels] = useState([]);
  
  // Common state
  const [_error, _setError] = useState(null);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [jobModalOpen, setJobModalOpen] = useState(false);
  const [deviceModalOpen, setDeviceModalOpen] = useState(false);
  // A new profile filled from this machine's detected hardware: {values, note}.
  const [devicePrefill, setDevicePrefill] = useState(null);
  const [detectingHardware, setDetectingHardware] = useState(false);
  const [parseModalOpen, setParseModalOpen] = useState(false);
  const [buildModalOpen, setBuildModalOpen] = useState(false);
  const [currentItem, setCurrentItem] = useState(null);
  // Starting values for a new dataset (a finished parse's output); kept in
  // state so the jobs list refreshing does not reset the open form.
  const [datasetPrefill, setDatasetPrefill] = useState(null);
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
      showMessage(err.message || t("training.errors.fetchDatasets"), "error");
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
      showMessage(err.message || t("training.errors.fetchJobs"), "error");
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
      showMessage(err.message || t("training.errors.fetchProfiles"), "error");
      setDeviceProfiles([]);
    } finally {
      setProfilesLoading(false);
    }
  }, [showMessage]);

  const fetchBaseModels = useCallback(async () => {
    try {
      const data = await getBaseModels();
      setBaseModels(Array.isArray(data?.models) ? data.models : []);
    } catch (err) {
      console.error("Error fetching base models:", err);
    }
  }, []);

  const fetchDemonstrations = useCallback(async () => {
    setDemosLoading(true);
    try {
      const res = await axios.get(`${LEARN_API}/demonstrations`);
      setDemonstrations(res.data.demonstrations || []);
    } catch (err) {
      console.error("Error fetching demonstrations:", err);
      showMessage(t("training.errors.fetchDemos"), "error");
      setDemonstrations([]);
    } finally {
      setDemosLoading(false);
    }
  }, [showMessage]);

  const handleDeleteDemo = async (demo) => {
    try {
      await axios.delete(`${LEARN_API}/demonstrations/${demo.id}`);
      showMessage(t("training.demos.deleted", { name: demo.name || t("training.demos.untitled") }), "success");
      setDemonstrations((prev) => prev.filter((d) => d.id !== demo.id));
      if (expandedDemoId === demo.id) setExpandedDemoId(null);
    } catch (err) {
      showMessage(t("training.demos.deleteFailed", { error: err.message }), "error");
    }
  };

  const handleAttemptDemo = async (demo) => {
    try {
      const res = await axios.post(`${LEARN_API}/demonstrations/${demo.id}/attempt`);
      if (res.data.success) {
        showMessage(t("training.demos.attemptStarted", { mode: res.data.autonomy_level }), "info");
      }
    } catch (err) {
      showMessage(t("training.demos.attemptFailed", { error: err.message }), "error");
    }
  };

  useEffect(() => {
    fetchDemonstrations();
    fetchDatasets();
    fetchJobs();
    fetchDeviceProfiles();
    fetchBaseModels();
  }, [fetchDemonstrations, fetchDatasets, fetchJobs, fetchDeviceProfiles, fetchBaseModels]);

  // Handle URL params for deep-linking from TrainingFloater
  useEffect(() => {
    const tab = searchParams.get("tab");
    if (tab in TAB_INDEX) {
      setActiveTab(TAB_INDEX[tab]);
      const demoId = searchParams.get("demo");
      if (tab === "demonstrations" && demoId) setExpandedDemoId(parseInt(demoId, 10));
      // Clear params after applying
      setSearchParams({}, { replace: true });
    }
  }, []);

  // Refresh jobs periodically when on jobs tab
  useEffect(() => {
    if (activeTab === 2) {
      const interval = setInterval(() => {
        fetchJobs();
      }, 5000); // Refresh every 5 seconds
      return () => clearInterval(interval);
    }
  }, [activeTab, fetchJobs]);

  const handleOpenEditModal = (item = null, prefill = null) => {
    setCurrentItem(item);
    setDatasetPrefill(prefill);
    setEditModalOpen(true);
  };

  const handleCloseEditModal = () => {
    if (isSaving) return;
    setCurrentItem(null);
    setDatasetPrefill(null);
    setEditModalOpen(false);
  };

  // Parse and folder-build jobs both end in a .jsonl in the datasets folder.
  const isDatasetJob = (job) => job.pipeline_stage === "parsing" || job.pipeline_stage === "building";

  const handleAddParsedDataset = (job) => {
    handleOpenEditModal(null, {
      name: (job.name || "").replace(/^(Parse|Build):\s*/, "") ||
        (job.pipeline_stage === "building" ? "Notes" : "Parsed transcripts"),
      path: job.config?.output_path || "",
    });
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
        message: t("training.datasets.saved"),
        severity: "success",
      });
      await fetchDatasets();
      handleCloseEditModal();
    } catch (err) {
      console.error("Error saving dataset:", err);
      showMessage(t("training.common.saveFailed", { error: err.message }), "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCloseSnackbar = () => {
    setFeedback((prev) => ({ ...prev, open: false }));
  };

  const handleDeleteDataset = async (dataset) => {
    if (!window.confirm(t("training.datasets.deleteConfirm", { name: dataset.name }))) return;
    try {
      await deleteTrainingDataset(dataset.id);
      showMessage(t("training.datasets.deleted", { name: dataset.name }), "success");
      fetchDatasets();
    } catch (err) {
      showMessage(t("training.datasets.deleteFailed", { error: err.message }), "error");
    }
  };

  const handleCancelJob = async (jobId) => {
    try {
      await cancelTrainingJob(jobId);
      showMessage(t("training.jobs.cancelled"), "success");
      fetchJobs();
    } catch (err) {
      showMessage(t("training.jobs.cancelFailed", { error: err.message }), "error");
    }
  };

  const handleStartJob = async (jobId) => {
    try {
      await startTrainingJob(jobId);
      showMessage(t("training.jobs.started"), "success");
      fetchJobs();
    } catch (err) {
      showMessage(t("training.jobs.startFailed", { error: err.message }), "error");
    }
  };

  const handleResumeJob = async (jobId) => {
    try {
      await resumeTrainingJob(jobId);
      showMessage(t("training.jobs.resumed"), "success");
      fetchJobs();
    } catch (err) {
      showMessage(t("training.jobs.resumeFailed", { error: err.message }), "error");
    }
  };

  const handleDeleteJob = async (jobId) => {
    if (!window.confirm(t("training.jobs.deleteConfirm"))) return;
    try {
      await deleteTrainingJob(jobId);
      showMessage(t("training.jobs.deleted"), "success");
      fetchJobs();
    } catch (err) {
      showMessage(t("training.jobs.deleteFailed", { error: err.message }), "error");
    }
  };

  const [exportingJobs, setExportingJobs] = useState(new Set());
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [selectedJobForExport, setSelectedJobForExport] = useState(null);
  const [isReQuantize, setIsReQuantize] = useState(false);
  const [converterOpen, setConverterOpen] = useState(false);

  // Export cannot run now: say why before the form opens, and when the GGUF
  // converter is what it waits for, open the Install for it.
  const exportBlocked = (job) => {
    if (!job.export_blocked) return false;
    showMessage(job.export_blocked, "warning");
    if (["GGUF_CONVERTER_MISSING", "GGUF_CONVERTER_NOT_READY"].includes(job.export_blocked_code)) {
      setConverterOpen(true);
    }
    return true;
  };

  const handleExportToOllama = (job, reQuantize = false) => {
    if (exportBlocked(job)) return;
    setSelectedJobForExport(job);
    setIsReQuantize(reQuantize);
    setExportModalOpen(true);
  };

  // A run held as worse than its base model is exported only on the person's
  // explicit choice; the backend records it, then the usual export dialog opens.
  const handleExportAnyway = async (job) => {
    if (exportBlocked(job)) return;
    const ok = window.confirm(t("training.jobs.exportAnywayConfirm", { reason: job.error_message || "" }));
    if (!ok) return;
    try {
      const released = await exportTrainingJobAnyway(job.id, { by: "person", via: "training_page" });
      fetchJobs();
      handleExportToOllama(released, false);
    } catch (err) {
      showMessage(t("training.jobs.releaseFailed", { error: err.message }), "error");
    }
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
        result.message || t("training.jobs.exportStarted", { model: modelName, quantization: quantization.toUpperCase() }),
        "info"
      );
      setExportModalOpen(false);
      fetchJobs(); // Refresh to show updated status
    } catch (err) {
      showMessage(t("training.jobs.exportFailed", { error: err.message }), "error");
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
      showMessage(t("training.jobs.created"), "success");
      setJobModalOpen(false);
      fetchJobs();
    } catch (err) {
      showMessage(t("training.jobs.createFailed", { error: err.message }), "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleOpenDeviceModal = (profile = null) => {
    setCurrentItem(profile);
    setDevicePrefill(null);
    setDeviceModalOpen(true);
  };

  // Opens the Add form filled from /api/training/hardware; the person reviews
  // it and saves, nothing is created here.
  const handleCreateFromThisMachine = async () => {
    setDetectingHardware(true);
    try {
      const hw = await getHardwareCapabilities();
      setCurrentItem(null);
      setDevicePrefill({
        values: profileFromHardware(hw, deviceProfiles.map((p) => p.name)),
        note: hardwareNote(hw),
      });
      setDeviceModalOpen(true);
    } catch (err) {
      showMessage(t("training.devices.hardwareFailed", { error: err.message }), "error");
    } finally {
      setDetectingHardware(false);
    }
  };

  const handleCloseDeviceModal = () => {
    if (isSaving) return;
    setCurrentItem(null);
    setDevicePrefill(null);
    setDeviceModalOpen(false);
  };

  const handleSaveDeviceProfile = async (profileData) => {
    setIsSaving(true);
    try {
      if (currentItem) {
        await updateDeviceProfile(currentItem.id, profileData);
        showMessage(t("training.devices.updated"), "success");
      } else {
        await createDeviceProfile(profileData);
        showMessage(t("training.devices.created"), "success");
      }
      handleCloseDeviceModal();
      fetchDeviceProfiles();
    } catch (err) {
      showMessage(t("training.devices.saveFailed", { error: err.message }), "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteDeviceProfile = async (profileId) => {
    if (!window.confirm(t("training.devices.deleteConfirm"))) return;
    try {
      await deleteDeviceProfile(profileId);
      showMessage(t("training.devices.deleted"), "success");
      fetchDeviceProfiles();
    } catch (err) {
      showMessage(t("training.devices.deleteFailed", { error: err.message }), "error");
    }
  };

  const rowMenu = useContextMenu();

  // A fine-tune created without starting: pending and never queued.
  const canStart = (job) =>
    job.status === "pending" && !job.celery_task_id && Boolean(job.base_model) &&
    (!job.pipeline_stage || job.pipeline_stage === "pending");
  const canResume = (job) => job.is_resumable && (job.status === "failed" || job.status === "cancelled");

  const menuActions = (payload) => {
    if (!payload) return [];
    const { kind, item } = payload;
    if (kind === "dataset") {
      return [
        { label: t("training.common.edit"), icon: <EditIcon fontSize="small" />, onClick: () => handleOpenEditModal(item) },
        { label: t("training.common.delete"), icon: <CloseIcon fontSize="small" />, onClick: () => handleDeleteDataset(item), color: "error.main", dividerBefore: true },
      ];
    }
    if (kind === "profile") {
      return [
        { label: t("training.common.edit"), icon: <EditIcon fontSize="small" />, onClick: () => handleOpenDeviceModal(item) },
        { label: t("training.common.delete"), icon: <CloseIcon fontSize="small" />, onClick: () => handleDeleteDeviceProfile(item.id), color: "error.main", dividerBefore: true },
      ];
    }
    if (kind === "job") {
      const job = item;
      const exporting = exportingJobs.has(job.id);
      return [
        canStart(job) && { label: t("training.jobs.start"), icon: <PlayArrowIcon fontSize="small" />, onClick: () => handleStartJob(job.id) },
        job.status === "running" && { label: t("training.common.cancel"), icon: <CancelIcon fontSize="small" />, onClick: () => handleCancelJob(job.id) },
        canResume(job) && { label: t("training.jobs.resume"), icon: <PlayArrowIcon fontSize="small" />, onClick: () => handleResumeJob(job.id) },
        job.export_verified && job.status === "completed" && job.lora_path && !job.ollama_model_name && {
          label: t("training.jobs.exportToOllama"), icon: <CloudUploadIcon fontSize="small" />, onClick: () => handleExportToOllama(job, false), disabled: exporting,
        },
        job.export_verified && job.status === "completed" && job.lora_path && job.ollama_model_name && {
          label: t("training.jobs.requantize"), icon: <RefreshIcon fontSize="small" />, onClick: () => handleExportToOllama(job, true), disabled: exporting,
        },
        job.export_verified && job.status === "failed: worse than base" && job.lora_path && {
          label: t("training.jobs.exportAnyway"), icon: <CloudUploadIcon fontSize="small" />, onClick: () => handleExportAnyway(job), disabled: exporting,
        },
        isDatasetJob(job) && job.status === "completed" && job.config?.pairs_count > 0 && {
          label: t("training.jobs.addAsDataset"), icon: <AddIcon fontSize="small" />, onClick: () => handleAddParsedDataset(job),
        },
        { label: t("training.common.delete"), icon: <CloseIcon fontSize="small" />, onClick: () => handleDeleteJob(job.id), color: "error.main", dividerBefore: true },
      ];
    }
    return [];
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
      // Held by the export gate (training_tasks.WORSE_THAN_BASE).
      case "failed: worse than base": return "error";
      case "cancelled": return "default";
      default: return "warning";
    }
  };

  return (
    <PageLayout
      title={t("training.page.title")}
      variant="standard"
      modelStatus
      activeModel={isLoadingModel ? t("training.page.modelLoading") : modelError ? t("training.page.modelError") : activeModel || t("training.page.modelDefault")}
    >
      <Alert severity="info" sx={{ mb: 2 }}>
        <Trans
          i18nKey="training.page.intro"
          components={{
            bold: <strong />,
            castLink: <Button component={RouterLink} to="/cast" size="small" variant="outlined" sx={{ ml: 0.5, verticalAlign: "baseline" }} />,
          }}
        />
      </Alert>
      <Paper elevation={2}>
        <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
          <Tabs value={activeTab} onChange={(e, v) => setActiveTab(v)} variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile>
            <Tab icon={<SchoolIcon />} iconPosition="start" label={t("training.tabs.demonstrations")} />
            <Tab icon={<StorageIcon />} iconPosition="start" label={t("training.tabs.datasets")} />
            <Tab icon={<WorkIcon />} iconPosition="start" label={t("training.tabs.jobs")} />
            <Tab icon={<ComputerIcon />} iconPosition="start" label={t("training.tabs.devices")} />
          </Tabs>
        </Box>

        {/* Demonstrations Tab */}
        {activeTab === 0 && (
          <Box sx={{ p: 2 }}>
            <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
              <Typography variant="h6">
                {t("training.demos.title")}
                {demonstrations.length > 0 && (
                  <Chip label={demonstrations.length} size="small" sx={{ ml: 1, height: 20, fontSize: "0.75rem" }} />
                )}
              </Typography>
              <Tooltip title={t("training.common.refresh")}>
                <span>
                  <IconButton onClick={fetchDemonstrations} disabled={demosLoading}>
                    <RefreshIcon />
                  </IconButton>
                </span>
              </Tooltip>
            </Box>
            {demosLoading ? (
              <Box display="flex" justifyContent="center" my={3}>
                <CircularProgress />
              </Box>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ width: 40 }} />
                      <TableCell>{t("training.common.name")}</TableCell>
                      <TableCell align="center">{t("training.demos.steps")}</TableCell>
                      <TableCell>{t("training.demos.autonomy")}</TableCell>
                      <TableCell align="center">{t("training.demos.attempts")}</TableCell>
                      <TableCell>{t("training.demos.created")}</TableCell>
                      <TableCell align="right">{t("training.common.actions")}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {demonstrations.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={7} align="center" sx={{ py: 4 }}>
                          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                            {t("training.demos.empty")}
                          </Typography>
                          <Button
                            variant="contained"
                            size="small"
                            startIcon={<SchoolIcon />}
                            onClick={() => setTrainerOpen(true)}
                          >
                            {t("training.demos.openTrainer")}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ) : (
                      demonstrations.map((demo) => (
                        <DemoRow
                          key={demo.id}
                          demo={demo}
                          expanded={expandedDemoId === demo.id}
                          onToggle={() => setExpandedDemoId(expandedDemoId === demo.id ? null : demo.id)}
                          onDelete={handleDeleteDemo}
                          onAttempt={handleAttemptDemo}
                          draft={stepDrafts[demo.id] || null}
                          updateDraft={updateStepDraft}
                          onSaved={handleDemoSaved}
                          showMessage={showMessage}
                          theme={theme}
                        />
                      ))
                    )}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Box>
        )}

        {/* Datasets Tab */}
        {activeTab === 1 && (
          <Box sx={{ p: 2 }}>
            <Box
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              mb={2}
            >
              <Typography variant="h6">{t("training.datasets.title")}</Typography>
              <Box display="flex" gap={1}>
                <Button
                  variant="outlined"
                  startIcon={<PlayArrowIcon />}
                  onClick={() => setParseModalOpen(true)}
                >
                  {t("training.datasets.parseTranscripts")}
                </Button>
                <Button
                  variant="outlined"
                  startIcon={<PlayArrowIcon />}
                  onClick={() => setBuildModalOpen(true)}
                >
                  {t("training.datasets.buildFromFolder")}
                </Button>
                <Button
                  variant="contained"
                  startIcon={<AddIcon />}
                  onClick={() => handleOpenEditModal(null)}
                >
                  {t("training.datasets.add")}
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
                      <TableCell>{t("training.common.name")}</TableCell>
                      <TableCell>{t("training.datasets.description")}</TableCell>
                      <TableCell>{t("training.datasets.path")}</TableCell>
                      <TableCell align="right">{t("training.common.actions")}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {datasets.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={4} align="center" sx={{ py: 4 }}>
                          <Typography variant="body2" color="text.secondary">
                            {t("training.datasets.empty")}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ) : (
                      datasets.map((ds) => (
                        <TableRow key={ds.id} hover onContextMenu={(e) => rowMenu.open(e, { kind: "dataset", item: ds })}>
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
                              <Tooltip title={t("training.datasets.editRename")}>
                                <IconButton
                                  size="small"
                                  onClick={() => handleOpenEditModal(ds)}
                                  color="primary"
                                >
                                  <EditIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                              <Tooltip title={t("training.datasets.deleteTooltip")}>
                                <IconButton
                                  size="small"
                                  onClick={() => handleDeleteDataset(ds)}
                                  color="error"
                                >
                                  <CloseIcon fontSize="small" />
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
        {activeTab === 2 && (
          <Box sx={{ p: 2 }}>
            <Box
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              mb={2}
            >
              <Typography variant="h6">{t("training.jobs.title")}</Typography>
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={() => setJobModalOpen(true)}
              >
                {t("training.jobs.new")}
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
                    <Card key={job.id} sx={{ mb: 2 }} onContextMenu={(e) => rowMenu.open(e, { kind: "job", item: job })}>
                      <CardContent>
                        <Box display="flex" justifyContent="space-between" alignItems="start" mb={1} flexWrap="wrap" gap={1}>
                          <Box sx={{ minWidth: 0, flex: "1 1 220px" }}>
                            <Typography variant="h6">{job.name || job.job_id}</Typography>
                            <Typography variant="caption" color="text.secondary">
                              {job.base_model
                                ? `${job.base_model} → ${job.output_model_name || t("training.common.notAvailable")}`
                                : job.config?.input_path || ""}
                            </Typography>
                          </Box>
                          <Box display="flex" gap={1} alignItems="center" flexWrap="wrap">
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
                            {/* Export is offered only once it has been verified for the job's base model. */}
                            {job.export_verified && job.status === "completed" && job.lora_path && !job.ollama_model_name && (
                              <Tooltip title={t("training.jobs.exportToOllama")}>
                                <span>
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
                                </span>
                              </Tooltip>
                            )}
                            {/* Re-quantize button - show for completed exports */}
                            {job.export_verified && job.status === "completed" && job.lora_path && job.ollama_model_name && (
                              <Tooltip title={t("training.jobs.requantizeTooltip")}>
                                <span>
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
                                </span>
                              </Tooltip>
                            )}
                            {job.export_verified && job.status === "failed: worse than base" && job.lora_path && (
                              <Tooltip title={t("training.jobs.exportAnywayTooltip")}>
                                <span>
                                  <IconButton
                                    size="small"
                                    color="warning"
                                    onClick={() => handleExportAnyway(job)}
                                    disabled={exportingJobs.has(job.id)}
                                  >
                                    <CloudUploadIcon fontSize="small" />
                                  </IconButton>
                                </span>
                              </Tooltip>
                            )}
                            {canStart(job) && (
                              <Tooltip title={t("training.jobs.startTooltip")}>
                                <IconButton size="small" color="primary" onClick={() => handleStartJob(job.id)}>
                                  <PlayArrowIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                            {job.status === "running" && (
                              <Tooltip title={t("training.common.cancel")}>
                                <IconButton size="small" onClick={() => handleCancelJob(job.id)}>
                                  <CancelIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                            {canResume(job) && (
                              <Tooltip title={t("training.jobs.resume")}>
                                <IconButton size="small" color="success" onClick={() => handleResumeJob(job.id)}>
                                  <PlayArrowIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            )}
                            <Tooltip title={t("training.common.delete")}>
                              <IconButton size="small" onClick={() => handleDeleteJob(job.id)}>
                                <CloseIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          </Box>
                        </Box>
                        {job.status === "running" && (
                          <Box sx={{ mt: 1 }}>
                            <LinearProgress variant="determinate" value={progress} />
                            <Typography variant="caption" color="text.secondary">
                              {t("training.jobs.progress", { progress, stage: job.pipeline_stage || t("training.jobs.processing") })}
                            </Typography>
                          </Box>
                        )}
                        {isDatasetJob(job) && job.status === "completed" && job.config?.output_path && (
                          <Box sx={{ mt: 1, display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                            <Typography variant="body2">
                              {job.pipeline_stage === "building"
                                ? job.config.files != null
                                  ? t("training.jobs.builtFrom", {
                                    built: t("training.jobs.builtPairs", { count: job.config.pairs_count ?? 0 }),
                                    files: t("training.jobs.fileCount", { count: job.config.files }),
                                    scrubbed: t("training.jobs.itemCount", { count: job.config.scrubbed ?? 0 }),
                                  })
                                  : t("training.jobs.builtPairs", { count: job.config.pairs_count ?? 0 })
                                : t("training.jobs.parsedPairs", { count: job.config.pairs_count ?? 0 })}
                            </Typography>
                            {job.config.pairs_count > 0 && (
                              <Button size="small" variant="outlined" startIcon={<AddIcon />} onClick={() => handleAddParsedDataset(job)}>
                                {t("training.jobs.addAsDataset")}
                              </Button>
                            )}
                          </Box>
                        )}
                        {job.error_message && (
                          <Typography variant="caption" color="error" sx={{ mt: 1, display: "block" }}>
                            {t("training.jobs.error", { error: job.error_message })}
                          </Typography>
                        )}
                        {job.config?.export_override && (
                          <Typography variant="caption" color="warning.main" sx={{ mt: 1, display: "block" }}>
                            {t("training.jobs.exportOverride", {
                              by: job.config.export_override.by,
                              via: job.config.export_override.via,
                              at: job.config.export_override.at,
                              note: job.config.export_override.gate_note,
                            })}
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
                    {t("training.jobs.empty")}
                  </Typography>
                )}
              </Box>
            )}
          </Box>
        )}

        {/* Devices Tab */}
        {activeTab === 3 && (
          <Box sx={{ p: 2 }}>
            <Box
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              mb={2}
            >
              <Typography variant="h6">{t("training.devices.title")}</Typography>
              <Box display="flex" gap={1}>
                <Button
                  variant="outlined"
                  startIcon={detectingHardware ? <CircularProgress size={16} /> : <ComputerIcon />}
                  onClick={handleCreateFromThisMachine}
                  disabled={detectingHardware}
                >
                  {t("training.devices.fromThisMachine")}
                </Button>
                <Button
                  variant="contained"
                  startIcon={<AddIcon />}
                  onClick={() => handleOpenDeviceModal(null)}
                >
                  {t("training.devices.add")}
                </Button>
              </Box>
            </Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {t("training.devices.help")}
            </Typography>
            {profilesLoading ? (
              <Box display="flex" justifyContent="center" my={3}>
                <CircularProgress />
              </Box>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>{t("training.common.name")}</TableCell>
                      <TableCell>{t("training.devices.type")}</TableCell>
                      <TableCell>VRAM</TableCell>
                      <TableCell>{t("training.devices.batchSize")}</TableCell>
                      <TableCell>{t("training.devices.seqLength")}</TableCell>
                      <TableCell>{t("training.devices.default")}</TableCell>
                      <TableCell align="right">{t("training.common.actions")}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {deviceProfiles.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={7} align="center" sx={{ py: 4 }}>
                          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                            {t("training.devices.empty")}
                          </Typography>
                          <Button
                            variant="contained"
                            size="small"
                            startIcon={detectingHardware ? <CircularProgress size={14} color="inherit" /> : <ComputerIcon />}
                            onClick={handleCreateFromThisMachine}
                            disabled={detectingHardware}
                          >
                            {t("training.devices.fromThisMachine")}
                          </Button>
                        </TableCell>
                      </TableRow>
                    )}
                    {deviceProfiles.map((profile) => (
                      <TableRow key={profile.id} hover onContextMenu={(e) => rowMenu.open(e, { kind: "profile", item: profile })}>
                        <TableCell>{profile.name}</TableCell>
                        <TableCell>{profile.device_type || "-"}</TableCell>
                        <TableCell>{profile.gpu_vram_mb ? `${(profile.gpu_vram_mb / 1024).toFixed(1)} GB` : t("training.common.notAvailable")}</TableCell>
                        <TableCell>{profile.max_batch_size}</TableCell>
                        <TableCell>{profile.max_seq_length}</TableCell>
                        <TableCell>{profile.is_default ? <Chip label={t("training.devices.default")} size="small" color="primary" /> : "-"}</TableCell>
                        <TableCell align="right">
                          <Tooltip title={t("training.common.edit")}>
                            <span>
                              <IconButton
                                size="small"
                                onClick={() => handleOpenDeviceModal(profile)}
                              >
                                <EditIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Tooltip title={t("training.common.delete")}>
                            <span>
                              <IconButton
                                size="small"
                                onClick={() => handleDeleteDeviceProfile(profile.id)}
                                sx={{ ml: 1 }}
                              >
                                <CloseIcon fontSize="small" />
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
          prefill={datasetPrefill}
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
          prefill={devicePrefill?.values}
          prefillNote={devicePrefill?.note}
          onSave={handleSaveDeviceProfile}
          isSaving={isSaving}
        />
      )}

      {parseModalOpen && (
        <ParseJobModal
          open={parseModalOpen}
          onClose={() => setParseModalOpen(false)}
          onSuccess={(_result) => {
            showMessage(t("training.datasets.parseStarted"), "success");
            fetchJobs();
          }}
        />
      )}

      {buildModalOpen && (
        <BuildFromFolderModal
          open={buildModalOpen}
          onClose={() => setBuildModalOpen(false)}
          onStarted={(_job, indexNote) => {
            showMessage(t("training.datasets.buildStarted", { note: indexNote || "" }), "success");
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
          levels={selectedJobForExport?.export_levels}
        />
      )}

      <TrainingLibrariesModal
        open={converterOpen}
        onClose={() => setConverterOpen(false)}
        showMessage={showMessage}
        onConverterChanged={() => fetchJobs()}
        focus="gguf-converter"
      />

      <EntityContextMenu
        anchorPosition={rowMenu.anchorPosition}
        onClose={rowMenu.close}
        actions={menuActions(rowMenu.payload)}
      />

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
