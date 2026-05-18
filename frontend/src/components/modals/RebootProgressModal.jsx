import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
  Alert,
  Paper,
  LinearProgress,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import {
  RestartAlt as RestartIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Close as CloseIcon,
} from '@mui/icons-material';
import { BASE_URL } from '../../api/apiClient';
import { getBackendHealth } from '../../api/devtoolsService';

// Strip ANSI escape codes from terminal output
// eslint-disable-next-line no-control-regex -- intentional: ANSI escape sequences are control chars by definition
const ANSI_RE = /\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07/g;
const stripAnsi = (s) => s.replace(ANSI_RE, '');

const RebootProgressModal = ({ open, onClose }) => {
  const _theme = useTheme();
  const [output, setOutput] = useState([]);
  const [status, setStatus] = useState('idle'); // idle | running | complete | error
  const [errorMessage, setErrorMessage] = useState(null);

  const outputEndRef = useRef(null);
  const abortRef = useRef(null);
  const logServerUrlRef = useRef(null);
  const logOffsetRef = useRef(0);
  const pollingRef = useRef(null);
  const mountedRef = useRef(true);

  // Auto-scroll on new output
  useEffect(() => {
    outputEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [output]);

  // Track mount state
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // Cleanup on unmount or close
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (pollingRef.current) clearTimeout(pollingRef.current);
    };
  }, []);

  // Start reboot when modal opens
  useEffect(() => {
    if (open) {
      setOutput([]);
      setStatus('idle');
      setErrorMessage(null);
      logOffsetRef.current = 0;
      logServerUrlRef.current = null;
      if (pollingRef.current) { clearTimeout(pollingRef.current); pollingRef.current = null; }
      startReboot();
    } else {
      abortRef.current?.abort();
      abortRef.current = null;
      if (pollingRef.current) { clearTimeout(pollingRef.current); pollingRef.current = null; }
    }
  }, [open]);

  // ---- helpers ----

  const addLine = useCallback((type, text) => {
    if (!mountedRef.current) return;
    setOutput(prev => {
      // dedup against last 8 lines
      const recent = prev.slice(-8).map(i => i.text);
      if (recent.includes(text)) return prev;
      return [...prev, { type, text }];
    });
  }, []);

  // ---- Phase 1: SSE from Flask ----

  const startReboot = async () => {
    setStatus('running');
    addLine('status', 'Initiating reboot...');

    try {
      abortRef.current = new AbortController();
      const response = await fetch(`${BASE_URL}/reboot/stream`, {
        method: 'POST',
        headers: { Accept: 'text/event-stream' },
        signal: abortRef.current.signal,
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      // eslint-disable-next-line no-constant-condition -- standard streaming-read pattern; loop exits via `if (done) break`
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6);
          if (raw === '[DONE]') break;

          try {
            const data = JSON.parse(raw);

            if (data.type === 'log_server') {
              logServerUrlRef.current = data.url;
            } else if (data.type === 'output') {
              addLine('output', stripAnsi(data.line));
            } else if (data.type === 'status') {
              addLine('status', data.message);
            } else if (data.type === 'warning') {
              addLine('status', data.message);
            } else if (data.type === 'error') {
              setErrorMessage(data.message);
              setStatus('error');
              addLine('error', data.message);
            } else if (data.type === 'complete' && data.polling) {
              if (data.logServerUrl) logServerUrlRef.current = data.logServerUrl;
              addLine('status', 'Switching to live log polling...');
              startLogServerPolling();
              return; // exit SSE loop
            }
          } catch {
            // ignore parse errors
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') return;

      // Network error or stream error = Flask died (expected during reboot)
      if (err.message?.includes('fetch') || err.message?.includes('network') || err.message?.includes('Network') || err.message?.includes('input stream') || err.message?.includes('stream')) {
        addLine('status', 'Server connection lost — services stopping...');
        startLogServerPolling();
        return;
      }

      setErrorMessage(err.message || 'Failed to start reboot');
      setStatus('error');
      addLine('error', err.message || 'Failed to start reboot');
    }
  };

  // ---- Phase 2: Poll the standalone log server ----

  const startLogServerPolling = () => {
    const url = logServerUrlRef.current;
    if (!url) {
      // No log server — skip straight to health-check polling
      addLine('status', 'Waiting for system to come back online...');
      startHealthCheckPolling();
      return;
    }

    let attempts = 0;
    const MAX_ATTEMPTS = 150; // 5 min at 2s
    let noUpdateCount = 0;
    let logServerDead = false;

    const poll = async () => {
      if (!mountedRef.current) return;
      if (attempts >= MAX_ATTEMPTS) {
        addLine('status', 'Max polling time reached — checking health...');
        startHealthCheckPolling();
        return;
      }
      attempts++;

      // --- Try reading from log server ---
      if (!logServerDead) {
        try {
          const resp = await fetch(`${url}/log?offset=${logOffsetRef.current}`);
          const data = await resp.json();

          if (data.success && data.content_lines?.length > 0) {
            noUpdateCount = 0;
            data.content_lines.forEach(ln => addLine('output', ln));
            if (data.offset !== undefined) logOffsetRef.current = data.offset;
          } else {
            noUpdateCount++;
          }
        } catch {
          // Log server died (maybe start.sh ended & it timed out, or port reused)
          logServerDead = true;
          noUpdateCount++;
        }
      }

      // --- Periodically try health check to see if Flask is back ---
      if (noUpdateCount >= 5 || logServerDead) {
        try {
          const health = await getBackendHealth();
          if (health?.status === 'ok') {
            // Grab any final log lines from the new Flask instance
            await fetchFinalLog();
            finishReboot();
            return;
          }
        } catch {
          // Flask not up yet — keep polling
        }
      }

      pollingRef.current = setTimeout(poll, 2000);
    };

    poll();
  };

  // ---- Phase 3: Health-check only (fallback if no log server) ----

  const startHealthCheckPolling = () => {
    let attempts = 0;
    const MAX = 60; // 3 min at 3s

    const poll = async () => {
      if (!mountedRef.current) return;
      if (attempts >= MAX) {
        setStatus('error');
        setErrorMessage('System did not come back online within the expected time.');
        addLine('error', 'Timeout waiting for system restart');
        return;
      }
      attempts++;

      if (attempts % 5 === 0) {
        addLine('status', `Waiting for system... (${attempts}/${MAX})`);
      }

      try {
        const health = await getBackendHealth();
        if (health?.status === 'ok') {
          await fetchFinalLog();
          finishReboot();
          return;
        }
      } catch {
        // not up yet
      }

      pollingRef.current = setTimeout(poll, 3000);
    };

    poll();
  };

  // ---- Finish ----

  const fetchFinalLog = async () => {
    // Read any remaining log lines from Flask's /api/reboot/log
    try {
      const resp = await fetch(`${BASE_URL}/reboot/log?offset=${logOffsetRef.current}`);
      const data = await resp.json();
      if (data.success && data.content_lines?.length > 0) {
        data.content_lines.forEach(ln => addLine('output', ln));
      }
    } catch {
      // fine — we have enough output
    }
  };

  const finishReboot = () => {
    if (!mountedRef.current) return;
    setStatus('complete');
    addLine('status', 'System is back online!');

    // Shutdown the log server (best-effort)
    if (logServerUrlRef.current) {
      fetch(`${logServerUrlRef.current}/shutdown`).catch(() => {});
    }

    setTimeout(() => {
      if (mountedRef.current) window.location.reload();
    }, 2500);
  };

  // ---- Close handler ----

  const handleClose = () => {
    if (status === 'running') return; // don't allow close mid-reboot
    abortRef.current?.abort();
    if (pollingRef.current) clearTimeout(pollingRef.current);
    onClose?.();
  };

  // ---- Render ----

  const statusIcon = status === 'complete'
    ? <CheckCircleIcon color="success" />
    : status === 'error'
      ? <ErrorIcon color="error" />
      : <RestartIcon color="primary" sx={status === 'running' ? { animation: 'spin 2s linear infinite', '@keyframes spin': { from: { transform: 'rotate(0deg)' }, to: { transform: 'rotate(360deg)' } } } : {}} />;

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      disableEscapeKeyDown={status === 'running'}
    >
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        {statusIcon}
        System Reboot
      </DialogTitle>

      <DialogContent>
        {status === 'running' && (
          <LinearProgress sx={{ mb: 1.5, borderRadius: 1 }} />
        )}

        {status === 'complete' && (
          <Alert severity="success" sx={{ mb: 1.5 }}>
            Reboot complete — reloading page...
          </Alert>
        )}

        {status === 'error' && errorMessage && (
          <Alert severity="error" sx={{ mb: 1.5 }}>
            {errorMessage}
          </Alert>
        )}

        {/* Terminal output */}
        <Paper
          sx={{
            p: 1.5,
            bgcolor: '#0d1117',
            color: '#c9d1d9',
            fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", monospace',
            fontSize: '0.8rem',
            lineHeight: 1.5,
            maxHeight: '55vh',
            minHeight: '200px',
            overflow: 'auto',
            borderRadius: 1,
            border: '1px solid',
            borderColor: 'divider',
          }}
        >
          {output.length === 0 ? (
            <Typography variant="body2" sx={{ color: '#8b949e', fontFamily: 'inherit' }}>
              Waiting for output...
            </Typography>
          ) : (
            output.map((item, idx) => {
              let color = '#c9d1d9';
              let prefix = '';
              if (item.type === 'error') {
                color = '#f85149';
                prefix = '✗ ';
              } else if (item.type === 'status') {
                color = '#58a6ff';
                prefix = '» ';
              }

              return (
                <Box
                  key={idx}
                  sx={{
                    color,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    py: 0.15,
                  }}
                >
                  {prefix}{item.text}
                </Box>
              );
            })
          )}
          <div ref={outputEndRef} />
        </Paper>
      </DialogContent>

      <DialogActions sx={{ p: 2 }}>
        <Button
          onClick={handleClose}
          disabled={status === 'running'}
          variant="contained"
          startIcon={<CloseIcon />}
        >
          {status === 'running' ? 'Rebooting...' : status === 'complete' ? 'Reloading...' : 'Close'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default RebootProgressModal;
