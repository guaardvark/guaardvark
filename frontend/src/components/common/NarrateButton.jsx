// NarrateButton.jsx — Generates narration audio from text content
import React, { useState, useRef } from 'react';
import {
  IconButton,
  Button,
  CircularProgress,
  Box,
  Tooltip,
  Typography,
  Link,
} from '@mui/material';
import RecordVoiceOverIcon from '@mui/icons-material/RecordVoiceOver';
import DownloadIcon from '@mui/icons-material/Download';
import CloseIcon from '@mui/icons-material/Close';
import voiceService from '../../api/voiceService';
import { BASE_URL } from '../../api/apiClient';

export default function NarrateButton({ text, voice, size = 'small', variant = 'icon' }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const audioRef = useRef(null);

  const handleNarrate = async () => {
    if (!text || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const data = await voiceService.narrate(text, { voice: voice || 'libritts' });
      setResult(data);
    } catch (err) {
      setError(err.message || 'Narration failed');
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    setResult(null);
    setError(null);
  };

  if (result) {
    const audioSrc = `${BASE_URL}${result.audio_url}`;
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5 }}>
        <audio ref={audioRef} controls src={audioSrc} style={{ height: 32, maxWidth: 260 }} />
        <Tooltip title="Download narration">
          <IconButton
            size="small"
            component={Link}
            href={audioSrc}
            download={result.filename}
          >
            <DownloadIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Close">
          <IconButton size="small" onClick={handleClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Typography variant="caption" color="text.secondary">
          {result.duration_seconds}s · {result.sections} section{result.sections !== 1 ? 's' : ''}
        </Typography>
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography variant="caption" color="error">{error}</Typography>
        <IconButton size="small" onClick={handleClose}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </Box>
    );
  }

  if (variant === 'button') {
    return (
      <Button
        size={size}
        startIcon={loading ? <CircularProgress size={16} /> : <RecordVoiceOverIcon />}
        onClick={handleNarrate}
        disabled={loading || !text}
        sx={{ textTransform: 'none' }}
      >
        {loading ? 'Narrating...' : 'Narrate'}
      </Button>
    );
  }

  return (
    <Tooltip title={loading ? 'Generating narration...' : 'Narrate this text'}>
      <span>
        <IconButton size={size} onClick={handleNarrate} disabled={loading || !text}>
          {loading ? <CircularProgress size={18} /> : <RecordVoiceOverIcon fontSize="small" />}
        </IconButton>
      </span>
    </Tooltip>
  );
}
