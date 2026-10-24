import React, { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
  Alert,
  CircularProgress,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Divider
} from '@mui/material';
import {
  Warning as WarningIcon,
  Stop as StopIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Memory as MemoryIcon,
  Storage as StorageIcon,
  Speed as SpeedIcon
} from '@mui/icons-material';
import voiceService from '../../api/voiceService';
import { ConfirmActionDialog } from '../settings/ui';
import { useUiTranslation, Trans } from '../../i18n';

const KillSwitchModal = ({ open, onClose }) => {
  const { t } = useUiTranslation();
  const [isLoading, setIsLoading] = useState(false);
  const [systemStatus, setSystemStatus] = useState(null);
  const [killResult, setKillResult] = useState(null);
  const [confirmKill, setConfirmKill] = useState(false);
  const [error, setError] = useState(null);

  const handleGetSystemStatus = async () => {
    try {
      setIsLoading(true);
      setError(null);
      
      const response = await voiceService.getSystemStatus();
      setSystemStatus(response.system_status);
    } catch (err) {
      setError(t('backups.killSwitch.statusFailed', { error: err.message }));
    } finally {
      setIsLoading(false);
    }
  };

  const handleKillAllProcesses = async () => {
    try {
      setIsLoading(true);
      setError(null);
      setKillResult(null);
      
      const response = await voiceService.killAllProcesses();
      setKillResult(response);
      
      // Refresh system status after killing processes
      setTimeout(() => {
        handleGetSystemStatus();
      }, 1000);
      
    } catch (err) {
      setError(t('backups.killSwitch.killFailed', { error: err.message }));
    } finally {
      setIsLoading(false);
    }
  };

  const handleCleanupProcesses = async () => {
    try {
      setIsLoading(true);
      setError(null);
      
      await voiceService.cleanupProcesses();
      
      // Refresh system status after cleanup
      setTimeout(() => {
        handleGetSystemStatus();
      }, 500);
      
    } catch (err) {
      setError(t('backups.killSwitch.cleanupFailed', { error: err.message }));
    } finally {
      setIsLoading(false);
    }
  };

  const getStatusColor = (value, threshold) => {
    if (value >= threshold * 0.9) return 'error';
    if (value >= threshold * 0.7) return 'warning';
    return 'success';
  };

  const _formatBytes = (bytes) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  return (
    <Dialog 
      open={open} 
      onClose={onClose}
      maxWidth="md"
      fullWidth
    >
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <WarningIcon color="error" />
        {t('backups.killSwitch.title')}
      </DialogTitle>
      
      <DialogContent>
        <Alert severity="warning" sx={{ mb: 2 }}>
          <Typography variant="body2">
            <Trans i18nKey="backups.killSwitch.warning" components={{ bold: <strong /> }} />
          </Typography>
        </Alert>

        {/* System Status Section */}
        <Box sx={{ mb: 3 }}>
          <Typography variant="h6" sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <MemoryIcon />
            {t('backups.killSwitch.systemStatus')}
          </Typography>
          
          {systemStatus ? (
            <Box>
              <List dense>
                <ListItem>
                  <ListItemIcon>
                    <SpeedIcon color={getStatusColor(systemStatus.cpu_percent, 80)} />
                  </ListItemIcon>
                  <ListItemText 
                    primary={t('backups.killSwitch.cpuUsage', { percent: systemStatus.cpu_percent.toFixed(1) })}
                    secondary={systemStatus.cpu_percent > 80 ? t('backups.killSwitch.highCpu') : t('backups.killSwitch.normal')}
                  />
                </ListItem>
                
                <ListItem>
                  <ListItemIcon>
                    <MemoryIcon color={getStatusColor(systemStatus.memory_percent, 80)} />
                  </ListItemIcon>
                  <ListItemText 
                    primary={t('backups.killSwitch.memoryUsage', { percent: systemStatus.memory_percent.toFixed(1) })}
                    secondary={t('backups.killSwitch.memoryAvailable', { gb: systemStatus.memory_available_gb.toFixed(1) })}
                  />
                </ListItem>
                
                <ListItem>
                  <ListItemIcon>
                    <StorageIcon color={getStatusColor(systemStatus.disk_percent, 90)} />
                  </ListItemIcon>
                  <ListItemText 
                    primary={t('backups.killSwitch.diskUsage', { percent: systemStatus.disk_percent.toFixed(1) })}
                    secondary={t('backups.killSwitch.diskFree', { gb: systemStatus.disk_free_gb.toFixed(1) })}
                  />
                </ListItem>
                
                <Divider sx={{ my: 1 }} />
                
                <ListItem>
                  <ListItemIcon>
                    <StopIcon color={systemStatus.active_processes > 0 ? "warning" : "success"} />
                  </ListItemIcon>
                  <ListItemText 
                    primary={t('backups.killSwitch.activeProcesses', { count: systemStatus.active_processes })}
                    secondary={systemStatus.system_overloaded ? t('backups.killSwitch.overloaded') : t('backups.killSwitch.stable')}
                  />
                </ListItem>
              </List>
              
              {systemStatus.system_overloaded && (
                <Alert severity="error" sx={{ mt: 1 }}>
                  {t('backups.killSwitch.overloadedAlert')}
                </Alert>
              )}
            </Box>
          ) : (
            <Typography variant="body2" color="text.secondary">
              {t('backups.killSwitch.statusHint')}
            </Typography>
          )}
        </Box>

        {/* Kill Results Section */}
        {killResult && (
          <Box sx={{ mb: 3 }}>
            <Typography variant="h6" sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
              <StopIcon />
              {t('backups.killSwitch.killResults')}
            </Typography>
            
            <Alert severity="success" sx={{ mb: 2 }}>
              {killResult.total_failed > 0
                ? t('backups.killSwitch.killedWithFailures', { count: killResult.total_killed, failed: killResult.total_failed })
                : t('backups.killSwitch.killed', { count: killResult.total_killed })}
            </Alert>

            {killResult.web_access && (
              <Alert severity={killResult.web_access.allow_web_search === false ? "warning" : "error"} sx={{ mb: 2 }}>
                {killResult.web_access.allow_web_search === false
                  ? t('backups.killSwitch.webAccessOff')
                  : t('backups.killSwitch.webAccessFailed', { error: killResult.web_access.error || t('backups.killSwitch.unknownError') })}
                {killResult.web_access.agent_browser?.task_stopped && " " + t('backups.killSwitch.agentTaskStopped')}
                {killResult.web_access.agent_browser?.browser_closed > 0 && " " + t('backups.killSwitch.agentBrowserClosed')}
              </Alert>
            )}
            
            {killResult.killed_processes.length > 0 && (
              <List dense>
                {killResult.killed_processes.map((process, index) => (
                  <ListItem key={index}>
                    <ListItemIcon>
                      <CheckCircleIcon color="success" />
                    </ListItemIcon>
                    <ListItemText 
                      primary={t('backups.killSwitch.processWithType', { pid: process.pid, type: process.type })}
                      secondary={t('backups.killSwitch.duration', { seconds: process.duration?.toFixed(1) })}
                    />
                  </ListItem>
                ))}
              </List>
            )}
            
            {killResult.failed_processes.length > 0 && (
              <>
                <Typography variant="subtitle2" color="error" sx={{ mt: 2, mb: 1 }}>
                  {t('backups.killSwitch.failedToKill')}:
                </Typography>
                <List dense>
                  {killResult.failed_processes.map((process, index) => (
                    <ListItem key={index}>
                      <ListItemIcon>
                        <ErrorIcon color="error" />
                      </ListItemIcon>
                      <ListItemText 
                        primary={t('backups.killSwitch.process', { pid: process.pid })}
                        secondary={process.error}
                      />
                    </ListItem>
                  ))}
                </List>
              </>
            )}
          </Box>
        )}

        {/* Error Display */}
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
      </DialogContent>
      
      <DialogActions sx={{ p: 2, gap: 1 }}>
        <Button 
          onClick={handleGetSystemStatus}
          disabled={isLoading}
          variant="outlined"
          startIcon={isLoading ? <CircularProgress size={16} /> : <MemoryIcon />}
        >
          {t('backups.killSwitch.getSystemStatus')}
        </Button>
        
        <Button 
          onClick={handleCleanupProcesses}
          disabled={isLoading}
          variant="outlined"
          color="warning"
          startIcon={isLoading ? <CircularProgress size={16} /> : <StopIcon />}
        >
          {t('backups.killSwitch.cleanupDead')}
        </Button>
        
        <Button 
          onClick={() => setConfirmKill(true)}
          disabled={isLoading}
          variant="contained"
          color="error"
          startIcon={isLoading ? <CircularProgress size={16} /> : <StopIcon />}
        >
          {t('backups.killSwitch.killAll')}
        </Button>
        <ConfirmActionDialog
          open={confirmKill}
          onClose={() => setConfirmKill(false)}
          onConfirm={() => {
            setConfirmKill(false);
            handleKillAllProcesses();
          }}
          title={t('backups.killSwitch.confirmTitle')}
          description={t('backups.killSwitch.confirmDescription')}
          keeps={t('backups.killSwitch.confirmKeeps')}
          confirmLabel={t('backups.killSwitch.confirmLabel')}
        />
        
        <Button onClick={onClose} disabled={isLoading}>
          {t('dialogs.shared.close')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default KillSwitchModal; 