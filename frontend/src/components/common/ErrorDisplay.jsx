import React from 'react';
import { stripHtmlTags } from '../../utils/inputValidation';
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Collapse,
  IconButton,
  Typography,
  Stack,
  Card,
  CardContent,
  Snackbar
} from '@mui/material';
import {
  Error as ErrorIcon,
  Warning as WarningIcon,
  Info as InfoIcon,
  Close as CloseIcon,
  Refresh as RefreshIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  BugReport as BugReportIcon
} from '@mui/icons-material';
import { useState } from 'react';
import { translate, uiLocale, useUiTranslation, Trans } from '../../i18n';

/**
 * Comprehensive error display component
 * Supports different error types and severities with appropriate actions
 */
export const ErrorDisplay = ({
  error,
  severity = 'error',
  variant = 'filled',
  showIcon = true,
  _showDetails = false,
  onRetry = null,
  onDismiss = null,
  onReport = null,
  className = '',
  sx = {}
}) => {
  const { t } = useUiTranslation();
  const [showExpandedDetails, setShowExpandedDetails] = useState(false);
  
  if (!error) return null;

  // Parse error object or string
  const errorInfo = typeof error === 'string' 
    ? { message: error }
    : {
        message: error.message || t('layoutCommon.errorDisplay.unexpectedError'),
        code: error.code || error.status,
        details: error.details || error.stack,
        timestamp: error.timestamp || new Date().toISOString()
      };

  // Determine icon based on severity
  const getIcon = () => {
    switch (severity) {
      case 'error': return <ErrorIcon />;
      case 'warning': return <WarningIcon />;
      case 'info': return <InfoIcon />;
      default: return <ErrorIcon />;
    }
  };

  // Sanitize error message to prevent XSS
  const sanitizeErrorMessage = (message) => {
    if (typeof message !== 'string') return t('layoutCommon.errorDisplay.genericError');
    
    // Strip markup but keep its text; React escapes the result when rendering.
    return stripHtmlTags(message).trim();
  };

  // Format error for user display
  const formatErrorMessage = (message) => {
    // Sanitize first
    const sanitized = sanitizeErrorMessage(message);
    
    // Replace technical jargon with user-friendly messages
    const friendlyMessages = {
      'Network Error': t('layoutCommon.errorDisplay.friendly.network'),
      'timeout': t('layoutCommon.errorDisplay.friendly.timeout'),
      '401': t('layoutCommon.errorDisplay.friendly.unauthorized'),
      '403': t('layoutCommon.errorDisplay.friendly.forbidden'),
      '404': t('layoutCommon.errorDisplay.friendly.notFound'),
      '500': t('layoutCommon.errorDisplay.friendly.serverError'),
      '503': t('layoutCommon.errorDisplay.friendly.unavailable')
    };

    for (const [key, friendly] of Object.entries(friendlyMessages)) {
      if (sanitized.toLowerCase().includes(key.toLowerCase())) {
        return friendly;
      }
    }
    
    return sanitized;
  };

  const hasDetails = errorInfo.details || errorInfo.code || errorInfo.timestamp;

  return (
    <Alert
      severity={severity}
      variant={variant}
      icon={showIcon ? getIcon() : false}
      className={className}
      sx={{
        '& .MuiAlert-message': {
          width: '100%'
        },
        ...sx
      }}
      action={
        <Stack direction="row" spacing={1} alignItems="center">
          {hasDetails && (
            <IconButton
              size="small"
              onClick={() => setShowExpandedDetails(!showExpandedDetails)}
              aria-label={t('layoutCommon.errorDisplay.toggleDetails')}
            >
              {showExpandedDetails ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </IconButton>
          )}
          {onRetry && (
            <Button
              size="small"
              startIcon={<RefreshIcon />}
              onClick={onRetry}
              variant="outlined"
              color="inherit"
            >
              {t('common.retry')}
            </Button>
          )}
          {onReport && (
            <Button
              size="small"
              startIcon={<BugReportIcon />}
              onClick={() => onReport(errorInfo)}
              variant="outlined"
              color="inherit"
            >
              {t('layoutCommon.errorDisplay.report')}
            </Button>
          )}
          {onDismiss && (
            <IconButton
              size="small"
              onClick={onDismiss}
              aria-label={t('layoutCommon.errorDisplay.closeError')}
            >
              <CloseIcon />
            </IconButton>
          )}
        </Stack>
      }
    >
      <AlertTitle sx={{ mb: 1 }}>
        {severity === 'error' ? t('common.error') : 
         severity === 'warning' ? t('layoutCommon.errorDisplay.severity.warning') : 
         t('layoutCommon.errorDisplay.severity.info')}
      </AlertTitle>
      
      <Typography variant="body2" component="div">
        {formatErrorMessage(errorInfo.message)}
      </Typography>

      {hasDetails && (
        <Collapse in={showExpandedDetails} timeout="auto" unmountOnExit>
          <Box sx={{ mt: 2, p: 2, bgcolor: 'action.hover', borderRadius: 1 }}>
            <Typography variant="caption" color="text.secondary" component="div">
              <strong>{t('layoutCommon.errorDisplay.errorDetails')}:</strong>
            </Typography>
            {errorInfo.code && (
              <Typography variant="caption" color="text.secondary" component="div">
                <Trans i18nKey="layoutCommon.errorDisplay.code" values={{ code: errorInfo.code }} components={{ bold: <strong /> }} />
              </Typography>
            )}
            {errorInfo.timestamp && (
              <Typography variant="caption" color="text.secondary" component="div">
                <Trans
                  i18nKey="layoutCommon.errorDisplay.time"
                  values={{ time: new Date(errorInfo.timestamp).toLocaleString(uiLocale()) }}
                  components={{ bold: <strong /> }}
                />
              </Typography>
            )}
            {errorInfo.details && (
              <Box sx={{ mt: 1 }}>
                <Typography variant="caption" color="text.secondary" component="div">
                  <strong>{t('layoutCommon.errorDisplay.technicalDetails')}:</strong>
                </Typography>
                <Typography 
                  variant="caption" 
                  component="pre" 
                  sx={{ 
                    fontFamily: 'monospace',
                    fontSize: '0.7rem',
                    mt: 0.5,
                    overflow: 'auto',
                    maxHeight: '200px',
                    whiteSpace: 'pre-wrap'
                  }}
                >
                  {typeof errorInfo.details === 'string' 
                    ? errorInfo.details 
                    : JSON.stringify(errorInfo.details, null, 2)}
                </Typography>
              </Box>
            )}
          </Box>
        </Collapse>
      )}
    </Alert>
  );
};

/**
 * Error boundary wrapper component
 */
export const ErrorCard = ({ 
  error, 
  title = translate("layoutCommon.errorDisplay.cardTitle"),
  description = translate("layoutCommon.errorDisplay.cardDescription"),
  onRetry = null,
  showReportButton = true
}) => {
  const { t } = useUiTranslation();
  if (!error) return null;

  return (
    <Card sx={{ maxWidth: 600, mx: 'auto', mt: 4 }}>
      <CardContent>
        <Stack spacing={2} alignItems="center" textAlign="center">
          <ErrorIcon color="error" sx={{ fontSize: 48 }} />
          <Typography variant="h5" component="h2">
            {title}
          </Typography>
          <Typography variant="body1" color="text.secondary">
            {description}
          </Typography>
          
          <ErrorDisplay 
            error={error}
            severity="error"
            variant="outlined"
            showDetails={true}
            onRetry={onRetry}
            onReport={showReportButton ? (errorInfo) => {
              console.error('Error reported:', errorInfo);
              // Could integrate with error reporting service here
            } : null}
          />
          
          <Stack direction="row" spacing={2}>
            {onRetry && (
              <Button
                variant="contained"
                startIcon={<RefreshIcon />}
                onClick={onRetry}
              >
                {t('layoutCommon.errorDisplay.tryAgain')}
              </Button>
            )}
            <Button
              variant="outlined"
              onClick={() => window.location.reload()}
            >
              {t('layoutCommon.errorDisplay.refreshPage')}
            </Button>
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  );
};

/**
 * Toast notification for errors
 */
export const ErrorToast = ({
  error,
  open,
  onClose,
  autoHideDuration = 6000,
  severity = 'error'
}) => {
  const { t } = useUiTranslation();
  if (!error) return null;

  return (
    <Snackbar
      open={open}
      autoHideDuration={autoHideDuration}
      onClose={onClose}
      anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
    >
      <Alert 
        onClose={onClose} 
        severity={severity}
        variant="filled"
        sx={{ width: '100%' }}
      >
        {typeof error === 'string' ? error : error.message || t('layoutCommon.errorDisplay.genericError')}
      </Alert>
    </Snackbar>
  );
};

export default ErrorDisplay; 