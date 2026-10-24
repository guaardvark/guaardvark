import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Box, Typography, Chip, LinearProgress, Tooltip, IconButton, Link,
  Table, TableBody, TableRow, TableCell,
} from '@mui/material';
import {
  PlayArrow as PlayIcon,
  Pause as PauseIcon,
  Science as ScienceIcon,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import { io } from 'socket.io-client';
import { SOCKET_URL } from '../../api/apiClient';
import { ragAutoresearchService } from '../../api/ragAutoresearchService';
import DashboardCardWrapper from './DashboardCardWrapper';
import {
  baselineText,
  experimentLabel,
  experimentsSummary,
  runOutcome,
} from '../../utils/autoresearchLabels';
import { useUiTranslation } from '../../i18n';

const RAGAutoresearchCard = React.forwardRef(
  ({ style, isMinimized, onToggleMinimize, cardColor, onCardColorChange, ...props }, ref) => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const socketRef = useRef(null);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.getStatus();
      setStatus(data);
    } catch (e) { /* backend may not have endpoint yet */ }
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.getHistory(1, 5);
      setHistory(data.experiments || []);
    } catch (e) { /* ignore */ }
  }, []);

  const refresh = useCallback(() => {
    fetchStatus();
    fetchHistory();
  }, [fetchStatus, fetchHistory]);

  useEffect(() => {
    refresh();

    // Push updates via Socket.IO; the 30s poll below stays as fallback.
    try {
      const socket = io(SOCKET_URL, {
        reconnection: true,
        reconnectionAttempts: 3,
        transports: ['polling', 'websocket'],
      });
      socketRef.current = socket;
      socket.on('autoresearch:experiment_complete', refresh);
      socket.on('autoresearch:run_complete', refresh);
    } catch (e) { /* socket unavailable — polling covers it */ }

    const interval = setInterval(refresh, 30000);
    return () => {
      if (socketRef.current) {
        socketRef.current.disconnect();
        socketRef.current = null;
      }
      clearInterval(interval);
    };
  }, [refresh]);

  const handleStart = async () => {
    setLoading(true);
    try {
      await ragAutoresearchService.createRun({ budget_hours: 6 });
    } catch (e) {
      /* A refused run (422) shows below as "Not run: …"; the page has details. */
    } finally {
      await fetchStatus();
      setLoading(false);
    }
  };

  const handleStop = async () => {
    await ragAutoresearchService.stop();
    await fetchStatus();
  };

  const openPage = () => navigate('/autoresearch');

  const menuActions = [
    status?.running
      ? { label: t('dashboard.autoresearch.pauseRun'), icon: <PauseIcon fontSize="small" />, onClick: handleStop }
      : {
          label: t('dashboard.autoresearch.startRun'),
          icon: <PlayIcon fontSize="small" />,
          onClick: handleStart,
          disabled: loading || !status,
        },
    { label: t('common.refresh'), icon: <RefreshIcon fontSize="small" />, onClick: refresh },
  ];

  const lastRun = status?.last_run || null;
  const outcome = runOutcome(lastRun);

  return (
    <DashboardCardWrapper
      ref={ref}
      style={style}
      isMinimized={isMinimized}
      onToggleMinimize={onToggleMinimize}
      cardColor={cardColor}
      onCardColorChange={onCardColorChange}
      title={t('dashboard.autoresearch.title')}
      titleBarActions={
        <ScienceIcon fontSize="small" sx={{ color: status?.running ? 'success.main' : 'text.secondary', opacity: 0.8 }} />
      }
      {...props}
      contextMenuActions={menuActions}
    >
      <Box sx={{ height: '100%' }}>
      {!status ? (
        <Box sx={{ p: 1, textAlign: 'center' }}>
          <Typography variant="caption" color="text.secondary">{t('dashboard.autoresearch.unavailable')}</Typography>
        </Box>
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          {/* Status row */}
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Chip
                label={status.running ? t('dashboard.autoresearch.running') : status.paused ? t('dashboard.autoresearch.paused') : t('dashboard.autoresearch.idle')}
                size="small"
                color={status.running ? 'success' : 'default'}
                sx={{ height: 20, fontSize: '0.7rem' }}
              />
              <Typography variant="caption" color="text.secondary">
                {t('dashboard.autoresearch.phase', { phase: status.phase })}
                {status.current_parameter ? ` · ${status.current_parameter}` : ""}
              </Typography>
            </Box>
            {status.running ? (
              <Tooltip title={t('dashboard.autoresearch.pauseOptimization')}>
                <IconButton size="small" onClick={handleStop}><PauseIcon sx={{ fontSize: 16 }} /></IconButton>
              </Tooltip>
            ) : (
              <Tooltip title={t('dashboard.autoresearch.startBounded')}>
                <span>
                  <IconButton size="small" onClick={handleStart} disabled={loading}>
                    <PlayIcon sx={{ fontSize: 16 }} />
                  </IconButton>
                </span>
              </Tooltip>
            )}
          </Box>

          {status.running && <LinearProgress sx={{ mb: 1, borderRadius: 1 }} />}

          {/* Score */}
          <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 1, mb: 1 }}>
            <Typography variant="caption">{baselineText(status)}</Typography>
            <Tooltip
              title={
                status.total_health_checks
                  ? t('dashboard.autoresearch.healthChecksNotCounted', { count: status.total_health_checks })
                  : ''
              }
            >
              <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: 'nowrap' }}>
                {experimentsSummary(status)}
              </Typography>
            </Tooltip>
          </Box>

          {/* Latest run, whatever its status */}
          {lastRun && (
            <Box sx={{ mb: 1 }}>
              <Typography variant="caption" color="text.secondary">
                {t('dashboard.autoresearch.latestRun')}:{' '}
                <Link
                  component="button"
                  variant="caption"
                  onClick={openPage}
                  sx={{ verticalAlign: 'baseline' }}
                >
                  {lastRun.run_tag}
                </Link>
                {' '}
                <Tooltip title={outcome.detail}>
                  <Box
                    component="span"
                    sx={outcome.kind === 'not_run' ? { color: 'warning.main' } : undefined}
                  >
                    {outcome.text}
                  </Box>
                </Tooltip>
              </Typography>
            </Box>
          )}

          {/* Recent experiments */}
          {history.length > 0 && (
            <Box sx={{ flex: 1, overflow: 'auto' }}>
              <Table size="small" sx={{ '& td': { py: 0.25, px: 0.5, fontSize: '0.7rem' } }}>
                <TableBody>
                  {history.map((exp) => {
                    const label = experimentLabel(exp);
                    const isHealth = label.kind === 'health';
                    return (
                      <TableRow key={exp.id}>
                        <TableCell>{isHealth ? t('dashboard.autoresearch.testSuite') : exp.parameter_changed}</TableCell>
                        <TableCell>{exp.new_value}</TableCell>
                        <TableCell>
                          <Chip
                            label={label.selfReported ? t('dashboard.autoresearch.selfReported', { status: label.status }) : label.status}
                            size="small"
                            color={label.tone}
                            variant={isHealth ? 'outlined' : 'filled'}
                            sx={{ height: 16, fontSize: '0.6rem' }}
                          />
                        </TableCell>
                        <TableCell align="right">
                          {isHealth || typeof exp.delta !== 'number'
                            ? '—'
                            : exp.delta > 0 ? `+${exp.delta.toFixed(3)}` : exp.delta.toFixed(3)}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </Box>
          )}

          {history.length === 0 && !status.running && (
            <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Typography variant="caption" color="text.secondary">
                {t('dashboard.autoresearch.noExperiments')}
              </Typography>
            </Box>
          )}
        </Box>
      )}
      </Box>
    </DashboardCardWrapper>
  );
});

RAGAutoresearchCard.displayName = 'RAGAutoresearchCard';

export default RAGAutoresearchCard;
