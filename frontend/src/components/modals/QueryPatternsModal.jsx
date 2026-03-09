// frontend/src/components/modals/QueryPatternsModal.jsx
// Modal for analyzing RAG query patterns and performance

import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import { BASE_URL } from "../../api/apiClient";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
  Paper,
  Chip,
  CircularProgress,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Grid,
  Card,
  CardContent,
  Stack,
} from "@mui/material";

// Icons
import CloseIcon from "@mui/icons-material/Close";
import TrendingUpIcon from "@mui/icons-material/TrendingUp";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import SearchIcon from "@mui/icons-material/Search";
import ScoreIcon from "@mui/icons-material/Score";
import RefreshIcon from "@mui/icons-material/Refresh";
import AnalyticsIcon from "@mui/icons-material/Analytics";

const QueryPatternsModal = ({ open, onClose }) => {
  const [loading, setLoading] = useState(false);
  const [patterns, setPatterns] = useState(null);
  const [error, setError] = useState(null);

  const fetchPatterns = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${BASE_URL}/rag-debug/query-patterns`, {
        method: "GET",
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      
      if (!data.success) {
        throw new Error(data.error || "Failed to fetch query patterns");
      }

      setPatterns(data.data);
    } catch (err) {
      console.error("Query patterns error:", err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchPatterns();
    }
  }, [open, fetchPatterns]);

  const handleClose = () => {
    setPatterns(null);
    setError(null);
    onClose();
  };

  const formatTime = (time) => {
    if (typeof time === 'number') {
      return `${(time * 1000).toFixed(1)}ms`;
    }
    return 'N/A';
  };

  const formatScore = (score) => {
    if (typeof score === 'number') {
      return score.toFixed(3);
    }
    return 'N/A';
  };

  const getPerformanceColor = (time) => {
    if (time < 0.1) return "success";
    if (time < 0.5) return "warning";
    return "error";
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="lg" fullWidth>
      <DialogTitle>
        <Stack direction="row" alignItems="center" spacing={1}>
          <AnalyticsIcon color="primary" />
          <Typography variant="h6">Query Patterns Analysis</Typography>
          <Button
            size="small"
            onClick={fetchPatterns}
            startIcon={<RefreshIcon />}
            disabled={loading}
          >
            Refresh
          </Button>
        </Stack>
      </DialogTitle>

      <DialogContent>
        {loading && (
          <Box display="flex" justifyContent="center" alignItems="center" sx={{ py: 4 }}>
            <CircularProgress />
            <Typography variant="body2" sx={{ ml: 2 }}>
              Analyzing query patterns...
            </Typography>
          </Box>
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {patterns && !loading && (
          <Box>
            {/* Summary Cards */}
            <Grid container spacing={2} sx={{ mb: 3 }}>
              <Grid item xs={12} sm={6} md={3}>
                <Card>
                  <CardContent>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <SearchIcon color="primary" />
                      <Typography variant="h6">
                        {patterns.total_patterns || 0}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      Unique Patterns
                    </Typography>
                  </CardContent>
                </Card>
              </Grid>

              <Grid item xs={12} sm={6} md={3}>
                <Card>
                  <CardContent>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <TrendingUpIcon color="success" />
                      <Typography variant="h6">
                        {patterns.most_common_patterns?.[0]?.[1]?.count || 0}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      Most Used Pattern
                    </Typography>
                  </CardContent>
                </Card>
              </Grid>

              <Grid item xs={12} sm={6} md={3}>
                <Card>
                  <CardContent>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <AccessTimeIcon color="warning" />
                      <Typography variant="h6">
                        {formatTime(
                          Object.values(patterns.pattern_stats || {}).reduce((avg, stats, _, arr) => 
                            avg + stats.avg_retrieval_time / arr.length, 0
                          )
                        )}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      Avg Response Time
                    </Typography>
                  </CardContent>
                </Card>
              </Grid>

              <Grid item xs={12} sm={6} md={3}>
                <Card>
                  <CardContent>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <ScoreIcon color="info" />
                      <Typography variant="h6">
                        {formatScore(
                          Object.values(patterns.pattern_stats || {}).reduce((avg, stats, _, arr) => 
                            avg + stats.avg_similarity_score / arr.length, 0
                          )
                        )}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      Avg Similarity
                    </Typography>
                  </CardContent>
                </Card>
              </Grid>
            </Grid>

            {/* Most Common Patterns */}
            {patterns.most_common_patterns && patterns.most_common_patterns.length > 0 && (
              <Paper sx={{ mb: 3 }}>
                <Box sx={{ p: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
                  <Typography variant="h6">Most Common Query Patterns</Typography>
                  <Typography variant="body2" color="text.secondary">
                    Patterns are grouped by the first word of queries
                  </Typography>
                </Box>
                <TableContainer>
                  <Table>
                    <TableHead>
                      <TableRow>
                        <TableCell>Pattern</TableCell>
                        <TableCell align="right">Count</TableCell>
                        <TableCell align="right">Avg Time</TableCell>
                        <TableCell align="right">Avg Nodes</TableCell>
                        <TableCell align="right">Avg Score</TableCell>
                        <TableCell align="right">Performance</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {patterns.most_common_patterns.slice(0, 10).map(([pattern, stats], index) => (
                        <TableRow key={pattern}>
                          <TableCell>
                            <Chip 
                              label={pattern} 
                              size="small" 
                              variant="outlined"
                              color={index < 3 ? "primary" : "default"}
                            />
                          </TableCell>
                          <TableCell align="right">
                            <Typography variant="body2" fontWeight="medium">
                              {stats.count}
                            </Typography>
                          </TableCell>
                          <TableCell align="right">
                            <Typography variant="body2">
                              {formatTime(stats.avg_retrieval_time)}
                            </Typography>
                          </TableCell>
                          <TableCell align="right">
                            <Typography variant="body2">
                              {stats.avg_nodes_retrieved?.toFixed(1) || 'N/A'}
                            </Typography>
                          </TableCell>
                          <TableCell align="right">
                            <Typography variant="body2">
                              {formatScore(stats.avg_similarity_score)}
                            </Typography>
                          </TableCell>
                          <TableCell align="right">
                            <Chip
                              size="small"
                              label={
                                stats.avg_retrieval_time < 0.1 ? "Fast" :
                                stats.avg_retrieval_time < 0.5 ? "Good" : "Slow"
                              }
                              color={getPerformanceColor(stats.avg_retrieval_time)}
                            />
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              </Paper>
            )}

            {/* Performance Insights */}
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" gutterBottom>
                Performance Insights
              </Typography>
              
              {patterns.pattern_stats && Object.keys(patterns.pattern_stats).length > 0 ? (
                <Grid container spacing={2}>
                  <Grid item xs={12} md={6}>
                    <Typography variant="subtitle2" color="primary" gutterBottom>
                      Fastest Patterns
                    </Typography>
                    {Object.entries(patterns.pattern_stats)
                      .sort(([,a], [,b]) => a.avg_retrieval_time - b.avg_retrieval_time)
                      .slice(0, 3)
                      .map(([pattern, stats]) => (
                        <Box key={pattern} sx={{ mb: 1 }}>
                          <Stack direction="row" justifyContent="space-between" alignItems="center">
                            <Chip label={pattern} size="small" variant="outlined" />
                            <Typography variant="body2" color="success.main">
                              {formatTime(stats.avg_retrieval_time)}
                            </Typography>
                          </Stack>
                        </Box>
                      ))
                    }
                  </Grid>

                  <Grid item xs={12} md={6}>
                    <Typography variant="subtitle2" color="primary" gutterBottom>
                      Most Relevant Patterns
                    </Typography>
                    {Object.entries(patterns.pattern_stats)
                      .sort(([,a], [,b]) => b.avg_similarity_score - a.avg_similarity_score)
                      .slice(0, 3)
                      .map(([pattern, stats]) => (
                        <Box key={pattern} sx={{ mb: 1 }}>
                          <Stack direction="row" justifyContent="space-between" alignItems="center">
                            <Chip label={pattern} size="small" variant="outlined" />
                            <Typography variant="body2" color="info.main">
                              {formatScore(stats.avg_similarity_score)}
                            </Typography>
                          </Stack>
                        </Box>
                      ))
                    }
                  </Grid>
                </Grid>
              ) : (
                <Alert severity="info">
                  No query patterns available yet. Use the system to generate some queries first.
                </Alert>
              )}
            </Paper>
          </Box>
        )}

        {patterns && patterns.total_patterns === 0 && !loading && (
          <Alert severity="info">
            No query patterns found. Start using the RAG system to generate pattern data.
          </Alert>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={handleClose} startIcon={<CloseIcon />}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

QueryPatternsModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default QueryPatternsModal;