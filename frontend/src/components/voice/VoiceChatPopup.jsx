import React, { useEffect, useState, useRef } from 'react';
import PropTypes from 'prop-types';
import {
  Box,
  Typography,
  IconButton,
  Paper,
  Chip,
  Divider,
  Tooltip,
  CircularProgress,
  useTheme,
} from '@mui/material';
import {
  Close as CloseIcon,
  Mic as MicIcon,
  MicOff as MicOffIcon,
  VolumeUp as VolumeUpIcon,
  GraphicEq as GraphicEqIcon,
} from '@mui/icons-material';
import { keyframes } from '@mui/material/styles';

// Animations
const pulseAnimation = keyframes`
  0% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.05); opacity: 0.9; }
  100% { transform: scale(1); opacity: 1; }
`;

const waveformAnimation = keyframes`
  0%, 100% { height: 20%; }
  50% { height: 100%; }
`;

const glowAnimation = keyframes`
  0% { box-shadow: 0 0 5px rgba(25, 118, 210, 0.3); }
  50% { box-shadow: 0 0 20px rgba(25, 118, 210, 0.8); }
  100% { box-shadow: 0 0 5px rgba(25, 118, 210, 0.3); }
`;

/**
 * VoiceChatPopup - Draggable popup window for voice chat with dual waveforms
 *
 * Features:
 * - Draggable window (like System Metrics)
 * - Input waveform (user speech)
 * - Output waveform (AI TTS response)
 * - Voice activity detection visualization
 * - Processing status indicators
 * - Segment count and stats
 */
