// frontend/src/pages/BulkImportDocumentsPage.jsx
// Bulk Import UI for large document ingestion - Improved UX

import React, { useEffect, useMemo, useState } from "react";
import { BASE_URL } from "../api/apiClient";
import {
  Alert,
  AlertTitle,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  Grid,
  IconButton,
  LinearProgress,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import FolderIcon from "@mui/icons-material/Folder";
import FolderOpenIcon from "@mui/icons-material/FolderOpen";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import { useSnackbar } from "../components/common/SnackbarProvider";
import PageLayout from "../components/layout/PageLayout";
import {
  startBulkImport,
  getBulkImportStatus,
} from "../api/bulkImportService";
import { getClients } from "../api/clientService";
import { getProjects } from "../api/projectService";
import { getWebsites } from "../api/websiteService";

const POLL_INTERVAL_MS = 3000;

const initialForm = {
  sourcePath: "",
  targetFolder: "",
  projectId: null,
  clientId: null,
  websiteId: null,
  reindexMissing: true,
  forceCopy: false,
  dryRun: false,
};

const BulkImportDocumentsPage = () => {
  const navigate = useNavigate();
  const { showMessage } = useSnackbar();

  const [form, setForm] = useState(initialForm);
  const [submitting, setSubmitting] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [polling, setPolling] = useState(false);
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false);

  // Dropdown data
  const [clients, setClients] = useState([]);
  const [projects, setProjects] = useState([]);
  const [websites, setWebsites] = useState([]);
  const [loadingEntities, setLoadingEntities] = useState(true);

  // File browser
  const [browserOpen, setBrowserOpen] = useState(false);
  const [currentPath, setCurrentPath] = useState("/");
  const [directories, setDirectories] = useState([]);
  const [loadingDirs, setLoadingDirs] = useState(false);

  // Load entities on mount
  useEffect(() => {
    const loadEntities = async () => {
      setLoadingEntities(true);
      try {
        const [clientsData, projectsData, websitesData] = await Promise.all([
          getClients(),
          getProjects(),
          getWebsites(),
        ]);

        setClients(Array.isArray(clientsData) ? clientsData : []);
        setProjects(Array.isArray(projectsData) ? projectsData : []);
        setWebsites(Array.isArray(websitesData) ? websitesData : []);
      } catch (error) {
        console.error("Error loading entities:", error);
        showMessage("Failed to load clients/projects/websites", "error");
      } finally {
        setLoadingEntities(false);
      }
    };
    loadEntities();
  }, [showMessage]);

  // Load directories when browser opens
  useEffect(() => {
    if (browserOpen) {
      loadDirectories(currentPath);
    }
  }, [browserOpen, currentPath]);

  const loadDirectories = async (path) => {
    setLoadingDirs(true);
    try {
      const response = await fetch(
        `${BASE_URL}/files/browse-server?path=${encodeURIComponent(path)}`
      );
      const data = await response.json();
      if (response.ok && data.directories) {
        setDirectories(data.directories);
      } else {
        showMessage(data.error || "Failed to load directories", "error");
        setDirectories([]);
      }
    } catch (error) {
      console.error("Error loading directories:", error);
      showMessage("Failed to load directories", "error");
      setDirectories([]);
    } finally {
      setLoadingDirs(false);
    }
  };

  const handleBrowserNavigate = (path) => {
    setCurrentPath(path);
  };

  const handleBrowserSelect = () => {
    setForm((prev) => ({ ...prev, sourcePath: currentPath }));
    setBrowserOpen(false);
  };

  const canSubmit = useMemo(
    () => form.sourcePath.trim().length > 0 && !submitting,
    [form.sourcePath, submitting]
  );

  const handleChange = (field) => (event) => {
    const value = event.target.value;
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleAutocompleteChange = (field) => (event, newValue) => {
    setForm((prev) => ({ ...prev, [field]: newValue }));
  };

  const handleCheckbox = (field) => (event) => {
    setForm((prev) => ({ ...prev, [field]: event.target.checked }));
  };

  const resetForm = () => {
    setForm(initialForm);
    setJobId(null);
    setJobStatus(null);
  };

  const handleStartClick = () => {
    if (!canSubmit) return;
    if (!form.dryRun) {
      setConfirmDialogOpen(true);
    } else {
      submitImport();
    }
  };

  const submitImport = async () => {
    if (!canSubmit) return;
    setConfirmDialogOpen(false);
    setSubmitting(true);
    try {
      const payload = {
        source_path: form.sourcePath.trim(),
        target_folder: form.targetFolder.trim() || null,
        project_id: form.projectId?.id || null,
        client_id: form.clientId?.id || null,
        website_id: form.websiteId?.id || null,
        reindex_missing: !!form.reindexMissing,
        force_copy: !!form.forceCopy,
        dry_run: !!form.dryRun,
      };

      const response = await startBulkImport(payload);
      if (response?.job_id) {
        setJobId(response.job_id);
        setJobStatus({
          status: response.status || "queued",
          message: response.message || "Import started.",
        });
        showMessage("Bulk import started successfully", "success");
        setPolling(true);
      } else {
        showMessage(response?.message || "Bulk import response received.", "info");
      }
    } catch (error) {
      console.error("Submit import error:", error);
      showMessage(error.message || "Failed to start bulk import", "error");
    } finally {
      setSubmitting(false);
    }
  };

  // Poll job status
  useEffect(() => {
    if (!jobId || !polling) return undefined;
    const interval = setInterval(async () => {
      try {
        const statusResp = await getBulkImportStatus(jobId);
        setJobStatus(statusResp);
        if (
          statusResp?.status &&
          ["completed", "error", "failed", "cancelled"].includes(
            statusResp.status.toLowerCase()
          )
        ) {
          setPolling(false);
        }
      } catch (err) {
        setPolling(false);
        showMessage(err.message || "Failed to fetch status", "error");
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [jobId, polling, showMessage]);

  const renderStatus = () => {
    if (!jobId) {
      return (
        <Alert severity="info" icon={<InfoOutlinedIcon />}>
          No import job running. Configure and start an import to see status here.
        </Alert>
      );
    }

    const severity = jobStatus?.status === "completed"
      ? "success"
      : jobStatus?.status === "error" || jobStatus?.status === "failed"
      ? "error"
      : "info";

    return (
      <Alert severity={severity}>
        <AlertTitle>Job Status</AlertTitle>
        <Typography variant="body2" component="div" sx={{ mt: 1 }}>
          <strong>Job ID:</strong> {jobId}
        </Typography>
        <Typography variant="body2" component="div">
          <strong>Status:</strong> {jobStatus?.status || "unknown"}
        </Typography>
        {jobStatus?.message && (
          <Typography variant="body2" component="div">
            <strong>Message:</strong> {jobStatus.message}
          </Typography>
        )}
        {typeof jobStatus?.progress === "number" && (
          <Box sx={{ mt: 1 }}>
            <Typography variant="body2" component="div" sx={{ mb: 0.5 }}>
              <strong>Progress:</strong> {jobStatus.progress}%
            </Typography>
            <LinearProgress variant="determinate" value={jobStatus.progress} />
          </Box>
        )}
        {jobStatus?.stats && (
          <Typography variant="body2" component="div" sx={{ mt: 1 }}>
            <strong>Results:</strong>
            <Box component="pre" sx={{ mt: 1, fontSize: "0.75rem", whiteSpace: "pre-wrap" }}>
              {JSON.stringify(jobStatus.stats, null, 2)}
            </Box>
          </Typography>
        )}
      </Alert>
    );
  };

  return (
    <PageLayout
      title="Bulk Import Documents"
      variant="standard"
      actions={
        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            startIcon={<RestartAltIcon />}
            onClick={resetForm}
            disabled={submitting}
          >
            Reset
          </Button>
          <Button
            variant="text"
            onClick={() => navigate("/documents")}
            disabled={submitting}
          >
            Back to Documents
          </Button>
        </Stack>
      }
    >
      <Typography variant="body1" color="text.secondary" sx={{ mb: 2 }}>
        Import documents from server directories into your file manager and index them for search.
      </Typography>

      <Paper sx={{ p: 3 }}>
        <Stack spacing={3}>
          <Typography variant="h6">Import Configuration</Typography>

          <Grid container spacing={2}>
            <Grid item xs={12}>
              <TextField
                label="Source Directory"
                value={form.sourcePath}
                onChange={handleChange("sourcePath")}
                fullWidth
                required
                placeholder="/path/to/documents"
                helperText="Path to the directory containing files to import"
                disabled={submitting}
                InputProps={{
                  endAdornment: (
                    <Button
                      size="small"
                      onClick={() => setBrowserOpen(true)}
                      disabled={submitting}
                    >
                      Browse
                    </Button>
                  ),
                }}
              />
            </Grid>

            <Grid item xs={12}>
              <TextField
                label="Target Folder (optional)"
                value={form.targetFolder}
                onChange={handleChange("targetFolder")}
                fullWidth
                placeholder="Imports"
                helperText="Folder name in Documents where files will be organized"
                disabled={submitting}
              />
            </Grid>

            <Grid item xs={12} sm={4}>
              <Autocomplete
                options={clients}
                getOptionLabel={(option) => option.name || ""}
                value={form.clientId}
                onChange={handleAutocompleteChange("clientId")}
                loading={loadingEntities}
                disabled={submitting}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Client (optional)"
                    helperText="Link documents to a client"
                  />
                )}
              />
            </Grid>

            <Grid item xs={12} sm={4}>
              <Autocomplete
                options={projects}
                getOptionLabel={(option) => option.name || ""}
                value={form.projectId}
                onChange={handleAutocompleteChange("projectId")}
                loading={loadingEntities}
                disabled={submitting}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Project (optional)"
                    helperText="Link documents to a project"
                  />
                )}
              />
            </Grid>

            <Grid item xs={12} sm={4}>
              <Autocomplete
                options={websites}
                getOptionLabel={(option) => option.url || ""}
                value={form.websiteId}
                onChange={handleAutocompleteChange("websiteId")}
                loading={loadingEntities}
                disabled={submitting}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Website (optional)"
                    helperText="Link documents to a website"
                  />
                )}
              />
            </Grid>
          </Grid>

          <Divider />

          <Stack spacing={1}>
            <Typography variant="subtitle1">Options</Typography>
            <FormControlLabel
              control={
                <Checkbox
                  checked={form.reindexMissing}
                  onChange={handleCheckbox("reindexMissing")}
                  disabled={submitting}
                />
              }
              label="Re-index existing documents"
            />
            <FormControlLabel
              control={
                <Checkbox
                  checked={form.forceCopy}
                  onChange={handleCheckbox("forceCopy")}
                  disabled={submitting}
                />
              }
              label="Force copy files (even if already imported)"
            />
            <FormControlLabel
              control={
                <Checkbox
                  checked={form.dryRun}
                  onChange={handleCheckbox("dryRun")}
                  disabled={submitting}
                />
              }
              label="Dry run (preview without importing)"
            />
          </Stack>

          <Stack direction="row" spacing={2} justifyContent="flex-end">
            <Button
              variant="contained"
              startIcon={<CloudUploadIcon />}
              onClick={handleStartClick}
              disabled={!canSubmit}
              size="large"
            >
              {submitting ? "Starting..." : form.dryRun ? "Preview Import" : "Start Import"}
            </Button>
          </Stack>
        </Stack>
      </Paper>

      {/* Directory Browser Dialog */}
      <Dialog
        open={browserOpen}
        onClose={() => setBrowserOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          <Stack direction="row" alignItems="center" spacing={1}>
            <FolderOpenIcon />
            <Typography variant="h6">Browse Server Directory</Typography>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2}>
            <TextField
              label="Current Path"
              value={currentPath}
              fullWidth
              InputProps={{
                readOnly: true,
                startAdornment: currentPath !== "/" && (
                  <IconButton
                    size="small"
                    onClick={() => {
                      const parentPath = currentPath.split("/").slice(0, -1).join("/") || "/";
                      handleBrowserNavigate(parentPath);
                    }}
                  >
                    <ArrowBackIcon />
                  </IconButton>
                ),
              }}
            />

            {loadingDirs ? (
              <Box sx={{ p: 2, textAlign: "center" }}>
                <Typography variant="body2" color="text.secondary">
                  Loading directories...
                </Typography>
              </Box>
            ) : directories.length === 0 ? (
              <Alert severity="info">
                No subdirectories found. You can select this directory.
              </Alert>
            ) : (
              <List sx={{ maxHeight: 300, overflow: "auto" }}>
                {directories.map((dir) => (
                  <ListItemButton
                    key={dir}
                    onClick={() => {
                      const newPath = currentPath === "/" ? `/${dir}` : `${currentPath}/${dir}`;
                      handleBrowserNavigate(newPath);
                    }}
                  >
                    <ListItemIcon>
                      <FolderIcon />
                    </ListItemIcon>
                    <ListItemText primary={dir} />
                  </ListItemButton>
                ))}
              </List>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setBrowserOpen(false)}>
            Cancel
          </Button>
          <Button onClick={handleBrowserSelect} variant="contained">
            Select This Directory
          </Button>
        </DialogActions>
      </Dialog>

      {/* Confirmation Dialog */}
      <Dialog open={confirmDialogOpen} onClose={() => setConfirmDialogOpen(false)}>
        <DialogTitle>Confirm Bulk Import</DialogTitle>
        <DialogContent>
          <Typography variant="body1" gutterBottom>
            Ready to import files:
          </Typography>
          <Box component="ul" sx={{ pl: 2, mt: 1 }}>
            <li>From: <strong>{form.sourcePath || "(not specified)"}</strong></li>
            <li>To: <strong>{form.targetFolder || "root"}</strong></li>
            {form.clientId && <li>Client: <strong>{form.clientId.name}</strong></li>}
            {form.projectId && <li>Project: <strong>{form.projectId.name}</strong></li>}
            {form.websiteId && <li>Website: <strong>{form.websiteId.url}</strong></li>}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDialogOpen(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submitImport} variant="contained" disabled={submitting}>
            Confirm Import
          </Button>
        </DialogActions>
      </Dialog>

      {/* Job Status */}
      <Paper sx={{ p: 3 }}>
        <Stack spacing={2}>
          <Stack direction="row" alignItems="center" justifyContent="space-between">
            <Typography variant="h6">Import Status</Typography>
            {polling && (
              <Stack direction="row" alignItems="center" spacing={1}>
                <InfoOutlinedIcon fontSize="small" color="action" />
                <Typography variant="body2" color="text.secondary">
                  Polling...
                </Typography>
              </Stack>
            )}
          </Stack>
          {renderStatus()}
        </Stack>
      </Paper>
    </PageLayout>
  );
};

export default BulkImportDocumentsPage;
