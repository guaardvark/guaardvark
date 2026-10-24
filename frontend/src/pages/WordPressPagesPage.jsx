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
import { ContextualLoader } from "../components/common/LoadingStates";
import EntityContextMenu from "../components/common/EntityContextMenu";
import useContextMenu from "../hooks/useContextMenu";
import { translate, uiLocale, useUiTranslation, Trans } from "../i18n";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

const processStatusLabels = () => ({
  completed: translate("wordpress.pages.processStatus.completed"),
  processing: translate("wordpress.pages.processStatus.processing"),
  pending: translate("wordpress.pages.processStatus.pending"),
  approved: translate("wordpress.pages.processStatus.approved"),
  rejected: translate("wordpress.pages.processStatus.rejected"),
});

const pullStatusLabels = () => ({
  pulled: translate("wordpress.pages.pullStatus.pulled"),
  error: translate("wordpress.pages.pullStatus.error"),
  pending: translate("wordpress.pages.pullStatus.pending"),
});

const ProcessStatusChip = ({ status }) => {
  const { t } = useUiTranslation();
  if (!status) return <Chip label={t("wordpress.pages.processStatus.none")} size="small" color="default" />;
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
      label={processStatusLabels()[status] || status}
      color={color}
      size="small"
      icon={icon}
      sx={{ textTransform: "capitalize" }}
    />
  );
};

const PullStatusChip = ({ status }) => {
  useUiTranslation();
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
    <Chip
      label={pullStatusLabels()[status] || status}
      color={color}
      size="small"
      sx={{ textTransform: "capitalize" }}
    />
  );
};

