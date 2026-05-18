// frontend/src/components/settings/AgentDisplaySection.jsx
// Detector + installer for the Agent Vision Control virtual display stack
// (Xvfb, x11vnc, openbox, tint2, xdotool, scrot, browser, python mss).
//
// Mirrors VoiceSettingsContent's Whisper installer — same alert + button shape.

import React, { useEffect, useState, useCallback } from 'react';
import { Box, Button, CircularProgress, Typography, Tooltip } from '@mui/material';
import MuiAlert from '@mui/material/Alert';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import RefreshIcon from '@mui/icons-material/Refresh';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import RestartAltIcon from '@mui/icons-material/RestartAlt';

import {
  getDisplayStatus,
  installDisplay,
  startDisplay,
  stopDisplay,
} from '../../api/agentDisplayService';

// Friendly labels — keep them tight so the row renders cleanly.
const COMPONENT_LABELS = {
  Xvfb: 'Xvfb (virtual X server)',
  x11vnc: 'x11vnc (VNC bridge)',
  openbox: 'Openbox (window manager)',
  tint2: 'Tint2 (taskbar)',
  xdotool: 'xdotool (input synthesis)',
  scrot: 'scrot (screen capture fallback)',
  browser: 'Browser (Firefox / Chromium)',
  mss: 'mss (Python screen capture)',
  start_script: 'start_agent_display.sh',
  display_running: 'Display :99 is live',
};

const COMPONENT_ORDER = [
  'Xvfb', 'x11vnc', 'openbox', 'tint2', 'xdotool', 'scrot',
  'browser', 'mss', 'start_script', 'display_running',
];

const StatusRow = ({ label, ok, version, hint }) => (
  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.5 }}>
    {ok ? (
      <CheckCircleOutlineIcon fontSize="small" sx={{ color: 'success.main' }} />
    ) : (
      <ErrorOutlineIcon fontSize="small" sx={{ color: 'warning.main' }} />
    )}
    <Typography variant="body2" sx={{ flex: 1 }}>{label}</Typography>
    {version && (
      <Tooltip title={version}>
        <Typography variant="caption" color="text.secondary" sx={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {version}
        </Typography>
      </Tooltip>
    )}
    {!ok && hint && (
      <Typography variant="caption" color="warning.main">{hint}</Typography>
    )}
  </Box>
);

