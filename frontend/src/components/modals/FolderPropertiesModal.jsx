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
import { formatUiError } from '../../utils/uiError';
import ConfirmActionDialog from '../settings/ui/ConfirmActionDialog';
import { translate, uiLocale, useUiTranslation, Trans } from '../../i18n';

// The server's reason from an axios error, else the error's own text.
const reasonOf = (err) => formatUiError(err?.response?.data) || formatUiError(err) || translate('dialogs.folderProperties.requestFailed');

const API_BASE = '/api/files';

const FolderPropertiesModal = ({
  open,
  onClose,
  folderData,
  onSave,
  onDelete,
}) => {
  const { t } = useUiTranslation();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [clients, setClients] = useState([]);
  const [projects, setProjects] = useState([]);
  const [websites, setWebsites] = useState([]);
  const [isRepository, setIsRepository] = useState(false);
  const [togglingRepo, setTogglingRepo] = useState(false);
  const [parentProps, setParentProps] = useState(null); // Inherited properties from parent
  // A failed load, save, delete or repository toggle, said in the dialog.
  const [failure, setFailure] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

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
      // These services answer { error } instead of throwing.
      const refused = [clientsList, projectsList, websitesList].find((l) => !Array.isArray(l) && l?.error);
      if (refused) setFailure(t('dialogs.folderProperties.entitiesFailed', { reason: formatUiError(refused.error) }));
    } catch (err) {
      console.error('Failed to load entities:', err);
      setFailure(t('dialogs.folderProperties.entitiesFailed', { reason: reasonOf(err) }));
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!folderData) return;

    setSaving(true);
    setFailure(null);
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
      setFailure(t('dialogs.folderProperties.saveFailed', { reason: reasonOf(err) }));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!folderData) return;
    setConfirmingDelete(false);
    setDeleting(true);
    setFailure(null);
    const fid = folderData.id;
    try {
      // Optimistic event for instant UI remove (parent will also close + refresh)
      try {
        window.dispatchEvent(new CustomEvent('documents-items-removed', { detail: { keys: [`folder-${fid}`], contextKey: 'folder-properties' } }));
      } catch (_) {
        // Ignore event dispatch failure
      }
      await onDelete(fid);
    } catch (err) {
      setFailure(t('dialogs.folderProperties.deleteFailed', { reason: reasonOf(err) }));
    } finally {
      setDeleting(false);
    }
  };

  const handleToggleRepository = async () => {
    if (!folderData) return;
    setTogglingRepo(true);
    setFailure(null);
    try {
      await axios.put(`${API_BASE}/folder/${folderData.id}/toggle-repo`);
      setIsRepository(prev => !prev);
    } catch (err) {
      console.error('Failed to toggle repository status:', err);
      setFailure(t('dialogs.folderProperties.repoToggleFailed', { reason: reasonOf(err) }));
    } finally {
      setTogglingRepo(false);
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return t('dialogs.fileProperties.notAvailable');
    return new Date(dateString).toLocaleString(uiLocale());
  };

  if (!folderData) return null;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="h6">{t('dialogs.folderProperties.title')}</Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent dividers>
        {failure && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setFailure(null)}>
            {failure}
          </Alert>
        )}
        {/* Folder Information */}
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            {t('dialogs.folderProperties.folderInformation')}
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <Typography variant="body2">
                <Trans i18nKey="dialogs.folderProperties.name" values={{ name: folderData.name }} components={{ bold: <strong /> }} />
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <Trans i18nKey="dialogs.folderProperties.path" values={{ path: folderData.path || t('dialogs.fileProperties.notAvailable') }} components={{ bold: <strong /> }} />
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <Trans i18nKey="dialogs.folderProperties.created" values={{ date: formatDate(folderData.created_at) }} components={{ bold: <strong /> }} />
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <Trans i18nKey="dialogs.folderProperties.subfolders" values={{ count: folderData.subfolder_count || 0 }} components={{ bold: <strong /> }} />
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="body2">
                <Trans i18nKey="dialogs.folderProperties.files" values={{ count: folderData.document_count || 0 }} components={{ bold: <strong /> }} />
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
                    {t('dialogs.folderProperties.codeRepository')}
                    {parentProps?.is_repository && isRepository && (
                      <Chip label={t('dialogs.folderProperties.inherited')} size="small" variant="outlined" color="info" sx={{ height: 20, fontSize: '0.7rem' }} />
                    )}
                  </Box>
                }
              />
              {isRepository && folderData.repo_metadata && (
                <Box sx={{ mt: 1, display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                  {(Array.isArray(folderData.repo_metadata.languages)
                    ? folderData.repo_metadata.languages
                    : Object.keys(folderData.repo_metadata.languages || {})
                  ).map((lang) => (
                    <Chip key={lang} label={lang} size="small" variant="outlined" />
                  ))}
                  {folderData.repo_metadata.frameworks?.map((fw) => (
                    <Chip key={fw} label={fw} size="small" color="primary" variant="outlined" />
                  ))}
                  {folderData.repo_metadata.file_count != null && (
                    <Chip label={t('dialogs.folderProperties.fileCount', { count: folderData.repo_metadata.file_count })} size="small" variant="outlined" />
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
            <Trans i18nKey="dialogs.folderProperties.cascadeNote" components={{ bold: <strong /> }} />
          </Typography>
        </Alert>

        {parentProps && (parentProps.client_id || parentProps.project_id || parentProps.website_id || parentProps.tags || parentProps.notes) && (
          <Alert severity="info" sx={{ mb: 2 }}>
            <Typography variant="body2">
              <Trans
                i18nKey="dialogs.folderProperties.inheritedNote"
                components={{
                  chip: <Chip label={t('dialogs.folderProperties.inherited')} size="small" variant="outlined" color="info" sx={{ height: 18, fontSize: '0.65rem', mx: 0.5 }} />,
                }}
              />
            </Typography>
          </Alert>
        )}

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
                    <TextField
                      {...params}
                      label={parentProps?.client_id && formData.client_id === parentProps.client_id ? t('dialogs.folderProperties.linkClientInherited') : t('dialogs.fileProperties.linkClient')}
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
                      label={parentProps?.project_id && formData.project_id === parentProps.project_id ? t('dialogs.folderProperties.linkProjectInherited') : t('dialogs.fileProperties.linkProject')}
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
                      label={parentProps?.website_id && formData.website_id === parentProps.website_id ? t('dialogs.folderProperties.linkWebsiteInherited') : t('dialogs.fileProperties.linkWebsite')}
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
            {t('dialogs.fileProperties.metadata')}
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label={parentProps?.tags && formData.tags && parentProps.tags === formData.tags ? t('dialogs.folderProperties.tagsInherited') : t('dialogs.fileProperties.tags')}
                size="small"
                value={formData.tags}
                onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
                placeholder={t('dialogs.fileProperties.tagsPlaceholder')}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label={parentProps?.notes && formData.notes && parentProps.notes === formData.notes ? t('dialogs.folderProperties.notesInherited') : t('dialogs.fileProperties.notes')}
                size="small"
                multiline
                rows={3}
                value={formData.notes}
                onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                placeholder={t('dialogs.folderProperties.notesPlaceholder')}
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
              onClick={() => setConfirmingDelete(true)}
              color="error"
              variant="outlined"
              size="small"
              disabled={saving || deleting}
            >
              {deleting ? t('dialogs.fileProperties.deleting') : t('dialogs.shared.delete')}
            </Button>
          )}
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button onClick={onClose} disabled={saving || deleting}>{t('dialogs.shared.cancel')}</Button>
          <Button 
            onClick={handleSave} 
            variant="contained" 
            disabled={saving || deleting}
            startIcon={saving ? <CircularProgress size={16} /> : null}
          >
            {saving ? t('dialogs.folderProperties.saving') : t('dialogs.folderProperties.saveAndApply')}
          </Button>
        </Box>
      </DialogActions>
      <ConfirmActionDialog
        open={confirmingDelete}
        title={t('dialogs.folderProperties.deleteTitle')}
        description={t('dialogs.folderProperties.deleteDescription', { name: folderData?.name })}
        confirmLabel={t('dialogs.folderProperties.deleteFolder')}
        onConfirm={handleDelete}
        onClose={() => setConfirmingDelete(false)}
      />
    </Dialog>
  );
};

export default FolderPropertiesModal;
