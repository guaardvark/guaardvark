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

const OutputModal = ({ 
  open, 
  onClose, 
  output, 
  onDelete 
}) => {
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
      setError(`Failed to download CSV: ${err.message}`);
    } finally {
      setActionLoading(prev => ({ ...prev, csv: false }));
    }
  };

  const handleDownloadXML = async () => {
    setActionLoading(prev => ({ ...prev, xml: true }));
    try {
      await downloadOutputXML(output.filename);
    } catch (err) {
      setError(`Failed to download XML: ${err.message}`);
    } finally {
      setActionLoading(prev => ({ ...prev, xml: false }));
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`Are you sure you want to delete output "${output.job_id}"? This action cannot be undone.`)) {
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
      setError(`Failed to delete output: ${err.message}`);
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
      setError(`Retry job started successfully! Job ID: ${result.retry_job_id}`);
    } catch (err) {
      let errorMessage = err.message;
      
      // Provide helpful error messages for common issues
      if (errorMessage.includes('No failed topics found')) {
        if (retryOptions.retry_mode === 'partial_failures') {
          errorMessage = 'No partial failures found. Try "All Failed" or "Zero Attempts Only" instead.';
        } else if (retryOptions.retry_mode === 'zero_attempts') {
          errorMessage = 'No zero-attempt failures found. Try "All Failed" instead.';
        } else {
          errorMessage = 'No failed topics found for the selected retry mode. Try a different mode.';
        }
      }
      
      setError(`Failed to start retry job: ${errorMessage}`);
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
      setError(`Retry job started successfully! Job ID: ${result.retry_job_id}`);
    } catch (err) {
      setError(`Failed to start retry job: ${err.message}`);
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
    if (row.status === 'failed') return 'Failed';
    if (row.replacement_count > 0) return `Replaced (${row.replacement_count} attempts)`;
    return 'Original';
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
            label={output.failed_rows > 0 ? "Failed" : output.replaced_rows > 0 ? "Replaced" : "Complete"}
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
                Summary
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={6} sm={3}>
                  <Box sx={{ textAlign: "center" }}>
                    <Typography variant="h4" sx={{ fontWeight: "bold", color: "success.main" }}>
                      {output.active_rows}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Generated
                    </Typography>
                  </Box>
                </Grid>
                <Grid item xs={6} sm={3}>
                  <Box sx={{ textAlign: "center" }}>
                    <Typography variant="h4" sx={{ fontWeight: "bold", color: "warning.main" }}>
                      {output.replaced_rows}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Replaced
                    </Typography>
                  </Box>
                </Grid>
                <Grid item xs={6} sm={3}>
                  <Box sx={{ textAlign: "center" }}>
                    <Typography variant="h4" sx={{ fontWeight: "bold", color: "error.main" }}>
                      {output.failed_rows}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Failed
                    </Typography>
                  </Box>
                </Grid>
                <Grid item xs={6} sm={3}>
                  <Box sx={{ textAlign: "center" }}>
                    <Typography variant="h4" sx={{ fontWeight: "bold", color: "primary.main" }}>
                      {output.success_rate.toFixed(0)}%
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Success Rate
                    </Typography>
                  </Box>
                </Grid>
              </Grid>
            </Box>

            <Divider sx={{ my: 2 }} />

            {/* Generation Metadata */}
            <Box sx={{ mb: 3 }}>
              <Typography variant="h6" sx={{ mb: 2, fontWeight: "bold" }}>
                Generation Details
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={12} sm={6}>
                  <Typography variant="body2" color="text.secondary">
                    Generated
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                    {formatTimestamp(output.export_timestamp)}
                  </Typography>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Typography variant="body2" color="text.secondary">
                    LLM Model
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: "medium", fontFamily: "monospace" }}>
                    {output.model_name || "Unknown"}
                  </Typography>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Typography variant="body2" color="text.secondary">
                    Target Rows
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                    {output.target_row_count}
                  </Typography>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Typography variant="body2" color="text.secondary">
                    File Size
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                    {(output.file_size / 1024).toFixed(1)} KB
                  </Typography>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Typography variant="body2" color="text.secondary">
                    Total Rows
                  </Typography>
                  <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                    {output.total_rows}
                  </Typography>
                </Grid>
                {output.client && (
                  <Grid item xs={12} sm={6}>
                    <Typography variant="body2" color="text.secondary">
                      Client
                    </Typography>
                    <Typography variant="body1" sx={{ fontWeight: "medium" }}>
                      {output.client}
                    </Typography>
                  </Grid>
                )}
                {output.project && (
                  <Grid item xs={12} sm={6}>
                    <Typography variant="body2" color="text.secondary">
                      Project
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
                    Row Details ({details.row_records.length} rows)
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
                          primary={`Row ${index + 1}: ${row.original_topic || 'Unknown'}`}
                          secondary={`${getRowStatusText(row)} - ${row.final_word_count || 0} words`}
                        />
                      </ListItem>
                    ))}
                    {details.row_records.length > 20 && (
                      <ListItem>
                        <ListItemText
                          secondary={`... and ${details.row_records.length - 20} more rows`}
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
          {output?.has_retries ? 'CSV (Merged)' : 'CSV'}
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
            RETRY FAILED
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
          DELETE
        </Button>
      </DialogActions>

      {/* Retry Configuration Dialog */}
      <Dialog
        open={showRetryDialog}
        onClose={() => setShowRetryDialog(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Retry Failed Rows</DialogTitle>
        <DialogContent sx={{ p: 3 }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Retry generation for {output?.inactive_rows || 0} failed rows from job {output?.job_id}
          </Typography>

          <Box sx={{ mb: 3 }}>
            <FormLabel component="legend" sx={{ mb: 2, fontWeight: "bold" }}>
              Retry Mode
            </FormLabel>
            <RadioGroup
              value={retryOptions.retry_mode}
              onChange={(e) => handleRetryModeChange(e.target.value)}
            >
              <FormControlLabel
                value="all_inactive"
                control={<Radio />}
                label={`All Failed (${output?.inactive_rows || 0} rows) - Recommended`}
              />
              <FormControlLabel
                value="zero_attempts"
                control={<Radio />}
                label={`Zero Attempts Only (${output?.inactive_rows - (output?.replaced_rows || 0) || 0} rows)`}
              />
              <FormControlLabel
                value="partial_failures"
                control={<Radio />}
                label={`Partial Failures (${output?.replaced_rows || 0} rows) - May be empty`}
              />
            </RadioGroup>
            <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
              💡 Tip: If "Partial Failures" shows 0 rows, use "All Failed" or "Zero Attempts Only"
            </Typography>
          </Box>

          <Box sx={{ mb: 3 }}>
            <TextField
              fullWidth
              label="Output Filename"
              value={retryOptions.output_filename}
              onChange={(e) => handleRetryFilenameChange(e.target.value)}
              placeholder="retry_output.csv"
              variant="outlined"
            />
          </Box>

          <Box sx={{ mb: 3 }}>
            <FormControl fullWidth variant="outlined">
              <InputLabel>Model</InputLabel>
              <Select
                value={retryOptions.model_name}
                onChange={(e) => setRetryOptions(prev => ({ ...prev, model_name: e.target.value }))}
                label="Model"
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
            Cancel
          </Button>
          <Button
            variant="contained"
            startIcon={actionLoading.retry ? <CircularProgress size={16} /> : <RefreshIcon />}
            onClick={handleRetry}
            disabled={actionLoading.retry || !retryOptions.model_name}
          >
            Start Retry
          </Button>
        </DialogActions>
      </Dialog>
    </Dialog>
  );
};

export default OutputModal;
