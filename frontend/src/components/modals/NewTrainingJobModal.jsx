import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Grid,
  Alert,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  FormControlLabel,
  Checkbox,
  Typography,
  Divider,
  RadioGroup,
  Radio,
  ListItemText,
} from "@mui/material";
import {
  getTrainingDatasets,
  getDeviceProfiles,
  getBaseModels,
  getImageFolders,
  getHardwareCapabilities,
  getTrainingLibraries,
} from "../../api/trainingService";
import TrainingLibrariesModal from "./TrainingLibrariesModal";
import { translate, useUiTranslation, Trans } from "../../i18n";

import ComputerIcon from "@mui/icons-material/Computer";

const emptyForm = () => ({
  name: "",
  task_type: "text", // "text" or "vision"
  base_model: "",
  dataset_id: "",
  images_path: "", // For vision tasks
  device_profile_id: "",
  output_model_name: "",
  config: {
    // Empty: the backend picks the steps from the dataset's size.
    steps: "",
    lr: 0.0002,
    batch_size: 2,
    rank: 16,
    seq_length: 2048,
  },
  start_immediately: false,
});

/** Keep batch size and sequence length inside what the model declares. */
const withinModel = (config, model) => {
  if (!model) return config;
  return {
    ...config,
    batch_size: Math.min(config.batch_size || 1, model.max_batch_size),
    seq_length: Math.min(config.seq_length || model.max_seq_length, model.max_seq_length),
  };
};

const loadLabels = () => [
  translate("dialogs.newTrainingJob.loadLabels.datasets"),
  translate("dialogs.newTrainingJob.loadLabels.deviceProfiles"),
  translate("dialogs.newTrainingJob.loadLabels.baseModels"),
  translate("dialogs.newTrainingJob.loadLabels.imageFolders"),
  translate("dialogs.newTrainingJob.loadLabels.hardware"),
];

