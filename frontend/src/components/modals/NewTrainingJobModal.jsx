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
  Box,
  Divider,
  RadioGroup,
  Radio,
} from "@mui/material";
import {
  getTrainingDatasets,
  getDeviceProfiles,
  getBaseModels,
  getImageFolders,
  getHardwareCapabilities,
} from "../../api";

import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StorageIcon from "@mui/icons-material/Storage";
import WorkIcon from "@mui/icons-material/Work";
import ComputerIcon from "@mui/icons-material/Computer";

const NewTrainingJobModal = ({
  open,
  onClose,
  onSave,
  isSaving,
}) => {
  const [formData, setFormData] = useState({
    name: "",
    task_type: "text", // "text" or "vision"
    base_model: "",
    dataset_id: "",
    images_path: "", // For vision tasks
    device_profile_id: "",
    output_model_name: "",
    config: {
      steps: 500,
      lr: 0.0002,
      batch_size: 2,
      rank: 16,
      seq_length: 2048,
    },
    start_immediately: false,
  });
  const [formError, setFormError] = useState(null);
  const [datasets, setDatasets] = useState([]);
  const [deviceProfiles, setDeviceProfiles] = useState([]);
  const [baseModels, setBaseModels] = useState([]);
  const [imageFolders, setImageFolders] = useState([]);
  const [hardwareCaps, setHardwareCaps] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (open) {
      setFormData({
        name: "",
        task_type: "text",
        base_model: "",
        dataset_id: "",
        images_path: "",
        device_profile_id: "",
        output_model_name: "",
        config: {
          steps: 500,
          lr: 0.0002,
          batch_size: 2,
          rank: 16,
          seq_length: 2048,
        },
        start_immediately: false,
      });
      setFormError(null);
      loadOptions();
    }
  }, [open]);

  const loadOptions = async () => {
    setLoading(true);
    try {
      const [datasetsData, profilesData, modelsData, imagesData, capsData] = await Promise.all([
        getTrainingDatasets(),
        getDeviceProfiles(),
        getBaseModels(),
        getImageFolders(),
        getHardwareCapabilities(),
      ]);
      
      setDatasets(Array.isArray(datasetsData) ? datasetsData : []);
      setDeviceProfiles(Array.isArray(profilesData) ? profilesData : []);
      setBaseModels(Array.isArray(modelsData) ? modelsData : []);
      setImageFolders(Array.isArray(imagesData) ? imagesData : []);
      setHardwareCaps(capsData);

      // Apply intelligent defaults from hardware detection
      if (capsData && capsData.recommended_config) {
        setFormData(prev => ({
          ...prev,
          config: {
            ...prev.config,
            batch_size: capsData.recommended_config.batch_size,
            seq_length: capsData.recommended_config.max_seq_length,
            rank: capsData.recommended_config.lora_rank,
            cpu_offload: capsData.recommended_config.cpu_offload
          }
        }));
      }
    } catch (err) {
      console.error("Error loading options:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    if (name.startsWith("config.")) {
      const configKey = name.split(".")[1];
      setFormData((prev) => ({
        ...prev,
        config: {
          ...prev.config,
          [configKey]: type === "number" ? parseFloat(value) || 0 : value,
        },
      }));
    } else if (name === "start_immediately") {
      setFormData((prev) => ({ ...prev, [name]: checked }));
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
        config: {
          ...prev.config,
          batch_size: profile.max_batch_size || prev.config.batch_size,
          seq_length: profile.max_seq_length || prev.config.seq_length,
        },
      }));
    }
  };

  const handleSave = () => {
    if (!formData.name.trim()) {
      setFormError("Job name is required.");
      return;
    }
    if (!formData.base_model) {
      setFormError("Base model is required.");
      return;
    }
    if (!formData.dataset_id) {
      setFormError("Dataset is required.");
      return;
    }
    if (formData.task_type === "vision" && !formData.images_path) {
        setFormError("Image folder is required for vision tasks.");
        return;
    }

    setFormError(null);
    
    // Convert IDs to integers
    const jobData = {
      ...formData,
      dataset_id: parseInt(formData.dataset_id),
      device_profile_id: formData.device_profile_id ? parseInt(formData.device_profile_id) : null,
      config: {
          ...formData.config,
          images_path: formData.task_type === "vision" ? formData.images_path : null
      }
    };
    
    onSave(jobData);
  };

  const selectedProfile = deviceProfiles.find(
    (p) => p.id === parseInt(formData.device_profile_id)
  );

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Create New Training Job</DialogTitle>
      <DialogContent dividers>
        {hardwareCaps && (
          <Alert severity="success" icon={<ComputerIcon />} sx={{ mb: 2 }}>
            <Typography variant="subtitle2">
              Detected Hardware: {hardwareCaps.gpu_name || "CPU Only"} ({hardwareCaps.vram_total_mb ? `${(hardwareCaps.vram_total_mb / 1024).toFixed(1)}GB VRAM` : "No GPU"})
            </Typography>
            <Typography variant="caption">
              Intelligent defaults applied based on your {(hardwareCaps.vram_total_mb / 1024).toFixed(0)}GB {hardwareCaps.gpu_name}.
            </Typography>
          </Alert>
        )}
        {formError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formError}
          </Alert>
        )}
        {loading && (
          <Alert severity="info" sx={{ mb: 2 }}>
            Loading options...
          </Alert>
        )}
        <Grid container spacing={2} sx={{ mt: 0 }}>
          <Grid item xs={12}>
            <TextField
              autoFocus
              required
              fullWidth
              margin="dense"
              label="Job Name"
              name="name"
              value={formData.name}
              onChange={handleInputChange}
              disabled={isSaving}
              helperText="A descriptive name for this training job"
            />
          </Grid>
          
          <Grid item xs={12}>
            <FormControl component="fieldset">
              <Typography variant="caption" color="textSecondary">Task Type</Typography>
              <RadioGroup
                row
                name="task_type"
                value={formData.task_type}
                onChange={handleInputChange}
              >
                <FormControlLabel value="text" control={<Radio />} label="Text Generation" disabled={isSaving} />
                <FormControlLabel value="vision" control={<Radio />} label="Vision Fine-Tuning" disabled={isSaving} />
              </RadioGroup>
            </FormControl>
          </Grid>

          <Grid item xs={12} sm={6}>
            <FormControl fullWidth margin="dense" required>
              <InputLabel>Base Model</InputLabel>
              <Select
                name="base_model"
                value={formData.base_model}
                onChange={handleInputChange}
                disabled={isSaving}
                label="Base Model"
              >
                {baseModels.map((model) => (
                  <MenuItem key={model.name || model} value={model.name || model}>
                    {model.name || model}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>

          <Grid item xs={12} sm={6}>
            <TextField
              fullWidth
              margin="dense"
              label="Output Model Name"
              name="output_model_name"
              value={formData.output_model_name}
              onChange={handleInputChange}
              disabled={isSaving}
              helperText="Optional: Name for the fine-tuned model"
            />
          </Grid>

          <Grid item xs={12} sm={6}>
            <FormControl fullWidth margin="dense" required>
              <InputLabel>Dataset (JSONL)</InputLabel>
              <Select
                name="dataset_id"
                value={formData.dataset_id}
                onChange={handleInputChange}
                disabled={isSaving}
                label="Dataset (JSONL)"
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
                <InputLabel>Image Folder</InputLabel>
                <Select
                    name="images_path"
                    value={formData.images_path}
                    onChange={handleInputChange}
                    disabled={isSaving}
                    label="Image Folder"
                >
                    {imageFolders.map((folder) => (
                    <MenuItem key={folder.path} value={folder.path}>
                        {folder.name} ({folder.image_count} images)
                    </MenuItem>
                    ))}
                </Select>
               </FormControl>
            </Grid>
          )}

          <Grid item xs={12} sm={6}>
            <FormControl fullWidth margin="dense">
              <InputLabel>Device Profile</InputLabel>
              <Select
                name="device_profile_id"
                value={formData.device_profile_id}
                onChange={handleDeviceProfileChange}
                disabled={isSaving}
                label="Device Profile"
              >
                {deviceProfiles.map((profile) => (
                  <MenuItem key={profile.id} value={profile.id}>
                    {profile.name} {profile.is_default && "(Default)"}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            {selectedProfile && (
              <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
                {selectedProfile.device_type?.toUpperCase()} | 
                Max Batch: {selectedProfile.max_batch_size} | 
                Max Seq: {selectedProfile.max_seq_length}
                {selectedProfile.gpu_vram_mb && ` | VRAM: ${selectedProfile.gpu_vram_mb / 1024}GB`}
              </Typography>
            )}
          </Grid>

          <Grid item xs={12}>
            <Divider sx={{ my: 1 }} />
            <Typography variant="subtitle2" gutterBottom>
              Training Configuration
            </Typography>
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label="Training Steps"
              name="config.steps"
              value={formData.config.steps}
              onChange={handleInputChange}
              disabled={isSaving}
              inputProps={{ min: 1 }}
            />
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label="Learning Rate"
              name="config.lr"
              value={formData.config.lr}
              onChange={handleInputChange}
              disabled={isSaving}
              inputProps={{ min: 0, step: 0.0001 }}
              helperText="e.g., 0.0002"
            />
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label="Batch Size"
              name="config.batch_size"
              value={formData.config.batch_size}
              onChange={handleInputChange}
              disabled={isSaving}
              inputProps={{ min: 1 }}
              helperText={selectedProfile && `Max: ${selectedProfile.max_batch_size}`}
            />
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label="LoRA Rank"
              name="config.rank"
              value={formData.config.rank}
              onChange={handleInputChange}
              disabled={isSaving}
              inputProps={{ min: 1 }}
              helperText="LoRA adapter rank (typically 8-32)"
            />
          </Grid>

          <Grid item xs={12} sm={6} md={4}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label="Max Sequence Length"
              name="config.seq_length"
              value={formData.config.seq_length}
              onChange={handleInputChange}
              disabled={isSaving}
              inputProps={{ min: 128 }}
              helperText={selectedProfile && `Max: ${selectedProfile.max_seq_length}`}
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
              label="Start training immediately after creation"
            />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSaving}>
          Cancel
        </Button>
        <Button onClick={handleSave} variant="contained" disabled={isSaving}>
          Create Job
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default NewTrainingJobModal;
