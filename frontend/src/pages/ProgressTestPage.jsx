// frontend/src/pages/ProgressTestPage.jsx
// Test page for debugging progress tracking

import React, { useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Card,
  CardContent,
  Grid,
  Alert,
  CircularProgress,
  Chip,
  Paper,
  Divider
} from '@mui/material';
import {
  Image as ImageIcon,
  Description as FileIcon,
  TableChart as CsvIcon,
  Search as IndexIcon,
  Code as CodeIcon,
  Upload as UploadIcon,
  BugReport as DebugIcon
} from '@mui/icons-material';

import { useUnifiedProgress } from '../contexts/UnifiedProgressContext';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

const ProgressTestPage = () => {
  const [loading, setLoading] = useState({});
  const [results, setResults] = useState({});
  const { activeProcesses, globalProgress } = useUnifiedProgress();

  const testProcesses = [
    {
      id: 'socketio-test',
      name: 'SocketIO Test',
      icon: <DebugIcon />,
      color: '#ff6b6b',
      endpoint: '/progress-test/test-socketio'
    },
    {
      id: 'csv-gen',
      name: 'CSV Generation (7 items)',
      icon: <CsvIcon />,
      color: '#14b8a6',
      endpoint: '/progress-test/test-csv-gen'
    },
    {
      id: 'image-gen',
      name: 'Image Generation',
      icon: <ImageIcon />,
      color: '#8b5cf6',
      endpoint: '/progress-test/test-image-gen'
    },
    {
      id: 'file-gen',
      name: 'File Generation',
      icon: <FileIcon />,
      color: '#10b981',
      endpoint: '/progress-test/test-file-gen'
    },
    {
      id: 'indexing',
      name: 'Document Indexing',
      icon: <IndexIcon />,
      color: '#3b82f6',
      endpoint: '/progress-test/test-indexing'
    },
    {
      id: 'analysis',
      name: 'Code Analysis',
      icon: <CodeIcon />,
      color: '#f97316',
      endpoint: '/progress-test/test-analysis'
    },
    {
      id: 'upload',
      name: 'File Upload',
      icon: <UploadIcon />,
      color: '#06b6d4',
      endpoint: '/progress-test/test-upload'
    }
  ];

  const runTest = async (test) => {
    setLoading(prev => ({ ...prev, [test.id]: true }));
    setResults(prev => ({ ...prev, [test.id]: null }));

    try {
      const response = await fetch(`${API_BASE}${test.endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });

      const data = await response.json();
      setResults(prev => ({ ...prev, [test.id]: data }));
    } catch (error) {
      setResults(prev => ({ 
        ...prev, 
        [test.id]: { error: error.message } 
      }));
    } finally {
      setLoading(prev => ({ ...prev, [test.id]: false }));
    }
  };

  const getStatusColor = (processType) => {
    const colors = {
      image_generation: '#8b5cf6',
      file_generation: '#10b981',
      csv_processing: '#14b8a6',
      indexing: '#3b82f6',
      analysis: '#f97316',
      upload: '#06b6d4',
      processing: '#6b7280'
    };
    return colors[processType] || '#6b7280';
  };

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" component="h1" gutterBottom>
        Progress Tracking Debug
      </Typography>
      
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Test progress tracking for all system processes. Check the ProgressFooterBar at the bottom of the screen.
      </Typography>

      {/* Global Progress Status */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Global Progress Status
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2 }}>
            <Chip 
              label={globalProgress.active ? 'Active' : 'Idle'} 
              color={globalProgress.active ? 'primary' : 'default'}
              icon={<DebugIcon />}
            />
            <Typography variant="body2">
              Active Processes: {activeProcesses.size}
            </Typography>
            {globalProgress.active && (
              <Typography variant="body2">
                Progress: {globalProgress.progress}% - {globalProgress.message}
              </Typography>
            )}
          </Box>
          
          {activeProcesses.size > 0 && (
            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Active Processes:
              </Typography>
              {Array.from(activeProcesses.values()).map((process) => (
                <Chip
                  key={process.job_id}
                  label={`${process.processType || 'processing'}: ${process.progress}%`}
                  size="small"
                  sx={{ 
                    mr: 1, 
                    mb: 1,
                    backgroundColor: getStatusColor(process.processType),
                    color: 'white'
                  }}
                />
              ))}
            </Box>
          )}
        </CardContent>
      </Card>

      {/* Test Buttons */}
      <Grid container spacing={2}>
        {testProcesses.map((test) => (
          <Grid item xs={12} sm={6} md={4} key={test.id}>
            <Card>
              <CardContent>
                <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                  <Box sx={{ color: test.color, mr: 1 }}>
                    {test.icon}
                  </Box>
                  <Typography variant="h6">
                    {test.name}
                  </Typography>
                </Box>
                
                <Button
                  variant="contained"
                  fullWidth
                  onClick={() => runTest(test)}
                  disabled={loading[test.id]}
                  sx={{ mb: 2 }}
                >
                  {loading[test.id] ? (
                    <CircularProgress size={20} color="inherit" />
                  ) : (
                    'Start Test'
                  )}
                </Button>

                {results[test.id] && (
                  <Paper sx={{ p: 2, bgcolor: 'grey.50' }}>
                    {results[test.id].error ? (
                      <Alert severity="error" sx={{ mb: 1 }}>
                        {results[test.id].error}
                      </Alert>
                    ) : (
                      <Alert severity="success" sx={{ mb: 1 }}>
                        Test started successfully
                      </Alert>
                    )}
                    {results[test.id].process_id && (
                      <Typography variant="caption" color="text.secondary">
                        Process ID: {results[test.id].process_id}
                      </Typography>
                    )}
                  </Paper>
                )}
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      {/* Instructions */}
      <Card sx={{ mt: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            How to Test
          </Typography>
          <Typography variant="body2" paragraph>
            1. Click any "Start Test" button above
          </Typography>
          <Typography variant="body2" paragraph>
            2. Watch the ProgressFooterBar at the bottom of the screen
          </Typography>
          <Typography variant="body2" paragraph>
            3. The progress should show the process type, percentage, and status message
          </Typography>
          <Typography variant="body2" paragraph>
            4. You can run multiple tests simultaneously to see how the system handles concurrent processes
          </Typography>
          <Divider sx={{ my: 2 }} />
          <Typography variant="body2" color="text.secondary">
            <strong>Expected Behavior:</strong> All process types (ImageGen, FileGen, CSV, Indexing, Analysis, Upload) 
            should appear in the ProgressFooterBar with appropriate colors and messages.
          </Typography>
        </CardContent>
      </Card>
    </Box>
  );
};

export default ProgressTestPage;