const NewTrainingJobModal = ({
  open,
  onClose,
  onSave,
  isSaving,
}) => {
  const { t } = useUiTranslation();
  const [formData, setFormData] = useState(emptyForm);
  const [formError, setFormError] = useState(null);
  const [datasets, setDatasets] = useState([]);
  const [deviceProfiles, setDeviceProfiles] = useState([]);
  const [baseModels, setBaseModels] = useState([]);
  const [imageFolders, setImageFolders] = useState([]);
  const [hardwareCaps, setHardwareCaps] = useState(null);
  const [loading, setLoading] = useState(false);
  // What could not be read, so a list left empty says why.
  const [loadProblems, setLoadProblems] = useState([]);
  // Settings > Training libraries status; null until read. Jobs need them, so
  // when they are missing the form points there instead of creating a job
  // that would fail.
  const [libraries, setLibraries] = useState(null);
  const [librariesOpen, setLibrariesOpen] = useState(false);

  const checkLibraries = () => {
    getTrainingLibraries()
      .then(setLibraries)
      .catch(() => setLibraries(null));
  };

  const applyBaseModels = (status) => {
    const models = Array.isArray(status?.models) ? status.models : [];
    setBaseModels(models);
    setFormData((prev) => {
      const usable = (m) => m.fits && m.installed && m.trainable !== false;
      const current = models.find((m) => m.id === prev.base_model && usable(m));
      const chosen = current || models.find(usable);
      return { ...prev, base_model: chosen ? chosen.id : "", config: withinModel(prev.config, chosen) };
    });
  };

  const loadOptions = async () => {
    setLoading(true);
    const results = await Promise.allSettled([
      getTrainingDatasets(),
      getDeviceProfiles(),
      getBaseModels(),
      getImageFolders(),
      getHardwareCapabilities(),
    ]);
    const value = (i) => (results[i].status === "fulfilled" ? results[i].value : null);
    const labels = loadLabels();
    const problems = results
      .map((r, i) => {
        if (r.status === "rejected") return t("dialogs.newTrainingJob.loadProblem", { what: labels[i], reason: r.reason?.message || t("dialogs.newTrainingJob.loadFailed") });
        if (r.value && r.value.error) return t("dialogs.newTrainingJob.loadProblem", { what: labels[i], reason: r.value.error });
        return null;
      })
      .filter(Boolean);
    setLoadProblems(problems);
    setDatasets(Array.isArray(value(0)) ? value(0) : []);
    setDeviceProfiles(Array.isArray(value(1)) ? value(1) : []);
    setImageFolders(Array.isArray(value(3)) ? value(3) : []);
    const capsData = value(4);
    setHardwareCaps(capsData && !capsData.error ? capsData : null);
    if (capsData?.recommended_config) {
      setFormData((prev) => ({
        ...prev,
        config: {
          ...prev.config,
          batch_size: capsData.recommended_config.batch_size,
          seq_length: capsData.recommended_config.max_seq_length,
          rank: capsData.recommended_config.lora_rank,
        },
      }));
    }
    applyBaseModels(value(2));
    setLoading(false);
  };

  useEffect(() => {
    if (open) {
      setFormData(emptyForm());
      setFormError(null);
      loadOptions();
      checkLibraries();
    }
  }, [open]);

  const selectedModel = baseModels.find((m) => m.id === formData.base_model);
  const fittingModels = baseModels.filter((m) => m.fits);
  const notDownloaded = fittingModels.filter((m) => !m.installed && m.trainable !== false);
  // The Vision choice appears once a vision base model is declared.
  const visionDeclared = baseModels.some((m) => m.vision);

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    if (name.startsWith("config.")) {
      const configKey = name.split(".")[1];
      setFormData((prev) => ({
        ...prev,
        config: {
          ...prev.config,
          [configKey]:
            type === "number"
              ? value === ""
                ? ""
                : parseFloat(value) || 0
              : value,
        },
      }));
    } else if (name === "start_immediately") {
      setFormData((prev) => ({ ...prev, [name]: checked }));
    } else if (name === "base_model") {
      const model = baseModels.find((m) => m.id === value);
      setFormData((prev) => ({ ...prev, base_model: value, config: withinModel(prev.config, model) }));
    } else if (name === "dataset_id" || name === "device_profile_id") {
      // Keep as string for Select component, will convert on save
      setFormData((prev) => ({
        ...prev,
        [name]: value,
      }));
    } else {
      setFormData((prev) => ({
        ...prev,
        [name]: type === "number" ? parseInt(value) || "" : value,
      }));
    }
  };

  const handleDeviceProfileChange = (e) => {
    const profileId = e.target.value;
    setFormData((prev) => ({ ...prev, device_profile_id: profileId }));

    // Auto-fill config from device profile
    const profile = deviceProfiles.find((p) => p.id === parseInt(profileId));
    if (profile) {
      setFormData((prev) => ({
        ...prev,
        config: withinModel({
          ...prev.config,
          batch_size: profile.max_batch_size || prev.config.batch_size,
          seq_length: profile.max_seq_length || prev.config.seq_length,
        }, selectedModel),
      }));
    }
  };

  const handleSave = () => {
    if (!formData.name.trim()) {
      setFormError(t("dialogs.newTrainingJob.nameRequired"));
      return;
    }
    if (!formData.base_model || !selectedModel?.installed || selectedModel?.trainable === false) {
      setFormError(t("dialogs.newTrainingJob.modelRequired"));
      return;
    }
    if (!formData.dataset_id) {
      setFormError(t("dialogs.newTrainingJob.datasetRequired"));
      return;
    }
    if (formData.task_type === "vision" && !formData.images_path) {
        setFormError(t("dialogs.newTrainingJob.imagesRequired"));
        return;
    }

    setFormError(null);

    const { steps, ...config } = formData.config;
    const jobData = {
      ...formData,
      dataset_id: parseInt(formData.dataset_id),
      device_profile_id: formData.device_profile_id ? parseInt(formData.device_profile_id) : null,
      config: {
          ...config,
          ...(steps ? { steps: Math.round(steps) } : {}),
          images_path: formData.task_type === "vision" ? formData.images_path : null
      }
    };

    onSave(jobData);
  };

  const selectedProfile = deviceProfiles.find(
    (p) => p.id === parseInt(formData.device_profile_id)
  );
  // Not ready means one is missing or, all installed, a requirement is unmet.
  const missingLibraries = (libraries?.libraries || []).filter((l) => l.state === "missing");

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>{t("dialogs.newTrainingJob.title")}</DialogTitle>
      <DialogContent dividers>
        {hardwareCaps && (
          <Alert severity="success" icon={<ComputerIcon />} sx={{ mb: 2 }}>
            <Typography variant="subtitle2">
              {t("dialogs.newTrainingJob.detectedHardware", {
                gpu: hardwareCaps.gpu_name || t("dialogs.newTrainingJob.cpuOnly"),
                vram: hardwareCaps.vram_total_mb ? `${(hardwareCaps.vram_total_mb / 1024).toFixed(1)}GB VRAM` : t("dialogs.newTrainingJob.noGpu"),
              })}
            </Typography>
            <Typography variant="caption">
              {t("dialogs.newTrainingJob.defaultsApplied", { vram: (hardwareCaps.vram_total_mb / 1024).toFixed(0), gpu: hardwareCaps.gpu_name })}
            </Typography>
          </Alert>
        )}
        {libraries && !libraries.ready && (
          <Alert
            severity="warning"
            sx={{ mb: 2 }}
            action={
              <Button color="inherit" size="small" onClick={() => setLibrariesOpen(true)}>
                {t("dialogs.newTrainingJob.trainingLibraries")}
              </Button>
            }
          >
            {missingLibraries.length > 0 ? (
              <Typography variant="body2">
                <Trans
                  i18nKey="dialogs.newTrainingJob.librariesMissing"
                  values={{ libraries: missingLibraries.map((l) => l.name).join(", ") }}
                  components={{ bold: <strong /> }}
                />
              </Typography>
            ) : (
              <Typography variant="body2">
                <Trans
                  i18nKey="dialogs.newTrainingJob.librariesIncomplete"
                  values={{ unmet: (libraries.unmet || []).join("; ") }}
                  components={{ bold: <strong /> }}
                />
              </Typography>
            )}
          </Alert>
        )}
        {loadProblems.length > 0 && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            {t("dialogs.newTrainingJob.couldNotLoad", { problems: loadProblems.join(", ") })}
          </Alert>
        )}
        {formError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formError}
          </Alert>
        )}
        {loading && (
          <Alert severity="info" sx={{ mb: 2 }}>
            {t("dialogs.newTrainingJob.loadingOptions")}
          </Alert>
        )}
        <Grid container spacing={2} sx={{ mt: 0 }}>
          <Grid item xs={12}>
            <TextField
              autoFocus
              required
              fullWidth
              margin="dense"
              label={t("dialogs.newTrainingJob.jobName")}
              name="name"
              value={formData.name}
              onChange={handleInputChange}
              disabled={isSaving}
              helperText={t("dialogs.newTrainingJob.jobNameHelp")}
            />
          </Grid>

          {visionDeclared && (
            <Grid item xs={12}>
              <FormControl component="fieldset">
                <Typography variant="caption" color="textSecondary">{t("dialogs.newTrainingJob.taskType")}</Typography>
                <RadioGroup
                  row
                  name="task_type"
                  value={formData.task_type}
                  onChange={handleInputChange}
                >
                  <FormControlLabel value="text" control={<Radio />} label={t("dialogs.newTrainingJob.textGeneration")} disabled={isSaving} />
                  <FormControlLabel value="vision" control={<Radio />} label={t("dialogs.newTrainingJob.visionFineTuning")} disabled={isSaving} />
                </RadioGroup>
              </FormControl>
            </Grid>
          )}

          <Grid item xs={12} sm={6}>
            <FormControl fullWidth margin="dense" required>
              <InputLabel id="base-model-label">{t("dialogs.newTrainingJob.baseModel")}</InputLabel>
              <Select
                labelId="base-model-label"
                name="base_model"
                value={formData.base_model}
                onChange={handleInputChange}
                disabled={isSaving}
                label={t("dialogs.newTrainingJob.baseModel")}
                renderValue={(id) => baseModels.find((m) => m.id === id)?.name || id}
              >
                {fittingModels.map((model) => (
                  <MenuItem key={model.id} value={model.id} disabled={!model.installed || model.trainable === false}>
                    <ListItemText
                      primary={model.name}
                      secondary={
                        model.trainable === false
                          ? t("dialogs.newTrainingJob.notMeasured")
                          : model.installed
                          ? t("dialogs.newTrainingJob.modelDetails", { license: model.license, tokens: model.max_seq_length })
                          : t("dialogs.newTrainingJob.notDownloadedSize", { size: model.size_gb })
                      }
                    />
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            {!loading && baseModels.length > 0 && fittingModels.length === 0 && (
              <Alert severity="warning" sx={{ mt: 1 }}>
                {t("dialogs.newTrainingJob.noFit", { reason: baseModels[0].fit_reason })}
              </Alert>
            )}
            {notDownloaded.length > 0 && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                {t("dialogs.newTrainingJob.notDownloaded", { models: notDownloaded.map((m) => m.name).join(", "), count: notDownloaded.length })}{" "}
                <Button size="small" onClick={() => setLibrariesOpen(true)} sx={{ p: 0, minWidth: 0, verticalAlign: "baseline" }}>
                  {t("dialogs.newTrainingJob.downloadBaseModels")}
                </Button>
              </Typography>
            )}
          </Grid>

          <Grid item xs={12} sm={6}>
            <TextField
              fullWidth
              margin="dense"
              label={t("dialogs.newTrainingJob.outputModelName")}
              name="output_model_name"
              value={formData.output_model_name}
              onChange={handleInputChange}
              disabled={isSaving}
              helperText={t("dialogs.newTrainingJob.outputModelNameHelp")}
            />
          </Grid>

          <Grid item xs={12} sm={6}>
            <FormControl fullWidth margin="dense" required>
              <InputLabel id="dataset-label">{t("dialogs.newTrainingJob.dataset")}</InputLabel>
              <Select
                labelId="dataset-label"
                name="dataset_id"
                value={formData.dataset_id}
                onChange={handleInputChange}
                disabled={isSaving}
                label={t("dialogs.newTrainingJob.dataset")}
              >
                {datasets.map((ds) => (
                  <MenuItem key={ds.id} value={ds.id}>
                    {ds.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>

          {formData.task_type === "vision" && (
            <Grid item xs={12} sm={6}>
               <FormControl fullWidth margin="dense" required>
                <InputLabel>{t("dialogs.newTrainingJob.imageFolder")}</InputLabel>
                <Select
                    name="images_path"
                    value={formData.images_path}
                    onChange={handleInputChange}
                    disabled={isSaving}
                    label={t("dialogs.newTrainingJob.imageFolder")}
                >
                    {imageFolders.map((folder) => (
                    <MenuItem key={folder.path} value={folder.path}>
                        {t("dialogs.newTrainingJob.folderImages", { name: folder.name, count: folder.image_count })}
                    </MenuItem>
                    ))}
                </Select>
               </FormControl>
            </Grid>
          )}

          <Grid item xs={12} sm={6}>
            <FormControl fullWidth margin="dense">
              <InputLabel>{t("dialogs.newTrainingJob.deviceProfile")}</InputLabel>
              <Select
                name="device_profile_id"
                value={formData.device_profile_id}
                onChange={handleDeviceProfileChange}
                disabled={isSaving}
                label={t("dialogs.newTrainingJob.deviceProfile")}
              >
                {deviceProfiles.map((profile) => (
                  <MenuItem key={profile.id} value={profile.id}>
                    {profile.name} {profile.is_default && t("dialogs.newTrainingJob.defaultProfile")}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            {selectedProfile && (
              <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
                {t("dialogs.newTrainingJob.profileSummary", {
                  type: selectedProfile.device_type?.toUpperCase(),
                  batch: selectedProfile.max_batch_size,
                  seq: selectedProfile.max_seq_length,
                })}
                {selectedProfile.gpu_vram_mb && ` | VRAM: ${selectedProfile.gpu_vram_mb / 1024}GB`}
              </Typography>
            )}
          </Grid>

          <Grid item xs={12}>
            <Divider sx={{ my: 1 }} />
            <Typography variant="subtitle2" gutterBottom>
              {t("dialogs.newTrainingJob.trainingConfiguration")}
            </Typography>
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label={t("dialogs.newTrainingJob.steps")}
              name="config.steps"
              value={formData.config.steps}
              onChange={handleInputChange}
              disabled={isSaving}
              inputProps={{ min: 1 }}
              placeholder={t("dialogs.newTrainingJob.stepsAuto")}
              InputLabelProps={{ shrink: true }}
              helperText={t("dialogs.newTrainingJob.stepsHelp")}
            />
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label={t("dialogs.newTrainingJob.learningRate")}
              name="config.lr"
              value={formData.config.lr}
              onChange={handleInputChange}
              disabled={isSaving}
              inputProps={{ min: 0, step: 0.0001 }}
              helperText={t("dialogs.newTrainingJob.learningRateHelp")}
            />
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label={t("dialogs.newTrainingJob.batchSize")}
              name="config.batch_size"
              value={formData.config.batch_size}
              onChange={handleInputChange}
              disabled={isSaving}
              inputProps={{ min: 1, max: selectedModel?.max_batch_size }}
              helperText={selectedModel ? t("dialogs.newTrainingJob.max", { max: selectedModel.max_batch_size }) : selectedProfile && t("dialogs.newTrainingJob.max", { max: selectedProfile.max_batch_size })}
            />
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label={t("dialogs.newTrainingJob.loraRank")}
              name="config.rank"
              value={formData.config.rank}
              onChange={handleInputChange}
              disabled={isSaving}
              inputProps={{ min: 1 }}
              helperText={t("dialogs.newTrainingJob.loraRankHelp")}
            />
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label={t("dialogs.newTrainingJob.seqLength")}
              name="config.seq_length"
              value={formData.config.seq_length}
              onChange={handleInputChange}
              disabled={isSaving}
              inputProps={{ min: 128, max: selectedModel?.max_seq_length }}
              helperText={selectedModel ? t("dialogs.newTrainingJob.max", { max: selectedModel.max_seq_length }) : selectedProfile && t("dialogs.newTrainingJob.max", { max: selectedProfile.max_seq_length })}
            />
          </Grid>

          <Grid item xs={12}>
            <FormControlLabel
              control={
                <Checkbox
                  checked={formData.start_immediately}
                  onChange={handleInputChange}
                  name="start_immediately"
                  disabled={isSaving}
                />
              }
              label={t("dialogs.newTrainingJob.startImmediately")}
            />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSaving}>
          {t("dialogs.shared.cancel")}
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          disabled={isSaving || (libraries !== null && !libraries.ready) || !selectedModel?.installed}
        >
          {t("dialogs.newTrainingJob.createJob")}
        </Button>
      </DialogActions>
      <TrainingLibrariesModal
        open={librariesOpen}
        onClose={() => {
          setLibrariesOpen(false);
          checkLibraries();
          getBaseModels().then(applyBaseModels).catch(() => {});
        }}
        onChanged={setLibraries}
        onBaseModelsChanged={applyBaseModels}
      />
    </Dialog>
  );
};

export default NewTrainingJobModal;
