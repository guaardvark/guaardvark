// frontend/src/components/settings/RAGDebugSection.jsx
// RAG Performance & Debug section for SettingsPage

import React, { useState, useEffect, useCallback } from "react";
import {
  Typography,
  Box,
  Button,
  Paper,
  Grid,
  Chip,
  CircularProgress,
  Alert,
  LinearProgress,
  Tooltip,
  Stack,
} from "@mui/material";

// Icons
import SpeedIcon from "@mui/icons-material/Speed";
import StorageIcon from "@mui/icons-material/Storage";
import DnsIcon from "@mui/icons-material/Dns";
import RefreshIcon from "@mui/icons-material/Refresh";
import BugReportIcon from "@mui/icons-material/BugReport";
import AnalyticsIcon from "@mui/icons-material/Analytics";
import HealthAndSafetyIcon from "@mui/icons-material/HealthAndSafety";

import {
  getSystemHealth,
  getPerformanceMetrics,
  formatUptime,
  formatBytes,
  getHealthColor,
  getHealthPercentage,
} from "../../api/ragDebugService";

// Modal components
import TestRetrievalModal from "../modals/TestRetrievalModal";
import QueryPatternsModal from "../modals/QueryPatternsModal";
import ContextQualityModal from "../modals/ContextQualityModal";

