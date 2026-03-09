// frontend/src/components/common/ProjectStateErrorBoundary.jsx
// Error boundary specifically for project state management issues

import React from 'react';
import { Alert, Box, Button, Typography } from '@mui/material';
import { Refresh as RefreshIcon } from '@mui/icons-material';
import { useAppStore } from '../../stores/useAppStore';

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
        <Box sx={{ p: 3, textAlign: 'center' }}>
          <Alert severity="error" sx={{ mb: 2 }}>
            <Typography variant="h6" gutterBottom>
              Project State Error
            </Typography>
            <Typography variant="body2" sx={{ mb: 2 }}>
              There was an error with the project state management. This could be due to:
            </Typography>
            <Box component="ul" sx={{ textAlign: 'left', mb: 2 }}>
              <li>Network connectivity issues</li>
              <li>Data synchronization problems</li>
              <li>Invalid project relationships</li>
              <li>State corruption</li>
            </Box>
            <Typography variant="body2" color="text.secondary">
              Error: {this.state.error?.message || 'Unknown error'}
            </Typography>
          </Alert>
          
          <Button
            variant="contained"
            startIcon={<RefreshIcon />}
            onClick={this.handleRetry}
            sx={{ mr: 2 }}
          >
            Retry
          </Button>
          
          <Button
            variant="outlined"
            onClick={() => window.location.reload()}
          >
            Reload Page
          </Button>
        </Box>
      );
    }

    return this.props.children;
  }
}

export default ProjectStateErrorBoundary;