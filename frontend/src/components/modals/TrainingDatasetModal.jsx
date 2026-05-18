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
  InputAdornment,
  IconButton,
} from "@mui/material";
import FolderOpenIcon from "@mui/icons-material/FolderOpen";
import DirectoryPicker from "../common/DirectoryPicker";

const TrainingDatasetModal = ({
  open,
  onClose,
  datasetData,
  onSave,
  isSaving,
}) => {
  const [formData, setFormData] = useState({
    name: "",
    description: "",
    path: "",
  });
  const [formError, setFormError] = useState(null);
  const [directoryPickerOpen, setDirectoryPickerOpen] = useState(false);

  useEffect(() => {
    if (open) {
      setFormData({
        name: datasetData?.name || "",
        description: datasetData?.description || "",
        path: datasetData?.path || "",
      });
      setFormError(null);
    }
  }, [open, datasetData]);

  const handleInputChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handleSave = () => {
    if (!formData.name.trim()) {
      setFormError("Dataset name is required.");
      return;
    }
    setFormError(null);
    onSave(formData);
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        {datasetData ? `Edit Dataset: ${datasetData.name}` : "Add New Dataset"}
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
              label="Dataset Name"
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
              label="Description"
              name="description"
              value={formData.description}
              onChange={handleInputChange}
              disabled={isSaving}
            />
          </Grid>
          <Grid item xs={12}>
            <TextField
              fullWidth
              margin="dense"
              label="Path or URL"
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
                      title="Browse for directory"
                    >
                      <FolderOpenIcon />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
              helperText="Path to training data directory or URL"
            />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSaving}>
          Cancel
        </Button>
        <Button onClick={handleSave} variant="contained" disabled={isSaving}>
          Save
        </Button>
      </DialogActions>

      <DirectoryPicker
        open={directoryPickerOpen}
        onClose={() => setDirectoryPickerOpen(false)}
        onSelect={(selectedPath) => {
          setFormData((prev) => ({ ...prev, path: selectedPath }));
          setDirectoryPickerOpen(false);
        }}
        initialPath={formData.path || "/"}
        title="Select Training Data Directory"
      />
    </Dialog>
  );
};

export default TrainingDatasetModal;
