import React, { useState, useEffect, useRef } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Grid,
  Alert,
  InputAdornment,
  IconButton,
  Box,
  Chip,
  LinearProgress,
  Typography,
} from "@mui/material";
import FolderOpenIcon from "@mui/icons-material/FolderOpen";
import DirectoryPicker from "../common/DirectoryPicker";
import {
  getTrainingDatasetLocations,
  inspectTrainingDataset,
} from "../../api/trainingService";
import { translate, useUiTranslation } from "../../i18n";

const DATASET_EXTENSIONS = [".jsonl", ".json"];
// Typing pauses this long before the path is read.
const INSPECT_DELAY_MS = 300;

// "<count> <format>" for each format the inspector reports.
const formatCount = (fmt, count) => {
  switch (fmt) {
    case "messages":
      return translate("dialogs.trainingDataset.formats.messages", { count });
    case "sharegpt":
      return translate("dialogs.trainingDataset.formats.sharegpt", { count });
    case "prompt_completion":
      return translate("dialogs.trainingDataset.formats.promptCompletion", { count });
    case "alpaca":
      return translate("dialogs.trainingDataset.formats.alpaca", { count });
    case "text":
      return translate("dialogs.trainingDataset.formats.text", { count });
    default:
      return `${count} ${fmt}`;
  }
};

const baseName = (path) =>
  path.replace(/\/+$/, "").split("/").pop().replace(/\.(jsonl|json)$/i, "");

