import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Autocomplete,
  Box,
  Alert,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Typography
} from '@mui/material';
import { getProjects } from '../../api/projectService';
import { useUiTranslation } from '../../i18n';

const API_BASE = '/api';

const CreateProductionDialog = ({ open, onClose, onCreated }) => {
  const { t } = useUiTranslation();
  const [name, setName] = useState('');
  const [scriptText, setScriptText] = useState('');
  const [projectId, setProjectId] = useState(null);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Installed video models that can animate a storyboard still, from the
  // registry; a model that renders its own soundtrack is flagged so the
  // choice says what it changes (scene windows with spoken lines).
  const [videoModels, setVideoModels] = useState([]);
  const [videoModel, setVideoModel] = useState('');
  // Frame shape for the storyboard stills and clips; empty sends nothing, so the backend default (16:9) applies.
  const [aspectRatio, setAspectRatio] = useState('');

  useEffect(() => {
    if (open) {
      loadProjects();
      loadVideoModels();
    }
  }, [open]);

  const loadVideoModels = async () => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/models`);
      const data = res.ok ? await res.json() : null;
      const rows = (data?.data?.models || []).filter(
        (m) => m.capabilities && (m.capabilities.supports_i2v || (m.capabilities.modes || []).includes('ref2v'))
      );
      setVideoModels(rows);
    } catch (e) {
      setVideoModels([]);
    }
  };

  const loadProjects = async () => {
    const data = await getProjects();
    if (Array.isArray(data)) {
      setProjects(data);
    }
  };

  const handleSubmit = async () => {
    if (!name || !scriptText) {
      setError(t('filmCrew.create.required'));
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const settings = {
        ...(videoModel ? { video_model: videoModel } : {}),
        ...(aspectRatio ? { aspect_ratio: aspectRatio } : {}),
      };
      const payload = {
        name,
        script_text: scriptText,
        project_id: projectId?.id || null,
        ...(Object.keys(settings).length ? { settings } : {})
      };
      await onCreated(payload);
      onClose();
      // Reset form
      setName('');
      setScriptText('');
      setProjectId(null);
      setVideoModel('');
      setAspectRatio('');
    } catch (err) {
      setError(err.message || t('filmCrew.create.failed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{t('filmCrew.create.title')}</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label={t('filmCrew.create.name')}
            fullWidth
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <Autocomplete
            options={projects}
            getOptionLabel={(option) => option.name || ''}
            renderInput={(params) => <TextField {...params} label={t('filmCrew.create.project')} />}
            value={projectId}
            onChange={(_, newValue) => setProjectId(newValue)}
          />
          {videoModels.length > 0 && (
            <FormControl fullWidth size="small">
              <InputLabel>{t('filmCrew.create.videoModel')}</InputLabel>
              <Select
                value={videoModel}
                onChange={(e) => setVideoModel(e.target.value)}
                label={t('filmCrew.create.videoModel')}
              >
                <MenuItem value="">{t('filmCrew.create.defaultVideoModel')}</MenuItem>
                {videoModels.map((m) => (
                  <MenuItem key={m.id} value={m.id} disabled={!m.is_ready}>
                    <Box>
                      <Typography variant="body2">
                        {m.name}{m.is_ready ? '' : ` ${t('filmCrew.create.notInstalled')}`}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {m.capabilities.audio_out
                          ? t('filmCrew.create.audioModelHint')
                          : t('filmCrew.create.silentModelHint')}
                        {m.license?.attribution ? ` · ${m.license.attribution}` : ''}
                      </Typography>
                    </Box>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
          <FormControl fullWidth size="small">
            <InputLabel>{t('filmCrew.create.frame')}</InputLabel>
            <Select
              value={aspectRatio}
              onChange={(e) => setAspectRatio(e.target.value)}
              label={t('filmCrew.create.frame')}
            >
              <MenuItem value="">{t('filmCrew.create.frameWide')}</MenuItem>
              <MenuItem value="9:16">{t('filmCrew.create.frameTall')}</MenuItem>
              <MenuItem value="1:1">{t('filmCrew.create.frameSquare')}</MenuItem>
            </Select>
          </FormControl>
          <TextField
            label={t('filmCrew.create.script')}
            fullWidth
            multiline
            rows={10}
            value={scriptText}
            onChange={(e) => setScriptText(e.target.value)}
            required
            placeholder={t('filmCrew.create.scriptPlaceholder')}
            // The markup tokens go in as values: they are syntax the screenwriter
            // parses, and a literal {{…}} in the string would be read as a placeholder.
            helperText={t('filmCrew.create.castingMarkup', {
              pin: '[[Name]]',
              pinKind: '[[Name:prop]]',
              inline: '{{Name:prop}}',
            })}
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>{t('filmCrew.create.cancel')}</Button>
        <Button 
          onClick={handleSubmit} 
          variant="contained" 
          disabled={loading || !name || !scriptText}
        >
          {loading ? t('filmCrew.create.creating') : t('filmCrew.create.submit')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default CreateProductionDialog;
