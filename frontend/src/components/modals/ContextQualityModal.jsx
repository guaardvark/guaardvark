// frontend/src/components/modals/ContextQualityModal.jsx
// Modal for analyzing context quality and providing improvement recommendations

import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { BASE_URL } from "../../api/apiClient";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Button,
  Typography,
  Box,
  Paper,
  Chip,
  CircularProgress,
  Alert,
  Grid,
  Card,
  CardContent,
  LinearProgress,
  Stack,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Divider,
} from "@mui/material";

// Icons
import CloseIcon from "@mui/icons-material/Close";
import AssessmentIcon from "@mui/icons-material/Assessment";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import WarningIcon from "@mui/icons-material/Warning";
import TipsAndUpdatesIcon from "@mui/icons-material/TipsAndUpdates";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import SourceIcon from "@mui/icons-material/Source";
import CompressIcon from "@mui/icons-material/Compress";
import TrendingUpIcon from "@mui/icons-material/TrendingUp";
import AccessTimeIcon from "@mui/icons-material/AccessTime";

const ContextQualityModal = ({ open, onClose }) => {
  const [sessionId, setSessionId] = useState("");
  const [query, setQuery] = useState("");
  const [contextChunks, setContextChunks] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);

  const handleAnalyze = useCallback(async () => {
    if (!sessionId.trim()) {
      setError("Please enter a session ID");
      return;
    }

    if (!contextChunks.trim()) {
      setError("Please enter context chunks to analyze (one per line)");
      return;
    }

    setLoading(true);
    setError(null);
    setResults(null);

    try {
      // Parse context chunks from textarea (one per line)
      const chunks = contextChunks
        .split('\n')
        .map(chunk => chunk.trim())
        .filter(chunk => chunk.length > 0);

      if (chunks.length === 0) {
        throw new Error("No valid context chunks found");
      }

      const response = await fetch(`${BASE_URL}/rag-debug/context-quality`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          session_id: sessionId.trim(),
          context_chunks: chunks,
          query: query.trim() || "",
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      
      if (!data.success) {
        throw new Error(data.error || "Context quality analysis failed");
      }

      setResults(data.data);
    } catch (err) {
      console.error("Context quality analysis error:", err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [sessionId, query, contextChunks]);

  const handleClose = () => {
    setSessionId("");
    setQuery("");
    setContextChunks("");
    setResults(null);
    setError(null);
    onClose();
  };

  const getQualityColor = (score) => {
    if (score >= 0.8) return "success";
    if (score >= 0.6) return "warning";
    return "error";
  };

  const getQualityLabel = (assessment) => {
    switch (assessment) {
      case 'excellent': return { label: 'Excellent', color: 'success' };
      case 'good': return { label: 'Good', color: 'info' };
      case 'fair': return { label: 'Fair', color: 'warning' };
      case 'poor': return { label: 'Poor', color: 'error' };
      default: return { label: 'Unknown', color: 'default' };
    }
  };

  const formatScore = (score) => {
    if (typeof score === 'number') {
      return (score * 100).toFixed(1) + '%';
    }
    return 'N/A';
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="lg" fullWidth>
      <DialogTitle>
        <Stack direction="row" alignItems="center" spacing={1}>
          <AssessmentIcon color="primary" />
          <Typography variant="h6">Context Quality Analysis</Typography>
        </Stack>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ mb: 3 }}>
          <TextField
            label="Session ID"
            placeholder="e.g., session_1234567890"
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
            fullWidth
            sx={{ mb: 2 }}
          />

          <TextField
            label="Query (optional)"
            placeholder="Enter the query that was used with this context..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            fullWidth
            sx={{ mb: 2 }}
          />

          <TextField
            label="Context Chunks"
            placeholder="Enter context chunks, one per line..."
            value={contextChunks}
            onChange={(e) => setContextChunks(e.target.value)}
            fullWidth
            multiline
            rows={6}
            sx={{ mb: 2 }}
            helperText="Enter each context chunk on a separate line"
          />

          <Button
            variant="contained"
            onClick={handleAnalyze}
            disabled={loading || !sessionId.trim() || !contextChunks.trim()}
            startIcon={loading ? <CircularProgress size={20} /> : <AssessmentIcon />}
            sx={{ mb: 2 }}
          >
            {loading ? "Analyzing..." : "Analyze Quality"}
          </Button>
        </Box>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {results && (
          <Box>
            {/* Overall Quality Score */}
            <Paper elevation={1} sx={{ p: 2, mb: 3 }}>
              <Typography variant="h6" gutterBottom>
                Overall Quality Assessment
              </Typography>
              
              <Grid container spacing={3} alignItems="center">
                <Grid item xs={12} sm={6}>
                  <Box sx={{ mb: 2 }}>
                    <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
                      <Typography variant="body2">Quality Score</Typography>
                      <Typography variant="h6" color={getQualityColor(results.overall_quality_score)}>
                        {formatScore(results.overall_quality_score)}
                      </Typography>
                    </Stack>
                    <LinearProgress
                      variant="determinate"
                      value={(results.overall_quality_score || 0) * 100}
                      color={getQualityColor(results.overall_quality_score)}
                      sx={{ height: 8, borderRadius: 4 }}
                    />
                  </Box>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Chip
                    label={getQualityLabel(results.quality_assessment).label}
                    color={getQualityLabel(results.quality_assessment).color}
                    size="large"
                    sx={{ fontWeight: 'bold' }}
                  />
                </Grid>
              </Grid>
            </Paper>

            {/* Detailed Metrics */}
            <Grid container spacing={2} sx={{ mb: 3 }}>
              <Grid item xs={12} sm={6} md={3}>
                <Card>
                  <CardContent>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <ContentCopyIcon color="primary" />
                      <Typography variant="h6">
                        {results.metrics?.total_chunks || 0}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      Total Chunks
                    </Typography>
                  </CardContent>
                </Card>
              </Grid>

              <Grid item xs={12} sm={6} md={3}>
                <Card>
                  <CardContent>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <SourceIcon color="info" />
                      <Typography variant="h6">
                        {results.metrics?.unique_sources || 0}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      Unique Sources
                    </Typography>
                  </CardContent>
                </Card>
              </Grid>

              <Grid item xs={12} sm={6} md={3}>
                <Card>
                  <CardContent>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <CompressIcon color="warning" />
                      <Typography variant="h6">
                        {formatScore(results.metrics?.compression_ratio)}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      Compression Ratio
                    </Typography>
                  </CardContent>
                </Card>
              </Grid>

              <Grid item xs={12} sm={6} md={3}>
                <Card>
                  <CardContent>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <AccessTimeIcon color="success" />
                      <Typography variant="h6">
                        {results.metrics?.avg_chunk_length || 0}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      Avg Chunk Length
                    </Typography>
                  </CardContent>
                </Card>
              </Grid>
            </Grid>

            {/* Quality Metrics Detail */}
            <Paper sx={{ mb: 3 }}>
              <Box sx={{ p: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
                <Typography variant="h6">Quality Metrics</Typography>
              </Box>
              <Grid container sx={{ p: 2 }}>
                <Grid item xs={12} sm={6} md={3}>
                  <Box sx={{ p: 1 }}>
                    <Typography variant="body2" color="text.secondary">
                      Redundancy Score
                    </Typography>
                    <Typography variant="h6" color={results.metrics?.redundancy_score > 0.7 ? "error" : "success"}>
                      {formatScore(results.metrics?.redundancy_score)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Lower is better
                    </Typography>
                  </Box>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Box sx={{ p: 1 }}>
                    <Typography variant="body2" color="text.secondary">
                      Relevance Score
                    </Typography>
                    <Typography variant="h6" color={getQualityColor(results.metrics?.relevance_score)}>
                      {formatScore(results.metrics?.relevance_score)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Higher is better
                    </Typography>
                  </Box>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Box sx={{ p: 1 }}>
                    <Typography variant="body2" color="text.secondary">
                      Freshness Score
                    </Typography>
                    <Typography variant="h6" color={getQualityColor(results.metrics?.freshness_score)}>
                      {formatScore(results.metrics?.freshness_score)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Content recency
                    </Typography>
                  </Box>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Box sx={{ p: 1 }}>
                    <Typography variant="body2" color="text.secondary">
                      Session ID
                    </Typography>
                    <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>
                      {results.metrics?.session_id}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {new Date(results.metrics?.timestamp).toLocaleString()}
                    </Typography>
                  </Box>
                </Grid>
              </Grid>
            </Paper>

            {/* Recommendations */}
            {results.recommendations && results.recommendations.length > 0 && (
              <Paper sx={{ mb: 2 }}>
                <Box sx={{ p: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <TipsAndUpdatesIcon color="warning" />
                    <Typography variant="h6">Improvement Recommendations</Typography>
                  </Stack>
                </Box>
                <List>
                  {results.recommendations.map((recommendation, index) => (
                    <React.Fragment key={index}>
                      <ListItem>
                        <ListItemIcon>
                          <WarningIcon color="warning" />
                        </ListItemIcon>
                        <ListItemText
                          primary={recommendation}
                          primaryTypographyProps={{ variant: 'body2' }}
                        />
                      </ListItem>
                      {index < results.recommendations.length - 1 && <Divider />}
                    </React.Fragment>
                  ))}
                </List>
              </Paper>
            )}

            {results.recommendations && results.recommendations.length === 0 && (
              <Alert severity="success" icon={<CheckCircleIcon />}>
                Great! No issues found with this context. The quality is good across all metrics.
              </Alert>
            )}
          </Box>
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

ContextQualityModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default ContextQualityModal;