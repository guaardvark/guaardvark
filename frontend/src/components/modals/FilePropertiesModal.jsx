// frontend/src/components/modals/FilePropertiesModal.jsx
// File properties and entity linking modal for FileManager

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
} from '@mui/material';
import {
  Close as CloseIcon,
  Delete as DeleteIcon,
  Sync as SyncIcon,
} from '@mui/icons-material';
import axios from 'axios';
import * as apiService from '../../api';
import { uiLocale, useUiTranslation, Trans } from '../../i18n';

// API base URL for files endpoint
const API_BASE = '/api/files';

const FilePropertiesModal = ({
  open,
  onClose,
  fileData,
  onSave,
  onDelete,
  onReindex,
}) => {
  const { t } = useUiTranslation();
  const [loading, setLoading] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [clients, setClients] = useState([]);
  const [projects, setProjects] = useState([]);
  const [websites, setWebsites] = useState([]);
  const [fullFileData, setFullFileData] = useState(null);

  const [formData, setFormData] = useState({
    client_id: null,
    project_id: null,
    website_id: null,
    tags: '',
    notes: '',
  });

  // Load full file details if needed (when fileData is in light mode)
  useEffect(() => {
    if (open && fileData?.id) {
      // Check if we have full data (light mode only has 7 fields: id, filename, type, size, uploaded_at, index_status, path)
      // Full mode has tags, notes, client_id, etc.
      const hasFullData = 'tags' in fileData || 'notes' in fileData || 'client' in fileData;

      if (hasFullData) {
        // Already have full data, use it directly
        setFullFileData(fileData);
      } else {
        // Light mode data - fetch full details
        setLoading(true);
        axios.get(`${API_BASE}/document/${fileData.id}`)
          .then(response => {
            const fullData = response.data.data;
            setFullFileData(fullData);
          })
          .catch(err => {
            console.error('Failed to load full file details:', err);
            // Fallback to using light data (form fields will be empty)
            setFullFileData(fileData);
          })
          .finally(() => {
            setLoading(false);
          });
      }
    }
  }, [open, fileData]);

  // Load entity lists and populate form when we have full file data
  useEffect(() => {
    if (open && fullFileData) {
      loadEntities();
      setFormData({
        client_id: fullFileData.client_id || null,
        project_id: fullFileData.project_id || null,
        website_id: fullFileData.website_id || null,
        tags: Array.isArray(fullFileData.tags) ? fullFileData.tags.join(', ') : (fullFileData.tags || ''),
        notes: fullFileData.notes || '',
      });
    }
  }, [open, fullFileData]);

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

  const handleSave = () => {
    const payload = {
      client_id: formData.client_id,
      project_id: formData.project_id,
      website_id: formData.website_id,
      tags: formData.tags.split(',').map(t => t.trim()).filter(Boolean),
      notes: formData.notes,
    };
    onSave(fileData.id, payload);
  };

  const handleDelete = async () => {
    if (confirm(t('dialogs.fileProperties.deleteConfirm', { name: fileData?.filename }))) {
      setDeleting(true);
      try {
        await onDelete(fileData.id);
      } finally {
        setDeleting(false);
      }
    }
  };

  const handleReindex = async () => {
    if (onReindex && fileData?.id) {
      setReindexing(true);
      try {
        await onReindex(fileData.id);
      } finally {
        setReindexing(false);
      }
    }
  };

  const formatBytes = (bytes) => {
    if (!bytes) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i)) + ' ' + sizes[i];
  };

  const formatDate = (dateString) => {
    if (!dateString) return t('dialogs.fileProperties.notAvailable');
    return new Date(dateString).toLocaleString(uiLocale());
  };

  if (!fileData) return null;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="h6">{t('dialogs.fileProperties.title')}</Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent dividers>
        {/* File Information */}
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            {t('dialogs.fileProperties.fileInformation')}
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <Typography variant="body2">
                <Trans i18nKey="dialogs.fileProperties.name" values={{ name: fileData.filename }} components={{ bold: <strong /> }} />
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <Trans i18nKey="dialogs.fileProperties.size" values={{ size: formatBytes(fileData.size) }} components={{ bold: <strong /> }} />
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <Trans i18nKey="dialogs.fileProperties.imported" values={{ date: formatDate(fileData.uploaded_at) }} components={{ bold: <strong /> }} />
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <Trans i18nKey="dialogs.fileProperties.status" values={{ status: fileData.index_status || t('dialogs.fileProperties.notAvailable') }} components={{ bold: <strong /> }} />
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <Trans i18nKey="dialogs.fileProperties.path" values={{ path: fileData.path || t('dialogs.fileProperties.notAvailable') }} components={{ bold: <strong /> }} />
              </Typography>
            </Grid>
          </Grid>
        </Box>

        <Divider sx={{ my: 2 }} />

        {/* Entity Links */}
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            {t('dialogs.fileProperties.entityLinks')}
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
                    <TextField {...params} label={t('dialogs.fileProperties.linkClient')} size="small" />
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
                    <TextField {...params} label={t('dialogs.fileProperties.linkProject')} size="small" />
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
                    <TextField {...params} label={t('dialogs.fileProperties.linkWebsite')} size="small" />
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
            {t('dialogs.fileProperties.metadata')}
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label={t('dialogs.fileProperties.tags')}
                size="small"
                value={formData.tags}
                onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
                placeholder={t('dialogs.fileProperties.tagsPlaceholder')}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label={t('dialogs.fileProperties.notes')}
                size="small"
                multiline
                rows={3}
                value={formData.notes}
                onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                placeholder={t('dialogs.fileProperties.notesPlaceholder')}
              />
            </Grid>
          </Grid>
        </Box>
      </DialogContent>

      <DialogActions sx={{ justifyContent: 'space-between', px: 3, py: 2 }}>
        <Box sx={{ display: 'flex', gap: 1 }}>
          {onReindex && (
            <Button
              startIcon={reindexing ? <CircularProgress size={16} /> : <SyncIcon />}
              onClick={handleReindex}
              variant="outlined"
              size="small"
              disabled={reindexing || deleting}
            >
              {reindexing ? t('dialogs.fileProperties.reindexing') : t('dialogs.fileProperties.reindex')}
            </Button>
          )}
          <Button
            startIcon={deleting ? <CircularProgress size={16} /> : <DeleteIcon />}
            onClick={handleDelete}
            color="error"
            variant="outlined"
            size="small"
            disabled={reindexing || deleting}
          >
            {deleting ? t('dialogs.fileProperties.deleting') : t('dialogs.shared.delete')}
          </Button>
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button onClick={onClose} disabled={reindexing || deleting}>{t('dialogs.shared.cancel')}</Button>
          <Button onClick={handleSave} variant="contained" disabled={reindexing || deleting}>
            {t('dialogs.fileProperties.saveChanges')}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default FilePropertiesModal;
