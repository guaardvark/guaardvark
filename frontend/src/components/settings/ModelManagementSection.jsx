// frontend/src/components/settings/ModelManagementSection.jsx
// Extracted from SettingsPage.jsx - Model Management functionality

import React, { useState, useEffect, useCallback } from 'react';
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
  Tooltip,
  Switch,
  FormControlLabel,
  ToggleButton,
  ToggleButtonGroup,
  Divider,
  Alert
} from '@mui/material';
import { useSnackbar } from '../common/SnackbarProvider';
import * as apiService from '../../api';
import {
  getLlmProvider,
  setCloudModelsEnabled,
  setLlmProvider,
  getProviderModels,
  setMistralModel,
  setOpenaiModel,
  testProviderConnection,
} from '../../api/modelService';

const ModelManagementSection = ({
  availableModels,
  selectedModel,
  setSelectedModel,
  activeModel,
  isLoading,
  refreshActiveModel
}) => {
  const { showMessage } = useSnackbar();

  // --- Cloud Models (LLM provider: local Ollama vs hosted Mistral) ---
  // GET /api/llm/provider is the single source of truth — held in `providerState`
  // and re-synced on mount + after every mutation.
  const [providerState, setProviderState] = useState({
    cloud_models_enabled: false,
    provider: 'ollama',
    cloud_active: false,
    mistral_model: '',
    openai_model: '',
    providers: [],
  });
  const [mistralModels, setMistralModels] = useState([]);
  const [openaiModels, setOpenaiModels] = useState([]);
  const [providerBusy, setProviderBusy] = useState(false);
  const [testingConn, setTestingConn] = useState(false);
  const [testResult, setTestResult] = useState(null); // { ok, message }

  const cloudEnabled = !!providerState.cloud_models_enabled;
  const cloudActive = !!providerState.cloud_active;
  const activeProvider = providerState.provider || 'ollama';
  const mistralProvider = (providerState.providers || []).find(
    (p) => p.id === 'mistral',
  );
  const mistralAvailable = !!mistralProvider?.available;
  const openaiProvider = (providerState.providers || []).find(
    (p) => p.id === 'openai',
  );
  const openaiAvailable = !!openaiProvider?.available;

  // Fetch the Mistral catalogue (best-effort; the toggles still work without it).
  const loadMistralModels = useCallback(async () => {
    try {
      setMistralModels(await getProviderModels('mistral'));
    } catch {
      /* model list is best-effort */
    }
  }, []);

  const loadOpenaiModels = useCallback(async () => {
    try {
      setOpenaiModels(await getProviderModels('openai'));
    } catch {
      /* model list is best-effort */
    }
  }, []);

  const loadProvider = useCallback(async () => {
    try {
      const info = await getLlmProvider();
      if (info && typeof info === 'object') {
        setProviderState((prev) => ({ ...prev, ...info }));
        if (info.cloud_models_enabled && info.provider === 'mistral') {
          loadMistralModels();
        } else if (info.cloud_models_enabled && info.provider === 'openai') {
          loadOpenaiModels();
        }
      }
    } catch (err) {
      // Endpoint missing / backend down — keep local defaults, don't spam the user.
      console.error('Failed to load LLM provider info:', err.message);
    }
  }, [loadMistralModels, loadOpenaiModels]);

  useEffect(() => {
    loadProvider();
  }, [loadProvider]);

  // Master switch: POST /api/llm/cloud-enabled — returns the new provider_state.
  const handleCloudToggle = async (e) => {
    const enabled = e.target.checked;
    setProviderBusy(true);
    setTestResult(null);
    try {
      const next = await setCloudModelsEnabled(enabled);
      if (next && typeof next === 'object') {
        setProviderState((prev) => ({ ...prev, ...next }));
      } else {
        await loadProvider();
      }
      showMessage(
        enabled
          ? 'Cloud models enabled. Chat may now be routed off-device.'
          : 'Cloud models disabled. Guaardvark is fully local again.',
        enabled ? 'warning' : 'success',
      );
      if (enabled) {
        loadMistralModels();
        loadOpenaiModels();
      }
    } catch (err) {
      showMessage(`Could not change cloud models setting: ${err.message}`, 'error');
      await loadProvider();
    } finally {
      setProviderBusy(false);
    }
  };

  // POST /api/llm/provider — set active chat provider (400 if disabled / key missing).
  const handleProviderChange = async (_e, next) => {
    if (!next || next === activeProvider) return;
    setProviderBusy(true);
    setTestResult(null);
    try {
      const state = await setLlmProvider(next);
      if (state && typeof state === 'object') {
        setProviderState((prev) => ({ ...prev, ...state }));
      } else {
        await loadProvider();
      }
      const providerMsg =
        next === 'mistral'
          ? 'Chat now routes to Mistral (cloud API).'
          : next === 'openai'
            ? 'Chat now routes to the OpenAI-compatible provider.'
            : 'Chat now uses local Ollama.';
      showMessage(providerMsg, next === 'ollama' ? 'success' : 'warning');
      if (next === 'mistral' && mistralModels.length === 0) loadMistralModels();
      if (next === 'openai' && openaiModels.length === 0) loadOpenaiModels();
    } catch (err) {
      showMessage(`Could not switch provider: ${err.message}`, 'error');
      await loadProvider();
    } finally {
      setProviderBusy(false);
    }
  };

  // POST /api/llm/provider/mistral-model — set the active Mistral model.
  const handleMistralModelChange = async (e) => {
    const model = e.target.value;
    setProviderBusy(true);
    try {
      const state = await setMistralModel(model);
      if (state && typeof state === 'object' && state.provider) {
        setProviderState((prev) => ({ ...prev, ...state }));
      } else {
        setProviderState((prev) => ({ ...prev, mistral_model: model }));
      }
      showMessage(`Mistral model set to ${model}.`, 'success');
    } catch (err) {
      showMessage(`Could not set Mistral model: ${err.message}`, 'error');
      await loadProvider();
    } finally {
      setProviderBusy(false);
    }
  };  // POST /api/llm/provider/openai-model — set the active OpenAI-compatible model.
  const handleOpenaiModelChange = async (e) => {
    const model = e.target.value;
    setProviderBusy(true);
    try {
      const state = await setOpenaiModel(model);
      if (state && typeof state === 'object' && state.provider) {
        setProviderState((prev) => ({ ...prev, ...state }));
      } else {
        setProviderState((prev) => ({ ...prev, openai_model: model }));
      }
      showMessage(`OpenAI model set to ${model}.`, 'success');
    } catch (err) {
      showMessage(`Could not set OpenAI model: ${err.message}`, 'error');
      await loadProvider();
    } finally {
      setProviderBusy(false);
    }
  };


  // POST /api/llm/provider/test — live round-trip against the active provider.
  const handleTestConnection = async () => {
    setTestingConn(true);
    setTestResult(null);
    try {
      const res = await testProviderConnection();
      if (res?.connected) {
        setTestResult({
          ok: true,
          message: `Connected${res.model ? ` (${res.model})` : ''}: "${
            res.response || 'ok'
          }"`,
        });
      } else {
        setTestResult({
          ok: false,
          message: res?.response || res?.error || 'Connection failed.',
        });
      }
    } catch (err) {
      setTestResult({ ok: false, message: err.message });
    } finally {
      setTestingConn(false);
    }
  };

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
      apiService.getAvailableModels,
      [],
      null,
      "Refreshing available models...",
      "Models refreshed successfully.",
      "Failed to refresh models",
    );
  };

  // Mistral model dropdown options: prefer the fetched catalogue, otherwise
  // fall back to whatever model is currently active so the Select stays valid.
  const mistralModelOptions = mistralModels.length
    ? mistralModels
    : providerState.mistral_model
      ? [{ name: providerState.mistral_model, id: providerState.mistral_model }]
      : [];

  const openaiModelOptions = openaiModels.length
    ? openaiModels
    : providerState.openai_model
      ? [{ name: providerState.openai_model, id: providerState.openai_model }]
      : [];

  return (
    <Paper elevation={3} sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        Model Management
      </Typography>

      {/* --- Cloud-active warning banner (persistent while cloud chat is live) --- */}
      {cloudActive && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          ⚠ Cloud model active — chat is sent to{' '}
          <strong>{activeProvider}</strong>. Embeddings &amp; RAG stay local.
        </Alert>
      )}

      {/* --- Cloud Models master toggle --- */}
      <Box sx={{ mb: 2 }}>
        <FormControlLabel
          control={
            <Switch
              checked={cloudEnabled}
              onChange={handleCloudToggle}
              disabled={providerBusy}
              color="warning"
            />
          }
          label={
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              Enable Cloud Models
            </Typography>
          }
        />
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ ml: 0.5, maxWidth: 640 }}
        >
          Off by default. When off, Guaardvark stays 100% local. Cloud models send
          chat to a hosted API — your data leaves this machine.
        </Typography>
      </Box>

      {/* --- Cloud provider controls (hidden entirely until the master is ON) --- */}
      {cloudEnabled && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="body2" color="text.secondary" gutterBottom>
            Chat Provider
          </Typography>
          <ToggleButtonGroup
            exclusive
            color="primary"
            value={activeProvider}
            onChange={handleProviderChange}
            disabled={providerBusy}
            size="small"
          >
            <ToggleButton value="ollama">Ollama (local)</ToggleButton>
            <Tooltip
              title={
                mistralAvailable
                  ? "Route chat to Mistral's hosted API"
                  : `Set ${mistralProvider?.key_env || 'MISTRAL_API_KEY'} in .env to enable`
              }
            >
              <span>
                <ToggleButton value="mistral" disabled={!mistralAvailable}>
                  Mistral (cloud)
                </ToggleButton>
              </span>
            </Tooltip>
            <Tooltip
              title={
                openaiAvailable
                  ? "Route chat to your OpenAI-compatible provider"
                  : `Set ${openaiProvider?.key_env || 'GUAARDVARK_OPENAI_API_KEY'} in .env to enable`
              }
            >
              <span>
                <ToggleButton value="openai" disabled={!openaiAvailable}>
                  OpenAI-compatible (cloud)
                </ToggleButton>
              </span>
            </Tooltip>
          </ToggleButtonGroup>

          {!mistralAvailable && (
            <Typography
              variant="caption"
              color="text.secondary"
              display="block"
              sx={{ mt: 0.5 }}
            >
              Mistral unavailable — set{' '}
              <code>{mistralProvider?.key_env || 'MISTRAL_API_KEY'}</code> in .env.
            </Typography>
          )}

          {!openaiAvailable && (
            <Typography
              variant="caption"
              color="text.secondary"
              display="block"
              sx={{ mt: 0.5 }}
            >
              OpenAI-compatible unavailable — set{' '}
              <code>{openaiProvider?.key_env || 'GUAARDVARK_OPENAI_API_KEY'}</code>
              {' '}(and/or <code>GUAARDVARK_OPENAI_BASE_URL</code>) in .env.
            </Typography>
          )}

          {activeProvider === 'mistral' && (
            <Box sx={{ mt: 2 }}>
              <Grid container spacing={2} alignItems="center">
                <Grid item xs={12} md={8}>
                  <FormControl fullWidth size="small" disabled={providerBusy}>
                    <InputLabel>Mistral Model</InputLabel>
                    <Select
                      value={providerState.mistral_model || ''}
                      label="Mistral Model"
                      onChange={handleMistralModelChange}
                    >
                      {mistralModelOptions.map((m) => (
                        <MenuItem key={m.id || m.name} value={m.name}>
                          {m.name}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12} md={4}>
                  <Button
                    variant="outlined"
                    fullWidth
                    onClick={handleTestConnection}
                    disabled={testingConn || providerBusy}
                  >
                    {testingConn ? (
                      <CircularProgress size={22} />
                    ) : (
                      'Test Connection'
                    )}
                  </Button>
                </Grid>
              </Grid>
              {testResult && (
                <Alert
                  severity={testResult.ok ? 'success' : 'error'}
                  sx={{ mt: 2 }}
                >
                  {testResult.message}
                </Alert>
              )}
            </Box>
          )}

          {activeProvider === 'openai' && (
            <Box sx={{ mt: 2 }}>
              <Grid container spacing={2} alignItems="center">
                <Grid item xs={12} md={8}>
                  <FormControl fullWidth size="small" disabled={providerBusy}>
                    <InputLabel>OpenAI-compatible Model</InputLabel>
                    <Select
                      value={providerState.openai_model || ''}
                      label="OpenAI-compatible Model"
                      onChange={handleOpenaiModelChange}
                    >
                      {openaiModelOptions.map((m) => (
                        <MenuItem key={m.id || m.name} value={m.name}>
                          {m.name}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12} md={4}>
                  <Button
                    variant="outlined"
                    fullWidth
                    onClick={handleTestConnection}
                    disabled={testingConn || providerBusy}
                  >
                    {testingConn ? (
                      <CircularProgress size={22} />
                    ) : (
                      'Test Connection'
                    )}
                  </Button>
                </Grid>
              </Grid>
              {testResult && (
                <Alert
                  severity={testResult.ok ? 'success' : 'error'}
                  sx={{ mt: 2 }}
                >
                  {testResult.message}
                </Alert>
              )}
            </Box>
          )}
        </Box>
      )}

      <Divider sx={{ mb: 2 }} />

      <Typography variant="body2" color="text.secondary" gutterBottom>
        {cloudActive
          ? 'Local Ollama models (active when chat provider is set back to Ollama):'
          : 'Local Ollama models:'}
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
              {availableModels.map((model) => {
                const label = model?.name || model;
                return (
                  <MenuItem key={label} value={label}>
                    {label}
                  </MenuItem>
                );
              })}
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
