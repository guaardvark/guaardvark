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
  InputAdornment,
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
  Visibility as VisibilityIcon,
  VisibilityOff as VisibilityOffIcon,
} from '@mui/icons-material';
import { useSnackbar } from '../components/common/SnackbarProvider';
import PageLayout from '../components/layout/PageLayout';
import { ContextualLoader } from '../components/common/LoadingStates';
import EntityContextMenu from '../components/common/EntityContextMenu';
import useContextMenu from '../hooks/useContextMenu';
import { io } from 'socket.io-client';
import { SOCKET_URL } from '../api/apiClient';
import {
  listPlugins,
  startPlugin,
  stopPlugin,
  enablePlugin,
  disablePlugin,
  refreshPlugins,
  updatePluginConfig,
  getPluginLogs,
  startVisionCamera,
  stopVisionCamera,
  getVisionCameraStatus,
} from '../api/pluginsService';
import { getGpuStatus } from '../api/gpuService';
import OllamaLifecycleSection from '../components/settings/OllamaLifecycleSection';
import { ComfyUpdateSection, useComfyUpdate } from '../components/plugins/ComfyUpdate';
import { translate, useUiTranslation } from '../i18n';

// ── Constants ──────────────────────────────────────────────────────────
const STATUS_CONFIG = {
  running: { color: 'success', icon: HealthyIcon, label: () => translate('plugins.status.running') },
  stopped: { color: 'default', icon: StopIcon, label: () => translate('plugins.status.stopped') },
  starting: { color: 'warning', icon: CircularProgress, label: () => translate('plugins.status.starting') },
  stopping: { color: 'warning', icon: CircularProgress, label: () => translate('plugins.status.stopping') },
  error: { color: 'error', icon: ErrorIcon, label: () => translate('plugins.status.error') },
  disabled: { color: 'default', icon: UnknownIcon, label: () => translate('plugins.status.disabled') },
  unknown: { color: 'default', icon: UnknownIcon, label: () => translate('plugins.status.unknown') },
};

// The message for each plugin action the page sends.
const actionSucceeded = (action) =>
  ({
    start: translate('plugins.page.actionSucceeded.start'),
    stop: translate('plugins.page.actionSucceeded.stop'),
    enable: translate('plugins.page.actionSucceeded.enable'),
    disable: translate('plugins.page.actionSucceeded.disable'),
  })[action];

const actionFailed = (action) =>
  ({
    start: translate('plugins.page.actionFailed.start'),
    stop: translate('plugins.page.actionFailed.stop'),
    enable: translate('plugins.page.actionFailed.enable'),
    disable: translate('plugins.page.actionFailed.disable'),
  })[action];

const PLUGIN_COLORS = {
  ollama: '#7c4dff',
  comfyui: '#00c853',
  gpu_embedding: '#ff6d00',
};

// A plugin is "GPU-heavy", and so mutually exclusive with the other heavy
// ones, when the backend says so: `gpu_exclusive` in the listing comes from
// GPU_EXCLUSIVE_PLUGIN_IDS in backend/plugins/plugin_manager.py, the same set
// the operation gate and the route orchestrator use. A local copy drifted
// from it (a VRAM threshold flagged plugins the gate did not).
const isGpuHeavy = (plugin) => Boolean(plugin?.gpu_exclusive);

