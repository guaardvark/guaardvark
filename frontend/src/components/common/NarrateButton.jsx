// NarrateButton.jsx — Generates narration audio from text content
// Supports Piper (fast) and Bark (expressive) TTS engines
import React, { useState, useRef } from 'react';
import {
  IconButton,
  Button,
  CircularProgress,
  Box,
  Tooltip,
  Typography,
  Link,
  Chip,
} from '@mui/material';
import RecordVoiceOverIcon from '@mui/icons-material/RecordVoiceOver';
import DownloadIcon from '@mui/icons-material/Download';
import CloseIcon from '@mui/icons-material/Close';
import voiceService from '../../api/voiceService';
import { BASE_URL } from '../../api/apiClient';

export default function NarrateButton({ text, voice, size = 'small', variant = 'icon', showEngineToggle = true }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [engine, setEngine] = useState('piper'); // 'piper' or 'bark'
  const audioRef = useRef(null);

  const isBark = engine === 'bark';

  const handleNarrate = async () => {
    if (!text || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const options = {
        voice: isBark ? 'bark_speaker_3' : (voice || 'libritts'),
        engine: engine,
      };
      const data = await voiceService.narrate(text, options);
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

  const engineToggle = showEngineToggle ? (
    <Box sx={{ display: 'inline-flex', gap: 0.5, ml: 0.5 }}>
      <Chip
        label="Fast"
        size="small"
        variant={!isBark ? 'filled' : 'outlined'}
        color={!isBark ? 'primary' : 'default'}
        onClick={() => !loading && setEngine('piper')}
        sx={{ height: 20, fontSize: '0.65rem', cursor: loading ? 'default' : 'pointer' }}
      />
      <Chip
        label="Expressive"
        size="small"
        variant={isBark ? 'filled' : 'outlined'}
        color={isBark ? 'secondary' : 'default'}
        onClick={() => !loading && setEngine('bark')}
        sx={{ height: 20, fontSize: '0.65rem', cursor: loading ? 'default' : 'pointer' }}
      />
    </Box>
  ) : null;

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
          {result.engine === 'bark' ? ' · Bark' : ''}
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

  const loadingText = isBark ? 'Generating (slow)...' : 'Narrating...';

  if (variant === 'button') {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <Button
          size={size}
          startIcon={loading ? <CircularProgress size={16} /> : <RecordVoiceOverIcon />}
          onClick={handleNarrate}
          disabled={loading || !text}
          sx={{ textTransform: 'none' }}
        >
          {loading ? loadingText : 'Narrate'}
        </Button>
        {engineToggle}
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'inline-flex', alignItems: 'center' }}>
      <Tooltip title={loading ? (isBark ? 'Bark generating (10-30s per section)...' : 'Generating narration...') : 'Narrate this text'}>
        <span>
          <IconButton size={size} onClick={handleNarrate} disabled={loading || !text}>
            {loading ? <CircularProgress size={18} /> : <RecordVoiceOverIcon fontSize="small" />}
          </IconButton>
        </span>
      </Tooltip>
      {engineToggle}
    </Box>
  );
}
