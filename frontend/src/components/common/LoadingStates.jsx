// frontend/src/components/common/LoadingStates.jsx
// Enhanced Loading States and User Feedback Components
// Provides comprehensive loading indicators, progress feedback, and offline support

import React, { useState, useEffect } from 'react';
import {
  Box,
  CircularProgress,
  LinearProgress,
  Typography,
  Alert,
  Chip,
  Skeleton,
  Fade,
  Slide,
  Paper,
  Button,
  IconButton,
  Tooltip
} from '@mui/material';
import {
  Wifi as OnlineIcon,
  WifiOff as OfflineIcon,
  CheckCircle as SuccessIcon,
  Error as ErrorIcon,
} from '@mui/icons-material';
import { GuaardvarkLogo } from '../branding';

// Enhanced Loading Spinner with context
export const ContextualLoader = ({
  loading = false,
  message = "Loading...",
  submessage = null,
  progress = null,
  size = 40,
  variant = "indeterminate",
  estimatedTime = null,
  showProgress = true,
  inline = false,
  branded = false
}) => {
  const [elapsedTime, setElapsedTime] = useState(0);

  useEffect(() => {
    if (!loading) {
      setElapsedTime(0);
      return;
    }

    const interval = setInterval(() => {
      setElapsedTime(prev => prev + 1);
    }, 1000);

    return () => clearInterval(interval);
  }, [loading]);

  if (!loading) return null;

  const formatTime = (seconds) => {
    return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  };

  const LoaderContent = (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 1,
        p: inline ? 1 : 2,
        textAlign: 'center'
      }}
    >
      {/* Main Progress Indicator */}
      <Box sx={{ position: 'relative', display: 'inline-flex' }}>
        {branded && progress === null ? (
          <GuaardvarkLogo size={size} animate />
        ) : (
        <CircularProgress
          variant={variant}
          value={progress}
          size={size}
          thickness={4}
          sx={{
            color: progress !== null && progress > 80 ? 'success.main' : 'primary.main'
          }}
        />)}
        {progress !== null && (
          <Box
            sx={{
              top: 0,
              left: 0,
              bottom: 0,
              right: 0,
              position: 'absolute',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Typography
              variant="caption"
              component="div"
              color="text.secondary"
              sx={{ fontSize: '0.7rem', fontWeight: 'bold' }}
            >
              {`${Math.round(progress)}%`}
            </Typography>
          </Box>
        )}
      </Box>

      {/* Loading Message */}
      <Typography variant="body2" color="text.primary" sx={{ fontWeight: 500 }}>
        {message}
      </Typography>

      {/* Submessage */}
      {submessage && (
        <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
          {submessage}
        </Typography>
      )}

      {/* Progress Info */}
      {showProgress && (
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', justifyContent: 'center' }}>
          <Chip
            label={`${formatTime(elapsedTime)}`}
            size="small"
            variant="outlined"
            sx={{ fontSize: '0.6rem', height: 20 }}
          />
          {estimatedTime && (
            <Chip
              label={`~${estimatedTime}s remaining`}
              size="small"
              variant="outlined"
              color="info"
              sx={{ fontSize: '0.6rem', height: 20 }}
            />
          )}
        </Box>
      )}
    </Box>
  );

  if (inline) {
    return (
      <Fade in={loading}>
        {LoaderContent}
      </Fade>
    );
  }

  return (
    <Slide direction="down" in={loading} mountOnEnter unmountOnExit>
      <Paper
        elevation={2}
        sx={{
          position: 'absolute',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          zIndex: 1000,
          minWidth: 200,
          bgcolor: 'background.paper',
          borderRadius: 2
        }}
      >
        {LoaderContent}
      </Paper>
    </Slide>
  );
};

// Linear Progress Bar with enhanced feedback
export const EnhancedLinearProgress = ({
  progress = null,
  message = "Processing...",
  variant = "indeterminate",
  color = "primary",
  height = 4,
  showPercentage = true,
  animate = true
}) => {
  return (
    <Box sx={{ width: '100%', mb: 1 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.5 }}>
        <Typography variant="caption" color="text.secondary">
          {message}
        </Typography>
        {showPercentage && progress !== null && (
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold' }}>
            {Math.round(progress)}%
          </Typography>
        )}
      </Box>
      <LinearProgress
        variant={variant}
        value={progress}
        color={color}
        sx={{
          height: height,
          borderRadius: height / 2,
          bgcolor: 'grey.200',
          '& .MuiLinearProgress-bar': {
            borderRadius: height / 2,
            transition: animate ? 'transform 0.3s ease' : 'none'
          }
        }}
      />
    </Box>
  );
};

