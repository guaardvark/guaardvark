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
  CircularProgress,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import {
  RestartAlt as RestartIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Close as CloseIcon,
} from '@mui/icons-material';
import { BASE_URL } from '../../api/apiClient';
import { announceRefusal, authRefusalCode, describeAuthRefusal } from '../../api/apiAuth';
import { getBackendHealth } from '../../api/devtoolsService';
import { ApiKeyRefusalAlert } from '../common/ApiKeyRefusalNotice';
import { useUiTranslation } from '../../i18n';

// Strip ANSI escape codes from terminal output
// eslint-disable-next-line no-control-regex -- intentional: ANSI escape sequences are control chars by definition
const ANSI_RE = /\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07/g;
const stripAnsi = (s) => s.replace(ANSI_RE, '');

// One missed poll is a reconnect, not the end of the feed. Quiet is how long
// a reachable log may sit still, after the API is healthy, before the reboot
// counts as finished. The cap is the old five-minute poll budget.
const POLL_MS = 1000;
const QUIET_MS = 15000;
const MAX_POLL_MS = 5 * 60 * 1000;
const BOOT_DONE_RE = /GPU stack: (?:healthy\.|DEGRADED\b)/;

// mode "fast" restarts with start.sh --fast; anything else is a full restart.
const RebootProgressModal = ({ open, onClose, mode = 'restart' }) => {
  const { t } = useUiTranslation();
  const _theme = useTheme();
  const [output, setOutput] = useState([]);
  const [status, setStatus] = useState('idle'); // idle | running | complete | error
  const [errorMessage, setErrorMessage] = useState(null);
  // What to do when the backend refused this browser (no API key here).
  const [refusal, setRefusal] = useState(null);

  const outputEndRef = useRef(null);
  const abortRef = useRef(null);
  const logServerUrlRef = useRef(null);
  const logOffsetRef = useRef(0);
  const pollingRef = useRef(null);
  const mountedRef = useRef(true);
  const reconnectingShownRef = useRef(false);
  const bootDoneRef = useRef(false);
  const pollingStartedRef = useRef(false);
  const finishedRef = useRef(false);

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
      setRefusal(null);
      logOffsetRef.current = 0;
      logServerUrlRef.current = null;
      reconnectingShownRef.current = false;
      bootDoneRef.current = false;
      pollingStartedRef.current = false;
      finishedRef.current = false;
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
    addLine('status', t('backups.reboot.initiating'));

    try {
      abortRef.current = new AbortController();
      const response = await fetch(`${BASE_URL}/reboot/stream`, {
        method: 'POST',
        headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode === 'fast' ? 'fast' : 'restart' }),
        signal: abortRef.current.signal,
      });

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const code = response.status === 401 || response.status === 403 ? authRefusalCode(body) : null;
        if (code) {
          // Nothing restarted. Say what to do, as other refused actions do.
          const rejected = Boolean(body.credential_rejected);
          announceRefusal({ code, rejected, url: response.url });
          setRefusal(describeAuthRefusal(code, rejected));
          setStatus('error');
          return;
        }
        throw new Error(body?.error && typeof body.error === 'string' ? body.error : `HTTP ${response.status}`);
      }

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
              const text = stripAnsi(data.line);
              if (BOOT_DONE_RE.test(text)) bootDoneRef.current = true;
              addLine('output', text);
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
              addLine('status', t('backups.reboot.switchingToPolling'));
              startLogServerPolling();
              return; // exit SSE loop
            }
          } catch {
            // ignore parse errors
          }
        }
      }
      // The socket closed cleanly before the "switch to polling" event, which
      // is what happens when stop.sh kills Flask mid-stream.
      if (logServerUrlRef.current) startLogServerPolling();
    } catch (err) {
      if (err.name === 'AbortError') return;

      // Flask dying drops the socket. Any error after the log-server URL
      // arrived is that handoff, whatever the browser calls it.
      if (logServerUrlRef.current) {
        noteReconnecting();
        startLogServerPolling();
        return;
      }
      const message = err.message || '';
      if (err.name === 'TypeError' || /fetch|network|stream/i.test(message)) {
        addLine('status', t('backups.reboot.waitingForOnline'));
        startHealthCheckPolling();
        return;
      }

      setErrorMessage(message || t('backups.reboot.startFailed'));
      setStatus('error');
      addLine('error', message || t('backups.reboot.startFailed'));
    }
  };

  // One line for the gap. A retry must not stack another copy.
  const noteReconnecting = () => {
    if (reconnectingShownRef.current) return;
    reconnectingShownRef.current = true;
    addLine('status', t('backups.reboot.reconnecting'));
  };

  // ---- Phase 2: Poll the standalone log server ----

  const startLogServerPolling = () => {
    if (pollingStartedRef.current) return;
    const url = logServerUrlRef.current;
    if (!url) {
      // No log server — skip straight to health-check polling
      addLine('status', t('backups.reboot.waitingForOnline'));
      startHealthCheckPolling();
      return;
    }
    pollingStartedRef.current = true;

    const started = Date.now();
    let lastGrowth = Date.now();
    let failSince = 0;

    const readyToFinish = (lastPollOk) => {
      if (bootDoneRef.current) return true;
      if (lastPollOk && Date.now() - lastGrowth >= QUIET_MS) return true;
      // The log server itself is gone. Keep trying through the outage; once
      // it has been unreachable for a quiet window, the API health check is
      // allowed to end the reboot. A server that is still answering is not.
      return !lastPollOk && failSince !== 0 && Date.now() - failSince >= QUIET_MS;
    };

    const poll = async () => {
      if (!mountedRef.current || finishedRef.current) return;
      if (Date.now() - started >= MAX_POLL_MS) {
        addLine('status', t('backups.reboot.maxPollingReached'));
        startHealthCheckPolling();
        return;
      }

      let lastPollOk = false;
      try {
        const resp = await fetch(`${url}/log?offset=${logOffsetRef.current}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        lastPollOk = true;
        failSince = 0;
        if (data.success && data.content_lines?.length > 0) {
          lastGrowth = Date.now();
          data.content_lines.forEach((ln) => {
            if (BOOT_DONE_RE.test(ln)) bootDoneRef.current = true;
            addLine('output', ln);
          });
          if (data.offset !== undefined) logOffsetRef.current = data.offset;
        }
      } catch {
        lastPollOk = false;
        if (failSince === 0) failSince = Date.now();
        noteReconnecting();
      }

      if (readyToFinish(lastPollOk)) {
        try {
          const health = await getBackendHealth();
          if (health?.status === 'ok') {
            await fetchFinalLog();
            finishReboot();
            return;
          }
        } catch {
          // Flask not up yet — keep polling the log
        }
      }

      pollingRef.current = setTimeout(poll, POLL_MS);
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
        setErrorMessage(t('backups.reboot.didNotComeBack'));
        addLine('error', t('backups.reboot.timeout'));
        return;
      }
      attempts++;

      if (attempts % 5 === 0) {
        addLine('status', t('backups.reboot.waitingAttempt', { attempt: attempts, max: MAX }));
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
    if (!mountedRef.current || finishedRef.current) return;
    finishedRef.current = true;
    if (pollingRef.current) {
      clearTimeout(pollingRef.current);
      pollingRef.current = null;
    }
    setStatus('complete');
    addLine('status', t('backups.reboot.backOnline'));

    // Shutdown the log server (best-effort)
    if (logServerUrlRef.current) {
      fetch(`${logServerUrlRef.current}/shutdown`, { method: 'POST' }).catch(() => {});
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
      : status === 'running'
        ? <CircularProgress size={22} thickness={4} aria-label={t('backups.reboot.rebooting')} data-testid="reboot-spinner" />
        : <RestartIcon color="primary" data-testid="reboot-mark" />;

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
    >
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        {statusIcon}
        {t('backups.reboot.title')}
      </DialogTitle>

      <DialogContent>
        {status === 'running' && (
          <LinearProgress sx={{ mb: 1.5, borderRadius: 1 }} />
        )}

        {status === 'complete' && (
          <Alert severity="success" sx={{ mb: 1.5 }}>
            {t('backups.reboot.complete')}
          </Alert>
        )}

        {status === 'error' && errorMessage && (
          <Alert severity="error" sx={{ mb: 1.5 }}>
            {errorMessage}
          </Alert>
        )}

        {refusal && (
          <ApiKeyRefusalAlert message={refusal} severity="warning" sx={{ mb: 1.5 }} onFollow={() => handleClose()} />
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
              {t('backups.reboot.waitingForOutput')}
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
          {status === 'running' ? t('backups.reboot.rebooting') : status === 'complete' ? t('backups.reboot.reloading') : t('dialogs.shared.close')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default RebootProgressModal;
