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
  const [parentProps, setParentProps] = useState(null); // Inherited properties from parent

  const [formData, setFormData] = useState({
    client_id: null,
    project_id: null,
    website_id: null,
    tags: '',
    notes: '',
  });

  // Load entity lists and pre-populate form from existing folder data
  useEffect(() => {
    if (open) {
      loadEntities();
      setIsRepository(folderData?.is_repository || false);
      // Pre-populate from saved folder properties
      const existingTags = folderData?.tags || '';
      // Handle tags stored as JSON array string or plain string
      let tagsStr = '';
      if (existingTags) {
        try {
          const parsed = JSON.parse(existingTags);
          tagsStr = Array.isArray(parsed) ? parsed.join(', ') : existingTags;
        } catch {
          tagsStr = existingTags;
        }
      }
      setFormData({
        client_id: folderData?.client_id || null,
        project_id: folderData?.project_id || null,
        website_id: folderData?.website_id || null,
        tags: tagsStr,
        notes: folderData?.notes || '',
      });

      // Load parent folder properties to detect inheritance
      if (folderData?.parent_id) {
        loadParentProperties();
      } else {
        setParentProps(null);
      }
    }
  }, [open, folderData]);

  // Normalize tags from DB format (JSON array string or plain string) to CSV
  const normalizeTags = (raw) => {
    if (!raw) return '';
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.join(', ') : raw;
    } catch {
      return raw;
    }
  };

  const loadParentProperties = async () => {
    try {
      // Derive parent path from current folder path
      const folderPath = folderData?.path || '';
      const parentPath = folderPath.split('/').slice(0, -1).join('/') || '/';
      const response = await axios.get(`${API_BASE}/browse?path=${encodeURIComponent(parentPath)}&fields=light`);
      const parentFolder = response.data?.data?.current_folder;
      if (parentFolder) {
        setParentProps({
          is_repository: parentFolder.is_repository || false,
          client_id: parentFolder.client_id || null,
          project_id: parentFolder.project_id || null,
          website_id: parentFolder.website_id || null,
          tags: normalizeTags(parentFolder.tags),
          notes: parentFolder.notes || null,
        });
      } else {
        setParentProps(null);
      }
    } catch {
      setParentProps(null);
    }
  };

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
        is_repository: isRepository,
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
                    disabled={togglingRepo || (parentProps?.is_repository && isRepository)}
                    size="small"
                  />
                }
                label={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    Code Repository
                    {parentProps?.is_repository && isRepository && (
                      <Chip label="inherited" size="small" variant="outlined" color="info" sx={{ height: 20, fontSize: '0.7rem' }} />
                    )}
                  </Box>
                }
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

        {parentProps && (parentProps.client_id || parentProps.project_id || parentProps.website_id || parentProps.tags || parentProps.notes) && (
          <Alert severity="info" sx={{ mb: 2 }}>
            <Typography variant="body2">
              Some properties are inherited from the parent folder. Fields marked <Chip label="inherited" size="small" variant="outlined" color="info" sx={{ height: 18, fontSize: '0.65rem', mx: 0.5 }} /> were set by the parent.
            </Typography>
          </Alert>
        )}

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
                    <TextField
                      {...params}
                      label={parentProps?.client_id && formData.client_id === parentProps.client_id ? 'Link to Client (inherited)' : 'Link to Client'}
                      size="small"
                    />
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
                    <TextField
                      {...params}
                      label={parentProps?.project_id && formData.project_id === parentProps.project_id ? 'Link to Project (inherited)' : 'Link to Project'}
                      size="small"
                    />
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
                    <TextField
                      {...params}
                      label={parentProps?.website_id && formData.website_id === parentProps.website_id ? 'Link to Website (inherited)' : 'Link to Website'}
                      size="small"
                    />
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
                label={parentProps?.tags && formData.tags && parentProps.tags === formData.tags ? 'Tags (inherited)' : 'Tags (comma-separated)'}
                size="small"
                value={formData.tags}
                onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
                placeholder="important, draft, review"
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label={parentProps?.notes && formData.notes && parentProps.notes === formData.notes ? 'Notes (inherited)' : 'Notes'}
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
