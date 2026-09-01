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

const API_BASE = '/api';

const CreateProductionDialog = ({ open, onClose, onCreated }) => {
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
      setError('Name and Script Text are required.');
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const payload = {
        name,
        script_text: scriptText,
        project_id: projectId?.id || null,
        ...(videoModel ? { settings: { video_model: videoModel } } : {})
      };
      await onCreated(payload);
      onClose();
      // Reset form
      setName('');
      setScriptText('');
      setProjectId(null);
      setVideoModel('');
    } catch (err) {
      setError(err.message || 'Failed to create production');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>New Production</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label="Production Name"
            fullWidth
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <Autocomplete
            options={projects}
            getOptionLabel={(option) => option.name || ''}
            renderInput={(params) => <TextField {...params} label="Project (Optional)" />}
            value={projectId}
            onChange={(_, newValue) => setProjectId(newValue)}
          />
          {videoModels.length > 0 && (
            <FormControl fullWidth size="small">
              <InputLabel>Video model</InputLabel>
              <Select
                value={videoModel}
                onChange={(e) => setVideoModel(e.target.value)}
                label="Video model"
              >
                <MenuItem value="">Default (Wan 2.2 image-to-video, silent clips + narration)</MenuItem>
                {videoModels.map((m) => (
                  <MenuItem key={m.id} value={m.id} disabled={!m.is_ready}>
                    <Box>
                      <Typography variant="body2">
                        {m.name}{m.is_ready ? '' : ' (not installed)'}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {m.capabilities.audio_out
                          ? 'Renders each scene as one clip with spoken lines and sound'
                          : 'Silent clips per shot, narration added by the editor'}
                        {m.license?.attribution ? ` · ${m.license.attribution}` : ''}
                      </Typography>
                    </Box>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
          <TextField
            label="Script Text"
            fullWidth
            multiline
            rows={10}
            value={scriptText}
            onChange={(e) => setScriptText(e.target.value)}
            required
            placeholder="INT. ROOM - DAY..."
            helperText={
              "Casting markup (optional): [[Name]] pins a recurring cast member that gets its own " +
              "trained LoRA · [[Name:prop]] pins with a kind · {{Name:prop}} keeps something as set " +
              "dressing generated inline. By default only characters are cast; props & locations " +
              "are generated inline."
            }
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>Cancel</Button>
        <Button 
          onClick={handleSubmit} 
          variant="contained" 
          disabled={loading || !name || !scriptText}
        >
          {loading ? 'Creating...' : 'Roll Cameras'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default CreateProductionDialog;
