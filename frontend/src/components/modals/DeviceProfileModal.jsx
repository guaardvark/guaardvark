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
import { translate, useUiTranslation } from "../../i18n";

/**
 * Form values for a new profile from GET /api/training/hardware: the card and
 * memory this machine reports, and the batch size, sequence length and 4-bit
 * setting HardwareService recommends for that much GPU memory. `takenNames`
 * keeps the name unique, which the create route requires.
 */
export function profileFromHardware(hw, takenNames = []) {
  const rec = hw?.recommended_config || {};
  const hasGpu = Boolean(hw?.device_type) && hw.device_type !== "cpu";
  const base = hasGpu && hw.gpu_name ? hw.gpu_name : "This machine (CPU)";
  let name = base;
  for (let n = 2; takenNames.includes(name); n += 1) name = `${base} (${n})`;
  return {
    name,
    device_type: hasGpu ? "gpu" : "cpu",
    gpu_vram_mb: hasGpu && hw.vram_total_mb ? hw.vram_total_mb : "",
    system_ram_mb: hw?.ram_total_mb || "",
    max_batch_size: rec.batch_size || 1,
    max_seq_length: rec.max_seq_length || 512,
    supports_4bit: Boolean(rec.use_4bit),
  };
}

/** The line above a form filled from this machine: what was read, and that nothing is saved yet. */
export function hardwareNote(hw) {
  const hasGpu = Boolean(hw?.device_type) && hw.device_type !== "cpu";
  const ram = hw?.ram_total_mb || translate("dialogs.deviceProfile.unknownRam");
  if (!hasGpu) return translate("dialogs.deviceProfile.noteNoGpu", { ram });
  const gpu = hw.gpu_name || "GPU";
  return hw.vram_total_mb
    ? translate("dialogs.deviceProfile.noteGpuWithMemory", { gpu, vram: hw.vram_total_mb, ram })
    : translate("dialogs.deviceProfile.noteGpuNoMemory", { gpu, ram });
}

/**
 * @param {object} [prefill]      form values for a new profile (see profileFromHardware)
 * @param {string} [prefillNote]  shown above a prefilled form
 */
const DeviceProfileModal = ({
  open,
  onClose,
  profileData,
  prefill,
  prefillNote,
  onSave,
  isSaving,
}) => {
  const { t } = useUiTranslation();
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
          ...(prefill || {}),
        });
      }
      setFormError(null);
    }
  }, [open, profileData, prefill]);

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
      setFormError(t("dialogs.deviceProfile.nameRequired"));
      return;
    }
    if (formData.device_type === "gpu" && !formData.gpu_vram_mb) {
      setFormError(t("dialogs.deviceProfile.vramRequired"));
      return;
    }
    if (!formData.system_ram_mb) {
      setFormError(t("dialogs.deviceProfile.ramRequired"));
      return;
    }
    if (formData.max_batch_size < 1) {
      setFormError(t("dialogs.deviceProfile.batchMin"));
      return;
    }
    if (formData.max_seq_length < 128) {
      setFormError(t("dialogs.deviceProfile.seqMin"));
      return;
    }
    setFormError(null);
    onSave(formData);
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        {profileData
          ? t("dialogs.deviceProfile.editTitle", { name: profileData.name })
          : prefill
            ? t("dialogs.deviceProfile.fromMachineTitle")
            : t("dialogs.deviceProfile.addTitle")}
      </DialogTitle>
      <DialogContent dividers>
        {!profileData && prefill && prefillNote && (
          <Alert severity="info" sx={{ mb: 2 }} data-testid="device-profile-prefill-note">
            {prefillNote}
          </Alert>
        )}
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
              label={t("dialogs.deviceProfile.nameLabel")}
              name="name"
              value={formData.name}
              onChange={handleInputChange}
              disabled={isSaving}
              helperText={t("dialogs.deviceProfile.nameHelp")}
            />
          </Grid>

          <Grid item xs={12} sm={6}>
            <FormControl fullWidth margin="dense" required>
              <InputLabel>{t("dialogs.deviceProfile.deviceType")}</InputLabel>
              <Select
                name="device_type"
                value={formData.device_type}
                onChange={handleInputChange}
                disabled={isSaving}
                label={t("dialogs.deviceProfile.deviceType")}
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
                label={t("dialogs.deviceProfile.vramLabel")}
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
              label={t("dialogs.deviceProfile.ramLabel")}
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
              label={t("dialogs.deviceProfile.batchLabel")}
              name="max_batch_size"
              value={formData.max_batch_size}
              onChange={handleInputChange}
              disabled={isSaving}
              required
              inputProps={{ min: 1 }}
              helperText={t("dialogs.deviceProfile.batchHelp")}
            />
          </Grid>

          <Grid item xs={12} sm={6}>
            <TextField
              fullWidth
              type="number"
              margin="dense"
              label={t("dialogs.deviceProfile.seqLabel")}
              name="max_seq_length"
              value={formData.max_seq_length}
              onChange={handleInputChange}
              disabled={isSaving}
              required
              inputProps={{ min: 128 }}
              helperText={t("dialogs.deviceProfile.seqHelp")}
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
              label={t("dialogs.deviceProfile.supports4bit")}
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
              label={t("dialogs.deviceProfile.setDefault")}
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
              label={t("dialogs.deviceProfile.active")}
            />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSaving}>
          {t("dialogs.shared.cancel")}
        </Button>
        <Button onClick={handleSave} variant="contained" disabled={isSaving}>
          {profileData ? t("dialogs.shared.update") : t("dialogs.shared.create")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default DeviceProfileModal;
