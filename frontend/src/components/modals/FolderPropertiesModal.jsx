// frontend/src/components/modals/FolderPropertiesModal.jsx
// Folder properties modal with cascading property updates to all files and subfolders

import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Grid,
  Box,
  Typography,
  Divider,
  Autocomplete,
  IconButton,
  CircularProgress,
  Alert,
  Switch,
  FormControlLabel,
  Chip,
} from '@mui/material';
import {
  Close as CloseIcon,
  Delete as DeleteIcon,
  Warning as WarningIcon,
} from '@mui/icons-material';
import * as apiService from '../../api';
import axios from 'axios';

const API_BASE = '/api/files';

const FolderPropertiesModal = ({
  open,
  onClose,
  folderData,
  onSave,
  onDelete,
}) => {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [clients, setClients] = useState([]);
  const [projects, setProjects] = useState([]);
  const [websites, setWebsites] = useState([]);
  const [isRepository, setIsRepository] = useState(false);
  const [togglingRepo, setTogglingRepo] = useState(false);

  const [formData, setFormData] = useState({
    client_id: null,
    project_id: null,
    website_id: null,
    tags: '',
    notes: '',
  });

  // Load entity lists
  useEffect(() => {
    if (open) {
      loadEntities();
      setIsRepository(folderData?.is_repository || false);
      setFormData({
        client_id: null,
        project_id: null,
        website_id: null,
        tags: '',
        notes: '',
      });
    }
  }, [open, folderData]);

  const loadEntities = async () => {
    setLoading(true);
    try {
      const [clientsList, projectsList, websitesList] = await Promise.all([
        apiService.getClients(),
        apiService.getProjects(),
        apiService.getWebsites(),
      ]);

      setClients(Array.isArray(clientsList) ? clientsList : []);
      setProjects(Array.isArray(projectsList) ? projectsList : []);
      setWebsites(Array.isArray(websitesList) ? websitesList : []);
    } catch (err) {
      console.error('Failed to load entities:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!folderData) return;
    
    setSaving(true);
    try {
      const payload = {
        client_id: formData.client_id,
        project_id: formData.project_id,
        website_id: formData.website_id,
        tags: formData.tags.split(',').map(t => t.trim()).filter(Boolean),
        notes: formData.notes,
        cascade: true, // Always cascade to children
      };
      await onSave(folderData.id, payload);
    } catch (err) {
      console.error('Failed to save folder properties:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!folderData) return;
    
    if (confirm(`Delete folder "${folderData?.name}" and all its contents?`)) {
      setDeleting(true);
      try {
        await onDelete(folderData.id);
      } finally {
        setDeleting(false);
      }
    }
  };

  const handleToggleRepository = async () => {
    if (!folderData) return;
    setTogglingRepo(true);
    try {
      await axios.put(`${API_BASE}/folder/${folderData.id}/toggle-repo`);
      setIsRepository(prev => !prev);
    } catch (err) {
      console.error('Failed to toggle repository status:', err);
    } finally {
      setTogglingRepo(false);
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return 'N/A';
    return new Date(dateString).toLocaleString();
  };

  if (!folderData) return null;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="h6">Folder Properties</Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent dividers>
        {/* Folder Information */}
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            Folder Information
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <Typography variant="body2">
                <strong>Name:</strong> {folderData.name}
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <strong>Path:</strong> {folderData.path || 'N/A'}
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <strong>Created:</strong> {formatDate(folderData.created_at)}
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <strong>Subfolders:</strong> {folderData.subfolder_count || 0}
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <strong>Files:</strong> {folderData.document_count || 0}
              </Typography>
            </Grid>
            <Grid item xs={12}>
              <FormControlLabel
                control={
                  <Switch
                    checked={isRepository}
                    onChange={handleToggleRepository}
                    disabled={togglingRepo}
                    size="small"
                  />
                }
                label="Code Repository"
              />
              {isRepository && folderData.repo_metadata && (
                <Box sx={{ mt: 1, display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                  {folderData.repo_metadata.languages?.map((lang) => (
                    <Chip key={lang} label={lang} size="small" variant="outlined" />
                  ))}
                  {folderData.repo_metadata.frameworks?.map((fw) => (
                    <Chip key={fw} label={fw} size="small" color="primary" variant="outlined" />
                  ))}
                  {folderData.repo_metadata.file_count != null && (
                    <Chip label={`${folderData.repo_metadata.file_count} files`} size="small" variant="outlined" />
                  )}
                </Box>
              )}
            </Grid>
          </Grid>
        </Box>

        <Divider sx={{ my: 2 }} />

        {/* Warning about cascading */}
        <Alert 
          severity="info" 
          icon={<WarningIcon />}
          sx={{ mb: 2 }}
        >
          <Typography variant="body2">
            <strong>Note:</strong> Properties set here will be applied to all files and subfolders within this folder, including nested subfolders.
          </Typography>
        </Alert>

        {/* Entity Links */}
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            Entity Links (Optional)
          </Typography>

          {loading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
              <CircularProgress size={24} />
            </Box>
          ) : (
            <Grid container spacing={2}>
              <Grid item xs={12}>
                <Autocomplete
                  options={clients}
                  getOptionLabel={(option) => option.name || ''}
                  value={clients.find(c => c.id === formData.client_id) || null}
                  onChange={(e, newValue) => setFormData({ ...formData, client_id: newValue?.id || null })}
                  renderInput={(params) => (
                    <TextField {...params} label="Link to Client" size="small" />
                  )}
                />
              </Grid>
              <Grid item xs={12}>
                <Autocomplete
                  options={projects}
                  getOptionLabel={(option) => option.name || ''}
                  value={projects.find(p => p.id === formData.project_id) || null}
                  onChange={(e, newValue) => setFormData({ ...formData, project_id: newValue?.id || null })}
                  renderInput={(params) => (
                    <TextField {...params} label="Link to Project" size="small" />
                  )}
                />
              </Grid>
              <Grid item xs={12}>
                <Autocomplete
                  options={websites}
                  getOptionLabel={(option) => option.name || option.url || ''}
                  value={websites.find(w => w.id === formData.website_id) || null}
                  onChange={(e, newValue) => setFormData({ ...formData, website_id: newValue?.id || null })}
                  renderInput={(params) => (
                    <TextField {...params} label="Link to Website" size="small" />
                  )}
                />
              </Grid>
            </Grid>
          )}
        </Box>

        <Divider sx={{ my: 2 }} />

        {/* Tags and Notes */}
        <Box>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            Metadata
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label="Tags (comma-separated)"
                size="small"
                value={formData.tags}
                onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
                placeholder="important, draft, review"
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label="Notes"
                size="small"
                multiline
                rows={3}
                value={formData.notes}
                onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                placeholder="Add any notes about this folder..."
              />
            </Grid>
          </Grid>
        </Box>
      </DialogContent>

      <DialogActions sx={{ justifyContent: 'space-between', px: 3, py: 2 }}>
        <Box sx={{ display: 'flex', gap: 1 }}>
          {onDelete && (
            <Button
              startIcon={deleting ? <CircularProgress size={16} /> : <DeleteIcon />}
              onClick={handleDelete}
              color="error"
              variant="outlined"
              size="small"
              disabled={saving || deleting}
            >
              {deleting ? 'Deleting...' : 'Delete'}
            </Button>
          )}
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button onClick={onClose} disabled={saving || deleting}>Cancel</Button>
          <Button 
            onClick={handleSave} 
            variant="contained" 
            disabled={saving || deleting}
            startIcon={saving ? <CircularProgress size={16} /> : null}
          >
            {saving ? 'Saving...' : 'Save & Apply to All'}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default FolderPropertiesModal;