const VoiceChatPopup = ({
  open,
  onClose,
  isListening,
  onToggleListening,
  audioLevels = [],
  speechDetected = false,
  segmentCount = 0,
  processingQueue = 0,
  waveformActive = false,
  currentVolume = 0,
}) => {
  const theme = useTheme();

  // Dragging state
  const [isDragging, setIsDragging] = useState(false);
  const [position, setPosition] = useState({ x: 100, y: 100 });
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const modalRef = useRef(null);

  // Unified waveform state - shows either input or output
  const [displayMode, setDisplayMode] = useState('input'); // 'input' or 'output'

  // Handle drag start
  const handleMouseDown = (e) => {
    if (e.target.closest('.no-drag')) return; // Don't drag if clicking buttons

    setIsDragging(true);
    setDragOffset({
      x: e.clientX - position.x,
      y: e.clientY - position.y,
    });
  };

  // Handle drag move
  useEffect(() => {
    const handleMouseMove = (e) => {
      if (isDragging) {
        setPosition({
          x: e.clientX - dragOffset.x,
          y: e.clientY - dragOffset.y,
        });
      }
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, dragOffset]);

  // Auto-switch display mode based on activity
  useEffect(() => {
    if (speechDetected) {
      setDisplayMode('input');
    } else if (processingQueue > 0) {
      setDisplayMode('output');
    }
  }, [speechDetected, processingQueue]);

  // Determine current waveform state
  const getCurrentWaveformState = () => {
    if (displayMode === 'input') {
      return {
        levels: audioLevels.length > 0 ? audioLevels : new Array(40).fill(0),
        active: isListening && speechDetected,
        color: theme.palette.error.main,
        label: 'Your Voice',
        icon: MicIcon,
        initializing: isListening && !waveformActive
      };
    } else {
      // Output mode - simulated for now
      const simulatedLevels = Array.from({ length: 40 }, () => Math.random() * 0.7);
      return {
        levels: processingQueue > 0 ? simulatedLevels : new Array(40).fill(0),
        active: processingQueue > 0,
        color: theme.palette.success.main,
        label: 'AI Speaking',
        icon: VolumeUpIcon,
        initializing: false
      };
    }
  };

  const waveformState = getCurrentWaveformState();

  // Don't render if not open (AFTER all hooks)
  if (!open) return null;

  return (
    <Paper
      ref={modalRef}
      sx={{
        position: 'fixed',
        left: position.x,
        top: position.y,
        width: 400,
        maxHeight: '80vh',
        overflow: 'auto',
        zIndex: 1300,
        cursor: isDragging ? 'grabbing' : 'grab',
        boxShadow: theme.shadows[10],
        border: `2px solid ${isListening ? theme.palette.primary.main : theme.palette.divider}`,
        animation: isListening ? `${glowAnimation} 2s ease-in-out infinite` : 'none',
      }}
      onMouseDown={handleMouseDown}
    >
      {/* Header */}
      <Box
        sx={{
          p: 2,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          backgroundColor: theme.palette.mode === 'dark' ? 'grey.900' : 'primary.main',
          color: theme.palette.mode === 'dark' ? 'white' : 'white',
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <GraphicEqIcon />
          <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 'bold' }}>
            Voice Chat Monitor
          </Typography>
        </Box>
        <IconButton
          onClick={onClose}
          size="small"
          className="no-drag"
          sx={{ color: 'white' }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </Box>

      {/* Content */}
      <Box sx={{ p: 2 }}>
        {/* Status Bar */}
        <Box
          sx={{
            display: 'flex',
            gap: 1,
            mb: 2,
            flexWrap: 'wrap',
            p: 1,
            backgroundColor: theme.palette.mode === 'dark' ? 'grey.900' : 'grey.50',
            borderRadius: 1,
          }}
        >
          <Chip
            label={isListening ? 'LISTENING' : 'IDLE'}
            size="small"
            color={isListening ? 'success' : 'default'}
            icon={isListening ? <MicIcon /> : <MicOffIcon />}
          />
          {speechDetected && (
            <Chip
              label="SPEAKING"
              size="small"
              color="error"
              sx={{
                animation: `${pulseAnimation} 1s ease-in-out infinite`,
              }}
            />
          )}
          {segmentCount > 0 && (
            <Chip
              label={`${segmentCount} sent`}
              size="small"
              color="primary"
              variant="outlined"
            />
          )}
          {processingQueue > 0 && (
            <Chip
              label={`${processingQueue} processing`}
              size="small"
              color="warning"
              icon={<CircularProgress size={12} sx={{ color: 'inherit' }} />}
            />
          )}
        </Box>

        <Divider sx={{ my: 2 }} />

        {/* Unified Waveform - Shows input or output */}
        <Box sx={{ mb: 2 }}>
          {/* Header */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            {React.createElement(waveformState.icon, {
              sx: {
                fontSize: '1rem',
                color: waveformState.active ? waveformState.color : 'text.secondary',
                animation: waveformState.active ? `${pulseAnimation} 2s ease-in-out infinite` : 'none'
              }
            })}
            <Typography
              variant="caption"
              sx={{
                fontWeight: 'bold',
                color: waveformState.active ? waveformState.color : 'text.secondary'
              }}
            >
              {waveformState.label}
            </Typography>
            {waveformState.active && (
              <Chip
                label="ACTIVE"
                size="small"
                sx={{
                  height: 16,
                  fontSize: '0.6rem',
                  backgroundColor: waveformState.color,
                  color: 'white',
                  animation: `${pulseAnimation} 1.5s ease-in-out infinite`
                }}
              />
            )}
          </Box>

          {/* Waveform */}
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 0.3,
              height: 100,
              px: 2,
              backgroundColor: theme.palette.mode === 'dark' ? 'grey.900' : 'grey.100',
              borderRadius: 1,
              border: 1,
              borderColor: waveformState.active ? waveformState.color : 'divider',
              position: 'relative',
              overflow: 'hidden',
              boxShadow: waveformState.active ? `0 0 10px ${waveformState.color}40` : 'none',
            }}
          >
            {waveformState.levels.map((level, index) => (
              <Box
                key={index}
                sx={{
                  width: 3,
                  height: `${Math.max(10, level * 100)}%`,
                  maxHeight: '100%',
                  backgroundColor: waveformState.active ? waveformState.color : theme.palette.grey[400],
                  borderRadius: 1,
                  transition: 'height 0.1s ease-out',
                  opacity: waveformState.active ? 0.7 + (level * 0.3) : 0.3,
                  animation: waveformState.active && level > 0.2
                    ? `${waveformAnimation} ${0.5 + Math.random() * 0.5}s ease-in-out infinite`
                    : 'none',
                  animationDelay: `${index * 0.02}s`,
                }}
              />
            ))}

            {/* Initializing overlay */}
            {waveformState.initializing && (
              <Box
                sx={{
                  position: 'absolute',
                  top: '50%',
                  left: '50%',
                  transform: 'translate(-50%, -50%)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  backgroundColor: theme.palette.mode === 'dark' ? 'rgba(0,0,0,0.7)' : 'rgba(255,255,255,0.9)',
                  px: 2,
                  py: 1,
                  borderRadius: 1,
                }}
              >
                <CircularProgress size={16} />
                <Typography variant="caption" sx={{ fontSize: '0.7rem' }}>
                  Initializing audio...
                </Typography>
              </Box>
            )}

            {/* Idle state */}
            {!waveformState.active && !waveformState.initializing && (
              <Box
                sx={{
                  position: 'absolute',
                  top: '50%',
                  left: '50%',
                  transform: 'translate(-50%, -50%)',
                }}
              >
                <Typography
                  variant="caption"
                  sx={{
                    fontSize: '0.7rem',
                    color: 'text.disabled',
                    fontStyle: 'italic'
                  }}
                >
                  {isListening ? 'Listening...' : 'Click microphone to start'}
                </Typography>
              </Box>
            )}
          </Box>
        </Box>

        <Divider sx={{ my: 2 }} />

        {/* Controls */}
        <Box sx={{ display: 'flex', gap: 1, justifyContent: 'center' }}>
          <Tooltip title={isListening ? 'Stop listening' : 'Start listening'}>
            <IconButton
              onClick={onToggleListening}
              className="no-drag"
              sx={{
                width: 60,
                height: 60,
                backgroundColor: isListening ? 'error.main' : 'primary.main',
                color: 'white',
                '&:hover': {
                  backgroundColor: isListening ? 'error.dark' : 'primary.dark',
                },
                animation: isListening ? `${pulseAnimation} 2s ease-in-out infinite` : 'none',
              }}
            >
              {isListening ? <MicIcon /> : <MicOffIcon />}
            </IconButton>
          </Tooltip>
        </Box>

        {/* Stats */}
        <Box
          sx={{
            mt: 2,
            p: 1,
            backgroundColor: theme.palette.mode === 'dark' ? 'grey.900' : 'grey.50',
            borderRadius: 1,
          }}
        >
          <Typography variant="caption" sx={{ display: 'block', fontSize: '0.7rem', color: 'text.secondary' }}>
            Volume: {(currentVolume * 100).toFixed(1)}%
          </Typography>
          <Typography variant="caption" sx={{ display: 'block', fontSize: '0.7rem', color: 'text.secondary' }}>
            Status: {isListening ? (speechDetected ? 'Detecting speech' : 'Listening for speech') : 'Inactive'}
          </Typography>
        </Box>

        {/* Help Text */}
        <Typography
          variant="caption"
          sx={{
            display: 'block',
            mt: 2,
            fontSize: '0.65rem',
            color: 'text.disabled',
            fontStyle: 'italic',
            textAlign: 'center'
          }}
        >
          Drag this window anywhere • Hold Spacebar for quick activation
        </Typography>
      </Box>
    </Paper>
  );
};

VoiceChatPopup.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  isListening: PropTypes.bool,
  onToggleListening: PropTypes.func,
  audioLevels: PropTypes.arrayOf(PropTypes.number),
  speechDetected: PropTypes.bool,
  segmentCount: PropTypes.number,
  processingQueue: PropTypes.number,
  waveformActive: PropTypes.bool,
  currentVolume: PropTypes.number,
};

export default VoiceChatPopup;
