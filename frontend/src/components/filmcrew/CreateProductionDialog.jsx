import React, { useState, useEffect, useRef } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Autocomplete,
  Box,
  Typography,
  Stack
} from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import { getProjects } from '../../api/projectService';
import { listScriptTemplates, loadScriptTemplate } from '../../api/productionService';
import CollapsibleAlert from "../common/CollapsibleAlert";

const CreateProductionDialog = ({ open, onClose, onCreated }) => {
  const [name, setName] = useState('');
  const [scriptText, setScriptText] = useState('');
  const [projectId, setProjectId] = useState(null);
  const [projects, setProjects] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [loading, setLoading] = useState(false);
  const [templateLoading, setTemplateLoading] = useState(false);
  const [uploadedFileName, setUploadedFileName] = useState(null);
  const [error, setError] = useState(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (open) {
      loadProjects();
      loadTemplates();
    }
  }, [open]);

  const loadProjects = async () => {
    const data = await getProjects();
    if (Array.isArray(data)) {
      setProjects(data);
    }
  };

  const loadTemplates = async () => {
    try {
      const data = await listScriptTemplates();
      setTemplates(data.templates || []);
    } catch (err) {
      // Non-fatal: templates are optional.
      setTemplates([]);
    }
  };

  const handleTemplateChange = async (_, newValue) => {
    setSelectedTemplate(newValue);
    setUploadedFileName(null);
    if (!newValue) {
      return;
    }
    setTemplateLoading(true);
    setError(null);
    try {
      const text = await loadScriptTemplate(newValue.filename);
      setScriptText(text);
      if (!name) {
        setName(newValue.name);
      }
    } catch (err) {
      setError(err.message || `Failed to load template ${newValue.filename}`);
    } finally {
      setTemplateLoading(false);
    }
  };

  const handleFileSelect = (event) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    const allowed = /\.(txt|fountain|md)$/i;
    if (!allowed.test(file.name)) {
      setError(`Unsupported file type: ${file.name}. Please upload .txt, .fountain, or .md files.`);
      event.target.value = '';
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      setError(`File too large: ${file.name}. Maximum size is 2MB.`);
      event.target.value = '';
      return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
      setScriptText(e.target.result || '');
      setUploadedFileName(file.name);
      setSelectedTemplate(null);
      if (!name) {
        // Strip extension for the default production name.
        setName(file.name.replace(/\.[^/.]+$/, '').replace(/[_-]+/g, ' ').trim());
      }
      setError(null);
    };
    reader.onerror = () => {
      setError(`Failed to read file: ${file.name}`);
    };
    reader.readAsText(file);
    event.target.value = '';
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
      setSelectedTemplate(null);
      setUploadedFileName(null);
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
          {error && <CollapsibleAlert severity="error">{error}</CollapsibleAlert>}
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
          <Autocomplete
            options={templates}
            getOptionLabel={(option) => option.name || ''}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Load Script Template"
                helperText="Pick a pre-committed script from docs/film-crew-scripts to pre-fill the Script Text field."
              />
            )}
            value={selectedTemplate}
            onChange={handleTemplateChange}
            disabled={templateLoading}
          />
          <Box>
            <input
              type="file"
              accept=".txt,.fountain,.md"
              hidden
              ref={fileInputRef}
              onChange={handleFileSelect}
            />
            <Stack direction="row" spacing={1} alignItems="center">
              <Button
                variant="outlined"
                size="small"
                startIcon={<UploadFileIcon />}
                onClick={() => fileInputRef.current?.click()}
                disabled={templateLoading}
              >
                Browse Script File
              </Button>
              {uploadedFileName && (
                <Typography variant="caption" color="text.secondary">
                  Loaded: {uploadedFileName}
                </Typography>
              )}
            </Stack>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
              Upload a .txt, .fountain, or .md script from your computer. This replaces the Script Text field.
            </Typography>
          </Box>
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
