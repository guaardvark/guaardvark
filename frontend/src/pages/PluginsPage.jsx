// frontend/src/pages/PluginsPage.jsx
/**
 * Plugin Management Page
 * Plugin cards with on/off toggles, VRAM budget bar, and per-plugin log viewer.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Box,
  Typography,
  Paper,
  Grid,
  Card,
  CardContent,
  CardActions,
  Button,
  Chip,
  Switch,
  FormControlLabel,
  IconButton,
  Tooltip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  CircularProgress,
  Alert,
  Divider,
  Stack,
  Collapse,
} from '@mui/material';
import {
  PlayArrow as StartIcon,
  Stop as StopIcon,
  Refresh as RefreshIcon,
  Settings as SettingsIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  CheckCircle as HealthyIcon,
  Error as ErrorIcon,
  HelpOutline as UnknownIcon,
  Memory as GpuIcon,
  Extension as PluginIcon,
  Terminal as LogIcon,
} from '@mui/icons-material';
import { useSnackbar } from '../components/common/SnackbarProvider';
import PageLayout from '../components/layout/PageLayout';
import { ContextualLoader } from '../components/common/LoadingStates';
import {
  listPlugins,
  startPlugin,
  stopPlugin,
  enablePlugin,
  disablePlugin,
  refreshPlugins,
  updatePluginConfig,
  getPluginLogs,
} from '../api/pluginsService';

// ── Constants ──────────────────────────────────────────────────────────
const TOTAL_VRAM_MB = 16384; // 16GB
const STATUS_CONFIG = {
  running: { color: 'success', icon: HealthyIcon, label: 'Running' },
  stopped: { color: 'default', icon: StopIcon, label: 'Stopped' },
  starting: { color: 'warning', icon: CircularProgress, label: 'Starting' },
  stopping: { color: 'warning', icon: CircularProgress, label: 'Stopping' },
  error: { color: 'error', icon: ErrorIcon, label: 'Error' },
  disabled: { color: 'default', icon: UnknownIcon, label: 'Disabled' },
  unknown: { color: 'default', icon: UnknownIcon, label: 'Unknown' },
};

const PLUGIN_COLORS = {
  ollama: '#7c4dff',
  comfyui: '#00c853',
  gpu_embedding: '#ff6d00',
};

// ── VRAM Budget Bar ────────────────────────────────────────────────────
const VramBudgetBar = ({ plugins }) => {
  const activePlugins = plugins.filter(
    (p) => p.status === 'running' && p.vram_estimate_mb > 0
  );
  const usedMb = activePlugins.reduce((sum, p) => sum + p.vram_estimate_mb, 0);

  return (
    <Paper sx={{ p: 2, mb: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <GpuIcon fontSize="small" />
          <Typography variant="subtitle2">GPU VRAM Budget</Typography>
        </Box>
        <Typography variant="body2" color="text.secondary">
          {(usedMb / 1024).toFixed(1)} / {(TOTAL_VRAM_MB / 1024).toFixed(0)} GB
        </Typography>
      </Box>

      {/* Stacked bar */}
      <Box
        sx={{
          height: 24,
          borderRadius: 1,
          bgcolor: 'action.hover',
          overflow: 'hidden',
          display: 'flex',
        }}
      >
        {activePlugins.map((p) => {
          const pct = (p.vram_estimate_mb / TOTAL_VRAM_MB) * 100;
          return (
            <Tooltip
              key={p.id}
              title={`${p.name}: ~${(p.vram_estimate_mb / 1024).toFixed(1)} GB`}
            >
              <Box
                sx={{
                  width: `${pct}%`,
                  height: '100%',
                  bgcolor: PLUGIN_COLORS[p.id] || '#90a4ae',
                  transition: 'width 0.3s ease',
                  '&:hover': { opacity: 0.85 },
                }}
              />
            </Tooltip>
          );
        })}
      </Box>

      {/* Legend */}
      {activePlugins.length > 0 && (
        <Stack direction="row" spacing={2} sx={{ mt: 1 }}>
          {activePlugins.map((p) => (
            <Box key={p.id} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Box
                sx={{
                  width: 10,
                  height: 10,
                  borderRadius: '50%',
                  bgcolor: PLUGIN_COLORS[p.id] || '#90a4ae',
                }}
              />
              <Typography variant="caption" color="text.secondary">
                {p.name} (~{(p.vram_estimate_mb / 1024).toFixed(1)}GB)
              </Typography>
            </Box>
          ))}
        </Stack>
      )}

      {usedMb / TOTAL_VRAM_MB > 0.9 && (
        <Alert severity="warning" sx={{ mt: 1 }} variant="outlined">
          VRAM usage is near capacity. Running all plugins simultaneously may cause out-of-memory errors.
        </Alert>
      )}
    </Paper>
  );
};

