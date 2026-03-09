// frontend/src/pages/WordPressPagesPage.jsx
// WordPress Pages Management Page
// View pulled pages, process them, and review improvements

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Box,
  Typography,
  Button,
  CircularProgress,
  Alert as MuiAlert,
  Snackbar,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  Stack,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Tabs,
  Tab,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Tooltip,
  LinearProgress,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import RefreshIcon from "@mui/icons-material/Refresh";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import VisibilityIcon from "@mui/icons-material/Visibility";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import PendingIcon from "@mui/icons-material/Pending";
import ErrorIcon from "@mui/icons-material/Error";

import * as wordpressService from "../api/wordpressService";
import { useSearchParams } from "react-router-dom";
import PageLayout from "../components/layout/PageLayout";
import DOMPurify from "dompurify";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

const ProcessStatusChip = ({ status }) => {
  if (!status) return <Chip label="Pending" size="small" color="default" />;
  const normalized = status.toLowerCase();
  let color = "default";
  let icon = null;
  switch (normalized) {
    case "completed":
      color = "success";
      icon = <CheckCircleIcon fontSize="small" />;
      break;
    case "processing":
      color = "warning";
      icon = <PendingIcon fontSize="small" />;
      break;
    case "pending":
      color = "default";
      icon = <PendingIcon fontSize="small" />;
      break;
    case "approved":
      color = "success";
      icon = <CheckCircleIcon fontSize="small" />;
      break;
    case "rejected":
      color = "error";
      icon = <ErrorIcon fontSize="small" />;
      break;
    default:
      color = "default";
  }
  return (
    <Chip
      label={status}
      color={color}
      size="small"
      icon={icon}
      sx={{ textTransform: "capitalize" }}
    />
  );
};

const PullStatusChip = ({ status }) => {
  if (!status) return null;
  const normalized = status.toLowerCase();
  let color = "default";
  switch (normalized) {
    case "pulled":
      color = "success";
      break;
    case "error":
      color = "error";
      break;
    case "pending":
      color = "warning";
      break;
    default:
      color = "default";
  }
  return (
    <Chip label={status} color={color} size="small" sx={{ textTransform: "capitalize" }} />
  );
};

