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
  FormControlLabel,
  Checkbox,
  InputAdornment,
  IconButton,
} from "@mui/material";
import FolderOpenIcon from "@mui/icons-material/FolderOpen";
import DirectoryPicker from "../common/DirectoryPicker";
import { startParseJob } from "../../api";
import { useUiTranslation } from "../../i18n";

const ParseJobModal = ({ open, onClose, onSuccess, isSaving: externalSaving }) => {
  const { t } = useUiTranslation();
  const [formData, setFormData] = useState({
    name: "",
    input_path: "",
    recursive: true,
  });
  const [formError, setFormError] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const [directoryPickerOpen, setDirectoryPickerOpen] = useState(false);

  useEffect(() => {
    if (open) {
      setFormData({
        name: "",
        input_path: "",
        recursive: true,
      });
      setFormError(null);
    }
  }, [open]);

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: type === "checkbox" ? checked : value,
    }));
  };

  const handleSave = async () => {
    if (!formData.input_path.trim()) {
      setFormError(t("dialogs.parseJob.pathRequired"));
      return;
    }
    setFormError(null);
    setIsSaving(true);

    try {
      const result = await startParseJob(formData);
      if (result?.error) {
        throw new Error(result.error);
      }
      if (onSuccess) {
        onSuccess(result);
      }
      onClose();
    } catch (err) {
      setFormError(err.message || t("dialogs.parseJob.startFailed"));
    } finally {
      setIsSaving(false);
    }
  };

  const saving = externalSaving || isSaving;

  return (
    <>
      <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
        <DialogTitle>{t("dialogs.parseJob.title")}</DialogTitle>
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
                fullWidth
                margin="dense"
                label={t("dialogs.parseJob.nameLabel")}
                name="name"
                value={formData.name}
                onChange={handleInputChange}
                disabled={saving}
                helperText={t("dialogs.parseJob.nameHelp")}
              />
            </Grid>

            <Grid item xs={12}>
              <TextField
                required
                fullWidth
                margin="dense"
                label={t("dialogs.parseJob.pathLabel")}
                name="input_path"
                value={formData.input_path}
                onChange={handleInputChange}
                disabled={saving}
                InputProps={{
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton
                        onClick={() => setDirectoryPickerOpen(true)}
                        edge="end"
                        disabled={saving}
                        title={t("dialogs.parseJob.browse")}
                      >
                        <FolderOpenIcon />
                      </IconButton>
                    </InputAdornment>
                  ),
                }}
                helperText={t("dialogs.parseJob.pathHelp")}
              />
            </Grid>

            <Grid item xs={12}>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={formData.recursive}
                    onChange={handleInputChange}
                    name="recursive"
                    disabled={saving}
                  />
                }
                label={t("dialogs.parseJob.recursive")}
              />
            </Grid>
          </Grid>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={saving}>
            {t("dialogs.shared.cancel")}
          </Button>
          <Button onClick={handleSave} variant="contained" disabled={saving}>
            {t("dialogs.parseJob.start")}
          </Button>
        </DialogActions>
      </Dialog>

      <DirectoryPicker
        open={directoryPickerOpen}
        onClose={() => setDirectoryPickerOpen(false)}
        onSelect={(selectedPath) => {
          setFormData((prev) => ({ ...prev, input_path: selectedPath }));
          setDirectoryPickerOpen(false);
        }}
        initialPath={formData.input_path || "~"}
        title={t("dialogs.parseJob.pickerTitle")}
      />
    </>
  );
};

export default ParseJobModal;