// ── Log Viewer ─────────────────────────────────────────────────────────
const LogViewer = ({ pluginId, open }) => {
  const [logs, setLogs] = useState('');
  const [loading, setLoading] = useState(false);
  const logRef = useRef(null);

  const fetchLogs = useCallback(async () => {
    if (!open) return;
    setLoading(true);
    try {
      const response = await getPluginLogs(pluginId, 200);
      if (response.success) {
        setLogs(response.data.logs || '(no logs)');
      }
    } catch {
      setLogs('(failed to fetch logs)');
    } finally {
      setLoading(false);
    }
  }, [pluginId, open]);

  useEffect(() => {
    fetchLogs();
    if (!open) return;
    const interval = setInterval(fetchLogs, 5000);
    return () => clearInterval(interval);
  }, [fetchLogs, open]);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  if (!open) return null;

  return (
    <Box sx={{ mt: 1 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.5 }}>
        <Typography variant="caption" color="text.secondary">
          Logs (last 200 lines)
        </Typography>
        <IconButton size="small" onClick={fetchLogs} disabled={loading}>
          <RefreshIcon fontSize="small" />
        </IconButton>
      </Box>
      <Box
        ref={logRef}
        sx={{
          maxHeight: 240,
          overflow: 'auto',
          bgcolor: '#1e1e1e',
          color: '#d4d4d4',
          fontFamily: 'monospace',
          fontSize: '0.75rem',
          p: 1.5,
          borderRadius: 1,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
        }}
      >
        {loading && !logs ? 'Loading...' : logs}
      </Box>
    </Box>
  );
};

