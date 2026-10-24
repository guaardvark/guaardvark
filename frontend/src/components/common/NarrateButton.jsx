// NarrateButton.jsx — Generates narration audio from text content
// Two engines: "Fast" (Piper, the user-selected voice) and "Expressive"
// (audio_foundry plugin: Chatterbox primary, Kokoro fallback). Backend
// silently falls back to Piper if audio_foundry isn't running, so the
// button never breaks even when the plugin is disabled.
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
import { useUiTranslation } from '../../i18n';

export default function NarrateButton({ text, voice, size = 'small', variant = 'icon', showEngineToggle = true }) {
  const { t } = useUiTranslation();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [engine, setEngine] = useState('piper'); // 'piper' (Fast) or 'expressive' (Chatterbox/Kokoro)
  const audioRef = useRef(null);

  const isExpressive = engine === 'expressive';

  const handleNarrate = async () => {
    if (!text || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      // For Expressive, voice id is irrelevant — audio_foundry's dispatcher
      // picks Chatterbox first, Kokoro on fallback. For Piper we honor the
      // user's selected voice.
      const options = {
        voice: voice || 'libritts',
        engine: engine,
      };
      const data = await voiceService.narrate(text, options);
      setResult(data);
    } catch (err) {
      setError(err.message || t('layoutCommon.narrate.failed'));
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
        label={t('layoutCommon.narrate.fast')}
        size="small"
        variant={!isExpressive ? 'filled' : 'outlined'}
        color={!isExpressive ? 'primary' : 'default'}
        onClick={() => !loading && setEngine('piper')}
        sx={{ height: 20, fontSize: '0.65rem', cursor: loading ? 'default' : 'pointer' }}
      />
      <Chip
        label={t('layoutCommon.narrate.expressive')}
        size="small"
        variant={isExpressive ? 'filled' : 'outlined'}
        color={isExpressive ? 'secondary' : 'default'}
        onClick={() => !loading && setEngine('expressive')}
        sx={{ height: 20, fontSize: '0.65rem', cursor: loading ? 'default' : 'pointer' }}
      />
    </Box>
  ) : null;

  if (result) {
    const audioSrc = `${BASE_URL}${result.audio_url}`;
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5 }}>
        <audio ref={audioRef} controls src={audioSrc} style={{ height: 32, maxWidth: 260 }} />
        <Tooltip title={t('layoutCommon.narrate.download')}>
          <IconButton
            size="small"
            component={Link}
            href={audioSrc}
            download={result.filename}
          >
            <DownloadIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={t('common.close')}>
          <IconButton size="small" onClick={handleClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Typography variant="caption" color="text.secondary">
          {t('layoutCommon.narrate.summary', { seconds: result.duration_seconds, count: result.sections })}
          {result.engine && result.engine !== 'piper-tts' ? ` · ${result.engine}` : ''}
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

  const loadingText = isExpressive ? t('layoutCommon.narrate.generatingSlow') : t('layoutCommon.narrate.narrating');

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
          {loading ? loadingText : t('layoutCommon.narrate.narrate')}
        </Button>
        {engineToggle}
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'inline-flex', alignItems: 'center' }}>
      <Tooltip title={loading ? (isExpressive ? t('layoutCommon.narrate.expressiveGenerating') : t('layoutCommon.narrate.generating')) : t('layoutCommon.narrate.narrateText')}>
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
