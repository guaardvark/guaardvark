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

const ParseJobModal = ({ open, onClose, onSuccess, isSaving: externalSaving }) => {
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
      setFormError("Input path is required.");
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
      setFormError(err.message || "Failed to start parse job");
    } finally {
      setIsSaving(false);
    }
  };

  const saving = externalSaving || isSaving;

  return (
    <>
      <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
        <DialogTitle>Parse Transcripts</DialogTitle>
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
                label="Job Name (Optional)"
                name="name"
                value={formData.name}
                onChange={handleInputChange}
                disabled={saving}
                helperText="Leave empty to auto-generate from path"
              />
            </Grid>

            <Grid item xs={12}>
              <TextField
                required
                fullWidth
                margin="dense"
                label="Input Path"
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
                        title="Browse for directory"
                      >
                        <FolderOpenIcon />
                      </IconButton>
                    </InputAdornment>
                  ),
                }}
                helperText="Directory or file containing transcript files"
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
                label="Recursive (search subdirectories)"
              />
            </Grid>
          </Grid>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSave} variant="contained" disabled={saving}>
            Start Parsing
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
        initialPath={formData.input_path || "/home/llamax1/LLAMAX5.2/training/raw_transcripts"}
        title="Select Transcript Directory"
      />
    </>
  );
};

export default ParseJobModal;