// ── Plugin Card ────────────────────────────────────────────────────────
const PluginCard = ({ plugin, onAction, onConfigOpen }) => {
  const [expanded, setExpanded] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);

  const statusConfig = STATUS_CONFIG[plugin.status] || STATUS_CONFIG.unknown;
  const StatusIcon = statusConfig.icon;
  const accentColor = PLUGIN_COLORS[plugin.id] || '#90a4ae';

  const handleAction = async (action) => {
    setActionLoading(action);
    try {
      await onAction(plugin.id, action);
    } finally {
      setActionLoading(null);
    }
  };

  const isLoading = actionLoading !== null;
  const canStart = plugin.enabled && plugin.status === 'stopped';
  const canStop = plugin.status === 'running';

  return (
    <Card
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        opacity: plugin.enabled ? 1 : 0.6,
        borderLeft: `4px solid ${plugin.status === 'running' ? accentColor : 'transparent'}`,
      }}
    >
      <CardContent sx={{ flexGrow: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', mb: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <PluginIcon sx={{ color: accentColor }} />
            <Typography variant="h6" component="div">
              {plugin.name}
            </Typography>
          </Box>
          <Chip
            size="small"
            label={statusConfig.label}
            color={statusConfig.color}
            icon={
              plugin.status === 'starting' || plugin.status === 'stopping' ? (
                <CircularProgress size={12} color="inherit" />
              ) : (
                <StatusIcon fontSize="small" />
              )
            }
          />
        </Box>

        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {plugin.description || 'No description available.'}
        </Typography>

        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
          <Chip size="small" label={`v${plugin.version}`} variant="outlined" />
          <Chip size="small" label={plugin.type} variant="outlined" />
          {plugin.port && (
            <Chip size="small" label={`Port ${plugin.port}`} variant="outlined" />
          )}
          {plugin.vram_estimate_mb > 0 && (
            <Chip
              size="small"
              icon={<GpuIcon />}
              label={`~${(plugin.vram_estimate_mb / 1024).toFixed(1)} GB VRAM`}
              variant="outlined"
              color={plugin.status === 'running' ? 'primary' : 'default'}
            />
          )}
        </Stack>

        <Collapse in={expanded}>
          <Divider sx={{ my: 1.5 }} />
          <Typography variant="body2" color="text.secondary">
            Category: {plugin.category}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            ID: {plugin.id}
          </Typography>
          {plugin.plugin_dir && (
            <Typography variant="body2" color="text.secondary" sx={{ wordBreak: 'break-all' }}>
              Path: {plugin.plugin_dir}
            </Typography>
          )}
        </Collapse>

        <Collapse in={logsOpen}>
          <LogViewer pluginId={plugin.id} open={logsOpen} />
        </Collapse>
      </CardContent>

      <Divider />

      <CardActions sx={{ justifyContent: 'space-between', px: 2 }}>
        <FormControlLabel
          control={
            <Switch
              checked={plugin.enabled}
              onChange={() => handleAction(plugin.enabled ? 'disable' : 'enable')}
              disabled={isLoading}
              size="small"
            />
          }
          label="Enabled"
        />

        <Box sx={{ display: 'flex', gap: 0.5 }}>
          {canStart && (
            <Tooltip title="Start">
              <IconButton
                size="small"
                color="success"
                onClick={() => handleAction('start')}
                disabled={isLoading}
              >
                {actionLoading === 'start' ? <CircularProgress size={20} /> : <StartIcon />}
              </IconButton>
            </Tooltip>
          )}
          {canStop && (
            <Tooltip title="Stop">
              <IconButton
                size="small"
                color="error"
                onClick={() => handleAction('stop')}
                disabled={isLoading}
              >
                {actionLoading === 'stop' ? <CircularProgress size={20} /> : <StopIcon />}
              </IconButton>
            </Tooltip>
          )}

          <Tooltip title="Logs">
            <IconButton size="small" onClick={() => setLogsOpen(!logsOpen)}>
              <LogIcon fontSize="small" color={logsOpen ? 'primary' : 'action'} />
            </IconButton>
          </Tooltip>

          <Tooltip title="Settings">
            <IconButton size="small" onClick={() => onConfigOpen(plugin)} disabled={isLoading}>
              <SettingsIcon />
            </IconButton>
          </Tooltip>

          <Tooltip title={expanded ? 'Less' : 'More'}>
            <IconButton size="small" onClick={() => setExpanded(!expanded)}>
              {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </IconButton>
          </Tooltip>
        </Box>
      </CardActions>
    </Card>
  );
};

// ── Config Dialog ──────────────────────────────────────────────────────
const ConfigDialog = ({ open, plugin, onClose, onSave }) => {
  const [config, setConfig] = useState({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (plugin?.config) setConfig({ ...plugin.config });
  }, [plugin]);

  const handleSave = async () => {
    setLoading(true);
    try {
      await onSave(plugin.id, config);
      onClose();
    } finally {
      setLoading(false);
    }
  };

  if (!plugin) return null;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Configure: {plugin.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {plugin.config?.service_url !== undefined && (
            <TextField
              label="Service URL"
              value={config.service_url || ''}
              onChange={(e) => setConfig({ ...config, service_url: e.target.value })}
              fullWidth
              size="small"
            />
          )}
          {plugin.config?.timeout !== undefined && (
            <TextField
              label="Timeout (seconds)"
              type="number"
              value={config.timeout || 30}
              onChange={(e) => setConfig({ ...config, timeout: parseInt(e.target.value) || 30 })}
              fullWidth
              size="small"
            />
          )}
          <FormControlLabel
            control={
              <Switch
                checked={config.fallback_enabled ?? true}
                onChange={(e) => setConfig({ ...config, fallback_enabled: e.target.checked })}
              />
            }
            label="Enable fallback to CPU"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>Cancel</Button>
        <Button onClick={handleSave} variant="contained" disabled={loading}>
          {loading ? <CircularProgress size={20} /> : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Page ───────────────────────────────────────────────────────────────
const PluginsPage = () => {
  const [plugins, setPlugins] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [configPlugin, setConfigPlugin] = useState(null);
  const { showMessage } = useSnackbar();

  const fetchPlugins = useCallback(async () => {
    try {
      setError(null);
      const response = await listPlugins();
      if (response.success) {
        setPlugins(response.data.plugins || []);
      } else {
        setError(response.message || 'Failed to load plugins');
      }
    } catch (err) {
      setError(err.message || 'Failed to load plugins');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPlugins();
    const interval = setInterval(fetchPlugins, 10000);
    return () => clearInterval(interval);
  }, [fetchPlugins]);

  const handlePluginAction = async (pluginId, action) => {
    try {
      let response;
      switch (action) {
        case 'start': response = await startPlugin(pluginId); break;
        case 'stop': response = await stopPlugin(pluginId); break;
        case 'enable': response = await enablePlugin(pluginId); break;
        case 'disable': response = await disablePlugin(pluginId); break;
        default: throw new Error(`Unknown action: ${action}`);
      }
      if (response.success) {
        showMessage(response.message || `Plugin ${action} successful`, 'success');
        fetchPlugins();
      } else {
        showMessage(response.message || `Failed to ${action} plugin`, 'error');
      }
    } catch (err) {
      showMessage(err.message || `Failed to ${action} plugin`, 'error');
    }
  };

  const handleRefresh = async () => {
    try {
      const response = await refreshPlugins();
      if (response.success) {
        showMessage(`Found ${response.data.count} plugins`, 'success');
        fetchPlugins();
      } else {
        showMessage('Failed to refresh plugins', 'error');
      }
    } catch (err) {
      showMessage(err.message || 'Failed to refresh plugins', 'error');
    }
  };

  const handleConfigSave = async (pluginId, config) => {
    try {
      const response = await updatePluginConfig(pluginId, config);
      if (response.success) {
        showMessage('Configuration saved', 'success');
        fetchPlugins();
      } else {
        showMessage('Failed to save configuration', 'error');
      }
    } catch (err) {
      showMessage(err.message || 'Failed to save configuration', 'error');
    }
  };

  return (
    <PageLayout
      title="Plugins"
      variant="standard"
      actions={
        <Button
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={handleRefresh}
          disabled={loading}
        >
          Refresh
        </Button>
      }
    >
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Manage GPU services and optional features. Toggle plugins on/off to control VRAM usage.
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert>
      )}

      {/* VRAM Budget Bar */}
      {plugins.length > 0 && <VramBudgetBar plugins={plugins} />}

      {loading && plugins.length === 0 ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
          <ContextualLoader loading message="Loading plugins..." showProgress={false} inline />
        </Box>
      ) : plugins.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <PluginIcon sx={{ fontSize: 64, color: 'text.secondary', mb: 2 }} />
          <Typography variant="h6" gutterBottom>No Plugins Found</Typography>
          <Typography variant="body2" color="text.secondary">
            Add plugins to the /plugins/ directory and click Refresh.
          </Typography>
        </Paper>
      ) : (
        <Grid container spacing={3}>
          {plugins.map((plugin) => (
            <Grid item xs={12} sm={6} md={4} key={plugin.id}>
              <PluginCard
                plugin={plugin}
                onAction={handlePluginAction}
                onConfigOpen={setConfigPlugin}
              />
            </Grid>
          ))}
        </Grid>
      )}

      <ConfigDialog
        open={configPlugin !== null}
        plugin={configPlugin}
        onClose={() => setConfigPlugin(null)}
        onSave={handleConfigSave}
      />
    </PageLayout>
  );
};

export default PluginsPage;
