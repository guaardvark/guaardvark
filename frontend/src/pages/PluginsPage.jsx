// frontend/src/pages/PluginsPage.jsx
/**
 * Plugin Management Page
 * Allows users to view, enable/disable, start/stop, and configure plugins.
 */

import React, { useState, useEffect, useCallback } from 'react';
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
  Warning as WarningIcon,
  HelpOutline as UnknownIcon,
  Memory as GpuIcon,
  Extension as PluginIcon,
} from '@mui/icons-material';
import { useSnackbar } from '../components/common/SnackbarProvider';
import PageLayout from '../components/layout/PageLayout';
import { ContextualLoader } from '../components/common/LoadingStates';
import {
  listPlugins,
  getPluginHealth,
  startPlugin,
  stopPlugin,
  enablePlugin,
  disablePlugin,
  refreshPlugins,
  updatePluginConfig,
} from '../api/pluginsService';

// Status colors and icons
const STATUS_CONFIG = {
  running: { color: 'success', icon: HealthyIcon, label: 'Running' },
  stopped: { color: 'default', icon: StopIcon, label: 'Stopped' },
  starting: { color: 'warning', icon: CircularProgress, label: 'Starting' },
  stopping: { color: 'warning', icon: CircularProgress, label: 'Stopping' },
  error: { color: 'error', icon: ErrorIcon, label: 'Error' },
  disabled: { color: 'default', icon: UnknownIcon, label: 'Disabled' },
  unknown: { color: 'default', icon: UnknownIcon, label: 'Unknown' },
};

// Category icons
const CATEGORY_ICONS = {
  indexing: GpuIcon,
  default: PluginIcon,
};

const PluginCard = ({ plugin, onAction, onConfigOpen }) => {
  const [expanded, setExpanded] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);
  
  const statusConfig = STATUS_CONFIG[plugin.status] || STATUS_CONFIG.unknown;
  const StatusIcon = statusConfig.icon;
  const CategoryIcon = CATEGORY_ICONS[plugin.category] || CATEGORY_ICONS.default;
  
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
        opacity: plugin.enabled ? 1 : 0.7,
        border: plugin.running ? '1px solid' : 'none',
        borderColor: 'success.main',
      }}
    >
      <CardContent sx={{ flexGrow: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', mb: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <CategoryIcon color="action" />
            <Typography variant="h6" component="div">
              {plugin.name}
            </Typography>
          </Box>
          <Chip 
            size="small"
            label={statusConfig.label}
            color={statusConfig.color}
            icon={plugin.status === 'starting' || plugin.status === 'stopping' 
              ? <CircularProgress size={12} color="inherit" />
              : <StatusIcon fontSize="small" />
            }
          />
        </Box>
        
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {plugin.description || 'No description available.'}
        </Typography>
        
        <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
          <Chip size="small" label={`v${plugin.version}`} variant="outlined" />
          <Chip size="small" label={plugin.type} variant="outlined" />
          {plugin.port && (
            <Chip size="small" label={`Port ${plugin.port}`} variant="outlined" />
          )}
        </Stack>
        
        <Collapse in={expanded}>
          <Divider sx={{ my: 2 }} />
          <Typography variant="subtitle2" gutterBottom>Details</Typography>
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
      </CardContent>
      
      <Divider />
      
      <CardActions sx={{ justifyContent: 'space-between', px: 2 }}>
        <Box>
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
        </Box>
        
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
          
          <Tooltip title="Settings">
            <IconButton 
              size="small"
              onClick={() => onConfigOpen(plugin)}
              disabled={isLoading}
            >
              <SettingsIcon />
            </IconButton>
          </Tooltip>
          
          <Tooltip title={expanded ? "Less" : "More"}>
            <IconButton 
              size="small"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </IconButton>
          </Tooltip>
        </Box>
      </CardActions>
    </Card>
  );
};

const ConfigDialog = ({ open, plugin, onClose, onSave }) => {
  const [config, setConfig] = useState({});
  const [loading, setLoading] = useState(false);
  
  useEffect(() => {
    if (plugin?.config) {
      setConfig({ ...plugin.config });
    }
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
      <DialogTitle>
        Configure: {plugin.name}
      </DialogTitle>
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
          
          {plugin.config?.model !== undefined && (
            <TextField
              label="Model"
              value={config.model || ''}
              onChange={(e) => setConfig({ ...config, model: e.target.value })}
              fullWidth
              size="small"
            />
          )}
          
          {plugin.config?.batch_size !== undefined && (
            <TextField
              label="Batch Size"
              type="number"
              value={config.batch_size || 32}
              onChange={(e) => setConfig({ ...config, batch_size: parseInt(e.target.value) || 32 })}
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

const PluginsPage = () => {
  const [plugins, setPlugins] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [configPlugin, setConfigPlugin] = useState(null);
  const { showMessage } = useSnackbar();
  
  const fetchPlugins = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await listPlugins();
      if (response.success) {
        setPlugins(response.data.plugins || []);
      } else {
        setError(response.message || 'Failed to load plugins');
      }
    } catch (err) {
      console.error('Error fetching plugins:', err);
      setError(err.message || 'Failed to load plugins');
    } finally {
      setLoading(false);
    }
  }, []);
  
  useEffect(() => {
    fetchPlugins();
    
    // Poll for status updates every 10 seconds
    const interval = setInterval(fetchPlugins, 10000);
    return () => clearInterval(interval);
  }, [fetchPlugins]);
  
  const handlePluginAction = async (pluginId, action) => {
    try {
      let response;
      switch (action) {
        case 'start':
          response = await startPlugin(pluginId);
          break;
        case 'stop':
          response = await stopPlugin(pluginId);
          break;
        case 'enable':
          response = await enablePlugin(pluginId);
          break;
        case 'disable':
          response = await disablePlugin(pluginId);
          break;
        default:
          throw new Error(`Unknown action: ${action}`);
      }
      
      if (response.success) {
        showMessage(response.message || `Plugin ${action} successful`, 'success');
        fetchPlugins();
      } else {
        showMessage(response.message || `Failed to ${action} plugin`, 'error');
      }
    } catch (err) {
      console.error(`Error ${action} plugin:`, err);
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
      console.error('Error refreshing plugins:', err);
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
      console.error('Error saving config:', err);
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
        Manage optional features and services
      </Typography>
      
      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}
      
      {loading && plugins.length === 0 ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
          <ContextualLoader loading message="Loading plugins..." showProgress={false} inline />
        </Box>
      ) : plugins.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <PluginIcon sx={{ fontSize: 64, color: 'text.secondary', mb: 2 }} />
          <Typography variant="h6" gutterBottom>
            No Plugins Found
          </Typography>
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
