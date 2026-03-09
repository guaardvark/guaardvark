// frontend/src/components/modals/RuleActionModal.jsx
// Version 4.0.0: Intelligent auto-detection and simplified UX
// - Removed Level and Type dropdowns (auto-detected)
// - Smart detection: COMMAND (has command), SYSTEM (qa_default/global), PROMPT (others)
// - Cleaner UI with better organization and user experience

import CloseIcon from "@mui/icons-material/Close";
import LinkIcon from "@mui/icons-material/Link";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Grid,
  IconButton,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import React, { useCallback, useEffect, useState } from "react";
import { useTheme } from "@mui/material/styles";

import * as apiService from "../../api";

const ALL_MODELS_VALUE = "__ALL__";

// Auto-detect rule type based on content and name
const detectRuleType = (formData) => {
  const name = formData.name?.toLowerCase() || "";
  const hasCommand = formData.command_label?.trim();

  if (hasCommand) return "COMMAND";
  if (name === "qa_default" || name === "global_default_chat_system_prompt")
    return "SYSTEM";
  return "PROMPT";
};

// Convert display type to database type and level
const getDbTypeAndLevel = (displayType, isSystemPrompt = false) => {
  switch (displayType) {
    case "COMMAND":
      return { type: "COMMAND_RULE", level: "USER_GLOBAL" };
    case "SYSTEM":
      return { type: "PROMPT_TEMPLATE", level: "SYSTEM" };
    default:
      return { type: "PROMPT_TEMPLATE", level: "USER_GLOBAL" };
  }
};