const AgentDisplaySection = ({ showMessage }) => {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [installing, setInstalling] = useState(false);
  // controlAction: null | "start" | "stop" | "restart" — drives the spinner
  // on whichever button the user clicked.
  const [controlAction, setControlAction] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getDisplayStatus();
      setStatus(data);
    } catch (e) {
      setError(e.message || 'Failed to probe agent display');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onInstall = async () => {
    setInstalling(true);
    showMessage?.('Installing agent display dependencies… apt-get may take a minute.', 'info');
    try {
      const result = await installDisplay();
      if (result.success) {
        if (result.already_installed) {
          showMessage?.('Agent Display dependencies already installed.', 'info');
        } else {
          showMessage?.('Agent Display dependencies installed.', 'success');
        }
      } else {
        showMessage?.(`Install failed: ${result.error}`, 'error');
      }
    } catch (e) {
      showMessage?.(`Install failed: ${e.message}`, 'error');
    } finally {
      setInstalling(false);
      refresh();
    }
  };

  const onStart = async () => {
    setControlAction('start');
    showMessage?.('Starting agent display…', 'info');
    try {
      const result = await startDisplay();
      if (result.success) {
        showMessage?.('Agent display is up on :99.', 'success');
      } else {
        showMessage?.(`Start failed: ${result.error}`, 'error');
      }
    } catch (e) {
      showMessage?.(`Start failed: ${e.message}`, 'error');
    } finally {
      setControlAction(null);
      refresh();
    }
  };

  const onStop = async () => {
    setControlAction('stop');
    showMessage?.('Stopping agent display…', 'info');
    try {
      const result = await stopDisplay();
      if (result.success) {
        showMessage?.('Agent display stopped.', 'success');
      } else {
        showMessage?.(`Stop failed: ${result.error}`, 'error');
      }
    } catch (e) {
      showMessage?.(`Stop failed: ${e.message}`, 'error');
    } finally {
      setControlAction(null);
      refresh();
    }
  };

  const onRestart = async () => {
    setControlAction('restart');
    showMessage?.('Restarting agent display…', 'info');
    try {
      await stopDisplay().catch(() => {}); // tolerate stop failures (might already be down)
      const startResult = await startDisplay();
      if (startResult.success) {
        showMessage?.('Agent display restarted.', 'success');
      } else {
        showMessage?.(`Restart failed: ${startResult.error}`, 'error');
      }
    } catch (e) {
      showMessage?.(`Restart failed: ${e.message}`, 'error');
    } finally {
      setControlAction(null);
      refresh();
    }
  };

  if (loading && !status) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
        <CircularProgress size={20} />
      </Box>
    );
  }

  if (error) {
    return (
      <MuiAlert severity="error" sx={{ mb: 1 }}>
        {error}
      </MuiAlert>
    );
  }

  if (!status) return null;

  const components = status.components || {};
  const missingApt = status.missing_apt_packages || [];
  const missingPip = status.missing_pip_packages || [];
  const needsInstall = missingApt.length > 0 || missingPip.length > 0;

  return (
    <Box>
      {needsInstall ? (
        <MuiAlert
          severity="warning"
          sx={{ mb: 1.5 }}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={installing ? <CircularProgress size={16} color="inherit" /> : <FileDownloadIcon />}
              onClick={onInstall}
              disabled={installing}
            >
              {installing ? 'Installing…' : 'Install Missing'}
            </Button>
          }
        >
          Agent Display is missing components: {[...missingApt, ...missingPip].join(', ')}.
          Click to install via apt-get + pip.
        </MuiAlert>
      ) : status.display_running ? (
        <MuiAlert
          severity="success"
          sx={{ mb: 1.5 }}
          action={
            <Box sx={{ display: 'flex', gap: 0.5 }}>
              <Button
                color="inherit"
                size="small"
                startIcon={controlAction === 'restart' ? <CircularProgress size={16} color="inherit" /> : <RestartAltIcon />}
                onClick={onRestart}
                disabled={controlAction !== null}
              >
                {controlAction === 'restart' ? 'Restarting…' : 'Restart'}
              </Button>
              <Button
                color="inherit"
                size="small"
                startIcon={controlAction === 'stop' ? <CircularProgress size={16} color="inherit" /> : <StopIcon />}
                onClick={onStop}
                disabled={controlAction !== null}
              >
                {controlAction === 'stop' ? 'Stopping…' : 'Stop'}
              </Button>
            </Box>
          }
        >
          Agent Display is fully installed and running on :99.
        </MuiAlert>
      ) : (
        <MuiAlert
          severity="info"
          sx={{ mb: 1.5 }}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={controlAction === 'start' ? <CircularProgress size={16} color="inherit" /> : <PlayArrowIcon />}
              onClick={onStart}
              disabled={controlAction !== null}
            >
              {controlAction === 'start' ? 'Starting…' : 'Start Display'}
            </Button>
          }
        >
          All dependencies installed. Display :99 is not running yet.
        </MuiAlert>
      )}

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        {COMPONENT_ORDER.map((key) => {
          const comp = components[key];
          if (!comp) return null;
          const label = COMPONENT_LABELS[key] || key;
          return (
            <StatusRow
              key={key}
              label={label}
              ok={!!comp.installed}
              version={comp.version}
              hint={!comp.installed && comp.apt_package ? `apt: ${comp.apt_package}` :
                    !comp.installed && comp.pip_package ? `pip: ${comp.pip_package}` : null}
            />
          );
        })}
      </Box>

      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 1 }}>
        <Button
          size="small"
          variant="text"
          startIcon={<RefreshIcon fontSize="small" />}
          onClick={refresh}
          disabled={loading || installing}
        >
          Recheck
        </Button>
      </Box>
    </Box>
  );
};

export default AgentDisplaySection;