const RAGDebugSection = ({ ragDebugEnabled }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [systemHealth, setSystemHealth] = useState(null);
  const [_performanceMetrics, setPerformanceMetrics] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  // Modal states
  const [testRetrievalModalOpen, setTestRetrievalModalOpen] = useState(false);
  const [queryPatternsModalOpen, setQueryPatternsModalOpen] = useState(false);
  const [contextQualityModalOpen, setContextQualityModalOpen] = useState(false);

  // Fetch RAG data — only when RAG debug is enabled
  const fetchRAGData = useCallback(async () => {
    if (!ragDebugEnabled) return;

    setLoading(true);
    setError(null);

    try {
      const [healthResponse, metricsResponse] = await Promise.all([
        getSystemHealth(),
        getPerformanceMetrics(24),
      ]);

      setSystemHealth(healthResponse.data);
      setPerformanceMetrics(metricsResponse.data);
      setLastUpdated(new Date());
    } catch (err) {
      console.error("Failed to fetch RAG data:", err);
      setError(err.message || "Failed to load RAG information");
    } finally {
      setLoading(false);
    }
  }, [ragDebugEnabled]);

  // Fetch when enabled, clear polling when disabled
  useEffect(() => {
    if (!ragDebugEnabled) {
      setSystemHealth(null);
      setPerformanceMetrics(null);
      setError(null);
      return;
    }
    fetchRAGData();
    const interval = setInterval(fetchRAGData, 30000);
    return () => clearInterval(interval);
  }, [fetchRAGData, ragDebugEnabled]);

  return (
    <Box>
      <Box display="flex" justifyContent="flex-end" alignItems="center" mb={1.5}>
        <Stack direction="row" spacing={1} alignItems="center">
          {lastUpdated && (
            <Typography variant="caption" color="text.secondary">
              Updated: {lastUpdated.toLocaleTimeString()}
            </Typography>
          )}
          <Tooltip title="Refresh data">
            <Button
              size="small"
              variant="outlined"
              onClick={fetchRAGData}
              disabled={loading}
              startIcon={loading ? <CircularProgress size={16} /> : <RefreshIcon />}
            >
              Refresh
            </Button>
          </Tooltip>
        </Stack>
      </Box>

      {error && (
        <Alert severity="info" sx={{ mb: 2 }}>
          No RAG data available. Index some documents to see performance stats.
        </Alert>
      )}

      {loading && !systemHealth && (
        <Box display="flex" justifyContent="center" py={4}>
          <CircularProgress />
        </Box>
      )}

      {systemHealth && (
        <Grid container spacing={2}>
          {/* System Health Overview */}
          <Grid item xs={12} sm={6} md={4}>
            <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
              <Typography variant="subtitle1" gutterBottom display="flex" alignItems="center" gap={1}>
                <HealthAndSafetyIcon fontSize="small" />
                System Health
              </Typography>

              <Box display="flex" alignItems="center" gap={2} mb={2}>
                <Chip
                  label={`${getHealthPercentage(systemHealth.health_score)}%`}
                  color={getHealthColor(systemHealth.health_level)}
                  variant="filled"
                  size="small"
                />
                <Typography variant="body2" color="text.secondary">
                  {systemHealth.health_level?.toUpperCase()}
                </Typography>
              </Box>

              <Typography variant="body2" color="text.secondary" mb={1}>
                Uptime: {formatUptime(systemHealth.uptime)}
              </Typography>

              {systemHealth.health_issues?.length > 0 && (
                <Alert severity="warning" size="small">
                  <Typography variant="caption">
                    Issues: {systemHealth.health_issues.join(", ")}
                  </Typography>
                </Alert>
              )}
            </Paper>
          </Grid>

          {/* Index Statistics */}
          <Grid item xs={12} sm={6} md={4}>
            <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
              <Typography variant="subtitle1" gutterBottom display="flex" alignItems="center" gap={1}>
                <StorageIcon fontSize="small" />
                Index Statistics
              </Typography>

              <Stack spacing={1}>
                <Box display="flex" justifyContent="space-between">
                  <Typography variant="body2">Cached Indexes:</Typography>
                  <Typography variant="body2" fontWeight="bold">
                    {systemHealth.index_stats?.total_cached || 0}
                  </Typography>
                </Box>

                <Box display="flex" justifyContent="space-between">
                  <Typography variant="body2">Memory Usage:</Typography>
                  <Typography variant="body2" fontWeight="bold">
                    {formatBytes(systemHealth.index_stats?.total_memory_usage || 0)}
                  </Typography>
                </Box>

                <Box>
                  <Typography variant="body2" mb={0.5}>
                    Cache Hit Rate: {Math.round((systemHealth.index_stats?.access_stats?.cache_hits || 0) /
                    Math.max(1, (systemHealth.index_stats?.access_stats?.total_loads || 1)) * 100)}%
                  </Typography>
                  <LinearProgress
                    variant="determinate"
                    value={Math.round((systemHealth.index_stats?.access_stats?.cache_hits || 0) /
                    Math.max(1, (systemHealth.index_stats?.access_stats?.total_loads || 1)) * 100)}
                    color="primary"
                    sx={{ height: 6, borderRadius: 3 }}
                  />
                </Box>
              </Stack>
            </Paper>
          </Grid>

          {/* Performance Metrics */}
          <Grid item xs={12} sm={6} md={4}>
            <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
              <Typography variant="subtitle1" gutterBottom display="flex" alignItems="center" gap={1}>
                <SpeedIcon fontSize="small" />
                Performance
              </Typography>

              <Stack spacing={1}>
                <Box display="flex" justifyContent="space-between">
                  <Typography variant="body2">Total Queries:</Typography>
                  <Typography variant="body2" fontWeight="bold">
                    {systemHealth.system_metrics?.total_queries || 0}
                  </Typography>
                </Box>

                <Box display="flex" justifyContent="space-between">
                  <Typography variant="body2">Avg Response:</Typography>
                  <Typography variant="body2" fontWeight="bold">
                    {(systemHealth.system_metrics?.avg_retrieval_time || 0).toFixed(2)}ms
                  </Typography>
                </Box>

                <Box display="flex" justifyContent="space-between">
                  <Typography variant="body2">Error Rate:</Typography>
                  <Typography
                    variant="body2"
                    fontWeight="bold"
                    color={systemHealth.system_metrics?.error_rate > 0.05 ? "error.main" : "text.primary"}
                  >
                    {((systemHealth.system_metrics?.error_rate || 0) * 100).toFixed(1)}%
                  </Typography>
                </Box>
              </Stack>
            </Paper>
          </Grid>

          {/* Debug Actions — only shown when RAG Debug is enabled */}
          {ragDebugEnabled && (
            <Grid item xs={12}>
              <Paper variant="outlined" sx={{ p: 2 }}>
                <Typography variant="subtitle1" gutterBottom>
                  Debug Actions
                </Typography>

                <Stack direction="row" spacing={2} flexWrap="wrap">
                  <Button
                    variant="outlined"
                    size="small"
                    startIcon={<AnalyticsIcon />}
                    onClick={() => setTestRetrievalModalOpen(true)}
                  >
                    Test Retrieval
                  </Button>

                  <Button
                    variant="outlined"
                    size="small"
                    startIcon={<DnsIcon />}
                    onClick={() => setQueryPatternsModalOpen(true)}
                  >
                    Query Patterns
                  </Button>

                  <Button
                    variant="outlined"
                    size="small"
                    startIcon={<BugReportIcon />}
                    onClick={() => setContextQualityModalOpen(true)}
                  >
                    Context Quality
                  </Button>
                </Stack>
              </Paper>
            </Grid>
          )}
        </Grid>
      )}

      {/* RAG Debug Modals */}
      <TestRetrievalModal
        open={testRetrievalModalOpen}
        onClose={() => setTestRetrievalModalOpen(false)}
      />
      <QueryPatternsModal
        open={queryPatternsModalOpen}
        onClose={() => setQueryPatternsModalOpen(false)}
      />
      <ContextQualityModal
        open={contextQualityModalOpen}
        onClose={() => setContextQualityModalOpen(false)}
      />
    </Box>
  );
};

export default RAGDebugSection;