function WordPressPagesPage() {
  const { t } = useUiTranslation();
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
  const [_error, setError] = useState(null);
  const [feedback, setFeedback] = useState({
    open: false,
    message: "",
    severity: "info",
  });

  const [selectedPage, setSelectedPage] = useState(null);
  const [diffDialogOpen, setDiffDialogOpen] = useState(false);
  const [diffTab, setDiffTab] = useState(0); // Add state for diff dialog tabs
  const [pullDialogOpen, setPullDialogOpen] = useState(false);
  const [processType, _setProcessType] = useState("full");
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
        throw new Error(response.error || t("wordpress.pages.fetchFailed"));
      }
    } catch (err) {
      console.error("Error fetching WordPress pages:", err);
      setError(err.message || t("wordpress.pages.loadFailed"));
      setFeedback({
        open: true,
        message: err.message || t("wordpress.pages.loadFailed"),
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
          message: t("wordpress.pages.pulled", { count: response.data.total_pulled || 0 }),
          severity: "success",
        });
        // Refresh pages after successful pull
        const timeoutId = setTimeout(() => {
          fetchPages();
        }, 500);
        timeoutRefs.current.push(timeoutId);
      } else {
        throw new Error(response.error || t("wordpress.pages.pullFailed"));
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || t("wordpress.pages.pullFailed"),
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
          message: t("wordpress.pages.processStarted"),
          severity: "success",
        });
        // Refresh pages after a delay to show updated status
        const timeoutId = setTimeout(() => {
          fetchPages();
        }, 2000);
        timeoutRefs.current.push(timeoutId);
      } else {
        throw new Error(response.error || t("wordpress.pages.processFailed"));
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || t("wordpress.pages.processFailed"),
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
        message: t("wordpress.pages.nonePending"),
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
          message: t("wordpress.pages.queued", { count: response.data?.queued || pendingPages.length }),
          severity: "success",
        });
        // Refresh pages after queueing
        const timeoutId = setTimeout(() => {
          fetchPages();
        }, 500);
        timeoutRefs.current.push(timeoutId);
      } else {
        throw new Error(response.error || t("wordpress.pages.queueFailed"));
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || t("wordpress.pages.queueFailed"),
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
          message: t("wordpress.pages.processed", { succeeded, total }),
          severity: "success",
        });
        const timeoutId = setTimeout(() => {
          fetchPages();
        }, 2000);
        timeoutRefs.current.push(timeoutId);
      } else {
        throw new Error(response.error || t("wordpress.pages.executeFailed"));
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || t("wordpress.pages.executeFailed"),
        severity: "error",
      });
    } finally {
      setIsProcessing(false);
    }
  };

  const pageMenu = useContextMenu();

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
      title={t("wordpress.pages.title")}
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
              {t("wordpress.pages.pullPages")}
            </Button>
            <Button
              variant="outlined"
              startIcon={<AutoAwesomeIcon />}
              onClick={handleQueueProcessing}
              disabled={isProcessing || pendingCount === 0}
              size="small"
            >
              {t("wordpress.pages.queueProcessing", { count: pendingCount })}
            </Button>
            <Button
              variant="contained"
              startIcon={<PlayArrowIcon />}
              onClick={handleExecuteQueue}
              disabled={isProcessing || pendingCount === 0}
              size="small"
            >
              {t("wordpress.pages.processQueue")}
            </Button>
          </Stack>
        )
      }
    >
      {/* Site Selector */}
      <Box sx={{ mb: 3 }}>
        <FormControl fullWidth sx={{ maxWidth: 400 }}>
          <InputLabel>{t("wordpress.pages.site")}</InputLabel>
          <Select
            value={selectedSite || ""}
            label={t("wordpress.pages.site")}
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
          <InputLabel>{t("wordpress.pages.pullStatusLabel")}</InputLabel>
          <Select
            value={filterStatus}
            label={t("wordpress.pages.pullStatusLabel")}
            onChange={(e) => setFilterStatus(e.target.value)}
          >
            <MenuItem value="all">{t("wordpress.pages.filters.all")}</MenuItem>
            <MenuItem value="pulled">{t("wordpress.pages.filters.pulled")}</MenuItem>
            <MenuItem value="pending">{t("wordpress.pages.filters.pending")}</MenuItem>
            <MenuItem value="error">{t("wordpress.pages.filters.error")}</MenuItem>
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 150 }}>
          <InputLabel>{t("wordpress.pages.processStatusLabel")}</InputLabel>
          <Select
            value={filterProcessStatus}
            label={t("wordpress.pages.processStatusLabel")}
            onChange={(e) => setFilterProcessStatus(e.target.value)}
          >
            <MenuItem value="all">{t("wordpress.pages.filters.all")}</MenuItem>
            <MenuItem value="pending">{t("wordpress.pages.filters.pending")}</MenuItem>
            <MenuItem value="processing">{t("wordpress.pages.filters.processing")}</MenuItem>
            <MenuItem value="completed">{t("wordpress.pages.filters.completed")}</MenuItem>
            <MenuItem value="approved">{t("wordpress.pages.filters.approved")}</MenuItem>
          </Select>
        </FormControl>
      </Stack>

      {/* Stats */}
      <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
        <Chip label={t("wordpress.pages.statTotal", { count: pages.length })} variant="outlined" />
        <Chip
          label={t("wordpress.pages.statPending", { count: pendingCount })}
          color="warning"
          variant="outlined"
        />
        <Chip
          label={t("wordpress.pages.statCompleted", { count: completedCount })}
          color="success"
          variant="outlined"
        />
      </Stack>

      {isLoading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <ContextualLoader loading message={t("wordpress.pages.loading")} showProgress={false} inline />
        </Box>
      ) : (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>{t("wordpress.pages.columns.title")}</TableCell>
                <TableCell>{t("wordpress.pages.columns.type")}</TableCell>
                <TableCell>{t("wordpress.pages.columns.pullStatus")}</TableCell>
                <TableCell>{t("wordpress.pages.columns.processStatus")}</TableCell>
                <TableCell>{t("wordpress.pages.columns.hasImprovements")}</TableCell>
                <TableCell align="right">{t("wordpress.pages.columns.actions")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {pages.map((page) => (
                <TableRow 
                  key={page.id} 
                  hover 
                  sx={{ cursor: "pointer" }}
                  onClick={() => handleViewDiff(page)}
                  onContextMenu={(e) => pageMenu.open(e, page)}
                >
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                      {page.title || t("wordpress.pages.untitled")}
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
                      <Chip label={t("wordpress.pages.yes")} color="success" size="small" />
                    ) : (
                      <Chip label={t("wordpress.pages.no")} size="small" />
                    )}
                  </TableCell>
                  <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                    <Stack direction="row" spacing={1} justifyContent="flex-end">
                      <Tooltip title={t("wordpress.pages.viewDetails")}>
                        <IconButton size="small" onClick={() => handleViewDiff(page)}>
                          <VisibilityIcon />
                        </IconButton>
                      </Tooltip>
                      {page.pull_status === "pulled" && page.process_status === "pending" && (
                        <Tooltip title={t("wordpress.pages.processNow")}>
                          <span>
                            <IconButton
                              size="small"
                              onClick={() => handleProcessPage(page.id)}
                              disabled={isProcessing}
                            >
                              <AutoAwesomeIcon />
                            </IconButton>
                          </span>
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

      <EntityContextMenu
        anchorPosition={pageMenu.anchorPosition}
        onClose={pageMenu.close}
        actions={
          pageMenu.payload
            ? [
                { label: t("wordpress.pages.menu.viewDetails"), onClick: () => handleViewDiff(pageMenu.payload) },
                pageMenu.payload.pull_status === "pulled" &&
                  pageMenu.payload.process_status === "pending" && {
                    label: t("wordpress.pages.menu.processNow"),
                    onClick: () => handleProcessPage(pageMenu.payload.id),
                    disabled: isProcessing,
                  },
                { label: t("wordpress.pages.menu.refresh"), onClick: fetchPages, dividerBefore: true },
                {
                  label: t("wordpress.pages.menu.pullPages"),
                  onClick: () => setPullDialogOpen(true),
                  disabled: isPulling,
                },
              ]
            : []
        }
      />

      {pages.length === 0 && !isLoading && (
        <Box sx={{ textAlign: "center", py: 8 }}>
          <Typography variant="h6" color="text.secondary" gutterBottom>
            {t("wordpress.pages.emptyTitle")}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {selectedSite
              ? t("wordpress.pages.emptyPull")
              : t("wordpress.pages.emptySelect")}
          </Typography>
          {selectedSite && (
            <Button variant="contained" startIcon={<DownloadIcon />} onClick={() => setPullDialogOpen(true)}>
              {t("wordpress.pages.pullPages")}
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
        <DialogTitle>{t("wordpress.pages.pullTitle")}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <FormControl fullWidth>
              <InputLabel>{t("wordpress.pages.postType")}</InputLabel>
              <Select
                value={pullOptions.post_type}
                label={t("wordpress.pages.postType")}
                onChange={(e) =>
                  setPullOptions({ ...pullOptions, post_type: e.target.value })
                }
              >
                <MenuItem value="post">{t("wordpress.pages.postTypePosts")}</MenuItem>
                <MenuItem value="page">{t("wordpress.pages.postTypePages")}</MenuItem>
              </Select>
            </FormControl>
            <TextField
              fullWidth
              type="number"
              label={t("wordpress.pages.maxPages")}
            value={pullOptions.max_pages || ""}
            onChange={(e) => {
              const value = e.target.value;
              setPullOptions({
                ...pullOptions,
                max_pages: value ? (parseInt(value, 10) || null) : null,
              });
            }}
            helperText={t("wordpress.pages.maxPagesHelp")}
            inputProps={{ min: 1 }}
            />
            {isPulling && <LinearProgress />}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPullDialogOpen(false)}>{t("wordpress.pages.cancel")}</Button>
          <Button variant="contained" onClick={handlePull} disabled={isPulling}>
            {isPulling ? t("wordpress.pages.pulling") : t("wordpress.pages.pullPages")}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Diff View Dialog */}
      <Dialog open={diffDialogOpen} onClose={() => setDiffDialogOpen(false)} maxWidth="lg" fullWidth>
        <DialogTitle>
          <Typography variant="h6">{t("wordpress.pages.detailsTitle")}</Typography>
          <Typography variant="caption" color="text.secondary">
            {selectedPage?.title}
          </Typography>
          {selectedPage && (
            <Box sx={{ mt: 1, display: "flex", gap: 1, flexWrap: "wrap" }}>
              <ProcessStatusChip status={selectedPage.process_status} />
              <PullStatusChip status={selectedPage.pull_status} />
              {selectedPage.process_status === "processing" && (
                <Chip 
                  label={t("wordpress.pages.processingInProgress")}
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
                <Tab label={t("wordpress.pages.tabs.title")} />
                <Tab label={t("wordpress.pages.tabs.content")} />
                <Tab label={t("wordpress.pages.tabs.excerpt")} />
                <Tab label={t("wordpress.pages.tabs.seo")} />
                <Tab label={t("wordpress.pages.tabs.metadata")} />
              </Tabs>
              <Box sx={{ mt: 2, maxHeight: 500, overflow: "auto" }}>
                {diffTab === 0 && (
                  <>
                    <Typography variant="subtitle2" gutterBottom>
                      {t("wordpress.pages.originalTitle")}
                    </Typography>
                    <Paper sx={{ p: 2, mb: 2 }}>
                      {selectedPage.title || t("wordpress.pages.notAvailable")}
                    </Paper>
                    {selectedPage.improved_title ? (
                      <>
                        <Typography variant="subtitle2" gutterBottom>
                          {t("wordpress.pages.improvedTitle")}
                        </Typography>
                        <Paper sx={{ p: 2, mb: 2 }}>
                          {selectedPage.improved_title}
                        </Paper>
                      </>
                    ) : (
                      selectedPage.process_status === "pending" && (
                        <Paper sx={{ p: 2, mb: 2 }}>
                          <Typography variant="body2" color="text.secondary">
                            {t("wordpress.pages.notProcessed")}
                          </Typography>
                        </Paper>
                      )
                    )}
                  </>
                )}
                {diffTab === 1 && (
                  <>
                    <Typography variant="subtitle2" gutterBottom>
                      {t("wordpress.pages.originalContent")}
                    </Typography>
                    <Paper sx={{ p: 2, mb: 2, maxHeight: 300, overflow: "auto" }}>
                      {selectedPage.content ? (
                        <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(selectedPage.content) }} />
                      ) : (
                        <Typography variant="body2" color="text.secondary">{t("wordpress.pages.notAvailable")}</Typography>
                      )}
                    </Paper>
                    {selectedPage.improved_content ? (
                      <>
                        <Typography variant="subtitle2" gutterBottom>
                          {t("wordpress.pages.improvedContent")}
                        </Typography>
                        <Paper sx={{ p: 2, mb: 2, maxHeight: 300, overflow: "auto" }}>
                          <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(selectedPage.improved_content) }} />
                        </Paper>
                      </>
                    ) : (
                      selectedPage.process_status === "pending" && (
                        <Paper sx={{ p: 2, mb: 2 }}>
                          <Typography variant="body2" color="text.secondary">
                            {t("wordpress.pages.notProcessed")}
                          </Typography>
                        </Paper>
                      )
                    )}
                  </>
                )}
                {diffTab === 2 && (
                  <>
                    <Typography variant="subtitle2" gutterBottom>
                      {t("wordpress.pages.originalExcerpt")}
                    </Typography>
                    <Paper sx={{ p: 2, mb: 2 }}>
                      {selectedPage.excerpt || t("wordpress.pages.notAvailable")}
                    </Paper>
                    {selectedPage.improved_excerpt ? (
                      <>
                        <Typography variant="subtitle2" gutterBottom>
                          {t("wordpress.pages.improvedExcerpt")}
                        </Typography>
                        <Paper sx={{ p: 2, mb: 2 }}>
                          {selectedPage.improved_excerpt}
                        </Paper>
                      </>
                    ) : (
                      selectedPage.process_status === "pending" && (
                        <Paper sx={{ p: 2, mb: 2 }}>
                          <Typography variant="body2" color="text.secondary">
                            {t("wordpress.pages.notProcessed")}
                          </Typography>
                        </Paper>
                      )
                    )}
                  </>
                )}
                {diffTab === 3 && (
                  <>
                    <Typography variant="subtitle2" gutterBottom>
                      {t("wordpress.pages.seoMetadata")}
                    </Typography>
                    <Paper sx={{ p: 2, mb: 2 }}>
                      <Typography variant="body2">
                        <Trans
                          i18nKey="wordpress.pages.metaTitle"
                          values={{
                            title:
                              selectedPage.improved_meta_title ||
                              selectedPage.title ||
                              t("wordpress.pages.notAvailable"),
                          }}
                          components={{ bold: <strong /> }}
                        />
                      </Typography>
                      <Typography variant="body2" sx={{ mt: 1 }}>
                        <Trans
                          i18nKey="wordpress.pages.metaDescription"
                          values={{
                            description:
                              selectedPage.improved_meta_description ||
                              selectedPage.excerpt ||
                              t("wordpress.pages.notAvailable"),
                          }}
                          components={{ bold: <strong /> }}
                        />
                      </Typography>
                      {selectedPage.improved_schema && (
                        <>
                          <Typography variant="body2" sx={{ mt: 1 }}>
                            <Trans i18nKey="wordpress.pages.schemaMarkup" components={{ bold: <strong /> }} />
                          </Typography>
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
                      {t("wordpress.pages.pageMetadata")}
                    </Typography>
                    <Paper sx={{ p: 2, mb: 2 }}>
                      <Typography variant="body2">
                        <Trans
                          i18nKey="wordpress.pages.postId"
                          values={{ id: selectedPage.wordpress_post_id }}
                          components={{ bold: <strong /> }}
                        />
                      </Typography>
                      <Typography variant="body2" sx={{ mt: 1 }}>
                        <Trans
                          i18nKey="wordpress.pages.postTypeValue"
                          values={{ type: selectedPage.post_type }}
                          components={{ bold: <strong /> }}
                        />
                      </Typography>
                      <Typography variant="body2" sx={{ mt: 1 }}>
                        <Trans
                          i18nKey="wordpress.pages.slug"
                          values={{ slug: selectedPage.slug || t("wordpress.pages.notAvailable") }}
                          components={{ bold: <strong /> }}
                        />
                      </Typography>
                      <Typography variant="body2" sx={{ mt: 1 }}>
                        <Trans
                          i18nKey="wordpress.pages.status"
                          values={{ status: selectedPage.status }}
                          components={{ bold: <strong /> }}
                        />
                      </Typography>
                      <Typography variant="body2" sx={{ mt: 1 }}>
                        <Trans
                          i18nKey="wordpress.pages.author"
                          values={{ author: selectedPage.author_name || t("wordpress.pages.notAvailable") }}
                          components={{ bold: <strong /> }}
                        />
                      </Typography>
                      {selectedPage.date && (
                        <Typography variant="body2" sx={{ mt: 1 }}>
                          <Trans
                            i18nKey="wordpress.pages.published"
                            values={{ date: new Date(selectedPage.date).toLocaleString(uiLocale()) }}
                            components={{ bold: <strong /> }}
                          />
                        </Typography>
                      )}
                      {selectedPage.modified && (
                        <Typography variant="body2" sx={{ mt: 1 }}>
                          <Trans
                            i18nKey="wordpress.pages.modified"
                            values={{ date: new Date(selectedPage.modified).toLocaleString(uiLocale()) }}
                            components={{ bold: <strong /> }}
                          />
                        </Typography>
                      )}
                      {selectedPage.pulled_at && (
                        <Typography variant="body2" sx={{ mt: 1 }}>
                          <Trans
                            i18nKey="wordpress.pages.pulledAt"
                            values={{ date: new Date(selectedPage.pulled_at).toLocaleString(uiLocale()) }}
                            components={{ bold: <strong /> }}
                          />
                        </Typography>
                      )}
                      {selectedPage.processed_at && (
                        <Typography variant="body2" sx={{ mt: 1 }}>
                          <Trans
                            i18nKey="wordpress.pages.processedAt"
                            values={{ date: new Date(selectedPage.processed_at).toLocaleString(uiLocale()) }}
                            components={{ bold: <strong /> }}
                          />
                        </Typography>
                      )}
                    </Paper>
                    {selectedPage.improvement_summary && (
                      <>
                        <Typography variant="subtitle2" gutterBottom sx={{ mt: 2 }}>
                          {t("wordpress.pages.improvementSummary")}
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
          <Button onClick={() => setDiffDialogOpen(false)}>{t("wordpress.pages.close")}</Button>
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
            {t("wordpress.pages.refresh")}
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
              {t("wordpress.pages.processNow")}
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

