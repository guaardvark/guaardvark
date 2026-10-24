import React from 'react';
import { Stepper, Step, StepLabel, Box, Tooltip, Typography } from '@mui/material';
import { useUiTranslation, translate } from '../../i18n';

const stages = () => [
  { key: 'screenwriting', label: translate('filmCrew.stages.screenwriting') },
  { key: 'casting', label: translate('filmCrew.stages.casting') },
  { key: 'cinematography', label: translate('filmCrew.stages.cinematography') },
  { key: 'storyboard_gen', label: translate('filmCrew.stages.storyboard') },
  { key: 'awaiting_approval', label: translate('filmCrew.stages.approval') },
  { key: 'rendering', label: translate('filmCrew.stages.rendering') },
  { key: 'complete', label: translate('filmCrew.stages.complete') }
];

const GATED_STAGES = ['casting', 'awaiting_approval'];

const StageProgress = ({ currentStage, status, errorBlob }) => {
  const { t } = useUiTranslation();
  const STAGES = stages();
  // Decision: Map draft to Screenwriting (step 0) as it advances immediately anyway.
  // A finished production has every step done, Complete included: past the last index.
  const activeStep = currentStage === 'complete'
    ? STAGES.length
    : STAGES.findIndex(s => s.key === currentStage);
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
              <Tooltip title={errorBlob?.error || t('filmCrew.stages.unknownError')}>
                <Typography variant="caption" color="error">
                  {t('filmCrew.stages.failed')}
                </Typography>
              </Tooltip>
            );
          } else if (isGated) {
            labelProps.optional = (
              <Typography variant="caption" color="warning.main" sx={{ fontWeight: 'bold' }}>
                {t('filmCrew.stages.actionNeeded')}
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
