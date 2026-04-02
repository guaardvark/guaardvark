import React, { useState, useEffect, useCallback } from 'react';
import {
  Box, Typography, Chip, LinearProgress, Tooltip, IconButton,
  Table, TableBody, TableRow, TableCell,
} from '@mui/material';
import {
  PlayArrow as PlayIcon,
  Pause as PauseIcon,
  Science as ScienceIcon,
} from '@mui/icons-material';
import { ragAutoresearchService } from '../../api/ragAutoresearchService';
import DashboardCardWrapper from './DashboardCardWrapper';

const RAGAutoresearchCard = React.forwardRef(
  ({ style, isMinimized, onToggleMinimize, cardColor, onCardColorChange, ...props }, ref) => {
  const [status, setStatus] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);

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

  useEffect(() => {
    fetchStatus();
    fetchHistory();
    const interval = setInterval(() => {
      fetchStatus();
      fetchHistory();
    }, 30000);
    return () => clearInterval(interval);
  }, [fetchStatus, fetchHistory]);

  const handleStart = async () => {
    setLoading(true);
    try {
      await ragAutoresearchService.start();
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
              </Typography>
            </Box>
            {status.running ? (
              <Tooltip title="Pause optimization">
                <IconButton size="small" onClick={handleStop}><PauseIcon sx={{ fontSize: 16 }} /></IconButton>
              </Tooltip>
            ) : (
              <Tooltip title="Start optimization">
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
                No experiments yet. Click play to start.
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
