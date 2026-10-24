// frontend/src/components/modals/OutputModal.jsx
// OutputModal component for viewing output details and actions

import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
  Grid,
  Chip,
  Divider,
  IconButton,
  CircularProgress,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  TextField,
  Radio,
  RadioGroup,
  FormControlLabel,
  FormLabel,
} from "@mui/material";
import {
  Close as CloseIcon,
  Download as DownloadIcon,
  Delete as DeleteIcon,
  ExpandMore as ExpandMoreIcon,
  CheckCircle as CheckCircleIcon,
  Warning as WarningIcon,
  Error as ErrorIcon,
  Refresh as RefreshIcon,
} from "@mui/icons-material";
import { useTheme } from "@mui/material/styles";
import { getOutputDetails, downloadOutputCSV, downloadOutputXML, deleteOutput, retryFailedRows } from "../../api/outputService";
import { getAvailableModels } from "../../api";
import { useUiTranslation } from "../../i18n";

const OutputModal = ({ 
  open, 
  onClose, 
  output, 
  onDelete 
}) => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const [details, setDetails] = useState(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState({ csv: false, xml: false, delete: false, retry: false });
  const [error, setError] = useState(null);
  const [showRetryDialog, setShowRetryDialog] = useState(false);
  const [availableModels, setAvailableModels] = useState([]);
  const [isLoadingModels, setIsLoadingModels] = useState(false);
  const [retryOptions, setRetryOptions] = useState({
    retry_mode: 'all_inactive',
    output_filename: 'retry_output.csv',
    model_name: '',
    prompt_rule_id: 19
  });

  // Load detailed output data when modal opens
  useEffect(() => {
    if (open && output?.filename) {
      loadOutputDetails();
    }
  }, [open, output]);

  // Load available models when modal opens
  useEffect(() => {
    if (open) {
      loadAvailableModels();
    }
  }, [open]);

  const loadAvailableModels = async () => {
    setIsLoadingModels(true);
    try {
      const models = await getAvailableModels();
      if (models && !models.error) {
        setAvailableModels(Array.isArray(models) ? models : []);
        // Set default model if none selected
        if (!retryOptions.model_name && models.length > 0) {
          setRetryOptions(prev => ({ ...prev, model_name: models[0].name }));
        }
      }
    } catch (err) {
      console.error('Failed to load models:', err);
    } finally {
      setIsLoadingModels(false);
    }
  };

  const loadOutputDetails = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getOutputDetails(output.filename);
      setDetails(result.data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleDownloadCSV = async () => {
    setActionLoading(prev => ({ ...prev, csv: true }));
    try {
      await downloadOutputCSV(output.filename);
    } catch (err) {
      setError(t("dialogs.output.downloadCsvFailed", { error: err.message }));
    } finally {
      setActionLoading(prev => ({ ...prev, csv: false }));
    }
  };

  const handleDownloadXML = async () => {
    setActionLoading(prev => ({ ...prev, xml: true }));
    try {
      await downloadOutputXML(output.filename);
    } catch (err) {
      setError(t("dialogs.output.downloadXmlFailed", { error: err.message }));
    } finally {
      setActionLoading(prev => ({ ...prev, xml: false }));
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(t("dialogs.output.deleteConfirm", { name: output.job_id }))) {
      return;
    }

    setActionLoading(prev => ({ ...prev, delete: true }));
    try {
      await deleteOutput(output.filename);
      if (onDelete && typeof onDelete === 'function') {
        onDelete(output);
      }
      onClose();
    } catch (err) {
      setError(t("dialogs.output.deleteFailed", { error: err.message }));
    } finally {
      setActionLoading(prev => ({ ...prev, delete: false }));
    }
  };

  const handleRetry = async () => {
    setActionLoading(prev => ({ ...prev, retry: true }));
    try {
      const result = await retryFailedRows(output.filename, retryOptions);
      setError(null);
      // Close retry dialog and show success
      setShowRetryDialog(false);
      setError(t("dialogs.output.retryStarted", { id: result.retry_job_id }));
    } catch (err) {
      let errorMessage = err.message;
      
      // Provide helpful error messages for common issues
      if (errorMessage.includes('No failed topics found')) {
        if (retryOptions.retry_mode === 'partial_failures') {
          errorMessage = t("dialogs.output.noPartialFailures");
        } else if (retryOptions.retry_mode === 'zero_attempts') {
          errorMessage = t("dialogs.output.noZeroAttemptFailures");
        } else {
          errorMessage = t("dialogs.output.noFailedTopics");
        }
      }
      
      setError(t("dialogs.output.retryFailed", { error: errorMessage }));
    } finally {
      setActionLoading(prev => ({ ...prev, retry: false }));
    }
  };

  const handleQuickRetry = async () => {
    setActionLoading(prev => ({ ...prev, retry: true }));
    try {
      // Use simple retry options - retry all failed rows with original model
      const quickRetryOptions = {
        retry_mode: 'all_inactive',
        output_filename: `retry_${output.job_id}.csv`,
        model_name: '', // Will use original model
        prompt_rule_id: 19 // Default rule
      };

      const result = await retryFailedRows(output.filename, quickRetryOptions);
      setError(null);
      setError(t("dialogs.output.retryStarted", { id: result.retry_job_id }));
    } catch (err) {
      setError(t("dialogs.output.retryFailed", { error: err.message }));
    } finally {
      setActionLoading(prev => ({ ...prev, retry: false }));
    }
  };

  const handleRetryModeChange = (mode) => {
    setRetryOptions(prev => ({ ...prev, retry_mode: mode }));
  };

  const handleRetryFilenameChange = (filename) => {
    setRetryOptions(prev => ({ ...prev, output_filename: filename }));
  };

  const formatTimestamp = (timestamp) => {
    if (!timestamp) return "-";
    try {
      const date = new Date(timestamp);
      return date.toLocaleString("en-US", {
        year: "numeric",
        month: "long",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
    } catch (error) {
      return "-";
    }
  };

  const _formatDuration = (ms) => {
    if (!ms) return "-";
    const seconds = Math.round(ms / 1000);
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}m ${remainingSeconds}s`;
  };

  const getRowStatusIcon = (row) => {
    if (row.status === 'failed') return <ErrorIcon color="error" fontSize="small" />;
    if (row.replacement_count > 0) return <WarningIcon color="warning" fontSize="small" />;
    return <CheckCircleIcon color="success" fontSize="small" />;
  };

  const getRowStatusText = (row) => {
    if (row.status === 'failed') return t("dialogs.output.rowFailed");
    if (row.replacement_count > 0) return t("dialogs.output.rowReplaced", { count: row.replacement_count });
    return t("dialogs.output.rowOriginal");
  };

  if (!output) return null;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          maxHeight: "90vh",
          borderRadius: 2,
        },
      }}
    >
      <DialogTitle
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          pb: 1,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: "bold" }}>
            {output.job_id}
          </Typography>
          <Chip
            label={output.failed_rows > 0 ? t("dialogs.output.statusFailed") : output.replaced_rows > 0 ? t("dialogs.output.statusReplaced") : t("dialogs.output.statusComplete")}
            color={output.failed_rows > 0 ? "error" : output.replaced_rows > 0 ? "warning" : "success"}
            size="small"
          />
        </Box>
        <IconButton onClick={onClose} size="small">
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ p: 3 }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress />
          </Box>
        ) : error ? (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        ) : (
          <>
            {/* Summary Statistics */}
            <Box sx={{ mb: 3 }}>
              <Typography variant="h6" sx={{ mb: 2, fontWeight: "bold" }}>
                {t("dialogs.output.summary")}
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={6} sm={3}>
                  <Box sx={{ textAlign: "center" }}>
                    <Typography variant="h4" sx={{ fontWeight: "bold", color: "success.main" }}>
                      {output.active_rows}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {t("dialogs.output.generated")}
                    </Typography>
                  </Box>
                </Grid>
                <Grid item xs={6} sm={3}>
                  <Box sx={{ textAlign: "center" }}>
                    <Typography variant="h4" sx={{ fontWeight: "bold", color: "warning.main" }}>
                      {output.replaced_rows}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {t("dialogs.output.replaced")}
                    </Typography>
                  </Box>
                </Grid>
                <Grid item xs={6} sm={3}>
                  <Box sx={{ textAlign: "center" }}>
                    <Typography variant="h4" sx={{ fontWeight: "bold", color: "error.main" }}>
                      {output.failed_rows}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {t("dialogs.output.failed")}
                    </Typography>
                  </Box>
                </Grid>
                <Grid item xs={6} sm={3}>
                  <Box sx={{ textAlign: "center" }}>
                    <Typography variant="h4" sx={{ fontWeight: "bold", color: "primary.main" }}>
                      {output.success_rate.toFixed(0)}%
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {t("dialogs.output.successRate")}
                    </Typography>
                  </Box>
                </Grid>
              </Grid>
            </Box>

            <Divider sx={{ my: 2 }} />

            {/* Generation Metadata */}
            <Box sx={{ mb: 3 }}>
              <Typography variant="h6" sx={{ mb: 2, fontWeight: "bold" }}>
                {t("dialogs.output.generationDetails")}
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={12} sm={6}>
                  <Typography variant="body2" color="text.secondary">
                    {t("dialogs.output.generated")}
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                    {formatTimestamp(output.export_timestamp)}
                  </Typography>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Typography variant="body2" color="text.secondary">
                    {t("dialogs.output.llmModel")}
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: "medium", fontFamily: "monospace" }}>
                    {output.model_name || t("dialogs.output.unknown")}
                  </Typography>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Typography variant="body2" color="text.secondary">
                    {t("dialogs.output.targetRows")}
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                    {output.target_row_count}
                  </Typography>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Typography variant="body2" color="text.secondary">
                    {t("dialogs.output.fileSize")}
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                    {(output.file_size / 1024).toFixed(1)} KB
                  </Typography>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Typography variant="body2" color="text.secondary">
                    {t("dialogs.output.totalRows")}
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                    {output.total_rows}
                  </Typography>
                </Grid>
                {output.client && (
                  <Grid item xs={12} sm={6}>
                    <Typography variant="body2" color="text.secondary">
                      {t("dialogs.output.client")}
                    </Typography>
                    <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                      {output.client}
                    </Typography>
                  </Grid>
                )}
                {output.project && (
                  <Grid item xs={12} sm={6}>
                    <Typography variant="body2" color="text.secondary">
                      {t("dialogs.output.project")}
                    </Typography>
                    <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                      {output.project}
                    </Typography>
                  </Grid>
                )}
              </Grid>
            </Box>

            {/* Row Details */}
            {details && details.row_records && (
              <Accordion>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography variant="h6" sx={{ fontWeight: "bold" }}>
                    {t("dialogs.output.rowDetails", { count: details.row_records.length })}
                  </Typography>
                </AccordionSummary>
                <AccordionDetails>
                  <List dense>
                    {details.row_records.slice(0, 20).map((row, index) => (
                      <ListItem key={row.unique_id || index}>
                        <ListItemIcon>
                          {getRowStatusIcon(row)}
                        </ListItemIcon>
                        <ListItemText
                          primary={t("dialogs.output.rowTitle", { number: index + 1, topic: row.original_topic || t("dialogs.output.unknown") })}
                          secondary={t("dialogs.output.rowSummary", { status: getRowStatusText(row), count: row.final_word_count || 0 })}
                        />
                      </ListItem>
                    ))}
                    {details.row_records.length > 20 && (
                      <ListItem>
                        <ListItemText
                          secondary={t("dialogs.output.moreRows", { count: details.row_records.length - 20 })}
                        />
                      </ListItem>
                    )}
                  </List>
                </AccordionDetails>
              </Accordion>
            )}
          </>
        )}
      </DialogContent>

      <DialogActions sx={{ p: 2, gap: 1 }}>
        <Button
          variant="outlined"
          startIcon={actionLoading.xml ? <CircularProgress size={16} /> : <DownloadIcon />}
          onClick={handleDownloadXML}
          disabled={actionLoading.xml || actionLoading.csv || actionLoading.delete || actionLoading.retry}
        >
          XML
        </Button>
        <Button
          variant="outlined"
          startIcon={actionLoading.csv ? <CircularProgress size={16} /> : <DownloadIcon />}
          onClick={handleDownloadCSV}
          disabled={actionLoading.xml || actionLoading.csv || actionLoading.delete || actionLoading.retry}
        >
          {output?.has_retries ? t("dialogs.output.csvMerged") : 'CSV'}
        </Button>
        {output && output.inactive_rows > 0 && (
          <Button
            variant="outlined"
            startIcon={actionLoading.retry ? <CircularProgress size={16} /> : <RefreshIcon />}
            onClick={handleQuickRetry}
            disabled={actionLoading.xml || actionLoading.csv || actionLoading.delete || actionLoading.retry}
            sx={{
              borderColor: theme.palette.warning.main,
              color: theme.palette.warning.main,
              '&:hover': {
                backgroundColor: theme.palette.warning.light,
                borderColor: theme.palette.warning.dark,
              }
            }}
          >
            {t("dialogs.output.retryFailedButton")}
          </Button>
        )}
        <Button
          variant="outlined"
          startIcon={actionLoading.delete ? <CircularProgress size={16} /> : <DeleteIcon />}
          onClick={handleDelete}
          disabled={actionLoading.xml || actionLoading.csv || actionLoading.delete || actionLoading.retry}
          sx={{
            borderColor: theme.palette.grey[500],
            color: theme.palette.grey[700],
            '&:hover': {
              backgroundColor: theme.palette.grey[100],
              borderColor: theme.palette.grey[600],
            }
          }}
        >
          {t("dialogs.output.deleteButton")}
        </Button>
      </DialogActions>

      {/* Retry Configuration Dialog */}
      <Dialog
        open={showRetryDialog}
        onClose={() => setShowRetryDialog(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>{t("dialogs.output.retryTitle")}</DialogTitle>
        <DialogContent sx={{ p: 3 }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            {t("dialogs.output.retryIntro", { count: output?.inactive_rows || 0, job: output?.job_id })}
          </Typography>

          <Box sx={{ mb: 3 }}>
            <FormLabel component="legend" sx={{ mb: 2, fontWeight: "bold" }}>
              {t("dialogs.output.retryMode")}
            </FormLabel>
            <RadioGroup
              value={retryOptions.retry_mode}
              onChange={(e) => handleRetryModeChange(e.target.value)}
            >
              <FormControlLabel
                value="all_inactive"
                control={<Radio />}
                label={t("dialogs.output.modeAll", { count: output?.inactive_rows || 0 })}
              />
              <FormControlLabel
                value="zero_attempts"
                control={<Radio />}
                label={t("dialogs.output.modeZero", { count: output?.inactive_rows - (output?.replaced_rows || 0) || 0 })}
              />
              <FormControlLabel
                value="partial_failures"
                control={<Radio />}
                label={t("dialogs.output.modePartial", { count: output?.replaced_rows || 0 })}
              />
            </RadioGroup>
            <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
              {t("dialogs.output.retryTip")}
            </Typography>
          </Box>

          <Box sx={{ mb: 3 }}>
            <TextField
              fullWidth
              label={t("dialogs.output.outputFilename")}
              value={retryOptions.output_filename}
              onChange={(e) => handleRetryFilenameChange(e.target.value)}
              placeholder="retry_output.csv"
              variant="outlined"
            />
          </Box>

          <Box sx={{ mb: 3 }}>
            <FormControl fullWidth variant="outlined">
              <InputLabel>{t("dialogs.output.model")}</InputLabel>
              <Select
                value={retryOptions.model_name}
                onChange={(e) => setRetryOptions(prev => ({ ...prev, model_name: e.target.value }))}
                label={t("dialogs.output.model")}
                disabled={isLoadingModels}
              >
                {availableModels.map((model) => (
                  <MenuItem key={model.name} value={model.name}>
                    {model.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Box>
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button onClick={() => setShowRetryDialog(false)}>
            {t("dialogs.shared.cancel")}
          </Button>
          <Button
            variant="contained"
            startIcon={actionLoading.retry ? <CircularProgress size={16} /> : <RefreshIcon />}
            onClick={handleRetry}
            disabled={actionLoading.retry || !retryOptions.model_name}
          >
            {t("dialogs.output.startRetry")}
          </Button>
        </DialogActions>
      </Dialog>
    </Dialog>
  );
};

export default OutputModal;