function WordPressPagesPage() {
  const [pages, setPages] = useState([]);
  const [sites, setSites] = useState([]);
  const [searchParams] = useSearchParams();
  const siteIdFromQuery = searchParams.get("site_id");
  const [selectedSite, setSelectedSite] = useState(
    siteIdFromQuery ? parseInt(siteIdFromQuery, 10) : null
  );
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isPulling, setIsPulling] = useState(false);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState({
    open: false,
    message: "",
    severity: "info",
  });

  const [selectedPage, setSelectedPage] = useState(null);
  const [diffDialogOpen, setDiffDialogOpen] = useState(false);
  const [diffTab, setDiffTab] = useState(0); // Add state for diff dialog tabs
  const [pullDialogOpen, setPullDialogOpen] = useState(false);
  const [processType, setProcessType] = useState("full");
  const [pullOptions, setPullOptions] = useState({
    post_type: "post",
    max_pages: null,
  });

  const [filterStatus, setFilterStatus] = useState("all");
  const [filterProcessStatus, setFilterProcessStatus] = useState("all");
  
  // Ref to track timeouts for cleanup
  const timeoutRefs = useRef([]);

  const fetchSites = useCallback(async () => {
    try {
      const response = await wordpressService.getWordPressSites();
      if (response.success && response.data && Array.isArray(response.data)) {
        setSites(response.data);
        // Only set default if no site selected
        setSelectedSite((prevSelected) => {
          if (prevSelected && response.data.find(s => s.id === prevSelected)) {
            return prevSelected; // Keep current selection if still valid
          }
          const querySiteId = searchParams.get("site_id");
          if (querySiteId) {
            const siteId = parseInt(querySiteId, 10);
            if (!isNaN(siteId) && response.data.find(s => s.id === siteId)) {
              return siteId; // Use query param site if valid
            }
          }
          // Default to first site if available
          return response.data.length > 0 ? response.data[0].id : null;
        });
      } else {
        setSites([]);
        setSelectedSite(null);
      }
    } catch (err) {
      console.error("Error fetching WordPress sites:", err);
      setSites([]);
      setSelectedSite(null);
    }
  }, [searchParams]); // Only depend on searchParams for query param access
  
  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      timeoutRefs.current.forEach(timeout => clearTimeout(timeout));
      timeoutRefs.current = [];
    };
  }, []);

  const fetchPages = useCallback(async () => {
    if (!selectedSite) {
      setIsLoading(false);
      setPages([]);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const response = await wordpressService.getWordPressPages({
        site_id: selectedSite,
        process_status: filterProcessStatus !== "all" ? filterProcessStatus : undefined,
        pull_status: filterStatus !== "all" ? filterStatus : undefined,
        limit: 100,
      });
      if (response.success && response.data) {
        setPages(response.data.pages || []);
      } else {
        throw new Error(response.error || "Failed to fetch pages");
      }
    } catch (err) {
      console.error("Error fetching WordPress pages:", err);
      setError(err.message || "Failed to load pages");
      setFeedback({
        open: true,
        message: err.message || "Failed to load pages",
        severity: "error",
      });
    } finally {
      setIsLoading(false);
    }
  }, [selectedSite, filterStatus, filterProcessStatus]);

  useEffect(() => {
    fetchSites();
  }, [fetchSites]);

  // Update selectedSite when query param changes
  useEffect(() => {
    const siteIdFromQuery = searchParams.get("site_id");
    if (siteIdFromQuery) {
      const siteId = parseInt(siteIdFromQuery, 10);
      // Validate siteId is valid and exists in sites array
      if (!isNaN(siteId) && siteId !== selectedSite) {
        // Only update if site exists in loaded sites
        if (sites.length === 0 || sites.find(s => s.id === siteId)) {
          setSelectedSite(siteId);
        }
      }
    } else if (siteIdFromQuery === null && selectedSite) {
      // Query param removed, but keep selection if valid
      if (sites.length > 0 && !sites.find(s => s.id === selectedSite)) {
        // Current selection is invalid, reset to first site
        setSelectedSite(sites[0].id);
      }
    }
  }, [searchParams, selectedSite, sites]);

  useEffect(() => {
    fetchPages();
  }, [fetchPages]);

  const handlePull = async () => {
    if (!selectedSite) return;
    setIsPulling(true);
    try {
      const response = await wordpressService.pullPageList(selectedSite, pullOptions);
      if (response.success) {
        setFeedback({
          open: true,
          message: `Pulled ${response.data.total_pulled || 0} pages`,
          severity: "success",
        });
        // Refresh pages after successful pull
        const timeoutId = setTimeout(() => {
          fetchPages();
        }, 500);
        timeoutRefs.current.push(timeoutId);
      } else {
        throw new Error(response.error || "Failed to pull pages");
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || "Failed to pull pages",
        severity: "error",
      });
    } finally {
      setIsPulling(false);
      setPullDialogOpen(false);
    }
  };

  const handleProcessPage = async (pageId) => {
    setIsProcessing(true);
    try {
      const response = await wordpressService.processPage(pageId, processType);
      if (response.success) {
        setFeedback({
          open: true,
          message: `Page processing started. This may take a few moments. Refresh to see results.`,
          severity: "success",
        });
        // Refresh pages after a delay to show updated status
        const timeoutId = setTimeout(() => {
          fetchPages();
        }, 2000);
        timeoutRefs.current.push(timeoutId);
      } else {
        throw new Error(response.error || "Failed to process page");
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || "Failed to process page",
        severity: "error",
      });
    } finally {
      setIsProcessing(false);
    }
  };

  const handleQueueProcessing = async () => {
    if (!selectedSite) return;
    const pendingPages = pages
      .filter(
        (p) => p.pull_status === "pulled" && p.process_status === "pending"
      )
      .map((p) => p.id);

    if (pendingPages.length === 0) {
      setFeedback({
        open: true,
        message: "No pages pending processing",
        severity: "info",
      });
      return;
    }

    setIsProcessing(true);
    try {
      const response = await wordpressService.queuePagesForProcessing(pendingPages, {
        type: processType,
        site_id: selectedSite,
      });
      if (response.success) {
        setFeedback({
          open: true,
          message: `Queued ${response.data?.queued || pendingPages.length} pages for processing`,
          severity: "success",
        });
        // Refresh pages after queueing
        const timeoutId = setTimeout(() => {
          fetchPages();
        }, 500);
        timeoutRefs.current.push(timeoutId);
      } else {
        throw new Error(response.error || "Failed to queue pages");
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || "Failed to queue pages",
        severity: "error",
      });
    } finally {
      setIsProcessing(false);
    }
  };

  const handleExecuteQueue = async () => {
    if (!selectedSite) return;
    setIsProcessing(true);
    try {
      const response = await wordpressService.executeProcessingQueue({
        site_id: selectedSite,
        type: processType,
        max_pages: 10,
      });
      if (response.success) {
        const succeeded = response.data?.succeeded ?? 0;
        const total = response.data?.total ?? 0;
        setFeedback({
          open: true,
          message: `Processed ${succeeded}/${total} pages`,
          severity: "success",
        });
        const timeoutId = setTimeout(() => {
          fetchPages();
        }, 2000);
        timeoutRefs.current.push(timeoutId);
      } else {
        throw new Error(response.error || "Failed to execute queue");
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || "Failed to execute queue",
        severity: "error",
      });
    } finally {
      setIsProcessing(false);
    }
  };

  const handleViewDiff = (page) => {
    setSelectedPage(page);
    setDiffTab(0); // Reset to first tab
    setDiffDialogOpen(true);
  };

  // Auto-refresh dialog when processing status changes
  useEffect(() => {
    if (diffDialogOpen && selectedPage && selectedPage.process_status === "processing") {
      const intervalId = setInterval(() => {
        fetchPages();
      }, 3000); // Refresh every 3 seconds while processing
      
      return () => clearInterval(intervalId);
    }
  }, [diffDialogOpen, selectedPage, fetchPages]);
  
  // Update selected page when pages data changes
  useEffect(() => {
    if (diffDialogOpen && selectedPage && pages.length > 0) {
      const updatedPage = pages.find(p => p.id === selectedPage.id);
      if (updatedPage) {
        setSelectedPage(updatedPage);
      }
    }
  }, [pages, diffDialogOpen, selectedPage]);

  // Calculate pending and completed counts
  const pendingCount = useMemo(() => {
    return pages.filter(
      (p) => p.pull_status === "pulled" && p.process_status === "pending"
    ).length;
  }, [pages]);

  const completedCount = useMemo(() => {
    return pages.filter(
      (p) => p.process_status === "completed" || p.process_status === "approved"
    ).length;
  }, [pages]);

  return (
    <PageLayout
      title="WordPress Pages"
      variant="standard"
      actions={
        selectedSite && (
          <Stack direction="row" spacing={1}>
            <Button
              variant="outlined"
              startIcon={<DownloadIcon />}
              onClick={() => setPullDialogOpen(true)}
              disabled={isPulling}
              size="small"
            >
              Pull Pages
            </Button>
            <Button
              variant="outlined"
              startIcon={<AutoAwesomeIcon />}
              onClick={handleQueueProcessing}
              disabled={isProcessing || pendingCount === 0}
              size="small"
            >
              Queue Processing ({pendingCount})
            </Button>
            <Button
              variant="contained"
              startIcon={<PlayArrowIcon />}
              onClick={handleExecuteQueue}
              disabled={isProcessing || pendingCount === 0}
              size="small"
            >
              Process Queue
            </Button>
          </Stack>
        )
      }
    >
      {/* Site Selector */}
      <Box sx={{ mb: 3 }}>
        <FormControl fullWidth sx={{ maxWidth: 400 }}>
          <InputLabel>WordPress Site</InputLabel>
          <Select
            value={selectedSite || ""}
            label="WordPress Site"
            onChange={(e) => {
              const value = e.target.value;
              const siteId = value ? parseInt(value, 10) : null;
              if (!value || (!isNaN(siteId) && sites.find(s => s.id === siteId))) {
                setSelectedSite(siteId);
              }
            }}
          >
            {sites.map((site) => (
              <MenuItem key={site.id} value={site.id}>
                {site.site_name || site.url}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Box>

      {/* Filters */}
      <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
        <FormControl size="small" sx={{ minWidth: 150 }}>
          <InputLabel>Pull Status</InputLabel>
          <Select
            value={filterStatus}
            label="Pull Status"
            onChange={(e) => setFilterStatus(e.target.value)}
          >
            <MenuItem value="all">All</MenuItem>
            <MenuItem value="pulled">Pulled</MenuItem>
            <MenuItem value="pending">Pending</MenuItem>
            <MenuItem value="error">Error</MenuItem>
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 150 }}>
          <InputLabel>Process Status</InputLabel>
          <Select
            value={filterProcessStatus}
            label="Process Status"
            onChange={(e) => setFilterProcessStatus(e.target.value)}
          >
            <MenuItem value="all">All</MenuItem>
            <MenuItem value="pending">Pending</MenuItem>
            <MenuItem value="processing">Processing</MenuItem>
            <MenuItem value="completed">Completed</MenuItem>
            <MenuItem value="approved">Approved</MenuItem>
          </Select>
        </FormControl>
      </Stack>

      {/* Stats */}
      <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
        <Chip label={`Total: ${pages.length}`} variant="outlined" />
        <Chip label={`Pending: ${pendingCount}`} color="warning" variant="outlined" />
        <Chip label={`Completed: ${completedCount}`} color="success" variant="outlined" />
      </Stack>

      {isLoading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress />
        </Box>
      ) : (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Title</TableCell>
                <TableCell>Type</TableCell>
                <TableCell>Pull Status</TableCell>
                <TableCell>Process Status</TableCell>
                <TableCell>Has Improvements</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {pages.map((page) => (
                <TableRow 
                  key={page.id} 
                  hover 
                  sx={{ cursor: "pointer" }}
                  onClick={() => handleViewDiff(page)}
                >
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                      {page.title || "Untitled"}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {page.slug}
                    </Typography>
                  </TableCell>
                  <TableCell>{page.post_type}</TableCell>
                  <TableCell>
                    <PullStatusChip status={page.pull_status} />
                  </TableCell>
                  <TableCell>
                    <ProcessStatusChip status={page.process_status} />
                  </TableCell>
                  <TableCell>
                    {page.improved_title || page.improved_content ? (
                      <Chip label="Yes" color="success" size="small" />
                    ) : (
                      <Chip label="No" size="small" />
                    )}
                  </TableCell>
                  <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                    <Stack direction="row" spacing={1} justifyContent="flex-end">
                      <Tooltip title="View Details">
                        <IconButton size="small" onClick={() => handleViewDiff(page)}>
                          <VisibilityIcon />
                        </IconButton>
                      </Tooltip>
                      {page.pull_status === "pulled" && page.process_status === "pending" && (
                        <Tooltip title="Process Now">
                          <IconButton
                            size="small"
                            onClick={() => handleProcessPage(page.id)}
                            disabled={isProcessing}
                          >
                            <AutoAwesomeIcon />
                          </IconButton>
                        </Tooltip>
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {pages.length === 0 && !isLoading && (
        <Box sx={{ textAlign: "center", py: 8 }}>
          <Typography variant="h6" color="text.secondary" gutterBottom>
            No pages found
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {selectedSite
              ? "Pull pages from WordPress to get started"
              : "Select a WordPress site to view pages"}
          </Typography>
          {selectedSite && (
            <Button variant="contained" startIcon={<DownloadIcon />} onClick={() => setPullDialogOpen(true)}>
              Pull Pages
            </Button>
          )}
        </Box>
      )}

      {/* Pull Dialog */}
      <Dialog 
        open={pullDialogOpen} 
        onClose={() => {
          if (!isPulling) {
            setPullDialogOpen(false);
          }
        }} 
        maxWidth="sm" 
        fullWidth
      >
        <DialogTitle>Pull Pages from WordPress</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <FormControl fullWidth>
              <InputLabel>Post Type</InputLabel>
              <Select
                value={pullOptions.post_type}
                label="Post Type"
                onChange={(e) =>
                  setPullOptions({ ...pullOptions, post_type: e.target.value })
                }
              >
                <MenuItem value="post">Posts</MenuItem>
                <MenuItem value="page">Pages</MenuItem>
              </Select>
            </FormControl>
            <TextField
              fullWidth
              type="number"
              label="Max Pages (Optional)"
            value={pullOptions.max_pages || ""}
            onChange={(e) => {
              const value = e.target.value;
              setPullOptions({
                ...pullOptions,
                max_pages: value ? (parseInt(value, 10) || null) : null,
              });
            }}
            helperText="Leave empty to pull all pages"
            inputProps={{ min: 1 }}
            />
            {isPulling && <LinearProgress />}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPullDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handlePull} disabled={isPulling}>
            {isPulling ? "Pulling..." : "Pull Pages"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Diff View Dialog */}
      <Dialog open={diffDialogOpen} onClose={() => setDiffDialogOpen(false)} maxWidth="lg" fullWidth>
        <DialogTitle>
          <Typography variant="h6">Page Content Details</Typography>
          <Typography variant="caption" color="text.secondary">
            {selectedPage?.title}
          </Typography>
          {selectedPage && (
            <Box sx={{ mt: 1, display: "flex", gap: 1, flexWrap: "wrap" }}>
              <ProcessStatusChip status={selectedPage.process_status} />
              <PullStatusChip status={selectedPage.pull_status} />
              {selectedPage.process_status === "processing" && (
                <Chip 
                  label="Processing in progress..." 
                  color="warning" 
                  size="small" 
                  icon={<CircularProgress size={16} />}
                />
              )}
            </Box>
          )}
        </DialogTitle>
        <DialogContent>
          {selectedPage && (
            <Box sx={{ mt: 2 }}>
              <Tabs value={diffTab} onChange={(e, newValue) => setDiffTab(newValue)}>
                <Tab label="Title" />
                <Tab label="Content" />
                <Tab label="Excerpt" />
                <Tab label="SEO" />
                <Tab label="Metadata" />
              </Tabs>
              <Box sx={{ mt: 2, maxHeight: 500, overflow: "auto" }}>
                {diffTab === 0 && (
                  <>
                    <Typography variant="subtitle2" gutterBottom>
                      Original Title
                    </Typography>
                    <Paper sx={{ p: 2, mb: 2 }}>
                      {selectedPage.title || "N/A"}
                    </Paper>
                    {selectedPage.improved_title ? (
                      <>
                        <Typography variant="subtitle2" gutterBottom>
                          Improved Title
                        </Typography>
                        <Paper sx={{ p: 2, mb: 2 }}>
                          {selectedPage.improved_title}
                        </Paper>
                      </>
                    ) : (
                      selectedPage.process_status === "pending" && (
                        <Paper sx={{ p: 2, mb: 2 }}>
                          <Typography variant="body2" color="text.secondary">
                            Not processed yet. Click "Process Now" to generate improvements.
                          </Typography>
                        </Paper>
                      )
                    )}
                  </>
                )}
                {diffTab === 1 && (
                  <>
                    <Typography variant="subtitle2" gutterBottom>
                      Original Content
                    </Typography>
                    <Paper sx={{ p: 2, mb: 2, maxHeight: 300, overflow: "auto" }}>
                      {selectedPage.content ? (
                        <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(selectedPage.content) }} />
                      ) : (
                        <Typography variant="body2" color="text.secondary">N/A</Typography>
                      )}
                    </Paper>
                    {selectedPage.improved_content ? (
                      <>
                        <Typography variant="subtitle2" gutterBottom>
                          Improved Content
                        </Typography>
                        <Paper sx={{ p: 2, mb: 2, maxHeight: 300, overflow: "auto" }}>
                          <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(selectedPage.improved_content) }} />
                        </Paper>
                      </>
                    ) : (
                      selectedPage.process_status === "pending" && (
                        <Paper sx={{ p: 2, mb: 2 }}>
                          <Typography variant="body2" color="text.secondary">
                            Not processed yet. Click "Process Now" to generate improvements.
                          </Typography>
                        </Paper>
                      )
                    )}
                  </>
                )}
                {diffTab === 2 && (
                  <>
                    <Typography variant="subtitle2" gutterBottom>
                      Original Excerpt
                    </Typography>
                    <Paper sx={{ p: 2, mb: 2 }}>
                      {selectedPage.excerpt || "N/A"}
                    </Paper>
                    {selectedPage.improved_excerpt ? (
                      <>
                        <Typography variant="subtitle2" gutterBottom>
                          Improved Excerpt
                        </Typography>
                        <Paper sx={{ p: 2, mb: 2 }}>
                          {selectedPage.improved_excerpt}
                        </Paper>
                      </>
                    ) : (
                      selectedPage.process_status === "pending" && (
                        <Paper sx={{ p: 2, mb: 2 }}>
                          <Typography variant="body2" color="text.secondary">
                            Not processed yet. Click "Process Now" to generate improvements.
                          </Typography>
                        </Paper>
                      )
                    )}
                  </>
                )}
                {diffTab === 3 && (
                  <>
                    <Typography variant="subtitle2" gutterBottom>
                      SEO Metadata
                    </Typography>
                    <Paper sx={{ p: 2, mb: 2 }}>
                      <Typography variant="body2"><strong>Meta Title:</strong> {selectedPage.improved_meta_title || selectedPage.title || "N/A"}</Typography>
                      <Typography variant="body2" sx={{ mt: 1 }}><strong>Meta Description:</strong> {selectedPage.improved_meta_description || selectedPage.excerpt || "N/A"}</Typography>
                      {selectedPage.improved_schema && (
                        <>
                          <Typography variant="body2" sx={{ mt: 1 }}><strong>Schema Markup:</strong></Typography>
                          <pre style={{ fontSize: "0.75rem", overflow: "auto", maxHeight: 200 }}>
                            {(() => {
                              try {
                                const schema = typeof selectedPage.improved_schema === 'string' 
                                  ? JSON.parse(selectedPage.improved_schema) 
                                  : selectedPage.improved_schema;
                                return JSON.stringify(schema, null, 2);
                              } catch (e) {
                                return selectedPage.improved_schema;
                              }
                            })()}
                          </pre>
                        </>
                      )}
                    </Paper>
                  </>
                )}
                {diffTab === 4 && (
                  <>
                    <Typography variant="subtitle2" gutterBottom>
                      Page Metadata
                    </Typography>
                    <Paper sx={{ p: 2, mb: 2 }}>
                      <Typography variant="body2"><strong>Post ID:</strong> {selectedPage.wordpress_post_id}</Typography>
                      <Typography variant="body2" sx={{ mt: 1 }}><strong>Post Type:</strong> {selectedPage.post_type}</Typography>
                      <Typography variant="body2" sx={{ mt: 1 }}><strong>Slug:</strong> {selectedPage.slug || "N/A"}</Typography>
                      <Typography variant="body2" sx={{ mt: 1 }}><strong>Status:</strong> {selectedPage.status}</Typography>
                      <Typography variant="body2" sx={{ mt: 1 }}><strong>Author:</strong> {selectedPage.author_name || "N/A"}</Typography>
                      {selectedPage.date && (
                        <Typography variant="body2" sx={{ mt: 1 }}>
                          <strong>Published:</strong> {new Date(selectedPage.date).toLocaleString()}
                        </Typography>
                      )}
                      {selectedPage.modified && (
                        <Typography variant="body2" sx={{ mt: 1 }}>
                          <strong>Modified:</strong> {new Date(selectedPage.modified).toLocaleString()}
                        </Typography>
                      )}
                      {selectedPage.pulled_at && (
                        <Typography variant="body2" sx={{ mt: 1 }}>
                          <strong>Pulled:</strong> {new Date(selectedPage.pulled_at).toLocaleString()}
                        </Typography>
                      )}
                      {selectedPage.processed_at && (
                        <Typography variant="body2" sx={{ mt: 1 }}>
                          <strong>Processed:</strong> {new Date(selectedPage.processed_at).toLocaleString()}
                        </Typography>
                      )}
                    </Paper>
                    {selectedPage.improvement_summary && (
                      <>
                        <Typography variant="subtitle2" gutterBottom sx={{ mt: 2 }}>
                          Improvement Summary
                        </Typography>
                        <Paper sx={{ p: 2 }}>
                          {selectedPage.improvement_summary}
                        </Paper>
                      </>
                    )}
                  </>
                )}
              </Box>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDiffDialogOpen(false)}>Close</Button>
          <Button 
            startIcon={<RefreshIcon />}
            onClick={() => {
              fetchPages();
              // Refresh the selected page data
              if (selectedPage) {
                const updatedPage = pages.find(p => p.id === selectedPage.id);
                if (updatedPage) {
                  setSelectedPage(updatedPage);
                }
              }
            }}
          >
            Refresh
          </Button>
          {selectedPage && selectedPage.pull_status === "pulled" && selectedPage.process_status === "pending" && (
            <Button 
              variant="contained" 
              startIcon={<AutoAwesomeIcon />}
              onClick={() => {
                handleProcessPage(selectedPage.id);
                setDiffDialogOpen(false);
              }}
              disabled={isProcessing}
            >
              Process Now
            </Button>
          )}
        </DialogActions>
      </Dialog>

      {/* Feedback Snackbar */}
      <Snackbar
        open={feedback.open}
        autoHideDuration={6000}
        onClose={() => setFeedback({ ...feedback, open: false })}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <AlertSnackbar severity={feedback.severity}>{feedback.message}</AlertSnackbar>
      </Snackbar>
    </PageLayout>
  );
}

export default WordPressPagesPage;