// ── VRAM Budget Bar ────────────────────────────────────────────────────
const VramBudgetBar = ({ plugins, gpuVram }) => {
  const { t } = useUiTranslation();
  // The GPU query (/api/gpu/memory/status, then gpu:status pushes) is the only
  // source for these numbers. When it has not answered, or the backend
  // reports a failed query (null numbers), the card says "unknown" for the
  // total and for free VRAM instead of assuming a card size or printing 0.
  const totalMb = Number(gpuVram?.total_mb) || 0;
  const vramKnown = totalMb > 0;
  const usedMb = gpuVram?.used_mb || 0;
  const freeMb = vramKnown ? (gpuVram?.free_mb ?? totalMb - usedMb) : null;
  const usedPct = vramKnown ? (usedMb / totalMb) * 100 : 0;

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
            {t('plugins.vram.title')}{' '}
            {gpuVram?.gpu_name ? `— ${gpuVram.gpu_name}` : ''}
          </Typography>
        </Box>
        <Stack direction="row" spacing={2} alignItems="center">
          {gpuVram?.utilization_percent != null && (
            <Chip
              size="small"
              label={t('plugins.vram.utilization', { percent: gpuVram.utilization_percent })}
              color={gpuVram.utilization_percent > 80 ? 'warning' : 'default'}
              variant="outlined"
            />
          )}
          <Typography variant="body2" color="text.secondary" data-testid="vram-totals">
            {vramKnown
              ? t('plugins.vram.totals', {
                  used: (usedMb / 1024).toFixed(1),
                  total: (totalMb / 1024).toFixed(1),
                })
              : t('plugins.vram.totalUnknown')}
          </Typography>
        </Stack>
      </Box>

      {/* Live usage bar */}
      {vramKnown && (
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
      )}

      {/* Legend + free VRAM */}
      <Stack direction="row" spacing={2} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
          {freeMb != null
            ? t('plugins.vram.free', { size: (freeMb / 1024).toFixed(1) })
            : t('plugins.vram.freeUnknown')}
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
              {t('plugins.vram.pluginEstimate', {
                name: p.name,
                size: (p.vram_estimate_mb / 1024).toFixed(1),
              })}
            </Typography>
          </Box>
        ))}
      </Stack>

      {usedPct > 90 && (
        <Alert severity="warning" sx={{ mt: 1 }} variant="outlined">
          {t('plugins.vram.nearCapacity')}
        </Alert>
      )}
    </Paper>
  );
};

