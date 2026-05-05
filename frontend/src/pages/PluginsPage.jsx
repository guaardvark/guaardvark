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
  Videocam as CameraOnIcon,
  VideocamOff as CameraOffIcon,
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
  getLiveGpuStats,
  startVisionCamera,
  stopVisionCamera,
  getVisionCameraStatus,
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

// Plugins that require exclusive GPU access (only one can run at a time)
const GPU_EXCLUSIVE_PLUGIN_IDS = new Set(['ollama', 'comfyui']);

// ── VRAM Budget Bar ────────────────────────────────────────────────────
const VramBudgetBar = ({ plugins }) => {
  const [gpuStats, setGpuStats] = useState(null);

  useEffect(() => {
    const fetchGpu = async () => {
      try {
        const res = await getLiveGpuStats();
        if (res.success) setGpuStats(res.data);
      } catch { /* ignore */ }
    };
    fetchGpu();
    const interval = setInterval(fetchGpu, 5000);
    return () => clearInterval(interval);
  }, []);

  const totalMb = gpuStats?.total_mb || TOTAL_VRAM_MB;
  const usedMb = gpuStats?.used_mb || 0;
  const freeMb = gpuStats?.free_mb || totalMb;
  const usedPct = (usedMb / totalMb) * 100;

  const activePlugins = plugins.filter(
    (p) => p.status === 'running' && p.vram_estimate_mb > 0
  );

  return (
    <Paper sx={{ p: 2, mb: 3 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <GpuIcon fontSize="small" />
          <Typography variant="subtitle2">
            GPU VRAM {gpuStats?.gpu_name ? `— ${gpuStats.gpu_name}` : ''}
          </Typography>
        </Box>
        <Stack direction="row" spacing={2} alignItems="center">
          {gpuStats && (
            <>
              <Chip
                size="small"
                label={`${gpuStats.utilization_pct}% util`}
                color={gpuStats.utilization_pct > 80 ? 'warning' : 'default'}
                variant="outlined"
              />
              <Chip
                size="small"
                label={`${gpuStats.temperature_c}°C`}
                color={gpuStats.temperature_c > 80 ? 'error' : 'default'}
                variant="outlined"
              />
            </>
          )}
          <Typography variant="body2" color="text.secondary">
            {(usedMb / 1024).toFixed(1)} / {(totalMb / 1024).toFixed(1)} GB
          </Typography>
        </Stack>
      </Box>

      {/* Live usage bar */}
      <Box
        sx={{
          height: 24,
          borderRadius: 1,
          bgcolor: 'action.hover',
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        <Box
          sx={{
            width: `${usedPct}%`,
            height: '100%',
            bgcolor: usedPct > 90 ? 'error.main' : usedPct > 70 ? 'warning.main' : 'primary.main',
            transition: 'width 0.5s ease',
          }}
        />
        {/* Estimated segments overlay */}
        <Box
          sx={{
            position: 'absolute',
            top: 0,
            left: 0,
            height: '100%',
            display: 'flex',
            pointerEvents: 'none',
          }}
        >
          {activePlugins.map((p) => {
            const pct = (p.vram_estimate_mb / totalMb) * 100;
            return (
              <Box
                key={p.id}
                sx={{
                  width: `${pct}%`,
                  height: '100%',
                  borderRight: '2px solid rgba(255,255,255,0.4)',
                }}
              />
            );
          })}
        </Box>
      </Box>

      {/* Legend + free VRAM */}
      <Stack direction="row" spacing={2} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
          {(freeMb / 1024).toFixed(1)} GB free
        </Typography>
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
              {p.name} (~{(p.vram_estimate_mb / 1024).toFixed(1)}GB est.)
            </Typography>
          </Box>
        ))}
      </Stack>

      {usedPct > 90 && (
        <Alert severity="warning" sx={{ mt: 1 }} variant="outlined">
          VRAM usage is near capacity. Stop unused services from this page to free memory.
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
const PluginCard = ({ plugin, onAction, onConfigOpen, showMessage }) => {
  const [expanded, setExpanded] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);

  // Camera state (vision_pipeline only)
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraLoading, setCameraLoading] = useState(false);

  useEffect(() => {
    if (plugin.id !== 'vision_pipeline' || plugin.status !== 'running') {
      setCameraActive(false);
      return;
    }
    const fetchStatus = async () => {
      try {
        const resp = await getVisionCameraStatus();
        setCameraActive(resp?.active || false);
      } catch {
        setCameraActive(false);
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, [plugin.id, plugin.status]);

  const handleCameraToggle = async () => {
    setCameraLoading(true);
    try {
      if (cameraActive) {
        await stopVisionCamera();
        setCameraActive(false);
        if (showMessage) showMessage('Camera stopped', 'info');
      } else {
        await startVisionCamera(0);
        setCameraActive(true);
        if (showMessage) showMessage('Camera started', 'success');
      }
    } catch (err) {
      if (showMessage) showMessage(err.message || 'Camera action failed', 'error');
    } finally {
      setCameraLoading(false);
    }
  };

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

  return (
    <Card
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        // Opacity reflects the actual running state, not the enabled config flag.
        // Stopped plugins look dim regardless of whether they're "enabled" — what
        // matters to the user is whether the plugin is actually doing anything.
        opacity: plugin.status === 'running' ? 1 : 0.6,
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
        <Tooltip
          title={
            (plugin.cooldown_remaining || 0) > 0
              ? `Cooling down — wait ${Math.ceil(plugin.cooldown_remaining)}s before toggling again`
              : plugin.status !== 'running' && plugin.user_enabled === false
                ? 'Currently disabled. Toggling on will both start it AND re-enable it across restarts.'
                : 'Preference saved across restarts'
          }
          arrow
        >
          <FormControlLabel
            control={
              <Switch
                checked={plugin.status === 'running'}
                // 'disable' stops the service AND persists the off-state in
                // data/plugin_state.json's user_enabled overlay (NOT plugin.json,
                // which stays the canonical default in git). start.sh and the
                // backend both consult that overlay before plugin.json defaults,
                // so a plugin toggled off here stays off across reboots.
                onChange={() => handleAction(plugin.status === 'running' ? 'disable' : 'start')}
                disabled={
                  isLoading
                  || plugin.status === 'starting'
                  || plugin.status === 'stopping'
                  || (plugin.cooldown_remaining || 0) > 0
                }
                size="small"
                color="success"
              />
            }
            label={
              (plugin.cooldown_remaining || 0) > 0
                ? `${plugin.status === 'running' ? 'On' : 'Off'} (cooling ${Math.ceil(plugin.cooldown_remaining)}s)`
                : (plugin.status === 'running' ? 'On' : 'Off')
            }
          />
        </Tooltip>

        <Box sx={{ display: 'flex', gap: 0.5 }}>

          {plugin.id === 'vision_pipeline' && plugin.status === 'running' && (
            <Tooltip title={cameraActive ? 'Stop Camera' : 'Start Camera'}>
              <IconButton
                size="small"
                color={cameraActive ? 'primary' : 'default'}
                onClick={handleCameraToggle}
                disabled={cameraLoading}
              >
                {cameraLoading ? (
                  <CircularProgress size={20} />
                ) : cameraActive ? (
                  <CameraOnIcon />
                ) : (
                  <CameraOffIcon />
                )}
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

// ── GPU Conflict Dialog ────────────────────────────────────────────────
const GpuConflictDialog = ({ open, onClose, onConfirm, requestedPlugin, conflictingPlugin, loading }) => {
  if (!requestedPlugin || !conflictingPlugin) return null;

  return (
    <Dialog open={open} onClose={loading ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <GpuIcon color="warning" />
        GPU Conflict
      </DialogTitle>
      <DialogContent>
        <Typography variant="body1" sx={{ mb: 2 }}>
          <strong>{requestedPlugin.name}</strong> requires GPU memory that is currently in use
          by <strong>{conflictingPlugin.name}</strong>.
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Only one GPU-intensive service can run at a time on this 16 GB card.
        </Typography>
        <Alert severity="info" variant="outlined" sx={{ mt: 1 }}>
          {conflictingPlugin.name} will be stopped first, then {requestedPlugin.name} will start.
        </Alert>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>Cancel</Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          color="warning"
          disabled={loading}
          startIcon={loading ? <CircularProgress size={16} /> : null}
        >
          {loading ? 'Switching...' : `Stop ${conflictingPlugin.name} & Start ${requestedPlugin.name}`}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Restart Ollama Dialog ─────────────────────────────────────────────
const RestartOllamaDialog = ({ open, onClose, onConfirm, loading }) => {
  return (
    <Dialog open={open} onClose={loading ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Restart Ollama?</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary">
          ComfyUI has been stopped. Would you like to restart Ollama so that
          chat, RAG, and other AI features are available again?
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>No thanks</Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          disabled={loading}
          startIcon={loading ? <CircularProgress size={16} /> : <StartIcon />}
        >
          {loading ? 'Starting...' : 'Start Ollama'}
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

  // GPU conflict dialog state
  const [gpuConflict, setGpuConflict] = useState(null); // { requestedId, conflictingId }
  const [gpuSwitchLoading, setGpuSwitchLoading] = useState(false);

  // Restart Ollama dialog state (shown after stopping ComfyUI)
  const [showRestartOllama, setShowRestartOllama] = useState(false);
  const [restartOllamaLoading, setRestartOllamaLoading] = useState(false);

  const fetchPlugins = useCallback(async () => {
    try {
      setError(null);
      const response = await listPlugins();
      if (response.success) {
        const list = response.data.plugins || [];
        setPlugins(list);
        // Return the fresh list so callers awaiting a refresh can read it
        // without racing the React state update (which is async and would
        // hand them the pre-refresh array via the closure).
        return list;
      } else {
        setError(response.message || 'Failed to load plugins');
      }
    } catch (err) {
      setError(err.message || 'Failed to load plugins');
    } finally {
      setLoading(false);
    }
    return null;
  }, []);

  useEffect(() => {
    fetchPlugins();
    const interval = setInterval(fetchPlugins, 10000);
    return () => clearInterval(interval);
  }, [fetchPlugins]);

  // Find running GPU-exclusive plugins that conflict with a given plugin
  const findGpuConflict = useCallback((pluginId) => {
    if (!GPU_EXCLUSIVE_PLUGIN_IDS.has(pluginId)) return null;
    return plugins.find(
      (p) => p.id !== pluginId && GPU_EXCLUSIVE_PLUGIN_IDS.has(p.id) && p.status === 'running'
    );
  }, [plugins]);

  const handlePluginAction = async (pluginId, action) => {
    // Intercept start action for GPU plugins — check for conflicts
    if (action === 'start' && GPU_EXCLUSIVE_PLUGIN_IDS.has(pluginId)) {
      const conflict = findGpuConflict(pluginId);
      if (conflict) {
        setGpuConflict({ requestedId: pluginId, conflictingId: conflict.id });
        return; // Don't start yet — wait for dialog confirmation
      }
    }

    try {
      let response;
      switch (action) {
        case 'start':
          // Auto-enable if plugin is disabled so start doesn't get rejected
          if (!plugins.find((p) => p.id === pluginId)?.enabled) {
            await enablePlugin(pluginId);
          }
          response = await startPlugin(pluginId);
          break;
        case 'stop': response = await stopPlugin(pluginId); break;
        case 'enable': response = await enablePlugin(pluginId); break;
        case 'disable': response = await disablePlugin(pluginId); break;
        default: throw new Error(`Unknown action: ${action}`);
      }
      if (response.success) {
        // Backend returned 200. Two sub-cases:
        //  1. Operation succeeded → data.success === true
        //  2. Operation was rate-limited by the traffic light → data.gated === true
        const data = response.data || {};
        if (data.gated) {
          // Friendly cooldown notice, not an error
          const cd = data.cooldown_remaining ? Math.ceil(data.cooldown_remaining) : null;
          const suffix = cd ? ` (wait ${cd}s)` : '';
          showMessage(`${data.error || 'Cooling down'}${suffix}`, 'warning');
          await fetchPlugins(); // refresh to pick up the cooldown_remaining
          return;
        }
        showMessage(response.message || `Plugin ${action} successful`, 'success');
        const fresh = await fetchPlugins();

        // After stopping ComfyUI, offer to restart Ollama if it's not running.
        // Read from the *fresh* plugin list returned by fetchPlugins — the
        // `plugins` state variable still holds the pre-stop snapshot here
        // because React hasn't re-rendered yet.
        if (action === 'stop' && pluginId === 'comfyui') {
          const list = fresh || plugins;
          const ollamaPlugin = list.find((p) => p.id === 'ollama');
          if (ollamaPlugin && ollamaPlugin.enabled && ollamaPlugin.status !== 'running') {
            setShowRestartOllama(true);
          }
        }
      } else {
        showMessage(response.message || `Failed to ${action} plugin`, 'error');
      }
    } catch (err) {
      showMessage(err.message || `Failed to ${action} plugin`, 'error');
    }
  };

  // Handle GPU conflict confirmation — stop conflicting plugin, then start requested
  const handleGpuConflictConfirm = async () => {
    if (!gpuConflict) return;
    const { requestedId, conflictingId } = gpuConflict;
    setGpuSwitchLoading(true);

    try {
      // Step 1: Stop the conflicting plugin
      const stopResponse = await stopPlugin(conflictingId);
      if (!stopResponse.success) {
        showMessage(
          `Failed to stop ${conflictingId}: ${stopResponse.message || 'Unknown error'}`,
          'error'
        );
        return;
      }
      showMessage(`${conflictingId} stopped`, 'info');

      // Brief pause to allow GPU memory to be released
      await new Promise((r) => setTimeout(r, 2000));

      // Step 2: Start the requested plugin
      const startResponse = await startPlugin(requestedId);
      if (startResponse.success) {
        showMessage(startResponse.message || `${requestedId} started`, 'success');
      } else {
        showMessage(
          `${conflictingId} was stopped but ${requestedId} failed to start: ${startResponse.message || 'Unknown error'}`,
          'error'
        );
      }
    } catch (err) {
      showMessage(err.message || 'GPU switch failed', 'error');
    } finally {
      setGpuSwitchLoading(false);
      setGpuConflict(null);
      fetchPlugins();
    }
  };

  // Handle Restart Ollama confirmation
  const handleRestartOllama = async () => {
    setRestartOllamaLoading(true);
    try {
      // Make sure Ollama is enabled first
      const ollamaPlugin = plugins.find((p) => p.id === 'ollama');
      if (ollamaPlugin && !ollamaPlugin.enabled) {
        await enablePlugin('ollama');
      }
      const response = await startPlugin('ollama');
      if (response.success) {
        showMessage('Ollama started', 'success');
      } else {
        showMessage(response.message || 'Failed to start Ollama', 'error');
      }
    } catch (err) {
      showMessage(err.message || 'Failed to start Ollama', 'error');
    } finally {
      setRestartOllamaLoading(false);
      setShowRestartOllama(false);
      fetchPlugins();
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

  // Resolve plugin objects for the conflict dialog
  const conflictRequestedPlugin = gpuConflict
    ? plugins.find((p) => p.id === gpuConflict.requestedId)
    : null;
  const conflictConflictingPlugin = gpuConflict
    ? plugins.find((p) => p.id === gpuConflict.conflictingId)
    : null;

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
                showMessage={showMessage}
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

      {/* GPU Conflict Confirmation Dialog */}
      <GpuConflictDialog
        open={gpuConflict !== null}
        onClose={() => setGpuConflict(null)}
        onConfirm={handleGpuConflictConfirm}
        requestedPlugin={conflictRequestedPlugin}
        conflictingPlugin={conflictConflictingPlugin}
        loading={gpuSwitchLoading}
      />

      {/* Restart Ollama Dialog (after ComfyUI stopped) */}
      <RestartOllamaDialog
        open={showRestartOllama}
        onClose={() => setShowRestartOllama(false)}
        onConfirm={handleRestartOllama}
        loading={restartOllamaLoading}
      />
    </PageLayout>
  );
};

export default PluginsPage;
