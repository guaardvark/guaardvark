// frontend/src/pages/ContentLibraryPage.jsx
// Content Output Manager - View and manage bulk generation outputs
// Based on TaskPage structure for output management

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Box,
  Typography,
  Button,
  Paper,
  IconButton,
  Chip,
  Alert,
  Alert as MuiAlert,
  Snackbar,
  Tooltip,
} from '@mui/material';
import {
  Refresh as RefreshIcon,
  ClearAll as ClearAllIcon,
  LibraryBooksOutlined,
} from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';
import { useNavigate } from 'react-router-dom';

import PageLayout from '../components/layout/PageLayout';
import EmptyState from '../components/common/EmptyState';
import { getOutputs, retryFailedRows, deleteOutput } from '../api/outputService';
import OutputCard from '../components/cards/OutputCard';
import OutputModal from '../components/modals/OutputModal';
import { ContextualLoader } from '../components/common/LoadingStates';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

const ContentLibraryPage = () => {
  const theme = useTheme();
  const navigate = useNavigate();

  // State management
  const [outputs, setOutputs] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState({
    open: false,
    message: "",
    severity: "info",
  });
  const [selectedOutput, setSelectedOutput] = useState(null);
  const [showOutputModal, setShowOutputModal] = useState(false);

  // Load outputs on component mount
  useEffect(() => {
    fetchOutputs();
  }, []);

  // Fetch outputs from API
  const fetchOutputs = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await getOutputs();
      if (result.error) {
        setError(result.error);
      } else {
        setOutputs(result.outputs || []);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Handle output view
  const handleViewOutput = (output) => {
    setSelectedOutput(output);
    setShowOutputModal(true);
  };

  // Handle output delete
  const handleDeleteOutput = async (output) => {
    try {
      // Actually delete from backend
      await deleteOutput(output.filename);
      
      // Update local state
      setOutputs(prev => prev.filter(o => o.filename !== output.filename));
      setFeedback({
        open: true,
        message: `Output ${output.job_id} deleted successfully`,
        severity: "success",
      });
    } catch (err) {
      setFeedback({
        open: true,
        message: `Failed to delete output: ${err.message}`,
        severity: "error",
      });
    }
  };

  // Handle retry failed rows
  const handleRetryOutput = async (output) => {
    try {
      setFeedback({
        open: true,
        message: `Starting retry for ${output.inactive_rows} failed rows...`,
        severity: "info",
      });

      // Use the original model from the job context
      const retryOptions = {
        retry_mode: 'all_inactive',
        output_filename: `retry_${output.job_id}.csv`,
        model_name: '', // Will use original model
        prompt_rule_id: 19 // Default rule
      };

      const result = await retryFailedRows(output.filename, retryOptions);
      
      setFeedback({
        open: true,
        message: `Retry job started successfully! Job ID: ${result.retry_job_id}`,
        severity: "success",
      });

      // Refresh outputs to show updated status
      fetchOutputs();
      
    } catch (err) {
      setFeedback({
        open: true,
        message: `Failed to start retry: ${err.message}`,
        severity: "error",
      });
    }
  };

  // Handle modal close
  const handleCloseModal = () => {
    setShowOutputModal(false);
    setSelectedOutput(null);
  };

  // Handle clear all outputs
  const handleClearAllOutputs = async () => {
    if (outputs.length === 0) {
      setFeedback({
        open: true,
        message: "No outputs to clear",
        severity: "info",
      });
      return;
    }

    if (!window.confirm(`Are you sure you want to delete ALL ${outputs.length} outputs? This action cannot be undone.`)) {
      return;
    }

    try {
      setIsLoading(true);
      setFeedback({
        open: true,
        message: "Clearing all outputs...",
        severity: "info",
      });

      // Delete all outputs
      const deletePromises = outputs.map(output => deleteOutput(output.filename));
      await Promise.all(deletePromises);

      setFeedback({
        open: true,
        message: `Successfully deleted ${outputs.length} outputs`,
        severity: "success",
      });

      // Refresh outputs to show empty state
      fetchOutputs();
    } catch (err) {
      setFeedback({
        open: true,
        message: `Failed to clear outputs: ${err.message}`,
        severity: "error",
      });
    } finally {
      setIsLoading(false);
    }
  };

  // Handle feedback close
  const handleCloseFeedback = (event, reason) => {
    if (reason === "clickaway") return;
    setFeedback((prev) => ({ ...prev, open: false }));
  };

  // Calculate statistics for outputs
  const outputStats = useMemo(() => {
    const today = new Date().toDateString();
    const thisWeek = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toDateString();
    
    return {
      total: outputs.length,
      today: outputs.filter(o => new Date(o.created_at).toDateString() === today).length,
      thisWeek: outputs.filter(o => new Date(o.created_at).toDateString() >= thisWeek).length,
    };
  }, [outputs]);

  return (
    <PageLayout
      title="Content Outputs"
      variant="standard"
      actions={
        <>
          <Box sx={{ display: "flex", gap: 1 }}>
            <Chip
              label={`${outputStats.total} Total`}
              size="small"
              variant="outlined"
              sx={{ fontWeight: "bold" }}
            />
            <Chip
              label={`${outputStats.today} Today`}
              size="small"
              variant="outlined"
              sx={{ fontWeight: "bold" }}
            />
            <Chip
              label={`${outputStats.thisWeek} This Week`}
              size="small"
              variant="outlined"
              sx={{ fontWeight: "bold" }}
            />
          </Box>
          {outputs.length > 0 && (
            <Button
              variant="outlined"
              size="small"
              startIcon={<ClearAllIcon />}
              onClick={handleClearAllOutputs}
              disabled={isLoading}
              sx={{
                textTransform: "none",
                fontWeight: "medium",
              }}
            >
              Clear All
            </Button>
          )}
          <Tooltip title="Refresh Output List">
            <span>
              <IconButton
                onClick={fetchOutputs}
                disabled={isLoading}
              >
                <RefreshIcon />
              </IconButton>
            </span>
          </Tooltip>
        </>
      }
    >
        <Snackbar
          open={feedback.open}
          autoHideDuration={4000}
          onClose={handleCloseFeedback}
          anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        >
          <AlertSnackbar
            onClose={handleCloseFeedback}
            severity={feedback.severity || "info"}
            sx={{ width: "100%" }}
            variant="filled"
          >
            {feedback.message}
          </AlertSnackbar>
        </Snackbar>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {/* Output Modal */}
        <OutputModal
          open={showOutputModal}
          onClose={handleCloseModal}
          output={selectedOutput}
          onDelete={handleDeleteOutput}
        />

        {/* Content Outputs */}
        {isLoading ? (
              <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", py: 6 }}>
                <ContextualLoader loading message="Loading outputs..." showProgress={false} inline />
              </Box>
            ) : outputs.length === 0 ? (
              <Paper sx={{ p: 4, textAlign: "center", borderRadius: 3 }}>
                <EmptyState
                  icon={<LibraryBooksOutlined />}
                  title="No outputs found"
                  description="Generate some content using the bulk generation feature to see outputs here"
                />
              </Paper>
            ) : (
              <>
                {/* Output Cards Grid */}
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: {
                      xs: "1fr",
                      sm: "repeat(2, 1fr)",
                      md: "repeat(3, 1fr)",
                      lg: "repeat(4, 1fr)",
                    },
                    gap: 2,
                    mb: 2,
                  }}
                >
                  {outputs.map((output) => (
                    <OutputCard
                      key={output.filename}
                      output={output}
                      onView={handleViewOutput}
                      onDelete={handleDeleteOutput}
                      onRetry={handleRetryOutput}
                    />
                  ))}
                </Box>
              </>
            )}
    </PageLayout>
  );
};

export default ContentLibraryPage;