// ── Log Viewer ─────────────────────────────────────────────────────────
const LogViewer = ({ pluginId, open }) => {
  const { t } = useUiTranslation();
  const [logs, setLogs] = useState('');
  const [loading, setLoading] = useState(false);
  const logRef = useRef(null);

  const fetchLogs = useCallback(async () => {
    if (!open) return;
    setLoading(true);
    try {
      const response = await getPluginLogs(pluginId, 200);
      if (response.success) {
        setLogs(response.data.logs || t('plugins.logs.none'));
      }
    } catch {
      setLogs(t('plugins.logs.fetchFailed'));
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
          {t('plugins.logs.heading')}
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
        {loading && !logs ? t('plugins.logs.loading') : logs}
      </Box>
    </Box>
  );
};

// ── Plugin Card ────────────────────────────────────────────────────────
export const PluginCard = ({ plugin, onAction, onConfigOpen, showMessage }) => {
  const { t } = useUiTranslation();
  const [expanded, setExpanded] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);
  const cardMenu = useContextMenu();
  // Inert for every plugin but ComfyUI.
  const comfy = useComfyUpdate(plugin, showMessage);

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
        if (showMessage) showMessage(t('plugins.card.cameraStopped'), 'info');
      } else {
        await startVisionCamera(0);
        setCameraActive(true);
        if (showMessage) showMessage(t('plugins.card.cameraStarted'), 'success');
      }
    } catch (err) {
      if (showMessage) showMessage(err.message || t('plugins.card.cameraFailed'), 'error');
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
  const coolingDown = (plugin.cooldown_remaining || 0) > 0;
  const toggleDisabled =
    isLoading || plugin.status === 'starting' || plugin.status === 'stopping' || coolingDown;
  const cameraAvailable = plugin.id === 'vision_pipeline' && plugin.status === 'running';

  return (
    <Card
      onContextMenu={(e) => cardMenu.open(e)}
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
            label={statusConfig.label()}
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
          {plugin.description || t('plugins.card.noDescription')}
        </Typography>

        {/* Why the plugin is not (fully) ours to use: another install's copy
            holding the port, a tool its start script could not find. The
            backend sets it (status_message) and clears it on the next start. */}
        {plugin.status_message && (
          <Alert severity="warning" variant="outlined" sx={{ mb: 2 }} data-testid={`plugin-status-message-${plugin.id}`}>
            {plugin.status_message}
          </Alert>
        )}

        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
          <Chip size="small" label={`v${plugin.version}`} variant="outlined" />
          {comfy.installed && (
            <Chip size="small" label={`ComfyUI ${comfy.installed}`} variant="outlined" />
          )}
          <Chip size="small" label={plugin.type} variant="outlined" />
          {plugin.port && (
            <Chip size="small" label={t('plugins.card.port', { port: plugin.port })} variant="outlined" />
          )}
          {plugin.vram_estimate_mb > 0 && (
            <Chip
              size="small"
              icon={<GpuIcon />}
              label={t('plugins.card.vramEstimate', { size: (plugin.vram_estimate_mb / 1024).toFixed(1) })}
              variant="outlined"
              color={plugin.status === 'running' ? 'primary' : 'default'}
            />
          )}
        </Stack>

        <ComfyUpdateSection comfy={comfy} />

        <Collapse in={expanded}>
          <Divider sx={{ my: 1.5 }} />
          <Typography variant="body2" color="text.secondary">
            {t('plugins.card.category', { category: plugin.category })}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {t('plugins.card.id', { id: plugin.id })}
          </Typography>
          {plugin.plugin_dir && (
            <Typography variant="body2" color="text.secondary" sx={{ wordBreak: 'break-all' }}>
              {t('plugins.card.path', { path: plugin.plugin_dir })}
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
              ? t('plugins.card.coolingDownTooltip', { seconds: Math.ceil(plugin.cooldown_remaining) })
              : !plugin.enabled
                ? t('plugins.card.disabledTooltip')
                : t('plugins.card.savedTooltip')
          }
          arrow
        >
          <FormControlLabel
            control={
              <Switch
                checked={!!plugin.enabled}
                // The switch reflects the persistent user preference (enabled flag from
                // the user_enabled overlay in data/plugin_state.json). This ensures the
                // toggle updates visually when enable/disable succeeds (via socket
                // snapshot or optimistic update). Runtime 'status' (running/stopped/disabled)
                // drives the Chip and card opacity instead.
                onChange={() => handleAction(plugin.enabled ? 'disable' : 'start')}
                disabled={toggleDisabled}
                size="small"
                color="success"
              />
            }
            label={
              (plugin.cooldown_remaining || 0) > 0
                ? t('plugins.card.cooling', {
                    state: plugin.enabled ? t('plugins.card.on') : t('plugins.card.off'),
                    seconds: Math.ceil(plugin.cooldown_remaining),
                  })
                : (plugin.enabled ? t('plugins.card.on') : t('plugins.card.off'))
            }
          />
        </Tooltip>

        <Box sx={{ display: 'flex', gap: 0.5 }}>

          {cameraAvailable && (
            <Tooltip title={cameraActive ? t('plugins.card.stopCamera') : t('plugins.card.startCamera')}>
              <span>
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
              </span>
            </Tooltip>
          )}

          <Tooltip title={t('plugins.card.logs')}>
            <IconButton size="small" onClick={() => setLogsOpen(!logsOpen)}>
              <LogIcon fontSize="small" color={logsOpen ? 'primary' : 'action'} />
            </IconButton>
          </Tooltip>

          <Tooltip title={t('plugins.card.settings')}>
            <span>
              <IconButton size="small" onClick={() => onConfigOpen(plugin)} disabled={isLoading}>
                <SettingsIcon />
              </IconButton>
            </span>
          </Tooltip>

          <Tooltip title={expanded ? t('plugins.card.less') : t('plugins.card.more')}>
            <IconButton size="small" onClick={() => setExpanded(!expanded)}>
              {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </IconButton>
          </Tooltip>
        </Box>
      </CardActions>

      {/* Ollama alone owns start/stop behaviour in .env; the switches live on its
          card rather than behind the settings modal so the choice sits next to the
          service it governs. */}
      {plugin.id === 'ollama' && (
        <>
          <Divider />
          <Box sx={{ px: 2, pb: 2, pt: 0.5 }}>
            <OllamaLifecycleSection title={t('plugins.card.lifecycle')} />
          </Box>
        </>
      )}

      <EntityContextMenu
        anchorPosition={cardMenu.anchorPosition}
        onClose={cardMenu.close}
        actions={[
          {
            label: plugin.enabled ? t('plugins.card.menu.turnOff') : t('plugins.card.menu.turnOn'),
            onClick: () => handleAction(plugin.enabled ? 'disable' : 'start'),
            disabled: toggleDisabled,
          },
          cameraAvailable && {
            label: cameraActive ? t('plugins.card.menu.stopCamera') : t('plugins.card.menu.startCamera'),
            onClick: handleCameraToggle,
            disabled: cameraLoading,
          },
          comfy.available && {
            label: t('plugins.card.menu.updateComfy', { version: comfy.check.target }),
            onClick: comfy.openUpdate,
            disabled: Boolean(comfy.blocked),
          },
          {
            label: logsOpen ? t('plugins.card.menu.hideLogs') : t('plugins.card.menu.showLogs'),
            onClick: () => setLogsOpen(!logsOpen),
            dividerBefore: true,
          },
          { label: t('plugins.card.menu.settings'), onClick: () => onConfigOpen(plugin), disabled: isLoading },
          {
            label: expanded ? t('plugins.card.menu.fewerDetails') : t('plugins.card.menu.moreDetails'),
            onClick: () => setExpanded(!expanded),
          },
        ]}
      />
    </Card>
  );
};

// ── Config Dialog ──────────────────────────────────────────────────────
const ConfigDialog = ({ open, plugin, onClose, onSave }) => {
  const { t } = useUiTranslation();
  const [config, setConfig] = useState({});
  const [loading, setLoading] = useState(false);
  const [botToken, setBotToken] = useState('');
  const [showToken, setShowToken] = useState(false);

  useEffect(() => {
    if (plugin?.config) setConfig({ ...plugin.config });
    setBotToken('');
    setShowToken(false);
  }, [plugin]);

  const handleSave = async () => {
    setLoading(true);
    try {
      const payload = { ...config };
      if (plugin.id === 'discord' && botToken.trim()) {
        payload.bot_token = botToken.trim();
      }
      await onSave(plugin.id, payload);
      onClose();
    } finally {
      setLoading(false);
    }
  };

  if (!plugin) return null;

  const tokenStatus = plugin.secrets?.bot_token;
  const tokenHelper = tokenStatus?.configured
    ? t('plugins.config.tokenConfigured', { hint: tokenStatus.hint || '••••' })
    : t('plugins.config.tokenNotConfigured');

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t('plugins.config.title', { name: plugin.name })}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {plugin.id === 'discord' && (
            <TextField
              label={t('plugins.config.botToken')}
              type={showToken ? 'text' : 'password'}
              value={botToken}
              onChange={(e) => setBotToken(e.target.value)}
              fullWidth
              size="small"
              autoComplete="off"
              placeholder={tokenStatus?.configured ? '••••••••••••••••' : ''}
              helperText={tokenHelper}
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      aria-label={showToken ? t('plugins.config.hideToken') : t('plugins.config.showToken')}
                      onClick={() => setShowToken((v) => !v)}
                      edge="end"
                      size="small"
                    >
                      {showToken ? <VisibilityOffIcon /> : <VisibilityIcon />}
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />
          )}
          {plugin.config?.service_url !== undefined && (
            <TextField
              label={t('plugins.config.serviceUrl')}
              value={config.service_url || ''}
              onChange={(e) => setConfig({ ...config, service_url: e.target.value })}
              fullWidth
              size="small"
            />
          )}
          {plugin.config?.timeout !== undefined && (
            <TextField
              label={t('plugins.config.timeout')}
              type="number"
              value={config.timeout || 30}
              onChange={(e) => setConfig({ ...config, timeout: parseInt(e.target.value) || 30 })}
              fullWidth
              size="small"
            />
          )}
          {plugin.config?.fallback_enabled !== undefined && (
            <FormControlLabel
              control={
                <Switch
                  checked={config.fallback_enabled ?? true}
                  onChange={(e) => setConfig({ ...config, fallback_enabled: e.target.checked })}
                />
              }
              label={t('plugins.config.cpuFallback')}
            />
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>{t('plugins.config.cancel')}</Button>
        <Button onClick={handleSave} variant="contained" disabled={loading}>
          {loading ? <CircularProgress size={20} /> : t('plugins.config.save')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Restart Ollama Dialog ─────────────────────────────────────────────
const RestartOllamaDialog = ({ open, onClose, onConfirm, loading }) => {
  const { t } = useUiTranslation();
  return (
    <Dialog open={open} onClose={loading ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{t('plugins.restartOllama.title')}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary">
          {t('plugins.restartOllama.body')}
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>{t('plugins.restartOllama.noThanks')}</Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          disabled={loading}
          startIcon={loading ? <CircularProgress size={16} /> : <StartIcon />}
        >
          {loading ? t('plugins.restartOllama.starting') : t('plugins.restartOllama.start')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Page ───────────────────────────────────────────────────────────────
const applyPluginsPayload = (payload, setPlugins, setLoading, setError) => {
  const list = payload?.plugins;
  if (!Array.isArray(list)) return;
  setPlugins(list);
  setLoading(false);
  setError(null);
};

const PluginsPage = () => {
  const { t } = useUiTranslation();
  const [plugins, setPlugins] = useState([]);
  const [gpuVram, setGpuVram] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [configPlugin, setConfigPlugin] = useState(null);
  const { showMessage } = useSnackbar();
  const socketRef = useRef(null);
  const cooldownTimerRef = useRef(null);

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
        setError(response.message || t('plugins.page.loadFailed'));
      }
    } catch (err) {
      setError(err.message || t('plugins.page.loadFailed'));
    } finally {
      setLoading(false);
    }
    return null;
  }, []);

  // One-shot HTTP load + Socket.IO pushes (no polling — keeps logs quiet).
  useEffect(() => {
    fetchPlugins();

    const loadGpuOnce = async () => {
      try {
        const data = await getGpuStatus();
        if (data?.vram) setGpuVram(data.vram);
      } catch { /* gpu card optional */ }
    };
    loadGpuOnce();

    const socket = io(SOCKET_URL, {
      reconnection: true,
      reconnectionAttempts: 5,
      transports: ['polling', 'websocket'],
    });
    socketRef.current = socket;

    socket.on('connect', () => {
      socket.emit('subscribe_plugins');
      socket.emit('subscribe_gpu');
    });

    socket.on('plugins:status', (payload) => {
      applyPluginsPayload(payload, setPlugins, setLoading, setError);
    });

    socket.on('gpu:status', (payload) => {
      if (payload?.vram) setGpuVram(payload.vram);
    });

    return () => {
      socket.disconnect();
      socketRef.current = null;
    };
  }, [fetchPlugins]);

  // Local 1s countdown for gate cooldowns (no HTTP re-fetch).
  useEffect(() => {
    const hasCooldown = plugins.some((p) => (p.cooldown_remaining || 0) > 0);
    if (!hasCooldown) {
      if (cooldownTimerRef.current) {
        clearInterval(cooldownTimerRef.current);
        cooldownTimerRef.current = null;
      }
      return undefined;
    }
    if (cooldownTimerRef.current) return undefined;

    cooldownTimerRef.current = setInterval(() => {
      setPlugins((prev) => {
        const next = prev.map((p) => ({
          ...p,
          cooldown_remaining: Math.max(0, (p.cooldown_remaining || 0) - 1),
        }));
        if (!next.some((p) => (p.cooldown_remaining || 0) > 0) && cooldownTimerRef.current) {
          clearInterval(cooldownTimerRef.current);
          cooldownTimerRef.current = null;
        }
        return next;
      });
    }, 1000);

    return () => {
      if (cooldownTimerRef.current) {
        clearInterval(cooldownTimerRef.current);
        cooldownTimerRef.current = null;
      }
    };
  }, [plugins]);

  // Find a running GPU-heavy plugin that contends for VRAM with the one being
  // started. At most one heavy plugin runs at a time, so the first match is the
  // one to swap out.
  const findGpuConflict = useCallback((pluginId) => {
    const requested = plugins.find((p) => p.id === pluginId);
    if (!isGpuHeavy(requested)) return null;
    return plugins.find(
      (p) => p.id !== pluginId && isGpuHeavy(p) && p.status === 'running'
    );
  }, [plugins]);

  const handlePluginAction = async (pluginId, action) => {
    const settles = action === 'start' || action === 'stop';
    try {
      let response;
      switch (action) {
        case 'start': {
          // GPU-heavy plugins contend for VRAM (only one really fits on a 16 GB
          // card). Rather than block with a swap modal, give the operator the
          // benefit of the doubt: start what they asked for and drop a temporary
          // heads-up naming the other GPU plugin so they can toggle it off
          // themselves if VRAM runs short.
          const conflict = findGpuConflict(pluginId);
          if (conflict) {
            const requestedName = plugins.find((p) => p.id === pluginId)?.name || pluginId;
            showMessage(
              t('plugins.page.gpuConflict', { other: conflict.name, requested: requestedName }),
              'info'
            );
          }
          // Auto-enable if disabled so start (which rejects disabled plugins)
          // doesn't 400. This is the step the old swap handler skipped.
          if (!plugins.find((p) => p.id === pluginId)?.enabled) {
            await enablePlugin(pluginId);
          }
          // The call returns only once the service answers its health check
          // (a minute for ComfyUI), so show the wait while it runs.
          setPlugins((prev) => prev.map((p) => (
            p.id === pluginId ? { ...p, status: 'starting' } : p
          )));
          response = await startPlugin(pluginId);
          break;
        }
        case 'stop':
          setPlugins((prev) => prev.map((p) => (
            p.id === pluginId ? { ...p, status: 'stopping' } : p
          )));
          response = await stopPlugin(pluginId);
          break;
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
          const notice = data.error || t('plugins.page.coolingDown');
          showMessage(cd ? t('plugins.page.gatedWait', { message: notice, seconds: cd }) : notice, 'warning');
          if (cd) {
            setPlugins((prev) => prev.map((p) => (
              p.id === pluginId ? { ...p, cooldown_remaining: cd } : p
            )));
          }
          return;
        }
        showMessage(response.message || actionSucceeded(action), 'success');

        // Optimistic update: flip the enabled preference and status immediately so
        // the toggle switch in the UI reflects the change without waiting for the
        // 'plugins:status' socket round-trip. Start and stop need none: they answer
        // after the plugin has settled, and the listing fetched below is final.
        if (action === 'enable' || action === 'disable') {
          const newEnabled = action === 'enable';
          setPlugins((prev) => prev.map((p) =>
            p.id === pluginId
              ? { ...p, enabled: newEnabled, status: newEnabled ? 'stopped' : 'disabled', running: false }
              : p
          ));
        }

        // After ComfyUI goes down, offer to bring Ollama back once socket/HTTP
        // state settles: the route orchestrator stops Ollama to make room for
        // ComfyUI (plugin_bridge GPU swap), which leaves it enabled but not
        // running. The switch and the context menu send 'disable' (which stops
        // the service first); 'stop' is kept for callers that only stop.
        if ((action === 'stop' || action === 'disable') && pluginId === 'comfyui') {
          setTimeout(async () => {
            const list = (await fetchPlugins()) || plugins;
            const ollamaPlugin = list.find((p) => p.id === 'ollama');
            if (ollamaPlugin?.enabled && ollamaPlugin.status !== 'running') {
              setShowRestartOllama(true);
            }
          }, 400);
        }
      } else {
        showMessage(response.message || actionFailed(action), 'error');
      }
    } catch (err) {
      showMessage(err.message || actionFailed(action), 'error');
    } finally {
      // Replace the 'starting'/'stopping' shown during the call with the
      // backend's settled status, whether the call worked, failed or was gated.
      if (settles) await fetchPlugins();
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
        showMessage(t('plugins.page.ollamaStarted'), 'success');
      } else {
        showMessage(response.message || t('plugins.page.ollamaStartFailed'), 'error');
      }
    } catch (err) {
      showMessage(err.message || t('plugins.page.ollamaStartFailed'), 'error');
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
        showMessage(t('plugins.page.found', { count: response.data.count }), 'success');
        fetchPlugins();
      } else {
        showMessage(t('plugins.page.refreshFailed'), 'error');
      }
    } catch (err) {
      showMessage(err.message || t('plugins.page.refreshFailed'), 'error');
    }
  };

  const handleConfigSave = async (pluginId, config) => {
    try {
      const response = await updatePluginConfig(pluginId, config);
      if (response.success) {
        showMessage(response.message || t('plugins.page.configSaved'), 'success');
        fetchPlugins();
      } else {
        showMessage(t('plugins.page.configSaveFailed'), 'error');
      }
    } catch (err) {
      showMessage(err.message || t('plugins.page.configSaveFailed'), 'error');
    }
  };

  return (
    <PageLayout
      title={t('plugins.page.title')}
      variant="standard"
      actions={
        <Button
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={handleRefresh}
          disabled={loading}
        >
          {t('plugins.page.refresh')}
        </Button>
      }
    >
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        {t('plugins.page.intro')}
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert>
      )}

      {/* VRAM Budget Bar */}
      {plugins.length > 0 && <VramBudgetBar plugins={plugins} gpuVram={gpuVram} />}

      {loading && plugins.length === 0 ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
          <ContextualLoader loading message={t('plugins.page.loading')} showProgress={false} inline />
        </Box>
      ) : plugins.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <PluginIcon sx={{ fontSize: 64, color: 'text.secondary', mb: 2 }} />
          <Typography variant="h6" gutterBottom>{t('plugins.page.emptyTitle')}</Typography>
          <Typography variant="body2" color="text.secondary">
            {t('plugins.page.emptyBody')}
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
