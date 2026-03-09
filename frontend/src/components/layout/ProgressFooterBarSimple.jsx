// frontend/src/components/layout/ProgressFooterBarSimple.jsx
// Simple, independent progress bar that polls API directly (like SystemMetricsBar)
// No Context dependency - just reads metadata.json via API

import React, { useState, useEffect } from 'react';
import { Box, LinearProgress, Typography, useTheme } from '@mui/material';

const ProgressFooterBarSimple = () => {
  const theme = useTheme();
  const [progressData, setProgressData] = useState(null);
  const [active, setActive] = useState(false);

  useEffect(() => {
    let isMounted = true;

    const fetchProgress = async () => {
      try {
        const response = await fetch('/api/bulk-generate/progress/current');
        const data = await response.json();

        if (!isMounted) return;

        if (data && data.progress !== undefined) {
          setProgressData(data);
          // Show bar for processing, complete, error, or cancelled states
          const showStates = ['processing', 'complete', 'error', 'cancelled'];
          setActive(showStates.includes(data.status));
        } else {
          setProgressData(null);
          setActive(false);
        }
      } catch (err) {
        // Fail silently - no active job
        if (isMounted) {
          setProgressData(null);
          setActive(false);
        }
      }
    };

    // Initial fetch
    fetchProgress();

    // Poll every 1 second (like SystemMetricsBar)
    const pollInterval = setInterval(fetchProgress, 1000);

    return () => {
      isMounted = false;
      clearInterval(pollInterval);
    };
  }, []);

  if (!active || !progressData) {
    return null; // Hide when no active job
  }

  const { progress, message, additional_data, status } = progressData;
  const generatedCount = additional_data?.generated_count;
  const targetCount = additional_data?.target_count;

  // Determine color based on status
  const getStatusColor = () => {
    switch (status) {
      case 'complete':
        return 'success';
      case 'error':
        return 'error';
      case 'cancelled':
        return 'warning';
      case 'processing':
      default:
        return 'info';
    }
  };

  const statusColor = getStatusColor();

  return (
    <Box
      sx={{
        position: 'fixed',
        bottom: 0,
        left: 80,
        right: 0,
        height: '24px',
        zIndex: 9999,
        backgroundColor: theme.palette.background.paper,
        borderTop: `1px solid ${theme.palette.divider}`,
        display: 'flex',
        alignItems: 'center',
        px: 2,
        boxShadow: '0 -2px 8px rgba(0,0,0,0.1)',
      }}
    >
      <LinearProgress
        color={statusColor}
        variant="determinate"
        value={progress || 0}
        sx={{
          height: '4px',
          flexGrow: 1,
          mr: 2,
          borderRadius: '2px',
          backgroundColor: theme.palette.mode === 'dark' ? 'grey.800' : 'grey.300',
          '& .MuiLinearProgress-bar': {
            borderRadius: '2px',
          }
        }}
      />

      {/* Show row counts if available */}
      {generatedCount !== undefined && targetCount !== undefined ? (
        <Typography
          variant="caption"
          sx={{
            color: `${statusColor}.main`,
            fontSize: '0.65rem',
            fontWeight: 500,
            mr: 1,
            minWidth: '80px',
            textAlign: 'right'
          }}
        >
          {generatedCount}/{targetCount} ({Math.round(progress)}%)
        </Typography>
      ) : (
        <Typography
          variant="caption"
          sx={{
            color: `${statusColor}.main`,
            fontSize: '0.65rem',
            fontWeight: 500,
            mr: 1,
            minWidth: '40px',
            textAlign: 'right'
          }}
        >
          {Math.round(progress)}%
        </Typography>
      )}

      <Typography
        variant="caption"
        sx={{
          color: status === 'processing' ? 'text.secondary' : `${statusColor}.main`,
          whiteSpace: 'nowrap',
          fontSize: '0.7rem',
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
          fontWeight: status === 'processing' ? 400 : 500,
          letterSpacing: '0.02em'
        }}
      >
        {message}
      </Typography>
    </Box>
  );
};

export default ProgressFooterBarSimple;
