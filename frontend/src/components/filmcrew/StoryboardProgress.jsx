import React, { useEffect, useState } from 'react';
import { Box, Typography, LinearProgress, Paper } from '@mui/material';
import { useUnifiedProgress } from '../../contexts/UnifiedProgressContext';

const StoryboardProgress = ({ productionId }) => {
  const { activeProcesses } = useUnifiedProgress();
  const [lastSeen, setLastSeen] = useState(null);

  const jobId = `filmcrew_storyboard_${productionId}`;
  const process = activeProcesses.get(jobId);

  useEffect(() => {
    if (process) {
      setLastSeen(process);
    }
  }, [process]);

  // Keep showing the last known state briefly after the process disappears
  // (it completes and is cleaned up, but the backend stage may still say storyboard_gen).
  const display = process || lastSeen;

  if (!display) {
    return (
      <Paper sx={{ p: 2, mb: 3 }}>
        <Typography variant="body2" color="text.secondary">
          Storyboard generation queued...
        </Typography>
        <LinearProgress sx={{ mt: 1 }} />
      </Paper>
    );
  }

  const progress = display.progress ?? 0;
  const message = display.message || 'Generating storyboard images...';
  const additional = display.additional_data || {};
  const completed = additional.completed_shots ?? '—';
  const total = additional.total_shots ?? '—';

  return (
    <Paper sx={{ p: 2, mb: 3 }}>
      <Typography variant="subtitle2" gutterBottom>
        Storyboard Generation
      </Typography>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 1 }}>
        <Box sx={{ flexGrow: 1 }}>
          <LinearProgress variant="determinate" value={progress} />
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: 80 }}>
          {progress}%
        </Typography>
      </Box>
      <Typography variant="body2" color="text.secondary">
        {message} ({completed} / {total} shots)
      </Typography>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
        Images are generated one at a time. Each shot must finish before the next begins.
      </Typography>
    </Paper>
  );
};

export default StoryboardProgress;
