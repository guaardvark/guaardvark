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
  Alert
} from '@mui/material';
import { getProjects } from '../../api/projectService';

const CreateProductionDialog = ({ open, onClose, onCreated }) => {
  const [name, setName] = useState('');
  const [scriptText, setScriptText] = useState('');
  const [projectId, setProjectId] = useState(null);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open) {
      loadProjects();
    }
  }, [open]);

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
        project_id: projectId?.id || null
      };
      await onCreated(payload);
      onClose();
      // Reset form
      setName('');
      setScriptText('');
      setProjectId(null);
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
          <TextField
            label="Script Text"
            fullWidth
            multiline
            rows={10}
            value={scriptText}
            onChange={(e) => setScriptText(e.target.value)}
            required
            placeholder="INT. ROOM - DAY..."
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
