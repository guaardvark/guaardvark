// frontend/src/components/images/InfographicGenerator.jsx
// Flux-schnell-backed infographic generator. Two modes:
//   - Structured: guided fields (Title / Scene / Footer / Hashtags / Style / Aspect)
//   - Freeform:   single textarea pasted straight to Flux
//
// Calls /api/infographic/generate synchronously. Backend blocks ~5s on
// a 4070 Ti SUPER and returns a proxied image URL the <img> can render.

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

const InfographicGenerator = () => {
  const [mode, setMode] = useState('structured'); // structured | freeform

  // Structured fields
  const [title, setTitle] = useState('');
  const [scene, setScene] = useState('');
  const [footer, setFooter] = useState('');
  const [hashtags, setHashtags] = useState('');
  const [callouts, setCallouts] = useState('');
  const [style, setStyle] = useState('editorial');
  const [aspect, setAspect] = useState('16:9');

  // Freeform
  const [rawPrompt, setRawPrompt] = useState('');

  // Run state
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState(null); // {image_url, prompt, seed, duration_s, width, height}
  const [error, setError] = useState('');
  const [status, setStatus] = useState(null); // {ready, comfyui_reachable, assets:{...}}

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await getInfographicStatus());
    } catch (e) {
      setStatus({ ready: false, error: e.message });
    }
  }, []);

  useEffect(() => { refreshStatus(); }, [refreshStatus]);

  const canGenerate = (() => {
    if (generating) return false;
    if (mode === 'freeform') return rawPrompt.trim().length > 0;
    return scene.trim().length > 0;
  })();

  const handleGenerate = useCallback(async () => {
    setError('');
    setResult(null);
    setGenerating(true);
    try {
      const payload = mode === 'freeform'
        ? { raw_prompt: rawPrompt, style, aspect }
        : { title, scene, footer, hashtags, callouts, style, aspect };
      const data = await generateInfographic(payload);
      setResult(data);
    } catch (e) {
      setError(e.message || 'Generation failed');
    } finally {
      setGenerating(false);
    }
  }, [mode, rawPrompt, title, scene, footer, hashtags, callouts, style, aspect]);

  const reroll = useCallback(async () => {
    if (!result) return handleGenerate();
    // Same prompt, new seed — backend picks a random one when seed is omitted.
    setError('');
    setGenerating(true);
    try {
      const payload = mode === 'freeform'
        ? { raw_prompt: rawPrompt, style, aspect }
        : { title, scene, footer, hashtags, callouts, style, aspect };
      const data = await generateInfographic(payload);
      setResult(data);
    } catch (e) {
      setError(e.message || 'Generation failed');
    } finally {
      setGenerating(false);
    }
  }, [result, handleGenerate, mode, rawPrompt, title, scene, footer, hashtags, callouts, style, aspect]);

  // Status banner — surfaces missing assets / stopped ComfyUI before
  // the user wastes a click and waits 60s for a timeout.
  const renderStatusBanner = () => {
    if (!status) return null;
    if (status.ready) return null;
    if (!status.comfyui_reachable) {
      return (
        <Alert severity="warning" sx={{ mb: 2 }}>
          ComfyUI isn't running. Enable the <strong>comfyui</strong> plugin
          on /plugins (or start it manually) and refresh.
          <Button size="small" onClick={refreshStatus} sx={{ ml: 1 }}>Refresh</Button>
        </Alert>
      );
    }
    const missing = status.assets
      ? Object.entries(status.assets).filter(([, ok]) => !ok).map(([k]) => k)
      : [];
    if (missing.length) {
      return (
        <Alert severity="error" sx={{ mb: 2 }}>
          Missing Flux model assets: {missing.join(', ')}.
        </Alert>
      );
    }
    return null;
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, p: 2, maxWidth: 1600 }}>
      <Box>
        <Typography variant="h6" sx={{ fontWeight: 600 }}>
          Infographic Generator
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Flux schnell with integrated text. Title, footer and short labels
          render legibly — keep them brief for best results.
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
        <ToggleButton value="structured">Structured</ToggleButton>
        <ToggleButton value="freeform">Freeform</ToggleButton>
      </ToggleButtonGroup>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2 }}>
        {/* LEFT — form */}
        <Paper variant="outlined" sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 2 }}>
          {mode === 'structured' ? (
            <>
              <TextField
                label="Title (top headline)"
                placeholder="POST 2: THE GRID SOLUTION"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                size="small"
                fullWidth
                inputProps={{ maxLength: 90 }}
              />
              <TextField
                label="Scene description"
                placeholder="Transmission towers under a red heatwave sky, a hand flipping a breaker switch, a Tesla Megapack array, an Imperial Valley data center, flow arrows between them..."
                value={scene}
                onChange={(e) => setScene(e.target.value)}
                multiline
                minRows={4}
                size="small"
                fullWidth
              />
              <TextField
                label="Callout labels (one per line, optional)"
                placeholder={'SUMMER HEATWAVE\n220 TESLA MEGAPACKS\nIMPERIAL VALLEY DATA CENTER'}
                value={callouts}
                onChange={(e) => setCallouts(e.target.value)}
                multiline
                minRows={3}
                size="small"
                fullWidth
                helperText="Each line becomes a separate text label in the image. Keep short — Flux gets shaky past ~5 words."
              />
              <TextField
                label="Footer banner (optional)"
                placeholder="THE DATA CENTER DOESN'T DRAIN OUR GRID, IT PROTECTS IT."
                value={footer}
                onChange={(e) => setFooter(e.target.value)}
                size="small"
                fullWidth
                inputProps={{ maxLength: 140 }}
              />
              <TextField
                label="Hashtags (optional)"
                placeholder="#GridResilience #TeslaMegapack #ImperialValley"
                value={hashtags}
                onChange={(e) => setHashtags(e.target.value)}
                size="small"
                fullWidth
                helperText="Space or comma separated. # is optional — added if missing."
              />
            </>
          ) : (
            <TextField
              label="Freeform prompt"
              placeholder="Type or paste a full prompt. Use quotes for literal text. Example: Infographic poster, dramatic editorial illustration. Transmission towers under heat haze. Title at top reads: 'GRID SOLUTION'. Bottom banner reads: 'INNOVATION OVER FEAR'."
              value={rawPrompt}
              onChange={(e) => setRawPrompt(e.target.value)}
              multiline
              minRows={10}
              size="small"
              fullWidth
            />
          )}

          <Stack direction="row" spacing={2}>
            <TextField
              select
              label="Style"
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
              label="Aspect"
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
              {generating ? 'Generating…' : 'Generate'}
            </Button>
            {result && (
              <Button
                startIcon={<RefreshIcon />}
                onClick={reroll}
                disabled={generating}
                size="medium"
              >
                Re-roll
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
                Flux schnell, 4 steps. Usually ~5s.
              </Typography>
            </Stack>
          )}
          {!generating && !result && (
            <Typography variant="body2" sx={{ color: 'text.disabled' }}>
              Preview appears here
            </Typography>
          )}
          {!generating && result && (
            <Box sx={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 1 }}>
              <Box
                component="img"
                src={result.image_url}
                alt="generated infographic"
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
                  label={`${result.width}×${result.height}`}
                  variant="outlined"
                />
                <Chip
                  size="small"
                  label={`seed ${result.seed}`}
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
                  Download
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
