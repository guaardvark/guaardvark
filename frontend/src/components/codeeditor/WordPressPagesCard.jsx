// frontend/src/components/codeeditor/WordPressPagesCard.jsx
// WordPress Pages Card for CodeEditorPage
// Displays WordPress pages list, clicking a page opens it in CodeEditorCard

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
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Tooltip,
  LinearProgress,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import CircleIcon from "@mui/icons-material/Circle";
import SpeedIcon from "@mui/icons-material/Speed";
import TrendingUpIcon from "@mui/icons-material/TrendingUp";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";
import { loadedTab } from "./tabOrigin";
import * as wordpressService from "../../api/wordpressService";
import { useUiTranslation } from "../../i18n";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

const ProcessStatusChip = ({ status }) => {
  const normalized = status?.toLowerCase() || 'pending';
  const isProcessed = normalized === 'completed' || normalized === 'approved';
  
  if (isProcessed) {
    // Processed - green checkmark
    return (
      <Box
        sx={{
          fontSize: '1rem',
          color: 'success.main',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 24,
          height: 24,
        }}
      >
        ☑
      </Box>
    );
  } else {
    // Not processed - empty checkbox with alternate background
    return (
      <Box
        sx={{
          fontSize: '1rem',
          color: 'text.secondary',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 24,
          height: 24,
          bgcolor: 'action.hover',
          borderRadius: 1,
        }}
      >
        ☐
      </Box>
    );
  }
};

const SEOScoreBadge = ({ score, size = 'small' }) => {
  const { t } = useUiTranslation();
  if (score === null || score === undefined) {
    return null;
  }
  
  const getColor = (score) => {
    if (score >= 90) return 'success';
    if (score >= 70) return 'warning';
    return 'error';
  };
  
  return (
    <Chip
      label={t("codeWordPress.badges.seo", { score })}
      size={size}
      color={getColor(score)}
      sx={{ 
        fontSize: '0.6rem', 
        height: 18,
        fontWeight: 600,
        '& .MuiChip-label': { px: 0.5 }
      }}
    />
  );
};

const PageSpeedBadge = ({ mobile, desktop, size = 'small' }) => {
  const scores = [mobile, desktop].filter(s => s !== null && s !== undefined);
  if (scores.length === 0) {
    return null;
  }
  
  const avgScore = scores.reduce((a, b) => a + b, 0) / scores.length;
  const getColor = (score) => {
    if (score >= 90) return 'success';
    if (score >= 70) return 'warning';
    return 'error';
  };
  
  return (
    <Chip
      icon={<SpeedIcon sx={{ fontSize: '0.7rem !important' }} />}
      label={`${Math.round(avgScore)}`}
      size={size}
      color={getColor(avgScore)}
      sx={{ 
        fontSize: '0.6rem', 
        height: 18,
        '& .MuiChip-icon': { fontSize: '0.7rem' },
        '& .MuiChip-label': { px: 0.5 }
      }}
    />
  );
};

const AnalyticsBadge = ({ analytics, size = 'small' }) => {
  const { t } = useUiTranslation();
  if (!analytics || typeof analytics === 'string') {
    try {
      analytics = analytics ? JSON.parse(analytics) : null;
    } catch {
      analytics = null;
    }
  }
  
  if (!analytics || (!analytics.clicks && !analytics.impressions)) {
    return null;
  }
  
  const clicks = analytics.clicks || 0;
  const position = analytics.avg_position;
  
  return (
    <Tooltip
      title={t("codeWordPress.badges.analytics", {
        clicks,
        impressions: analytics.impressions || 0,
        position: position ? position.toFixed(1) : t("codeWordPress.badges.notAvailable"),
      })}
    >
      <Chip
        icon={<TrendingUpIcon sx={{ fontSize: '0.7rem !important' }} />}
        label={clicks > 0 ? clicks : '—'}
        size={size}
        sx={{ 
          fontSize: '0.6rem', 
          height: 18,
          '& .MuiChip-icon': { fontSize: '0.7rem' },
          '& .MuiChip-label': { px: 0.5 }
        }}
      />
    </Tooltip>
  );
};

const WordPressPagesCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      _openTabs,
      setOpenTabs,
      setActiveTabIndex,
      ...props
    },
    ref
  ) => {
    const { t } = useUiTranslation();
    const [pages, setPages] = useState([]);
    const [sites, setSites] = useState([]);
    const [selectedSite, setSelectedSite] = useState(null);
    const [isLoading, setIsLoading] = useState(true);
    const [isProcessing, setIsProcessing] = useState(false);
    const [isPulling, setIsPulling] = useState(false);
    const [error, setError] = useState(null);
    const [feedback, setFeedback] = useState({
      open: false,
      message: "",
      severity: "info",
    });

    const [pullDialogOpen, setPullDialogOpen] = useState(false);
    const [processType, _setProcessType] = useState("full");
    const [pullOptions, setPullOptions] = useState({
      post_type: "post",
      max_pages: null,
    });

    const [filterStatus, setFilterStatus] = useState("all");
    const [filterProcessStatus, setFilterProcessStatus] = useState("all");
    
    // Recommendations dialog state
    const [recommendationsDialogOpen, setRecommendationsDialogOpen] = useState(false);
    const [selectedPageForRecommendations, setSelectedPageForRecommendations] = useState(null);
    const [recommendations, setRecommendations] = useState(null);
    const [loadingRecommendations, setLoadingRecommendations] = useState(false);
    
    const timeoutRefs = useRef([]);

    const fetchSites = useCallback(async () => {
      try {
        const response = await wordpressService.getWordPressSites();
        if (response.success && response.data && Array.isArray(response.data)) {
          setSites(response.data);
          if (response.data.length > 0 && !selectedSite) {
            setSelectedSite(response.data[0].id);
          }
        } else {
          setSites([]);
          setSelectedSite(null);
        }
      } catch (err) {
        console.error("Error fetching WordPress sites:", err);
        setSites([]);
        setSelectedSite(null);
      }
    }, [selectedSite]);
    
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
          throw new Error(response.error || t("codeWordPress.errors.fetchFailed"));
        }
      } catch (err) {
        console.error("Error fetching WordPress pages:", err);
        setError(err.message || t("codeWordPress.errors.loadFailed"));
        setFeedback({
          open: true,
          message: err.message || t("codeWordPress.errors.loadFailed"),
          severity: "error",
        });
      } finally {
        setIsLoading(false);
      }
    }, [selectedSite, filterStatus, filterProcessStatus, t]);

    useEffect(() => {
      fetchSites();
    }, [fetchSites]);

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
            message: t("codeWordPress.feedback.pulled", { count: response.data.total_pulled || 0 }),
            severity: "success",
          });
          const timeoutId = setTimeout(() => {
            fetchPages();
          }, 500);
          timeoutRefs.current.push(timeoutId);
        } else {
          throw new Error(response.error || t("codeWordPress.errors.pullFailed"));
        }
      } catch (err) {
        setFeedback({
          open: true,
          message: err.message || t("codeWordPress.errors.pullFailed"),
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
          // Silently refresh pages after processing starts
          const timeoutId = setTimeout(() => {
            fetchPages();
          }, 2000);
          timeoutRefs.current.push(timeoutId);
        } else {
          throw new Error(response.error || t("codeWordPress.errors.processFailed"));
        }
      } catch (err) {
        setFeedback({
          open: true,
          message: err.message || t("codeWordPress.errors.processFailed"),
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
          message: t("codeWordPress.feedback.nonePending"),
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
            message: t("codeWordPress.feedback.queued", { count: response.data?.queued || pendingPages.length }),
            severity: "success",
          });
          const timeoutId = setTimeout(() => {
            fetchPages();
          }, 500);
          timeoutRefs.current.push(timeoutId);
        } else {
          throw new Error(response.error || t("codeWordPress.errors.queueFailed"));
        }
      } catch (err) {
        setFeedback({
          open: true,
          message: err.message || t("codeWordPress.errors.queueFailed"),
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
            message: t("codeWordPress.feedback.processed", { succeeded, total }),
            severity: "success",
          });
          const timeoutId = setTimeout(() => {
            fetchPages();
          }, 2000);
          timeoutRefs.current.push(timeoutId);
        } else {
          throw new Error(response.error || t("codeWordPress.errors.executeFailed"));
        }
      } catch (err) {
        setFeedback({
          open: true,
          message: err.message || t("codeWordPress.errors.executeFailed"),
          severity: "error",
        });
      } finally {
        setIsProcessing(false);
      }
    };

    // Open page in CodeEditorCard instead of dialog
    // Get AI recommendations for a page
    const handleGetRecommendations = useCallback(async (page) => {
      setSelectedPageForRecommendations(page);
      setRecommendationsDialogOpen(true);
      setLoadingRecommendations(true);
      setRecommendations(null);

      try {
        // TODO: Implement actual LLM-based recommendations endpoint
        // For now, this is a placeholder structure that will be replaced
        // with a real API call to get intelligent recommendations
        
        // Simulate loading delay
        await new Promise(resolve => setTimeout(resolve, 500));
        
        // Placeholder: This will be replaced with actual LLM call
        // Example structure for future implementation:
        // const response = await wordpressService.getPageRecommendations(page.id);
        // setRecommendations(response.data);
        
        setRecommendations({
          summary: t("codeWordPress.recommendations.summary"),
          recommendations: [
            {
              type: "content",
              priority: "high",
              title: t("codeWordPress.recommendations.content.title"),
              description: t("codeWordPress.recommendations.content.description")
            },
            {
              type: "seo",
              priority: "medium",
              title: t("codeWordPress.recommendations.seo.title"),
              description: t("codeWordPress.recommendations.seo.description")
            },
            {
              type: "structure",
              priority: "low",
              title: t("codeWordPress.recommendations.structure.title"),
              description: t("codeWordPress.recommendations.structure.description")
            }
          ]
        });
      } catch (err) {
        console.error("Error getting recommendations:", err);
        setFeedback({
          open: true,
          message: t("codeWordPress.errors.recommendationsFailed"),
          severity: "info",
        });
      } finally {
        setLoadingRecommendations(false);
      }
    }, [t]);

    // Determine if page needs AI improvement
    const needsAIImprovement = useCallback((page) => {
      // Pages that need improvement:
      // 1. Not processed yet (pending status)
      // 2. Processed but might benefit from additional improvements
      return page.process_status === "pending" || 
             (page.process_status === "completed" && !page.improved_title && !page.improved_content);
    }, []);

    // Get recommendation icon for a page
    const getRecommendationIcon = useCallback((page) => {
      const needsImprovement = needsAIImprovement(page);
      const hasImprovements = page.improved_title || page.improved_content;
      
      if (needsImprovement) {
        // Page needs AI processing - show thin circle icon
        return (
          <RadioButtonUncheckedIcon 
            sx={{ 
              color: 'warning.main',
              fontSize: '1rem'
            }} 
          />
        );
      } else if (hasImprovements) {
        // Page has improvements - show filled circle
        return (
          <CircleIcon sx={{ color: 'success.main', fontSize: '1rem' }} />
        );
      } else {
        // No improvements needed or available
        return (
          <CircleIcon sx={{ color: 'text.disabled', fontSize: '1rem' }} />
        );
      }
    }, [needsAIImprovement]);

    // Open page in CodeEditorCard instead of dialog
    const handlePageClick = useCallback((page) => {
      // Show raw HTML content - use improved content if available, otherwise original
      const content = page.improved_content || page.content || '';
      const fileName = `${page.slug || `page-${page.id}`}.html`;
      
      // Create new tab with raw HTML content
      const newTab = loadedTab({
        id: Math.random().toString(36).slice(2, 11),
        filePath: fileName,
        language: "html",
        isNew: false,
        source: "wordpress",
        wordpressPageId: page.id,
        wordpressPageTitle: page.title,
      }, content);
      
      setOpenTabs(prev => {
        const newTabs = [...prev, newTab];
        setActiveTabIndex(newTabs.length - 1);
        return newTabs;
      });
    }, [setOpenTabs, setActiveTabIndex]);

    const pendingCount = useMemo(() => {
      return pages.filter(
        (p) => p.pull_status === "pulled" && p.process_status === "pending"
      ).length;
    }, [pages]);

    // Priority values stay data (they pick the chip colour); only the label is worded.
    const priorityLabel = (priority) => ({
      high: t("codeWordPress.recommendations.priority.high"),
      medium: t("codeWordPress.recommendations.priority.medium"),
      low: t("codeWordPress.recommendations.priority.low"),
    })[priority] || priority;

    const completedCount = useMemo(() => {
      return pages.filter(
        (p) => p.process_status === "completed" || p.process_status === "approved"
      ).length;
    }, [pages]);

    return (
      <DashboardCardWrapper
        ref={ref}
        title={t("codeWordPress.title")}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        style={style}
        {...props}
      >
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Header Controls */}
          <Box sx={{ p: 0.75, borderBottom: 1, borderColor: 'divider', flexShrink: 0 }}>
            <Stack direction="row" spacing={0.5} sx={{ mb: 0.75, flexWrap: 'wrap' }}>
              {selectedSite && (
                <>
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={() => setPullDialogOpen(true)}
                    disabled={isPulling}
                    sx={{ 
                      fontSize: '0.7rem', 
                      py: 0.25,
                      borderWidth: 1,
                      borderColor: 'divider',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderWidth: 1,
                        bgcolor: 'action.hover'
                      }
                    }}
                  >
                    {t("codeWordPress.actions.pull")}
                  </Button>
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={handleQueueProcessing}
                    disabled={isProcessing || pendingCount === 0}
                    sx={{ 
                      fontSize: '0.7rem', 
                      py: 0.25,
                      borderWidth: 1,
                      borderColor: 'divider',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderWidth: 1,
                        bgcolor: 'action.hover'
                      }
                    }}
                  >
                    {t("codeWordPress.actions.queue", { count: pendingCount })}
                  </Button>
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={handleExecuteQueue}
                    disabled={isProcessing || pendingCount === 0}
                    sx={{ 
                      fontSize: '0.7rem', 
                      py: 0.25,
                      borderWidth: 1,
                      borderColor: 'divider',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderWidth: 1,
                        bgcolor: 'action.hover'
                      }
                    }}
                  >
                    {t("codeWordPress.actions.process")}
                  </Button>
                </>
              )}
              <IconButton size="small" onClick={fetchPages} sx={{ p: 0.5 }}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </Stack>

            {/* Site Selector */}
            <FormControl fullWidth size="small" sx={{ mb: 0.75 }}>
              <InputLabel sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.site")}</InputLabel>
              <Select
                value={selectedSite || ""}
                label={t("codeWordPress.site")}
                onChange={(e) => {
                  const value = e.target.value;
                  if (!value) {
                    setSelectedSite(null);
                    return;
                  }
                  const siteId = parseInt(value, 10);
                  if (!isNaN(siteId) && sites.find(s => s.id === siteId)) {
                    setSelectedSite(siteId);
                  } else {
                    setSelectedSite(null);
                  }
                }}
                sx={{ fontSize: '0.7rem', '& .MuiSelect-select': { py: 0.75 } }}
              >
                {sites.map((site) => (
                  <MenuItem key={site.id} value={site.id} sx={{ fontSize: '0.7rem' }}>
                    {site.site_name || site.url}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            {/* Filters */}
            <Stack direction="row" spacing={0.5} sx={{ mb: 0.75 }}>
              <FormControl size="small" sx={{ minWidth: 90, flex: 1 }}>
                <InputLabel sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.pull")}</InputLabel>
                <Select
                  value={filterStatus}
                  label={t("codeWordPress.filters.pull")}
                  onChange={(e) => setFilterStatus(e.target.value)}
                  sx={{ fontSize: '0.7rem', '& .MuiSelect-select': { py: 0.75 } }}
                >
                  <MenuItem value="all" sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.all")}</MenuItem>
                  <MenuItem value="pulled" sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.pulled")}</MenuItem>
                  <MenuItem value="pending" sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.pending")}</MenuItem>
                  <MenuItem value="error" sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.error")}</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" sx={{ minWidth: 90, flex: 1 }}>
                <InputLabel sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.process")}</InputLabel>
                <Select
                  value={filterProcessStatus}
                  label={t("codeWordPress.filters.process")}
                  onChange={(e) => setFilterProcessStatus(e.target.value)}
                  sx={{ fontSize: '0.7rem', '& .MuiSelect-select': { py: 0.75 } }}
                >
                  <MenuItem value="all" sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.all")}</MenuItem>
                  <MenuItem value="pending" sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.pending")}</MenuItem>
                  <MenuItem value="processing" sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.processing")}</MenuItem>
                  <MenuItem value="completed" sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.completed")}</MenuItem>
                  <MenuItem value="approved" sx={{ fontSize: '0.7rem' }}>{t("codeWordPress.filters.approved")}</MenuItem>
                </Select>
              </FormControl>
            </Stack>

            {/* Stats */}
            <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap' }}>
              <Chip 
                label={t("codeWordPress.stats.total", { total: pages.length })} 
                size="small" 
                variant="outlined" 
                sx={{ fontSize: '0.6rem', height: 20, '& .MuiChip-label': { px: 0.75 } }}
              />
              <Chip 
                label={t("codeWordPress.stats.pending", { pending: pendingCount })} 
                size="small" 
                color="warning" 
                variant="outlined" 
                sx={{ fontSize: '0.6rem', height: 20, '& .MuiChip-label': { px: 0.75 } }}
              />
              <Chip 
                label={t("codeWordPress.stats.completed", { completed: completedCount })} 
                size="small" 
                color="success" 
                variant="outlined" 
                sx={{ fontSize: '0.6rem', height: 20, '& .MuiChip-label': { px: 0.75 } }}
              />
            </Stack>
          </Box>

          {/* Pages Table */}
          <Box sx={{ flex: 1, overflow: 'auto' }}>
            {isLoading ? (
              <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
                <CircularProgress size={24} />
              </Box>
            ) : error ? (
              <Box sx={{ p: 1.5 }}>
                <MuiAlert severity="error" sx={{ mb: 1, fontSize: '0.7rem' }}>
                  {error}
                </MuiAlert>
              </Box>
            ) : pages.length === 0 ? (
              <Box sx={{ textAlign: "center", py: 4 }}>
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.7rem' }}>
                  {selectedSite
                    ? t("codeWordPress.empty.noPages")
                    : t("codeWordPress.empty.selectSite")}
                </Typography>
              </Box>
            ) : (
              <TableContainer component={Paper} sx={{ maxHeight: '100%' }}>
                <Table stickyHeader size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ fontSize: '0.7rem', py: 0.5, fontWeight: 600 }}>{t("codeWordPress.table.title")}</TableCell>
                      <TableCell sx={{ fontSize: '0.7rem', py: 0.5, fontWeight: 600 }}>{t("codeWordPress.table.type")}</TableCell>
                      <TableCell sx={{ fontSize: '0.7rem', py: 0.5, fontWeight: 600 }}>{t("codeWordPress.table.seo")}</TableCell>
                      <TableCell align="center" sx={{ fontSize: '0.7rem', py: 0.5, fontWeight: 600 }}></TableCell>
                      <TableCell align="center" sx={{ fontSize: '0.7rem', py: 0.5, fontWeight: 600 }}>{t("codeWordPress.table.ai")}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {pages.map((page) => (
                      <TableRow 
                        key={page.id} 
                        hover 
                        sx={{ cursor: "pointer" }}
                        onClick={() => handlePageClick(page)}
                      >
                        <TableCell sx={{ py: 0.5 }}>
                          <Typography variant="body2" sx={{ fontWeight: 500, fontSize: '0.7rem' }}>
                            {page.title || t("codeWordPress.untitled")}
                          </Typography>
                          <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.6rem' }}>
                            {page.slug}
                          </Typography>
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.7rem', py: 0.5 }}>{page.post_type}</TableCell>
                        <TableCell sx={{ py: 0.5 }}>
                          <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', gap: 0.5 }}>
                            <SEOScoreBadge score={page.seo?.seo_score || page.seo_score} />
                            <PageSpeedBadge 
                              mobile={page.seo?.pagespeed_score_mobile || page.pagespeed_score_mobile}
                              desktop={page.seo?.pagespeed_score_desktop || page.pagespeed_score_desktop}
                            />
                            <AnalyticsBadge analytics={page.seo?.analytics_data || page.analytics_data} />
                          </Stack>
                        </TableCell>
                        <TableCell sx={{ py: 0.5 }}>
                          <ProcessStatusChip status={page.process_status} />
                        </TableCell>
                        <TableCell 
                          align="center" 
                          sx={{ py: 0.5 }}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleGetRecommendations(page);
                          }}
                        >
                          <Tooltip title={needsAIImprovement(page) ? t("codeWordPress.tooltips.getRecommendations") : (page.improved_title || page.improved_content ? t("codeWordPress.tooltips.hasImprovements") : t("codeWordPress.tooltips.noImprovements"))}>
                            <IconButton
                              size="small"
                              sx={{ p: 0.5 }}
                            >
                              {getRecommendationIcon(page)}
                            </IconButton>
                          </Tooltip>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Box>

          {/* Recommendations Dialog */}
          <Dialog 
            open={recommendationsDialogOpen} 
            onClose={() => setRecommendationsDialogOpen(false)} 
            maxWidth="md" 
            fullWidth
          >
            <DialogTitle sx={{ fontSize: '0.875rem', fontWeight: 600 }}>
              {t("codeWordPress.recommendations.title")}
              {selectedPageForRecommendations && (
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem', display: 'block', mt: 0.5 }}>
                  {selectedPageForRecommendations.title || t("codeWordPress.untitled")}
                </Typography>
              )}
            </DialogTitle>
            <DialogContent>
              {loadingRecommendations ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                  <CircularProgress size={24} />
                </Box>
              ) : recommendations ? (
                <Box sx={{ mt: 1 }}>
                  {recommendations.summary && (
                    <Typography variant="body2" sx={{ mb: 2, fontSize: '0.7rem' }}>
                      {recommendations.summary}
                    </Typography>
                  )}
                  {recommendations.recommendations && recommendations.recommendations.length > 0 ? (
                    <Stack spacing={1}>
                      {recommendations.recommendations.map((rec, index) => (
                        <Paper key={index} sx={{ p: 1.5 }}>
                          <Box sx={{ display: 'flex', alignItems: 'start', gap: 1 }}>
                            <Chip 
                              label={priorityLabel(rec.priority)} 
                              size="small" 
                              color={rec.priority === 'high' ? 'error' : rec.priority === 'medium' ? 'warning' : 'default'}
                              sx={{ fontSize: '0.6rem', height: 18, textTransform: 'capitalize' }}
                            />
                            <Box sx={{ flex: 1 }}>
                              <Typography variant="subtitle2" sx={{ fontSize: '0.7rem', fontWeight: 600, mb: 0.5 }}>
                                {rec.title}
                              </Typography>
                              <Typography variant="body2" sx={{ fontSize: '0.7rem', color: 'text.secondary' }}>
                                {rec.description}
                              </Typography>
                            </Box>
                          </Box>
                        </Paper>
                      ))}
                    </Stack>
                  ) : (
                    <Typography variant="body2" sx={{ fontSize: '0.7rem', color: 'text.secondary' }}>
                      {t("codeWordPress.recommendations.none")}
                    </Typography>
                  )}
                </Box>
              ) : (
                <Typography variant="body2" sx={{ fontSize: '0.7rem', color: 'text.secondary' }}>
                  {t("codeWordPress.recommendations.loading")}
                </Typography>
              )}
            </DialogContent>
            <DialogActions sx={{ px: 2, pb: 2 }}>
              <Button 
                onClick={() => setRecommendationsDialogOpen(false)} 
                size="small"
                sx={{ fontSize: '0.7rem' }}
              >
                {t("codeWordPress.actions.close")}
              </Button>
              {selectedPageForRecommendations && needsAIImprovement(selectedPageForRecommendations) && (
                <Button 
                  variant="contained" 
                  startIcon={<AutoAwesomeIcon />}
                  onClick={() => {
                    handleProcessPage(selectedPageForRecommendations.id);
                    setRecommendationsDialogOpen(false);
                  }}
                  disabled={isProcessing}
                  size="small"
                  sx={{ fontSize: '0.7rem' }}
                >
                  {t("codeWordPress.recommendations.processNow")}
                </Button>
              )}
            </DialogActions>
          </Dialog>

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
            <DialogTitle>{t("codeWordPress.pullDialog.title")}</DialogTitle>
            <DialogContent>
              <Stack spacing={2} sx={{ mt: 1 }}>
                <FormControl fullWidth>
                  <InputLabel>{t("codeWordPress.pullDialog.postType")}</InputLabel>
                  <Select
                    value={pullOptions.post_type}
                    label={t("codeWordPress.pullDialog.postType")}
                    onChange={(e) =>
                      setPullOptions({ ...pullOptions, post_type: e.target.value })
                    }
                  >
                    <MenuItem value="post">{t("codeWordPress.pullDialog.posts")}</MenuItem>
                    <MenuItem value="page">{t("codeWordPress.pullDialog.pages")}</MenuItem>
                  </Select>
                </FormControl>
                <TextField
                  fullWidth
                  type="number"
                  label={t("codeWordPress.pullDialog.maxPages")}
                  value={pullOptions.max_pages || ""}
                  onChange={(e) => {
                    const value = e.target.value;
                    setPullOptions({
                      ...pullOptions,
                      max_pages: value ? (parseInt(value, 10) || null) : null,
                    });
                  }}
                  helperText={t("codeWordPress.pullDialog.maxPagesHelper")}
                  inputProps={{ min: 1 }}
                />
                {isPulling && <LinearProgress />}
              </Stack>
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setPullDialogOpen(false)}>{t("codeWordPress.actions.cancel")}</Button>
              <Button variant="contained" onClick={handlePull} disabled={isPulling}>
                {isPulling ? t("codeWordPress.pullDialog.pulling") : t("codeWordPress.pullDialog.submit")}
              </Button>
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
        </Box>
      </DashboardCardWrapper>
    );
  }
);

WordPressPagesCard.displayName = "WordPressPagesCard";

export default WordPressPagesCard;

