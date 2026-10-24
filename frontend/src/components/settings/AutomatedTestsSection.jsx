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
import { useUiTranslation } from '../../i18n';

const AutomatedTestsSection = ({ isLoading }) => {
  const { t } = useUiTranslation();
  const [isRunningTests, setIsRunningTests] = useState(false);
  const [testSuiteResults, setTestSuiteResults] = useState(null);
  const { showMessage } = useSnackbar();

  const handleRunAllTests = async () => {
    setIsRunningTests(true);
    showMessage(t("settingsSections.automatedTests.running"), "info");
    try {
      const result = await apiService.runTestSuite();
      setTestSuiteResults(result);
      
      if (result?.error) {
        showMessage(t("settingsSections.automatedTests.completedWithErrors", { error: result.error }), "warning");
      } else {
        showMessage(t("settingsSections.automatedTests.completed"), "success");
      }
    } catch (err) {
      console.error("Error running test suite:", err);
      showMessage(t("settingsSections.automatedTests.failed", { error: err.message }), "error");
    } finally {
      setIsRunningTests(false);
    }
  };

  return (
    <Paper elevation={3} sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        {t("settingsSections.automatedTests.title")}
      </Typography>
      <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
        <Tooltip title={t("settingsSections.automatedTests.runTooltip")}>
          <span>
            <Button
              variant="contained"
              onClick={handleRunAllTests}
              disabled={isRunningTests || isLoading}
            >
              {isRunningTests ? (
                <CircularProgress size={24} color="inherit" />
              ) : (
                t("settingsSections.automatedTests.run")
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
            {t("settingsSections.automatedTests.output")}
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