// frontend/src/components/settings/DebugSettingsSection.jsx
// Extracted from SettingsPage.jsx - Debug Settings functionality

import React from 'react';
import {
  Typography,
  Paper,
  Grid,
  Switch,
  FormControlLabel,
  Box
} from '@mui/material';

const DebugSettingsSection = ({ 
  ragDebug, 
  setRagDebug,
  advancedDebug,
  setAdvancedDebug,
  behaviorLearningEnabled,
  setBehaviorLearningEnabled,
  webSearchEnabled,
  setWebSearchEnabled
}) => {
  const WEB_SEARCH_ENABLED_KEY = 'web_search_enabled';
  const ADV_DEBUG_ENABLED_KEY = 'advanced_debug_enabled';
  const BEHAVIOR_LEARNING_ENABLED_KEY = 'behavior_learning_enabled';

  const handleWebSearchToggle = (event) => {
    const enabled = event.target.checked;
    setWebSearchEnabled(enabled);
    localStorage.setItem(WEB_SEARCH_ENABLED_KEY, enabled.toString());
  };

  const handleAdvancedDebugToggle = (event) => {
    const enabled = event.target.checked;
    setAdvancedDebug(enabled);
    localStorage.setItem(ADV_DEBUG_ENABLED_KEY, enabled.toString());
  };

  const handleBehaviorLearningToggle = (event) => {
    const enabled = event.target.checked;
    setBehaviorLearningEnabled(enabled);
    localStorage.setItem(BEHAVIOR_LEARNING_ENABLED_KEY, enabled.toString());
  };

  return (
    <Paper elevation={3} sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        Debug & Feature Settings
      </Typography>
      <Grid container spacing={2}>
        <Grid item xs={12}>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={ragDebug}
                  onChange={(e) => setRagDebug(e.target.checked)}
                />
              }
              label="Enable RAG Debug Mode"
            />
            <FormControlLabel
              control={
                <Switch
                  checked={advancedDebug}
                  onChange={handleAdvancedDebugToggle}
                />
              }
              label="Enable Advanced Debug"
            />
            <FormControlLabel
              control={
                <Switch
                  checked={behaviorLearningEnabled}
                  onChange={handleBehaviorLearningToggle}
                />
              }
              label="Enable Behavior Learning"
            />
            <FormControlLabel
              control={
                <Switch
                  checked={webSearchEnabled}
                  onChange={handleWebSearchToggle}
                />
              }
              label="Enable Web Search"
            />
          </Box>
        </Grid>
      </Grid>
    </Paper>
  );
};

export default DebugSettingsSection; 