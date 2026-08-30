import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Box, Typography, Chip, LinearProgress, Tooltip, IconButton, Link,
  Table, TableBody, TableRow, TableCell,
} from '@mui/material';
import {
  PlayArrow as PlayIcon,
  Pause as PauseIcon,
  Science as ScienceIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import { io } from 'socket.io-client';
import { SOCKET_URL } from '../../api/apiClient';
import { ragAutoresearchService } from '../../api/ragAutoresearchService';
import DashboardCardWrapper from './DashboardCardWrapper';

const RAGAutoresearchCard = React.forwardRef(
  ({ style, isMinimized, onToggleMinimize, cardColor, onCardColorChange, ...props }, ref) => {
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [history, setHistory] = useState([]);
  const [lastRun, setLastRun] = useState(null);
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

  const fetchLastRun = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.listRuns();
      const cutoff = Date.now() - 24 * 60 * 60 * 1000;
      const recent = (data.runs || []).find(
        (r) => r.status === 'completed' && r.ended_at
          && new Date(r.ended_at).getTime() >= cutoff,
      );
      setLastRun(recent || null);
    } catch (e) { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchHistory();
    fetchLastRun();

    // Push updates via Socket.IO; the 30s poll below stays as fallback.
    try {
      const socket = io(SOCKET_URL, {
        reconnection: true,
        reconnectionAttempts: 3,
        transports: ['polling', 'websocket'],
      });
      socketRef.current = socket;

      socket.on('autoresearch:experiment_complete', () => {
        fetchStatus();
        fetchHistory();
      });

      socket.on('autoresearch:run_complete', () => {
        fetchStatus();
        fetchHistory();
        fetchLastRun();
      });
    } catch (e) { /* socket unavailable — polling covers it */ }

    const interval = setInterval(() => {
      fetchStatus();
      fetchHistory();
      fetchLastRun();
    }, 30000);
    return () => {
      if (socketRef.current) {
        socketRef.current.disconnect();
        socketRef.current = null;
      }
      clearInterval(interval);
    };
  }, [fetchStatus, fetchHistory, fetchLastRun]);

  const handleStart = async () => {
    setLoading(true);
    try {
      await ragAutoresearchService.createRun({ budget_hours: 6 });
      await fetchStatus();
      await fetchLastRun();
    } catch (e) {
      if (e.status !== 409) {
        /* page / snackbar lives on Autoresearch; card stays quiet */
      }
      await fetchStatus();
    } finally { setLoading(false); }
  };

  const handleStop = async () => {
    await ragAutoresearchService.stop();
    await fetchStatus();
  };

  return (
    <DashboardCardWrapper
      ref={ref}
      style={style}
      isMinimized={isMinimized}
      onToggleMinimize={onToggleMinimize}
      cardColor={cardColor}
      onCardColorChange={onCardColorChange}
      title="RAG Autoresearch"
      titleBarActions={
        <ScienceIcon fontSize="small" sx={{ color: status?.running ? 'success.main' : 'text.secondary', opacity: 0.8 }} />
      }
      {...props}
    >
      {!status ? (
        <Box sx={{ p: 1, textAlign: 'center' }}>
          <Typography variant="caption" color="text.secondary">Autoresearch unavailable</Typography>
        </Box>
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          {/* Status row */}
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Chip
                label={status.running ? 'Running' : status.paused ? 'Paused' : 'Idle'}
                size="small"
                color={status.running ? 'success' : 'default'}
                sx={{ height: 20, fontSize: '0.7rem' }}
              />
              <Typography variant="caption" color="text.secondary">
                Phase {status.phase}
                {status.current_parameter ? ` · ${status.current_parameter}` : ""}
              </Typography>
            </Box>
            {status.running ? (
              <Tooltip title="Pause optimization">
                <IconButton size="small" onClick={handleStop}><PauseIcon sx={{ fontSize: 16 }} /></IconButton>
              </Tooltip>
            ) : (
              <Tooltip title="Start bounded research run">
                <IconButton size="small" onClick={handleStart} disabled={loading}>
                  <PlayIcon sx={{ fontSize: 16 }} />
                </IconButton>
              </Tooltip>
            )}
          </Box>

          {status.running && <LinearProgress sx={{ mb: 1, borderRadius: 1 }} />}

          {/* Score */}
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
            <Typography variant="caption">
              Score: <strong>{status.baseline_score?.toFixed(3) || '\u2014'}</strong>
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {status.total_experiments} runs / {status.total_improvements} improvements
            </Typography>
          </Box>

          {/* Last night's research */}
          {lastRun && (
            <Box sx={{ mb: 1 }}>
              <Typography variant="caption" color="text.secondary">
                Last night's research:{' '}
                <Link
                  component="button"
                  variant="caption"
                  onClick={() => navigate('/autoresearch')}
                  sx={{ verticalAlign: 'baseline' }}
                >
                  {lastRun.run_tag}
                </Link>
                {' '}
                {lastRun.baseline_score != null ? lastRun.baseline_score.toFixed(3) : '—'}
                {' → '}
                {lastRun.best_score != null ? lastRun.best_score.toFixed(3) : '—'}
                {lastRun.halt_reason ? ` (${lastRun.halt_reason})` : ''}
              </Typography>
            </Box>
          )}

          {/* Recent experiments */}
          {history.length > 0 && (
            <Box sx={{ flex: 1, overflow: 'auto' }}>
              <Table size="small" sx={{ '& td': { py: 0.25, px: 0.5, fontSize: '0.7rem' } }}>
                <TableBody>
                  {history.map((exp) => (
                    <TableRow key={exp.id}>
                      <TableCell>{exp.parameter_changed}</TableCell>
                      <TableCell>{exp.new_value}</TableCell>
                      <TableCell>
                        <Chip
                          label={exp.status}
                          size="small"
                          color={exp.status === 'keep' ? 'success' : exp.status === 'crash' ? 'error' : 'default'}
                          sx={{ height: 16, fontSize: '0.6rem' }}
                        />
                      </TableCell>
                      <TableCell align="right">
                        {exp.delta > 0 ? `+${exp.delta.toFixed(3)}` : exp.delta?.toFixed(3)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}

          {history.length === 0 && !status.running && (
            <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Typography variant="caption" color="text.secondary">
                No experiments yet. Click play to start a bounded run.
              </Typography>
            </Box>
          )}
        </Box>
      )}
    </DashboardCardWrapper>
  );
});

RAGAutoresearchCard.displayName = 'RAGAutoresearchCard';

export default RAGAutoresearchCard;
