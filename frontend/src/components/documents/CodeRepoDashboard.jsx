import React, { useState } from 'react';
import {
  Box,
  Typography,
  Chip,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Grid,
  IconButton,
  Divider,
  Tooltip,
} from '@mui/material';
import { Code, Map, GitMerge, X as CloseIcon } from 'lucide-react';
import { useTheme } from '@mui/material/styles';
import { useUiTranslation, uiLocale } from '../../i18n';

// The dialogs render in a portal, but React still bubbles their events up the
// component tree into the folder window's title bar, where a double mousedown
// minimizes the window and a right-click opens the folder menu.
const keepEventsInDialog = {
  onMouseDown: (e) => e.stopPropagation(),
  onClick: (e) => e.stopPropagation(),
  onDoubleClick: (e) => e.stopPropagation(),
  onContextMenu: (e) => e.stopPropagation(),
};

// A repository folder's analysis, opened from a small INTEL button so it does
// not take room above the folder's files.
const CodeRepoDashboard = ({ folder }) => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const [intelOpen, setIntelOpen] = useState(false);
  const [mapOpen, setMapOpen] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);

  if (!folder || !folder.is_repository) {
    return null;
  }

  let metadata = {};
  try {
    metadata = folder.repo_metadata && typeof folder.repo_metadata === 'string'
      ? JSON.parse(folder.repo_metadata)
      : (folder.repo_metadata || {});
  } catch (e) {
    console.warn('Failed to parse repo_metadata', e);
    metadata = {};
  }

  const {
    languages = {},
    frameworks = [],
    file_count = 0,
    repository_map = '',
    dependency_graph = {},
    analyzed_at,
  } = metadata;

  const languageEntries = Array.isArray(languages)
    ? languages.map((lang) => [lang, null])
    : Object.entries(languages || {});

  const topLanguages = languageEntries
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  const frameworkList = Array.isArray(frameworks) ? frameworks : [];
  const dependencyGraph = dependency_graph && typeof dependency_graph === 'object' && !Array.isArray(dependency_graph)
    ? dependency_graph
    : {};
  const dependencyCount = Object.keys(dependencyGraph).length;

  const formatAnalyzedAt = (dateString) => {
    if (!dateString) return t('documents.codeRepo.never');
    const date = new Date(dateString);
    return Number.isNaN(date.getTime()) ? t('documents.codeRepo.unknown') : date.toLocaleString(uiLocale());
  };

  return (
    <>
      <Tooltip title={t('documents.codeRepo.buttonTooltip')}>
        <Button
          variant="outlined"
          size="small"
          onClick={(e) => {
            e.stopPropagation();
            setIntelOpen(true);
          }}
          aria-label={t('documents.codeRepo.buttonLabel', { name: folder.name })}
          sx={{
            ml: 0.5,
            minWidth: 0,
            height: 20,
            px: 0.75,
            py: 0,
            fontSize: '0.6rem',
            fontWeight: 'bold',
            lineHeight: 1,
            letterSpacing: '0.05em',
          }}
        >
          {t('documents.codeRepo.button')}
        </Button>
      </Tooltip>

      <Dialog open={intelOpen} onClose={() => setIntelOpen(false)} maxWidth="md" fullWidth {...keepEventsInDialog}>
        <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
            <Code size={24} color={theme.palette.primary.main} />
            <Typography variant="h6" noWrap>{t('documents.codeRepo.title')}</Typography>
          </Box>
          <IconButton onClick={() => setIntelOpen(false)} size="small" aria-label={t('documents.actions.close')}>
            <CloseIcon size={20} />
          </IconButton>
        </DialogTitle>
        <Divider />
        <DialogContent>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 2, mb: 2 }}>
            <Typography variant="subtitle1" noWrap>{folder.name}</Typography>
            <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
              {t('documents.codeRepo.analyzed', { date: formatAnalyzedAt(analyzed_at) })}
            </Typography>
          </Box>

          {folder.description && (
            <Typography variant="body2" sx={{ mb: 2, whiteSpace: 'pre-wrap', maxHeight: '200px', overflowY: 'auto' }}>
              {folder.description}
            </Typography>
          )}

          <Grid container spacing={2} sx={{ mb: 2 }}>
            <Grid item xs={12} sm={6}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                {t('documents.codeRepo.languagesFrameworks')}
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                {frameworkList.map((fw) => (
                  <Chip key={fw} label={fw} size="small" color="primary" variant="outlined" />
                ))}
                {topLanguages.map(([lang, count]) => (
                  <Chip key={lang} label={count == null ? lang : `${lang} (${count})`} size="small" variant="outlined" />
                ))}
                {frameworkList.length === 0 && topLanguages.length === 0 && (
                  <Chip label={t('documents.codeRepo.analysisPending')} size="small" variant="outlined" />
                )}
              </Box>
            </Grid>
            <Grid item xs={12} sm={6}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                {t('documents.codeRepo.statistics')}
              </Typography>
              <Box sx={{ display: 'flex', gap: 1 }}>
                <Chip label={t('documents.codeRepo.fileCount', { count: file_count })} size="small" />
                {dependencyCount > 0 && (
                  <Chip label={t('documents.codeRepo.mappedFileCount', { count: dependencyCount })} size="small" />
                )}
              </Box>
            </Grid>
          </Grid>

          <Box sx={{ display: 'flex', gap: 1, mt: 2, flexWrap: 'wrap' }}>
            <Button
              variant="outlined"
              startIcon={<Map size={16} />}
              onClick={() => setMapOpen(true)}
              disabled={!repository_map}
              size="small"
            >
              {t('documents.codeRepo.viewMap')}
            </Button>
            <Button
              variant="outlined"
              startIcon={<GitMerge size={16} />}
              onClick={() => setGraphOpen(true)}
              disabled={dependencyCount === 0}
              size="small"
            >
              {t('documents.codeRepo.viewDependencies')}
            </Button>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setIntelOpen(false)}>{t('documents.actions.close')}</Button>
        </DialogActions>
      </Dialog>

      {/* Repository Map Dialog */}
      <Dialog open={mapOpen} onClose={() => setMapOpen(false)} maxWidth="lg" fullWidth {...keepEventsInDialog}>
        <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          {t('documents.codeRepo.mapTitle')}
          <IconButton onClick={() => setMapOpen(false)} size="small">
            <CloseIcon size={20} />
          </IconButton>
        </DialogTitle>
        <Divider />
        <DialogContent>
          <Box sx={{ bgcolor: 'background.default', p: 2, borderRadius: 1, overflowX: 'auto' }}>
            <Typography variant="body2" component="pre" sx={{ fontFamily: 'monospace' }}>
              {repository_map || t('documents.codeRepo.noMap')}
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMapOpen(false)}>{t('documents.actions.close')}</Button>
        </DialogActions>
      </Dialog>

      {/* Dependency Graph Dialog */}
      <Dialog open={graphOpen} onClose={() => setGraphOpen(false)} maxWidth="lg" fullWidth {...keepEventsInDialog}>
        <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          {t('documents.codeRepo.graphTitle')}
          <IconButton onClick={() => setGraphOpen(false)} size="small">
            <CloseIcon size={20} />
          </IconButton>
        </DialogTitle>
        <Divider />
        <DialogContent>
          <Box sx={{ bgcolor: 'background.default', p: 2, borderRadius: 1, overflowX: 'auto' }}>
            <Typography variant="body2" component="pre" sx={{ fontFamily: 'monospace' }}>
              {JSON.stringify(dependencyGraph, null, 2)}
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setGraphOpen(false)}>{t('documents.actions.close')}</Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export default CodeRepoDashboard;
