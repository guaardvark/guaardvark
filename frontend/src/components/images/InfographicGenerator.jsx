// frontend/src/components/images/InfographicGenerator.jsx
// Infographic generator. Two modes:
//   - Structured: guided fields (Title / Scene / Footer / Hashtags / Style / Aspect)
//   - Freeform:   single textarea sent to the model as written
//
// Calls /api/infographic/generate synchronously. The backend renders with the
// chosen model (Krea 2 Turbo, else Z-Image Turbo, by default) and returns an
// image URL the <img> can render.

import React, { useEffect, useState, useCallback } from 'react';
import {
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  MenuItem,
  Alert,
  CircularProgress,
  Stack,
  ToggleButtonGroup,
  ToggleButton,
  Chip,
} from '@mui/material';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import DownloadIcon from '@mui/icons-material/Download';
import RefreshIcon from '@mui/icons-material/Refresh';
import {
  generateInfographic,
  getInfographicStatus,
  INFOGRAPHIC_STYLES,
  INFOGRAPHIC_ASPECTS,
} from '../../api/infographicService';
import { useUiTranslation, Trans } from '../../i18n';

const InfographicGenerator = () => {
  const { t } = useUiTranslation();
  const [mode, setMode] = useState('structured'); // structured | freeform

  // Structured fields
  const [title, setTitle] = useState('');
  const [scene, setScene] = useState('');
  const [footer, setFooter] = useState('');
  const [hashtags, setHashtags] = useState('');
  const [callouts, setCallouts] = useState('');
  const [style, setStyle] = useState('editorial');
  const [aspect, setAspect] = useState('16:9');
  const [model, setModel] = useState('');

  // Freeform
  const [rawPrompt, setRawPrompt] = useState('');

  // Run state
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState(null); // {image_url, prompt, seed, duration_s, width, height}
  const [error, setError] = useState('');
  const [status, setStatus] = useState(null); // {ready, default_model, models:[...]}

  const refreshStatus = useCallback(async () => {
    try {
      const next = await getInfographicStatus();
      setStatus(next);
      // Keep a choice that is still installed; otherwise follow the default.
      setModel((prev) => {
        const installed = (next.models || []).filter((m) => m.installed).map((m) => m.id);
        return installed.includes(prev) ? prev : (next.default_model || installed[0] || '');
      });
    } catch (e) {
      setStatus({ ready: false, error: e.message });
    }
  }, []);

  const installedModels = (status?.models || []).filter((m) => m.installed);
  const chosenModel = installedModels.find((m) => m.id === model);

  useEffect(() => { refreshStatus(); }, [refreshStatus]);

  const canGenerate = (() => {
    if (generating || !model) return false;
    if (mode === 'freeform') return rawPrompt.trim().length > 0;
    return scene.trim().length > 0;
  })();

  const handleGenerate = useCallback(async () => {
    setError('');
    setResult(null);
    setGenerating(true);
    try {
      const payload = mode === 'freeform'
        ? { raw_prompt: rawPrompt, style, aspect, model }
        : { title, scene, footer, hashtags, callouts, style, aspect, model };
      const data = await generateInfographic(payload);
      setResult(data);
    } catch (e) {
      setError(e.message || t('images.infographic.failed'));
    } finally {
      setGenerating(false);
    }
  }, [mode, rawPrompt, title, scene, footer, hashtags, callouts, style, aspect, model]);

  const reroll = useCallback(async () => {
    if (!result) return handleGenerate();
    // Same prompt, new seed — backend picks a random one when seed is omitted.
    setError('');
    setGenerating(true);
    try {
      const payload = mode === 'freeform'
        ? { raw_prompt: rawPrompt, style, aspect, model }
        : { title, scene, footer, hashtags, callouts, style, aspect, model };
      const data = await generateInfographic(payload);
      setResult(data);
    } catch (e) {
      setError(e.message || t('images.infographic.failed'));
    } finally {
      setGenerating(false);
    }
  }, [result, handleGenerate, mode, rawPrompt, title, scene, footer, hashtags, callouts, style, aspect, model]);

  // Status banner: no model installed means nothing can render, so say how
  // to get one before the user fills in the form.
  const renderStatusBanner = () => {
    if (!status || installedModels.length) return null;
    return (
      <Alert severity="warning" sx={{ mb: 2 }}>
        <Trans i18nKey="images.infographic.noModel" components={{ bold: <strong /> }} />
        <Button size="small" onClick={refreshStatus} sx={{ ml: 1 }}>{t('images.infographic.refresh')}</Button>
      </Alert>
    );
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, p: 2, maxWidth: 1600 }}>
      <Box>
        <Typography variant="h6" sx={{ fontWeight: 600 }}>
          {t('images.infographic.title')}
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          {t('images.infographic.intro')}
        </Typography>
      </Box>

      {renderStatusBanner()}

      <ToggleButtonGroup
        value={mode}
        exclusive
        onChange={(_e, v) => v && setMode(v)}
        size="small"
        sx={{ alignSelf: 'flex-start' }}
      >
        <ToggleButton value="structured">{t('images.infographic.structured')}</ToggleButton>
        <ToggleButton value="freeform">{t('images.infographic.freeform')}</ToggleButton>
      </ToggleButtonGroup>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2 }}>
        {/* LEFT — form */}
        <Paper variant="outlined" sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 2 }}>
          {mode === 'structured' ? (
            <>
              <TextField
                label={t('images.infographic.titleLabel')}
                placeholder={t('images.infographic.titlePlaceholder')}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                size="small"
                fullWidth
                inputProps={{ maxLength: 90 }}
              />
              <TextField
                label={t('images.infographic.sceneLabel')}
                placeholder={t('images.infographic.scenePlaceholder')}
                value={scene}
                onChange={(e) => setScene(e.target.value)}
                multiline
                minRows={4}
                size="small"
                fullWidth
              />
              <TextField
                label={t('images.infographic.calloutsLabel')}
                placeholder={t('images.infographic.calloutsPlaceholder')}
                value={callouts}
                onChange={(e) => setCallouts(e.target.value)}
                multiline
                minRows={3}
                size="small"
                fullWidth
                helperText={t('images.infographic.calloutsHelp')}
              />
              <TextField
                label={t('images.infographic.footerLabel')}
                placeholder={t('images.infographic.footerPlaceholder')}
                value={footer}
                onChange={(e) => setFooter(e.target.value)}
                size="small"
                fullWidth
                inputProps={{ maxLength: 140 }}
              />
              <TextField
                label={t('images.infographic.hashtagsLabel')}
                placeholder={t('images.infographic.hashtagsPlaceholder')}
                value={hashtags}
                onChange={(e) => setHashtags(e.target.value)}
                size="small"
                fullWidth
                helperText={t('images.infographic.hashtagsHelp')}
              />
            </>
          ) : (
            <TextField
              label={t('images.infographic.freeformLabel')}
              placeholder={t('images.infographic.freeformPlaceholder')}
              value={rawPrompt}
              onChange={(e) => setRawPrompt(e.target.value)}
              multiline
              minRows={10}
              size="small"
              fullWidth
            />
          )}

          <TextField
            select
            label={t('images.infographic.model')}
            value={chosenModel ? model : ''}
            onChange={(e) => setModel(e.target.value)}
            size="small"
            fullWidth
            disabled={!installedModels.length}
            helperText={chosenModel?.license_note || ' '}
          >
            {installedModels.map((m) => (
              <MenuItem key={m.id} value={m.id}>{m.name}</MenuItem>
            ))}
          </TextField>

          <Stack direction="row" spacing={2}>
            <TextField
              select
              label={t('images.infographic.style')}
              value={style}
              onChange={(e) => setStyle(e.target.value)}
              size="small"
              sx={{ flex: 1 }}
            >
              {INFOGRAPHIC_STYLES.map((s) => (
                <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label={t('images.infographic.aspect')}
              value={aspect}
              onChange={(e) => setAspect(e.target.value)}
              size="small"
              sx={{ flex: 1 }}
            >
              {INFOGRAPHIC_ASPECTS.map((a) => (
                <MenuItem key={a.value} value={a.value}>{a.label}</MenuItem>
              ))}
            </TextField>
          </Stack>

          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mt: 1 }}>
            <Button
              variant="contained"
              startIcon={generating ? <CircularProgress size={16} color="inherit" /> : <AutoAwesomeIcon />}
              onClick={handleGenerate}
              disabled={!canGenerate}
              size="medium"
            >
              {generating ? t('images.infographic.generating') : t('images.shared.generate')}
            </Button>
            {result && (
              <Button
                startIcon={<RefreshIcon />}
                onClick={reroll}
                disabled={generating}
                size="medium"
              >
                {t('images.infographic.reroll')}
              </Button>
            )}
            {error && (
              <Alert severity="error" sx={{ flex: 1, py: 0 }}>{error}</Alert>
            )}
          </Box>
        </Paper>

        {/* RIGHT — preview */}
        <Paper
          variant="outlined"
          sx={{
            p: 2,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: 480,
            position: 'relative',
            bgcolor: 'rgba(0,0,0,0.02)',
          }}
        >
          {generating && (
            <Stack alignItems="center" spacing={1.5}>
              <CircularProgress size={36} />
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                {t('images.infographic.generatingHint', { model: chosenModel?.name || model })}
              </Typography>
            </Stack>
          )}
          {!generating && !result && (
            <Typography variant="body2" sx={{ color: 'text.secondary' }}>
              {t('images.infographic.previewEmpty')}
            </Typography>
          )}
          {!generating && result && (
            <Box sx={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 1 }}>
              <Box
                component="img"
                src={result.image_url}
                alt={t('images.infographic.resultAlt')}
                sx={{
                  width: '100%',
                  height: 'auto',
                  borderRadius: 1,
                  border: 1,
                  borderColor: 'divider',
                  display: 'block',
                }}
              />
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                <Chip
                  size="small"
                  label={result.model_name || result.model}
                  variant="outlined"
                />
                <Chip
                  size="small"
                  label={`${result.width}×${result.height}`}
                  variant="outlined"
                />
                <Chip
                  size="small"
                  label={t('images.infographic.seed', { seed: result.seed })}
                  variant="outlined"
                />
                <Chip
                  size="small"
                  label={`${result.duration_s}s`}
                  variant="outlined"
                />
                <Box sx={{ flex: 1 }} />
                <Button
                  size="small"
                  startIcon={<DownloadIcon />}
                  component="a"
                  href={result.image_url}
                  download={result.filename}
                >
                  {t('images.shared.download')}
                </Button>
              </Stack>
              <Typography
                variant="caption"
                sx={{
                  color: 'text.secondary',
                  fontFamily: 'monospace',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  mt: 1,
                }}
              >
                {result.prompt}
              </Typography>
            </Box>
          )}
        </Paper>
      </Box>
    </Box>
  );
};

export default InfographicGenerator;
