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
import { useUiTranslation } from "../../i18n";

function WordPressSiteModal({ open, onClose, site, clients, projects, onSave }) {
  const { t } = useUiTranslation();
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
      newErrors.url = t("dialogs.wordPressSite.urlRequired");
    } else if (!formData.url.match(/^https?:\/\/.+/)) {
      newErrors.url = t("dialogs.wordPressSite.urlScheme");
    }
    if (!formData.username.trim()) {
      newErrors.username = t("dialogs.wordPressSite.usernameRequired");
    }
    if (!isEditMode && !formData.api_key.trim()) {
      newErrors.api_key = t("dialogs.wordPressSite.apiKeyRequired");
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
            {isEditMode ? t("dialogs.wordPressSite.editTitle") : t("dialogs.wordPressSite.registerTitle")}
          </Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogContent>
        <Stack spacing={3} sx={{ mt: 1 }}>
          <Alert severity="info">
            {t("dialogs.wordPressSite.passwordHint")}
          </Alert>

          <TextField
            fullWidth
            label={t("dialogs.wordPressSite.urlLabel")}
            value={formData.url}
            onChange={(e) => handleChange("url", e.target.value)}
            error={!!errors.url}
            helperText={errors.url || t("dialogs.wordPressSite.urlHelp")}
            required
            placeholder="https://example.com"
          />

          <TextField
            fullWidth
            label={t("dialogs.wordPressSite.siteNameLabel")}
            value={formData.site_name}
            onChange={(e) => handleChange("site_name", e.target.value)}
            helperText={t("dialogs.wordPressSite.siteNameHelp")}
          />

          <TextField
            fullWidth
            label={t("dialogs.wordPressSite.usernameLabel")}
            value={formData.username}
            onChange={(e) => handleChange("username", e.target.value)}
            error={!!errors.username}
            helperText={errors.username || t("dialogs.wordPressSite.usernameHelp")}
            required
          />

          <TextField
            fullWidth
            label={t("dialogs.wordPressSite.passwordLabel")}
            type="password"
            value={formData.api_key}
            onChange={(e) => handleChange("api_key", e.target.value)}
            error={!!errors.api_key}
            helperText={
              errors.api_key ||
              (isEditMode
                ? t("dialogs.wordPressSite.passwordKeep")
                : t("dialogs.wordPressSite.passwordHelp"))
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
              <TextField {...params} label={t("dialogs.wordPressSite.clientLabel")} placeholder={t("dialogs.wordPressSite.clientPlaceholder")} />
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
              <TextField {...params} label={t("dialogs.wordPressSite.projectLabel")} placeholder={t("dialogs.wordPressSite.projectPlaceholder")} />
            )}
          />

          <FormControl fullWidth>
            <InputLabel>{t("dialogs.wordPressSite.status")}</InputLabel>
            <Select
              value={formData.status}
              label={t("dialogs.wordPressSite.status")}
              onChange={(e) => handleChange("status", e.target.value)}
            >
              <MenuItem value="active">{t("dialogs.wordPressSite.active")}</MenuItem>
              <MenuItem value="inactive">{t("dialogs.wordPressSite.inactive")}</MenuItem>
            </Select>
          </FormControl>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("dialogs.shared.cancel")}</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={isSaving}
        >
          {isSaving ? t("dialogs.wordPressSite.saving") : isEditMode ? t("dialogs.shared.update") : t("dialogs.wordPressSite.register")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default WordPressSiteModal;

