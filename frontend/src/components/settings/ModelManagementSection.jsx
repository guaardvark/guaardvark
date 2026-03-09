// frontend/src/components/settings/ModelManagementSection.jsx
// Extracted from SettingsPage.jsx - Model Management functionality

import React from 'react';
import {
  Typography,
  Box,
  Select,
  MenuItem,
  Button,
  FormControl,
  InputLabel,
  CircularProgress,
  Paper,
  Grid,
  Tooltip
} from '@mui/material';
import { useSnackbar } from '../../contexts/SnackbarProvider';
import apiService from '../../api/apiService';

const ModelManagementSection = ({ 
  availableModels, 
  selectedModel, 
  setSelectedModel,
  activeModel,
  isLoading,
  refreshActiveModel 
}) => {
  const { showMessage } = useSnackbar();

  const handleActionClick = async (
    actionFunction,
    actionArgs,
    confirmMessage,
    loadingMessage,
    successMessage,
    failureMessagePrefix,
  ) => {
    if (confirmMessage && !window.confirm(confirmMessage)) return;

    showMessage(loadingMessage || "Processing...", "info");
    try {
      const result = await actionFunction(...actionArgs);
      if (result?.error && !result.warning && result.error !== "User aborted") {
        throw new Error(result.error.message || result.error);
      }
      const message =
        result?.warning ||
        result?.message ||
        successMessage ||
        "Action completed successfully.";
      const severity = result?.warning ? "warning" : "success";

      showMessage(message, severity);

      if (actionFunction === apiService.setModel) {
        refreshActiveModel();
      }
    } catch (err) {
      if (err.message !== "User aborted") {
        showMessage(`${failureMessagePrefix}: ${err.message}`, "error");
      }
    }
  };

  const handleSetModelClick = () => {
    if (!selectedModel) {
      showMessage("Please select a model first.", "warning");
      return;
    }
    handleActionClick(
      apiService.setModel,
      [selectedModel],
      null,
      "Setting active model...",
      `Model set to ${selectedModel}.`,
      "Failed to set model",
    );
  };

  const handleRefreshModelsClick = () => {
    handleActionClick(
      apiService.refreshModels,
      [],
      null,
      "Refreshing available models...",
      "Models refreshed successfully.",
      "Failed to refresh models",
    );
  };

  return (
    <Paper elevation={3} sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        Model Management
      </Typography>
      <Grid container spacing={2}>
        <Grid item xs={12}>
          <Typography variant="body2" color="text.secondary" gutterBottom>
            Current Active Model: <strong>{activeModel || "Loading..."}</strong>
          </Typography>
        </Grid>
        <Grid item xs={12} md={6}>
          <FormControl fullWidth disabled={isLoading}>
            <InputLabel>Select Model</InputLabel>
            <Select
              value={selectedModel}
              label="Select Model"
              onChange={(e) => setSelectedModel(e.target.value)}
            >
              {availableModels.map((model) => (
                <MenuItem key={model} value={model}>
                  {model}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Grid>
        <Grid item xs={12} md={6}>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Tooltip title="Set the selected model as active">
              <span>
                <Button
                  variant="contained"
                  onClick={handleSetModelClick}
                  disabled={isLoading || !selectedModel}
                  fullWidth
                >
                  {isLoading ? (
                    <CircularProgress size={24} color="inherit" />
                  ) : (
                    "Set Model"
                  )}
                </Button>
              </span>
            </Tooltip>
          </Box>
        </Grid>
        <Grid item xs={12}>
          <Tooltip title="Refresh the list of available models">
            <span>
              <Button
                variant="outlined"
                onClick={handleRefreshModelsClick}
                disabled={isLoading}
                fullWidth
              >
                {isLoading ? (
                  <CircularProgress size={24} />
                ) : (
                  "Refresh Models"
                )}
              </Button>
            </span>
          </Tooltip>
        </Grid>
      </Grid>
    </Paper>
  );
};

export default ModelManagementSection; 