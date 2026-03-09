// frontend/src/components/modals/TestRetrievalModal.jsx
// Modal for testing RAG retrieval functionality

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
  Divider,
  Grid,
  LinearProgress,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Stack,
} from "@mui/material";

// Icons
import CloseIcon from "@mui/icons-material/Close";
import SearchIcon from "@mui/icons-material/Search";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ScoreIcon from "@mui/icons-material/Score";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import ArticleIcon from "@mui/icons-material/Article";

const TestRetrievalModal = ({ open, onClose }) => {
  const [query, setQuery] = useState("");
  const [projectId, setProjectId] = useState("");
  const [topK, setTopK] = useState(5);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);

  const handleTest = useCallback(async () => {
    if (!query.trim()) {
      setError("Please enter a query to test");
      return;
    }

    setLoading(true);
    setError(null);
    setResults(null);

    try {
      const response = await fetch(`${BASE_URL}/rag-debug/retrieve`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          query: query.trim(),
          project_id: projectId.trim() || null,
          top_k: topK,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      
      if (!data.success) {
        throw new Error(data.error || "Retrieval test failed");
      }

      setResults(data.data);
    } catch (err) {
      console.error("Retrieval test error:", err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [query, projectId, topK]);

  const handleClose = () => {
    setQuery("");
    setProjectId("");
    setTopK(5);
    setResults(null);
    setError(null);
    onClose();
  };

  const formatScore = (score) => {
    if (typeof score === 'number') {
      return score.toFixed(3);
    }
    return score || 'N/A';
  };

  const formatTime = (time) => {
    if (typeof time === 'number') {
      return `${(time * 1000).toFixed(1)}ms`;
    }
    return time || 'N/A';
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="lg" fullWidth>
      <DialogTitle>
        <Stack direction="row" alignItems="center" spacing={1}>
          <SearchIcon color="primary" />
          <Typography variant="h6">Test RAG Retrieval</Typography>
        </Stack>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ mb: 3 }}>
          <TextField
            label="Query"
            placeholder="Enter your test query..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            fullWidth
            multiline
            rows={2}
            sx={{ mb: 2 }}
          />

          <Grid container spacing={2} sx={{ mb: 2 }}>
            <Grid item xs={6}>
              <TextField
                label="Project ID (optional)"
                placeholder="Leave blank for global search"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                fullWidth
              />
            </Grid>
            <Grid item xs={6}>
              <TextField
                label="Top K Results"
                type="number"
                value={topK}
                onChange={(e) => setTopK(Math.max(1, Math.min(20, parseInt(e.target.value) || 5)))}
                inputProps={{ min: 1, max: 20 }}
                fullWidth
              />
            </Grid>
          </Grid>

          <Button
            variant="contained"
            onClick={handleTest}
            disabled={loading || !query.trim()}
            startIcon={loading ? <CircularProgress size={20} /> : <SearchIcon />}
            sx={{ mb: 2 }}
          >
            {loading ? "Testing..." : "Test Retrieval"}
          </Button>
        </Box>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {results && (
          <Box>
            {/* Query Analysis */}
            <Paper elevation={1} sx={{ p: 2, mb: 2 }}>
              <Typography variant="h6" gutterBottom>
                Query Analysis
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={4}>
                  <Chip
                    icon={<AccessTimeIcon />}
                    label={`${formatTime(results.metrics?.retrieval_time)}`}
                    variant="outlined"
                    size="small"
                  />
                </Grid>
                <Grid item xs={4}>
                  <Chip
                    icon={<ArticleIcon />}
                    label={`${results.metrics?.retrieved_nodes || 0} nodes`}
                    variant="outlined"
                    size="small"
                  />
                </Grid>
                <Grid item xs={4}>
                  <Chip
                    label={`${results.query_analysis?.query_complexity || 'Unknown'} complexity`}
                    variant="outlined"
                    size="small"
                  />
                </Grid>
              </Grid>

              <Box sx={{ mt: 2 }}>
                <Typography variant="body2" color="text.secondary">
                  <strong>Query:</strong> {results.metrics?.query}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  <strong>Avg Similarity:</strong> {formatScore(results.metrics?.avg_similarity_score)}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  <strong>Top Score:</strong> {formatScore(results.metrics?.top_score)}
                </Typography>
              </Box>
            </Paper>

            {/* Results */}
            {results.detailed_nodes && results.detailed_nodes.length > 0 ? (
              <Box>
                <Typography variant="h6" gutterBottom>
                  Retrieval Results ({results.detailed_nodes.length})
                </Typography>
                
                {results.detailed_nodes.map((node, index) => (
                  <Accordion key={index} sx={{ mb: 1 }}>
                    <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                      <Stack direction="row" alignItems="center" spacing={2} sx={{ width: '100%' }}>
                        <Chip
                          label={`Rank ${node.rank}`}
                          size="small"
                          variant="outlined"
                        />
                        <Chip
                          icon={<ScoreIcon />}
                          label={formatScore(node.score)}
                          size="small"
                          color={node.score > 0.7 ? "success" : node.score > 0.5 ? "warning" : "default"}
                        />
                        <Typography variant="body2" noWrap sx={{ flex: 1 }}>
                          {node.content_preview}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {node.content_length} chars
                        </Typography>
                      </Stack>
                    </AccordionSummary>
                    <AccordionDetails>
                      <Divider sx={{ mb: 2 }} />
                      <Typography variant="body2" sx={{ mb: 2, fontFamily: 'monospace' }}>
                        {node.content_preview}
                      </Typography>
                      
                      {node.metadata && Object.keys(node.metadata).length > 0 && (
                        <Box>
                          <Typography variant="subtitle2" gutterBottom>
                            Metadata
                          </Typography>
                          <Paper variant="outlined" sx={{ p: 1, bgcolor: 'grey.50' }}>
                            <pre style={{ fontSize: '12px', margin: 0, whiteSpace: 'pre-wrap' }}>
                              {JSON.stringify(node.metadata, null, 2)}
                            </pre>
                          </Paper>
                        </Box>
                      )}
                    </AccordionDetails>
                  </Accordion>
                ))}
              </Box>
            ) : (
              <Alert severity="info">
                No results found for this query. Try a different search term or check if documents are properly indexed.
              </Alert>
            )}

            {/* Error Handling */}
            {results.error && (
              <Alert severity="error" sx={{ mt: 2 }}>
                <Typography variant="subtitle2">Retrieval Error</Typography>
                {results.error}
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

TestRetrievalModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default TestRetrievalModal;