// Skeleton Loading Components
export const ChatMessageSkeleton = ({ count = 3 }) => (
  <Box sx={{ p: 1 }}>
    {Array.from({ length: count }).map((_, index) => (
      <Box key={index} sx={{ mb: 2, display: 'flex', alignItems: 'flex-start', gap: 1 }}>
        <Skeleton variant="circular" width={32} height={32} />
        <Box sx={{ flex: 1 }}>
          <Skeleton variant="text" width="20%" height={16} sx={{ mb: 0.5 }} />
          <Skeleton variant="text" width="90%" height={14} />
          <Skeleton variant="text" width="70%" height={14} />
          {index === 0 && <Skeleton variant="rectangular" width="60%" height={60} sx={{ mt: 1, borderRadius: 1 }} />}
        </Box>
      </Box>
    ))}
  </Box>
);

export const CodeEditorSkeleton = () => (
  <Box sx={{ p: 2 }}>
    <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton key={i} variant="rectangular" width={80} height={32} sx={{ borderRadius: 1 }} />
      ))}
    </Box>
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
      {Array.from({ length: 15 }).map((_, i) => (
        <Skeleton
          key={i}
          variant="text"
          width={`${Math.random() * 40 + 40}%`}
          height={16}
        />
      ))}
    </Box>
  </Box>
);

// Network Status Indicator
export const NetworkStatus = () => {
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [showOfflineAlert, setShowOfflineAlert] = useState(false);

  useEffect(() => {
    const handleOnline = () => {
      setIsOnline(true);
      setShowOfflineAlert(false);
    };

    const handleOffline = () => {
      setIsOnline(false);
      setShowOfflineAlert(true);
    };

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  return (
    <>
      {/* Status Indicator */}
      <Tooltip title={isOnline ? 'Connected' : 'Offline'}>
        <Chip
          icon={isOnline ? <OnlineIcon /> : <OfflineIcon />}
          label={isOnline ? 'Online' : 'Offline'}
          size="small"
          color={isOnline ? 'success' : 'error'}
          variant="outlined"
          sx={{
            fontSize: '0.7rem',
            height: 24,
            '& .MuiChip-icon': {
              fontSize: '0.9rem'
            }
          }}
        />
      </Tooltip>

      {/* Offline Alert */}
      {showOfflineAlert && (
        <Slide direction="down" in={showOfflineAlert}>
          <Alert
            severity="warning"
            sx={{
              position: 'fixed',
              top: 16,
              right: 16,
              zIndex: 2000,
              maxWidth: 400
            }}
            action={
              <IconButton
                size="small"
                onClick={() => setShowOfflineAlert(false)}
                color="inherit"
              >
                ×
              </IconButton>
            }
          >
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 'bold' }}>
                You're offline
              </Typography>
              <Typography variant="caption">
                Some features may not be available. Check your connection.
              </Typography>
            </Box>
          </Alert>
        </Slide>
      )}
    </>
  );
};

// Smart Loading Button
export const LoadingButton = ({
  children,
  loading = false,
  loadingText = "Loading...",
  success = false,
  error = false,
  onClick,
  ...props
}) => {
  const [showSuccess, setShowSuccess] = useState(false);

  useEffect(() => {
    if (success) {
      setShowSuccess(true);
      const timeout = setTimeout(() => setShowSuccess(false), 2000);
      return () => clearTimeout(timeout);
    }
  }, [success]);

  const getButtonColor = () => {
    if (error) return 'error';
    if (showSuccess) return 'success';
    return props.color || 'primary';
  };

  const getButtonContent = () => {
    if (loading) {
      return (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <CircularProgress size={16} color="inherit" />
          {loadingText}
        </Box>
      );
    }
    if (showSuccess) {
      return (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <SuccessIcon sx={{ fontSize: 16 }} />
          Success!
        </Box>
      );
    }
    if (error) {
      return (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <ErrorIcon sx={{ fontSize: 16 }} />
          Error
        </Box>
      );
    }
    return children;
  };

  return (
    <Button
      {...props}
      color={getButtonColor()}
      disabled={loading || props.disabled}
      onClick={loading ? undefined : onClick}
      sx={{
        transition: 'all 0.3s ease',
        ...props.sx
      }}
    >
      {getButtonContent()}
    </Button>
  );
};

// Progress Feedback with stages
export const StageProgress = ({ stages = [], currentStage = 0, error = null }) => {
  return (
    <Box sx={{ width: '100%', py: 2 }}>
      <Typography variant="body2" gutterBottom>
        {error ? 'Process Failed' : stages[currentStage] || 'Processing...'}
      </Typography>

      <LinearProgress
        variant="determinate"
        value={error ? 0 : ((currentStage + 1) / stages.length) * 100}
        color={error ? 'error' : 'primary'}
        sx={{ mb: 1, height: 6, borderRadius: 3 }}
      />

      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography variant="caption" color="text.secondary">
          {error ? 'Failed' : `Step ${currentStage + 1} of ${stages.length}`}
        </Typography>
        {error && (
          <Chip
            icon={<ErrorIcon />}
            label="Error"
            size="small"
            color="error"
            variant="outlined"
          />
        )}
      </Box>
    </Box>
  );
};

export default {
  ContextualLoader,
  EnhancedLinearProgress,
  ChatMessageSkeleton,
  CodeEditorSkeleton,
  NetworkStatus,
  LoadingButton,
  StageProgress
};