import React from 'react';
import { Stepper, Step, StepLabel, Box, Tooltip, Typography } from '@mui/material';

const STAGES = [
  { key: 'screenwriting', label: 'Screenwriting' },
  { key: 'casting', label: 'Casting' },
  { key: 'cinematography', label: 'Cinematography' },
  { key: 'storyboard_gen', label: 'Storyboard' },
  { key: 'awaiting_approval', label: 'Approval' },
  { key: 'rendering', label: 'Rendering' },
  { key: 'complete', label: 'Complete' }
];

const GATED_STAGES = ['casting', 'awaiting_approval'];

const StageProgress = ({ currentStage, status, errorBlob }) => {
  // Decision: Map draft to Screenwriting (step 0) as it advances immediately anyway.
  const activeStep = STAGES.findIndex(s => s.key === currentStage);
  const isFailed = status?.startsWith('failed');
  
  return (
    <Box sx={{ width: '100%', my: 4 }}>
      <Stepper activeStep={activeStep === -1 ? 0 : activeStep} alternativeLabel>
        {STAGES.map((stage) => {
          const isError = isFailed && currentStage === stage.key;
          const isGated = GATED_STAGES.includes(stage.key) && currentStage === stage.key;
          
          let labelProps = {};
          if (isError) {
            labelProps.error = true;
            labelProps.optional = (
              <Tooltip title={errorBlob?.error || 'Unknown error'}>
                <Typography variant="caption" color="error">
                  Failed
                </Typography>
              </Tooltip>
            );
          } else if (isGated) {
            labelProps.optional = (
              <Typography variant="caption" color="warning.main" sx={{ fontWeight: 'bold' }}>
                Action needed
              </Typography>
            );
          }

          return (
            <Step key={stage.key}>
              <StepLabel {...labelProps}>{stage.label}</StepLabel>
            </Step>
          );
        })}
      </Stepper>
    </Box>
  );
};

export default StageProgress;
