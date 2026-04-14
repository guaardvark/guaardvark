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

const KillSwitchModal = ({ open, onClose }) => {
  const [isLoading, setIsLoading] = useState(false);
  const [systemStatus, setSystemStatus] = useState(null);
  const [killResult, setKillResult] = useState(null);
  const [error, setError] = useState(null);

  const handleGetSystemStatus = async () => {
    try {
      setIsLoading(true);
      setError(null);
      
      const response = await voiceService.getSystemStatus();
      setSystemStatus(response.system_status);
    } catch (err) {
      setError(`Failed to get system status: ${err.message}`);
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
      setError(`Failed to kill processes: ${err.message}`);
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
      setError(`Failed to cleanup processes: ${err.message}`);
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
        Emergency Kill Switch
      </DialogTitle>
      
      <DialogContent>
        <Alert severity="warning" sx={{ mb: 2 }}>
          <Typography variant="body2">
            <strong>WARNING:</strong> This will forcefully terminate all active LLM processes. 
            Use only when the system is unresponsive or consuming excessive resources.
          </Typography>
        </Alert>

        {/* System Status Section */}
        <Box sx={{ mb: 3 }}>
          <Typography variant="h6" sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <MemoryIcon />
            System Status
          </Typography>
          
          {systemStatus ? (
            <Box>
              <List dense>
                <ListItem>
                  <ListItemIcon>
                    <SpeedIcon color={getStatusColor(systemStatus.cpu_percent, 80)} />
                  </ListItemIcon>
                  <ListItemText 
                    primary={`CPU Usage: ${systemStatus.cpu_percent.toFixed(1)}%`}
                    secondary={systemStatus.cpu_percent > 80 ? "High CPU usage detected" : "Normal"}
                  />
                </ListItem>
                
                <ListItem>
                  <ListItemIcon>
                    <MemoryIcon color={getStatusColor(systemStatus.memory_percent, 80)} />
                  </ListItemIcon>
                  <ListItemText 
                    primary={`Memory Usage: ${systemStatus.memory_percent.toFixed(1)}%`}
                    secondary={`Available: ${systemStatus.memory_available_gb.toFixed(1)} GB`}
                  />
                </ListItem>
                
                <ListItem>
                  <ListItemIcon>
                    <StorageIcon color={getStatusColor(systemStatus.disk_percent, 90)} />
                  </ListItemIcon>
                  <ListItemText 
                    primary={`Disk Usage: ${systemStatus.disk_percent.toFixed(1)}%`}
                    secondary={`Free: ${systemStatus.disk_free_gb.toFixed(1)} GB`}
                  />
                </ListItem>
                
                <Divider sx={{ my: 1 }} />
                
                <ListItem>
                  <ListItemIcon>
                    <StopIcon color={systemStatus.active_processes > 0 ? "warning" : "success"} />
                  </ListItemIcon>
                  <ListItemText 
                    primary={`Active LLM Processes: ${systemStatus.active_processes}`}
                    secondary={systemStatus.system_overloaded ? "System is overloaded" : "System is stable"}
                  />
                </ListItem>
              </List>
              
              {systemStatus.system_overloaded && (
                <Alert severity="error" sx={{ mt: 1 }}>
                  System is currently overloaded. Consider using the kill switch.
                </Alert>
              )}
            </Box>
          ) : (
            <Typography variant="body2" color="text.secondary">
              Click "Get System Status" to view current resource usage.
            </Typography>
          )}
        </Box>

        {/* Kill Results Section */}
        {killResult && (
          <Box sx={{ mb: 3 }}>
            <Typography variant="h6" sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
              <StopIcon />
              Kill Results
            </Typography>
            
            <Alert severity="success" sx={{ mb: 2 }}>
              Successfully killed {killResult.total_killed} processes
              {killResult.total_failed > 0 && `, ${killResult.total_failed} failed`}
            </Alert>
            
            {killResult.killed_processes.length > 0 && (
              <List dense>
                {killResult.killed_processes.map((process, index) => (
                  <ListItem key={index}>
                    <ListItemIcon>
                      <CheckCircleIcon color="success" />
                    </ListItemIcon>
                    <ListItemText 
                      primary={`PID ${process.pid} (${process.type})`}
                      secondary={`Duration: ${process.duration?.toFixed(1)}s`}
                    />
                  </ListItem>
                ))}
              </List>
            )}
            
            {killResult.failed_processes.length > 0 && (
              <>
                <Typography variant="subtitle2" color="error" sx={{ mt: 2, mb: 1 }}>
                  Failed to kill:
                </Typography>
                <List dense>
                  {killResult.failed_processes.map((process, index) => (
                    <ListItem key={index}>
                      <ListItemIcon>
                        <ErrorIcon color="error" />
                      </ListItemIcon>
                      <ListItemText 
                        primary={`PID ${process.pid}`}
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
          Get System Status
        </Button>
        
        <Button 
          onClick={handleCleanupProcesses}
          disabled={isLoading}
          variant="outlined"
          color="warning"
          startIcon={isLoading ? <CircularProgress size={16} /> : <StopIcon />}
        >
          Cleanup Dead Processes
        </Button>
        
        <Button 
          onClick={handleKillAllProcesses}
          disabled={isLoading}
          variant="contained"
          color="error"
          startIcon={isLoading ? <CircularProgress size={16} /> : <StopIcon />}
        >
          KILL ALL PROCESSES
        </Button>
        
        <Button onClick={onClose} disabled={isLoading}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default KillSwitchModal; 