// frontend/src/components/settings/AutomatedTestsSection.jsx
// Extracted from SettingsPage.jsx - Automated Tests functionality

import React, { useState } from 'react';
import {
  Typography,
  Button,
  Paper,
  Box,
  Tooltip,
  CircularProgress
} from '@mui/material';
import { useSnackbar } from '../../contexts/SnackbarProvider';
import apiService from '../../api/apiService';

const AutomatedTestsSection = ({ isLoading }) => {
  const [isRunningTests, setIsRunningTests] = useState(false);
  const [testSuiteResults, setTestSuiteResults] = useState(null);
  const { showMessage } = useSnackbar();

  const handleRunAllTests = async () => {
    setIsRunningTests(true);
    showMessage("Running comprehensive test suite...", "info");
    try {
      const result = await apiService.runTestSuite();
      setTestSuiteResults(result);
      
      if (result?.error) {
        showMessage(`Tests completed with errors: ${result.error}`, "warning");
      } else {
        showMessage("Test suite completed successfully.", "success");
      }
    } catch (err) {
      console.error("Error running test suite:", err);
      showMessage(`Test suite failed: ${err.message}`, "error");
    } finally {
      setIsRunningTests(false);
    }
  };

  return (
    <Paper elevation={3} sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        Automated Tests
      </Typography>
      <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
        <Tooltip title="Run unit, integration and end-to-end tests">
          <span>
            <Button
              variant="contained"
              onClick={handleRunAllTests}
              disabled={isRunningTests || isLoading}
            >
              {isRunningTests ? (
                <CircularProgress size={24} color="inherit" />
              ) : (
                "Run Test Suite"
              )}
            </Button>
          </span>
        </Tooltip>
      </Box>
      {testSuiteResults && (
        <Box
          mt={2}
          p={2}
          border={1}
          borderColor="divider"
          borderRadius={1}
        >
          <Typography variant="subtitle1" gutterBottom>
            Test Suite Output:
          </Typography>
          <pre
            style={{ whiteSpace: "pre-wrap", fontFamily: "monospace" }}
          >
            {testSuiteResults.stdout}
          </pre>
          {testSuiteResults.stderr && (
            <pre
              style={{
                whiteSpace: "pre-wrap",
                fontFamily: "monospace",
                color: "red",
              }}
            >
              {testSuiteResults.stderr}
            </pre>
          )}
        </Box>
      )}
    </Paper>
  );
};

export default AutomatedTestsSection; 