const InspectPanel = ({ report, inspecting }) => {
  const { t } = useUiTranslation();
  if (inspecting) {
    return (
      <Box sx={{ mt: 1 }}>
        <LinearProgress />
        <Typography variant="caption" color="text.secondary">
          {t("dialogs.trainingDataset.reading")}
        </Typography>
      </Box>
    );
  }
  if (!report) return null;
  if (!report.trainable) {
    return (
      <Alert severity="error" sx={{ mt: 1 }}>
        {report.reason ? t("dialogs.trainingDataset.notTrainableReason", { reason: report.reason }) : t("dialogs.trainingDataset.notTrainable")}
      </Alert>
    );
  }
  const formats = Object.entries(report.formats || {})
    .map(([fmt, n]) => formatCount(fmt, n))
    .join(", ");
  const fileCount = report.files?.length || 0;
  return (
    <Box sx={{ mt: 1 }} data-testid="dataset-inspect">
      <Alert severity="success">
        {t("dialogs.trainingDataset.usable", { usable: report.usable, rows: report.rows, formats, count: fileCount })}
      </Alert>
      {report.error_count > 0 && (
        <Alert severity="warning" sx={{ mt: 1 }}>
          {t("dialogs.trainingDataset.skipped", { count: report.error_count })}:
          <Box component="ul" sx={{ m: 0, pl: 2 }}>
            {report.errors.map((e) => (
              <li key={e}>
                <Typography variant="caption">{e}</Typography>
              </li>
            ))}
          </Box>
        </Alert>
      )}
      {report.samples?.length > 0 && (
        <Box sx={{ mt: 1 }}>
          <Typography variant="caption" color="text.secondary">
            {t("dialogs.trainingDataset.firstRows")}
          </Typography>
          {report.samples.map((sample, i) => (
            <Box
              key={i}
              sx={{ mt: 0.5, p: 1, borderRadius: 1, bgcolor: "action.hover", fontSize: "0.75rem" }}
            >
              {sample.text !== undefined ? (
                <Typography variant="caption" sx={{ whiteSpace: "pre-wrap" }}>
                  {sample.text}
                </Typography>
              ) : (
                sample.messages.map((m, j) => (
                  <Box key={j} sx={{ display: "flex", gap: 1, alignItems: "baseline" }}>
                    <Chip label={m.role} size="small" variant="outlined" sx={{ height: 18, fontSize: "0.65rem" }} />
                    <Typography variant="caption" sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {m.content}
                    </Typography>
                  </Box>
                ))
              )}
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
};

/**
 * Add or edit a training dataset: a .jsonl or .json file, or a folder of them,
 * on this machine. The path is read as the trainer reads it, and Save waits
 * until it can be trained on.
 *
 * @param {object}  [datasetData]  the dataset being edited
 * @param {object}  [prefill]      starting values for a new dataset ({ name, path })
 */
const TrainingDatasetModal = ({
  open,
  onClose,
  datasetData,
  prefill,
  onSave,
  isSaving,
}) => {
  const { t } = useUiTranslation();
  const [formData, setFormData] = useState({
    name: "",
    description: "",
    path: "",
  });
  const [formError, setFormError] = useState(null);
  const [directoryPickerOpen, setDirectoryPickerOpen] = useState(false);
  const [startPath, setStartPath] = useState("~");
  const [report, setReport] = useState(null);
  const [inspecting, setInspecting] = useState(false);
  const inspectId = useRef(0);

  useEffect(() => {
    if (open) {
      const source = datasetData || prefill || {};
      setFormData({
        name: source.name || "",
        description: source.description || "",
        path: source.path || "",
      });
      setFormError(null);
      setReport(null);
      getTrainingDatasetLocations()
        .then((locations) => setStartPath(locations?.default || "~"))
        .catch(() => setStartPath("~"));
    }
  }, [open, datasetData, prefill]);

  useEffect(() => {
    if (!open) return undefined;
    const path = formData.path.trim();
    const id = ++inspectId.current;
    if (!path) {
      setReport(null);
      setInspecting(false);
      return undefined;
    }
    setInspecting(true);
    const timer = setTimeout(() => {
      inspectTrainingDataset(path)
        .then((result) => {
          if (id === inspectId.current) setReport(result);
        })
        .catch((err) => {
          if (id === inspectId.current) {
            setReport({ trainable: false, reason: err.message || translate("dialogs.trainingDataset.unreadable") });
          }
        })
        .finally(() => {
          if (id === inspectId.current) setInspecting(false);
        });
    }, INSPECT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [open, formData.path]);

  const handleInputChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  // An existing dataset may keep the path it was saved with while it is
  // renamed; the server checks a changed path the same way.
  const pathUnchanged = Boolean(datasetData) && formData.path === (datasetData.path || "");
  const pathReady = pathUnchanged || (!inspecting && Boolean(report?.trainable));
  const canSave = !isSaving && Boolean(formData.name.trim()) && pathReady;

  const handleSave = () => {
    if (!formData.name.trim()) {
      setFormError(t("dialogs.trainingDataset.nameRequired"));
      return;
    }
    if (!pathReady) {
      setFormError(t("dialogs.trainingDataset.pathNotReady"));
      return;
    }
    setFormError(null);
    onSave({
      name: formData.name.trim(),
      description: formData.description,
      path: report?.trainable && report.path ? report.path : formData.path.trim(),
    });
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        {datasetData ? t("dialogs.trainingDataset.editTitle", { name: datasetData.name }) : t("dialogs.trainingDataset.addTitle")}
      </DialogTitle>
      <DialogContent dividers>
        {formError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formError}
          </Alert>
        )}
        <Grid container spacing={2} sx={{ mt: 0 }}>
          <Grid item xs={12}>
            <TextField
              autoFocus
              required
              fullWidth
              margin="dense"
              label={t("dialogs.trainingDataset.nameLabel")}
              name="name"
              value={formData.name}
              onChange={handleInputChange}
              disabled={isSaving}
            />
          </Grid>
          <Grid item xs={12}>
            <TextField
              fullWidth
              multiline
              minRows={2}
              margin="dense"
              label={t("dialogs.trainingDataset.description")}
              name="description"
              value={formData.description}
              onChange={handleInputChange}
              disabled={isSaving}
            />
          </Grid>
          <Grid item xs={12}>
            <TextField
              fullWidth
              required
              margin="dense"
              label={t("dialogs.trainingDataset.pathLabel")}
              name="path"
              value={formData.path}
              onChange={handleInputChange}
              disabled={isSaving}
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      onClick={() => setDirectoryPickerOpen(true)}
                      edge="end"
                      disabled={isSaving}
                      title={t("dialogs.trainingDataset.browse")}
                      aria-label={t("dialogs.trainingDataset.browse")}
                    >
                      <FolderOpenIcon />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
              helperText={t("dialogs.trainingDataset.pathHelp")}
            />
            <InspectPanel report={report} inspecting={inspecting} />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSaving}>
          {t("dialogs.shared.cancel")}
        </Button>
        <Button onClick={handleSave} variant="contained" disabled={!canSave}>
          {t("dialogs.shared.save")}
        </Button>
      </DialogActions>

      <DirectoryPicker
        open={directoryPickerOpen}
        onClose={() => setDirectoryPickerOpen(false)}
        onSelect={(selectedPath) => {
          setFormData((prev) => ({
            ...prev,
            path: selectedPath,
            name: prev.name.trim() ? prev.name : baseName(selectedPath),
          }));
          setDirectoryPickerOpen(false);
        }}
        initialPath={formData.path.trim() || startPath}
        fallbackPath={startPath}
        purpose="training"
        mode="fileOrFolder"
        fileExtensions={DATASET_EXTENSIONS}
        title={t("dialogs.trainingDataset.pickerTitle")}
      />
    </Dialog>
  );
};

export default TrainingDatasetModal;
