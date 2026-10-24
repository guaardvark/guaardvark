// frontend/src/components/common/ProjectStateErrorBoundary.jsx
// Error boundary specifically for project state management issues

import React from 'react';
import { Alert, Box, Button, Typography } from '@mui/material';
import { Refresh as RefreshIcon } from '@mui/icons-material';
import { useAppStore } from '../../stores/useAppStore';
import { useUiTranslation } from '../../i18n';

/** The boundary's recovery panel; a function component so it follows a language change. */
const ProjectStateErrorFallback = ({ error, onRetry }) => {
  const { t } = useUiTranslation();
  return (
    <Box sx={{ p: 3, textAlign: 'center' }}>
      <Alert severity="error" sx={{ mb: 2 }}>
        <Typography variant="h6" gutterBottom>
          {t('layoutCommon.projectState.title')}
        </Typography>
        <Typography variant="body2" sx={{ mb: 2 }}>
          {t('layoutCommon.projectState.intro')}
        </Typography>
        <Box component="ul" sx={{ textAlign: 'left', mb: 2 }}>
          <li>{t('layoutCommon.projectState.causes.network')}</li>
          <li>{t('layoutCommon.projectState.causes.sync')}</li>
          <li>{t('layoutCommon.projectState.causes.relationships')}</li>
          <li>{t('layoutCommon.projectState.causes.corruption')}</li>
        </Box>
        <Typography variant="body2" color="text.secondary">
          {t('layoutCommon.projectState.errorMessage', { message: error?.message || t('layoutCommon.projectState.unknownError') })}
        </Typography>
      </Alert>
      
      <Button
        variant="contained"
        startIcon={<RefreshIcon />}
        onClick={onRetry}
        sx={{ mr: 2 }}
      >
        {t('common.retry')}
      </Button>
      
      <Button
        variant="outlined"
        onClick={() => window.location.reload()}
      >
        {t('layoutCommon.errorBoundary.reloadPage')}
      </Button>
    </Box>
  );
};

class ProjectStateErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    // Update state so the next render will show the fallback UI
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    // Log project state errors for debugging
    console.error('Project State Error:', error, errorInfo);
    
    // Log current project state for debugging
    try {
      const store = useAppStore.getState();
      console.error('Project State at Error:', {
        projects: store.projects,
        activeProjectId: store.activeProjectId,
        clients: store.clients,
        isLoading: store.isLoading,
        error: store.error
      });
    } catch (storeError) {
      console.error('Could not access store state:', storeError);
    }
  }

  handleRetry = () => {
    // Reset error boundary and optionally refresh project data
    this.setState({ hasError: false, error: null });
    
    // Trigger a refresh of project data if available
    try {
      const store = useAppStore.getState();
      if (store.setProjects && store.setClients) {
        store.setProjects([]);
        store.setClients([]);
        store.clearError();
      }
    } catch (refreshError) {
      console.error('Could not refresh project state:', refreshError);
    }
  };

  render() {
    if (this.state.hasError) {
      return (
        <ProjectStateErrorFallback error={this.state.error} onRetry={this.handleRetry} />
      );
    }

    return this.props.children;
  }
}

export default ProjectStateErrorBoundary;