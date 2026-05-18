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
} from "@mui/material";

const DeviceProfileModal = ({
  open,
  onClose,
  profileData,
  onSave,
  isSaving,
}) => {
  const [formData, setFormData] = useState({
    name: "",
    device_type: "gpu",
    gpu_vram_mb: "",
    system_ram_mb: "",
    max_batch_size: 2,
    max_seq_length: 2048,
    supports_4bit: true,
    requires_cpu_offload: false,
    is_default: false,
    is_active: true,
  });
  const [formError, setFormError] = useState(null);

  useEffect(() => {
    if (open) {
      if (profileData) {
        setFormData({
          name: profileData.name || "",
          device_type: profileData.device_type || "gpu",
          gpu_vram_mb: profileData.gpu_vram_mb || "",
          system_ram_mb: profileData.system_ram_mb || "",
          max_batch_size: profileData.max_batch_size || 2,
          max_seq_length: profileData.max_seq_length || 2048,
          supports_4bit: profileData.supports_4bit !== false,
          requires_cpu_offload: profileData.requires_cpu_offload || false,
          is_default: profileData.is_default || false,
          is_active: profileData.is_active !== false,
        });
      } else {
        setFormData({
          name: "",
          device_type: "gpu",
          gpu_vram_mb: "",
          system_ram_mb: "",
          max_batch_size: 2,
          max_seq_length: 2048,
          supports_4bit: true,
          requires_cpu_offload: false,
          is_default: false,
          is_active: true,
        });
      }
      setFormError(null);
    }
  }, [open, profileData]);

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]:
        type === "checkbox"
          ? checked
          : type === "number"
          ? value === ""
            ? ""
            : parseInt(value) || 0
          : value,
    }));
    
    // Clear error when user starts typing
    if (formError) {
      setFormError(null);
    }
  };

  const handleSave = () => {
    if (!formData.name.trim()) {
      setFormError("Profile name is required.");
      return;
    }
    if (formData.device_type === "gpu" && !formData.gpu_vram_mb) {
      setFormError("GPU VRAM is required for GPU devices.");
      return;
    }
    if (!formData.system_ram_mb) {
      setFormError("System RAM is required.");
      return;
    }
    if (formData.max_batch_size < 1) {
      setFormError("Batch size must be at least 1.");
      return;
    }
    if (formData.max_seq_length < 128) {
      setFormError("Sequence length must be at least 128.");
      return;
    }
    setFormError(null);
    onSave(formData);
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        {profileData ? `Edit Device Profile: ${profileData.name}` : "Add New Device Profile"}
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
              label="Profile Name"
              name="name"
              value={formData.name}
              onChange={handleInputChange}
              disabled={isSaving}
              helperText="e.g., RTX 4070 Ti, Raspberry Pi"
            />
          </Grid>

          <Grid item xs={12} sm={6}>
            <FormControl fullWidth margin="dense" required>
              <InputLabel>Device Type</InputLabel>
              <Select
                name="device_type"
                value={formData.device_type}
                onChange={handleInputChange}
                disabled={isSaving}
                label="Device Type"
              >
                <MenuItem value="gpu">GPU</MenuItem>
                <MenuItem value="cpu">CPU</MenuItem>
              </Select>
            </FormControl>
          </Grid>

          {formData.device_type === "gpu" && (
            <Grid item xs={12} sm={6}>
              <TextField
                fullWidth
                type="number"
                margin="dense"
                label="GPU VRAM (MB)"
                name="gpu_vram_mb"
                value={formData.gpu_vram_mb}
                onChange={handleInputChange}
                disabled={isSaving}
                required
                inputProps={{ min: 0 }}
                helperText={`${formData.gpu_vram_mb ? (formData.gpu_vram_mb / 1024).toFixed(1) : 0} GB`}
              />
            </Grid>
          )}

          <Grid item xs={12} sm={6}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label="System RAM (MB)"
              name="system_ram_mb"
              value={formData.system_ram_mb}
              onChange={handleInputChange}
              disabled={isSaving}
              required
              inputProps={{ min: 0 }}
              helperText={`${formData.system_ram_mb ? (formData.system_ram_mb / 1024).toFixed(1) : 0} GB`}
            />
          </Grid>

          <Grid item xs={12} sm={6}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label="Max Batch Size"
              name="max_batch_size"
              value={formData.max_batch_size}
              onChange={handleInputChange}
              disabled={isSaving}
              required
              inputProps={{ min: 1 }}
              helperText="Maximum batch size for training"
            />
          </Grid>

          <Grid item xs={12} sm={6}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label="Max Sequence Length"
              name="max_seq_length"
              value={formData.max_seq_length}
              onChange={handleInputChange}
              disabled={isSaving}
              required
              inputProps={{ min: 128 }}
              helperText="Maximum sequence length (128-4096)"
            />
          </Grid>

          <Grid item xs={12}>
            <FormControlLabel
              control={
                <Checkbox
                  checked={formData.supports_4bit}
                  onChange={handleInputChange}
                  name="supports_4bit"
                  disabled={isSaving}
                />
              }
              label="Supports 4-bit Quantization"
            />
          </Grid>

          <Grid item xs={12}>
            <FormControlLabel
              control={
                <Checkbox
                  checked={formData.requires_cpu_offload}
                  onChange={handleInputChange}
                  name="requires_cpu_offload"
                  disabled={isSaving}
                />
              }
              label="Requires CPU Offload (for large models)"
            />
          </Grid>

          <Grid item xs={12}>
            <FormControlLabel
              control={
                <Checkbox
                  checked={formData.is_default}
                  onChange={handleInputChange}
                  name="is_default"
                  disabled={isSaving}
                />
              }
              label="Set as Default Profile"
            />
          </Grid>

          <Grid item xs={12}>
            <FormControlLabel
              control={
                <Checkbox
                  checked={formData.is_active}
                  onChange={handleInputChange}
                  name="is_active"
                  disabled={isSaving}
                />
              }
              label="Active (available for use)"
            />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSaving}>
          Cancel
        </Button>
        <Button onClick={handleSave} variant="contained" disabled={isSaving}>
          {profileData ? "Update" : "Create"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default DeviceProfileModal;