const RuleActionModal = ({
  open,
  onClose,
  ruleData,
  onSave,
  onDelete,
  onOpenLinker,
  isSaving,
}) => {
  const isNewRule = !ruleData?.id;
  const theme = useTheme();
  const [formData, setFormData] = useState({});
  const [modelOptions, setModelOptions] = useState([ALL_MODELS_VALUE]);
  const [isLoadingModels, setIsLoadingModels] = useState(false);
  const [modelFetchError, setModelFetchError] = useState(null);

  const initializeFormData = useCallback(() => {
    const initialData = {
      name: ruleData?.name || "",
      description: ruleData?.description || "",
      rule_text: ruleData?.rule_text || "",
      command_label: ruleData?.command_label || "",
      target_models:
        ruleData?.target_models?.length > 0
          ? ruleData.target_models
          : [ALL_MODELS_VALUE],
      is_active: ruleData?.is_active !== false,
      project_id: ruleData?.project_id || null,
      client_id: ruleData?.client_id || null,
    };
    setFormData(initialData);
  }, [ruleData]);

  useEffect(() => {
    if (open) {
      initializeFormData();

      const fetchModels = async () => {
        setIsLoadingModels(true);
        setModelFetchError(null);
        try {
          const fetchedApiModels = await apiService.getAvailableModels();
          if (fetchedApiModels.error) {
            throw new Error(
              fetchedApiModels.error.message || fetchedApiModels.error
            );
          }
          const names = Array.isArray(fetchedApiModels)
            ? fetchedApiModels.map((m) => m.name)
            : [];
          setModelOptions([ALL_MODELS_VALUE, ...new Set(names)]);
        } catch (err) {
          console.error("RuleActionModal: Failed to fetch models:", err);
          setModelFetchError(
            `Failed to load models: ${err.message}. Using default list.`
          );
          setModelOptions([
            ALL_MODELS_VALUE,
            "gpt-4",
            "gpt-3.5-turbo",
            "claude-2",
          ]);
        } finally {
          setIsLoadingModels(false);
        }
      };
      fetchModels();
    }
  }, [open, initializeFormData]);

  const handleChange = (event) => {
    const { name, value, type, checked } = event.target;
    const newFormData = {
      ...formData,
      [name]: type === "checkbox" ? checked : value,
    };
    setFormData(newFormData);
  };

  const handleMultiSelectChange = (name, newValue) => {
    let selection = newValue;
    if (newValue.includes(ALL_MODELS_VALUE) && newValue.length > 1) {
      selection = [ALL_MODELS_VALUE];
    }
    // Removed: else if (newValue.length === 0 && name === 'target_models') {
    // Removed:   selection = [ALL_MODELS_VALUE];
    // Removed: }
    // This allows the selection to be temporarily empty.
    // handleSave will default to [ALL_MODELS_VALUE] if it's empty on save.
    setFormData((prev) => ({ ...prev, [name]: selection }));
  };

  const handleSave = () => {
    if (onSave) {
      const detectedType = detectRuleType(formData);

      if (detectedType === "COMMAND" && !formData.command_label?.trim()) {
        alert("Command is required for command rules.");
        return;
      }

      const { type, level } = getDbTypeAndLevel(detectedType);
      const dataToSave = {
        ...formData,
        type,
        level,
      };

      if (ruleData?.id) {
        dataToSave.id = ruleData.id;
      }

      // Default to ALL_MODELS if target_models is empty
      if (
        !Array.isArray(dataToSave.target_models) ||
        dataToSave.target_models.length === 0
      ) {
        dataToSave.target_models = [ALL_MODELS_VALUE];
      }

      if (dataToSave.project_id === "") dataToSave.project_id = null;
      if (dataToSave.client_id === "") dataToSave.client_id = null;

      onSave(dataToSave);
    }
  };

  const handleDelete = () => {
    if (ruleData?.id && onDelete) {
      onDelete(ruleData.id);
    }
  };

  const handleOpenLinkerModal = () => {
    if (onOpenLinker && ruleData) {
      onOpenLinker(ruleData);
    }
  };

  // Get current detected type for display
  const detectedType = detectRuleType(formData);
  const getTypeDisplayInfo = (type) => {
    switch (type) {
      case "COMMAND":
        return {
          label: "COMMAND",
          color: theme.palette.primary.main,
          description: "Interactive slash command",
        };
      case "SYSTEM":
        return {
          label: "SYSTEM",
          color: theme.palette.error.main,
          description: "Core system prompt",
        };
      default:
        return {
          label: "PROMPT",
          color: theme.palette.warning.main,
          description: "User prompt template",
        };
    }
  };

  const typeInfo = getTypeDisplayInfo(detectedType);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      scroll="paper"
    >
      <DialogTitle
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        {isNewRule
          ? "Create Rule"
          : `Edit: ${formData.name || ruleData?.name || "Rule"}`}
        <IconButton onClick={onClose} size="small">
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {modelFetchError && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            {modelFetchError}
          </Alert>
        )}
        <Grid container spacing={3}>
          {/* Basic Information */}
          <Grid item xs={12}>
            <Grid container spacing={2}>
              <Grid item xs={12} sm={8}>
                <TextField
                  label="Name"
                  name="name"
                  value={formData.name || ""}
                  onChange={handleChange}
                  fullWidth
                  required
                  autoFocus
                  helperText="A unique and descriptive name"
                />
              </Grid>
              <Grid
                item
                xs={12}
                sm={4}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <FormControlLabel
                  control={
                    <Switch
                      name="is_active"
                      checked={formData.is_active || false}
                      onChange={handleChange}
                    />
                  }
                  label="Active"
                />
              </Grid>
            </Grid>
          </Grid>

          {/* Type Display & Command */}
          <Grid item xs={12}>
            <Grid container spacing={2} alignItems="center">
              <Grid item xs={12} sm={6}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Typography variant="body2" color="text.secondary">
                    Type:
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{
                      backgroundColor: typeInfo.color,
                      color: "white",
                      padding: "3px 8px",
                      borderRadius: "4px",
                      fontWeight: "medium",
                      fontSize: "12px",
                    }}
                  >
                    {typeInfo.label}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {typeInfo.description}
                  </Typography>
                </Box>
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  label="Command (optional)"
                  name="command_label"
                  value={formData.command_label || ""}
                  onChange={handleChange}
                  fullWidth
                  size="small"
                  placeholder="/your-command"
                  helperText={
                    detectedType === "COMMAND"
                      ? "Required for command rules"
                      : "Leave empty for prompt templates"
                  }
                  error={
                    detectedType === "COMMAND" &&
                    !formData.command_label?.trim()
                  }
                />
              </Grid>
            </Grid>
          </Grid>

          {/* Content */}
          <Grid item xs={12}>
            <TextField
              label={
                formData.type === "PROMPT_TEMPLATE"
                  ? "Prompt Template"
                  : "Rule Content"
              }
              name="rule_text"
              value={formData.rule_text || ""}
              onChange={handleChange}
              multiline
              minRows={6}
              maxRows={20}
              fullWidth
              required
              helperText={
                formData.type === "PROMPT_TEMPLATE"
                  ? "Enter the prompt template with {placeholders} as needed"
                  : "Define the rule logic or content"
              }
            />
          </Grid>

          {/* Description & Models */}
          <Grid item xs={12}>
            <TextField
              label="Description"
              name="description"
              value={formData.description || ""}
              onChange={handleChange}
              multiline
              minRows={2}
              maxRows={4}
              fullWidth
              helperText="Optional description of purpose and usage"
            />
          </Grid>

          <Grid item xs={12}>
            <Autocomplete
              multiple
              options={modelOptions}
              loading={isLoadingModels}
              value={formData.target_models || []}
              onChange={(event, newValue) =>
                handleMultiSelectChange("target_models", newValue)
              }
              getOptionLabel={(option) =>
                option === ALL_MODELS_VALUE ? "All Models" : option
              }
              isOptionEqualToValue={(option, value) => option === value}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => {
                  const { key, ...otherTagProps } = getTagProps({ index });
                  return (
                    <Chip
                      key={key || option}
                      variant="outlined"
                      label={
                        option === ALL_MODELS_VALUE ? "All Models" : option
                      }
                      {...otherTagProps}
                    />
                  );
                })
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Target Models"
                  placeholder="Select models or leave empty for all"
                  helperText="Choose which models this rule applies to"
                  InputProps={{
                    ...params.InputProps,
                    endAdornment: (
                      <React.Fragment>
                        {isLoadingModels && (
                          <CircularProgress color="inherit" size={20} />
                        )}
                        {params.InputProps.endAdornment}
                      </React.Fragment>
                    ),
                  }}
                />
              )}
            />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions sx={{ p: 2, gap: 1 }}>
        {!isNewRule && ruleData?.id && (
          <>
            <Button
              variant="outlined"
              size="small"
              startIcon={<LinkIcon />}
              onClick={handleOpenLinkerModal}
              disabled={isSaving}
            >
              Links
            </Button>
            <Button
              variant="text"
              color="error"
              size="small"
              onClick={handleDelete}
              disabled={isSaving}
            >
              Delete
            </Button>
          </>
        )}
        <Box sx={{ flexGrow: 1 }} />
        <Button onClick={onClose} disabled={isSaving}>
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          disabled={
            isSaving ||
            !formData.name?.trim() ||
            !formData.rule_text?.trim() ||
            (detectRuleType(formData) === "COMMAND" &&
              !formData.command_label?.trim())
          }
        >
          {isSaving ? "Saving..." : isNewRule ? "Create" : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default RuleActionModal;
