// frontend/src/components/modals/WordPressSiteModal.jsx
// Modal for registering/editing WordPress sites

import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Autocomplete,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Box,
  Typography,
  Alert,
  Stack,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import IconButton from "@mui/material/IconButton";

function WordPressSiteModal({ open, onClose, site, clients, projects, onSave }) {
  const isEditMode = Boolean(site);

  const [formData, setFormData] = useState({
    url: "",
    site_name: "",
    username: "",
    api_key: "",
    client_id: null,
    project_id: null,
    website_id: null,
    pull_settings: {},
    push_settings: {},
    status: "active",
  });

  const [errors, setErrors] = useState({});
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (site) {
      setFormData({
        url: site.url || "",
        site_name: site.site_name || "",
        username: site.username || "",
        api_key: "", // Don't populate for security
        client_id: site.client_id || null,
        project_id: site.project_id || null,
        website_id: site.website_id || null,
        pull_settings: site.pull_settings || {},
        push_settings: site.push_settings || {},
        status: site.status || "active",
      });
    } else {
      setFormData({
        url: "",
        site_name: "",
        username: "",
        api_key: "",
        client_id: null,
        project_id: null,
        website_id: null,
        pull_settings: {},
        push_settings: {},
        status: "active",
      });
    }
    setErrors({});
  }, [site, open]);

  const handleChange = (field, value) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
    if (errors[field]) {
      setErrors((prev) => ({ ...prev, [field]: null }));
    }
  };

  const validate = () => {
    const newErrors = {};
    if (!formData.url.trim()) {
      newErrors.url = "URL is required";
    } else if (!formData.url.match(/^https?:\/\/.+/)) {
      newErrors.url = "URL must start with http:// or https://";
    }
    if (!formData.username.trim()) {
      newErrors.username = "Username is required";
    }
    if (!isEditMode && !formData.api_key.trim()) {
      newErrors.api_key = "API key is required";
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate()) return;

    setIsSaving(true);
    try {
      await onSave({
        ...formData,
        api_key: formData.api_key || undefined, // Only send if provided
      });
    } catch (error) {
      console.error("Error saving WordPress site:", error);
    } finally {
      setIsSaving(false);
    }
  };

  const clientOptions = clients || [];
  const projectOptions = projects || [];

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Typography variant="h6">
            {isEditMode ? "Edit WordPress Site" : "Register WordPress Site"}
          </Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogContent>
        <Stack spacing={3} sx={{ mt: 1 }}>
          <Alert severity="info">
            Use WordPress Application Passwords for secure API access. Go to Users → Your Profile
            → Application Passwords in WordPress admin to create one.
          </Alert>

          <TextField
            fullWidth
            label="WordPress Site URL"
            value={formData.url}
            onChange={(e) => handleChange("url", e.target.value)}
            error={!!errors.url}
            helperText={errors.url || "Full URL including https://"}
            required
            placeholder="https://example.com"
          />

          <TextField
            fullWidth
            label="Site Name (Optional)"
            value={formData.site_name}
            onChange={(e) => handleChange("site_name", e.target.value)}
            helperText="Display name for this site"
          />

          <TextField
            fullWidth
            label="WordPress Username"
            value={formData.username}
            onChange={(e) => handleChange("username", e.target.value)}
            error={!!errors.username}
            helperText={errors.username || "WordPress username"}
            required
          />

          <TextField
            fullWidth
            label="Application Password"
            type="password"
            value={formData.api_key}
            onChange={(e) => handleChange("api_key", e.target.value)}
            error={!!errors.api_key}
            helperText={
              errors.api_key ||
              (isEditMode
                ? "Leave blank to keep existing password"
                : "WordPress Application Password (not your regular password)")
            }
            required={!isEditMode}
          />

          <Autocomplete
            options={clientOptions}
            getOptionLabel={(option) => option.name || option}
            value={clientOptions.find((c) => c.id === formData.client_id) || null}
            onChange={(event, newValue) => {
              handleChange("client_id", newValue ? newValue.id : null);
            }}
            renderInput={(params) => (
              <TextField {...params} label="Client (Optional)" placeholder="Select client" />
            )}
          />

          <Autocomplete
            options={projectOptions}
            getOptionLabel={(option) => option.name || option}
            value={projectOptions.find((p) => p.id === formData.project_id) || null}
            onChange={(event, newValue) => {
              handleChange("project_id", newValue ? newValue.id : null);
            }}
            renderInput={(params) => (
              <TextField {...params} label="Project (Optional)" placeholder="Select project" />
            )}
          />

          <FormControl fullWidth>
            <InputLabel>Status</InputLabel>
            <Select
              value={formData.status}
              label="Status"
              onChange={(e) => handleChange("status", e.target.value)}
            >
              <MenuItem value="active">Active</MenuItem>
              <MenuItem value="inactive">Inactive</MenuItem>
            </Select>
          </FormControl>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={isSaving}
        >
          {isSaving ? "Saving..." : isEditMode ? "Update" : "Register"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default WordPressSiteModal;

