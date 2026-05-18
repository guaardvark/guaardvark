// frontend/src/pages/WebsitesPage.jsx
// Version 1.5: Aligned UI with TaskPage, added view toggle (card/table), removed filter/refresh.
// - Header style, padding, margins, and colors updated.
// - Removed project filter and refresh icon from header.
// - Implemented card and table views with a toggle.
// - Card/row click now opens the edit modal.
// - Note: "Notes Excerpt" not added as 'notes' field is not in the Website model.
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from Dean (user/owner).

import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  Box,
  Typography,
  Button,
  Alert as MuiAlert,
  Grid,
  Card,
  CardActionArea,
  CardContent,
  Tooltip,
  Snackbar,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Chip,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import { useTheme } from "@mui/material/styles";
import { useSearchParams } from "react-router-dom"; // For modal linking

import * as apiService from "../api";
import * as wordpressService from "../api/wordpressService";
import { scrapeWebsite } from "../api/websiteService";
import WebsiteActionModal from "../components/modals/WebsiteActionModal";
import PageLayout from "../components/layout/PageLayout";
import EntityContextMenu from "../components/common/EntityContextMenu";
import { useStatus } from "../contexts/StatusContext"; // For active model display
import { useAppStore } from "../stores/useAppStore";
import ProjectStateErrorBoundary from "../components/common/ProjectStateErrorBoundary";
import { ContextualLoader } from "../components/common/LoadingStates";
import { getLogoUrl } from "../config/logoConfig";
import { useNavigate } from "react-router-dom";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

// Sorting functions (similar to other pages)
import { getComparator, stableSort } from "../utils/sortUtils";

const formatDate = (dateString) => {
  if (!dateString) return "-";
  try {
    return new Date(dateString).toLocaleString();
  } catch (e) {
    console.warn("Error formatting date:", dateString, e);
    return dateString;
  }
};

const WebsiteStatusChip = ({ status }) => {
  if (!status) return null;
  const normalized = status.toLowerCase();
  if (normalized === "pending" || normalized === "queued") return null;

  let color = "default";
  let label = status;
  switch (normalized) {
    case "active":
    case "online":
    case "indexed":
      color = "success";
      break;
    case "error":
    case "offline":
      color = "error";
      break;
    case "crawling":
    case "indexing":
      color = "warning";
      break;
    default:
      label = status.charAt(0).toUpperCase() + status.slice(1);
      break;
  }
  return (
    <Chip
      label={label}
      color={color}
      size="small"
      sx={{ textTransform: "capitalize" }}
    />
  );
};

const WebsitesPage = () => {
  const theme = useTheme();
  const navigate = useNavigate();

  const [searchParams, setSearchParams] = useSearchParams();
  const { activeModel } = useStatus();

  const [websites, setWebsites] = useState([]);
  const [wpSites, setWpSites] = useState([]); // WordPress sites for mapping
  // Use centralized project state instead of local state
  const projects = useAppStore((state) => state.projects);
  const setProjects = useAppStore((state) => state.setProjects);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState({
    open: false,
    message: "",
    severity: "info",
  });

  const [actionModalOpen, setActionModalOpen] = useState(false);
  const [currentWebsiteForModal, setCurrentWebsiteForModal] = useState(null);
  const [isModalSaving, setIsModalSaving] = useState(false);

  const [viewMode, setViewMode] = useState("card"); // 'card' or 'table'
  const [order, setOrder] = useState("asc");
  const [orderBy, setOrderBy] = useState("url");

  // Context menu state
  const [contextMenu, setContextMenu] = useState(null);
  const [contextItem, setContextItem] = useState(null);

  const handleContextMenu = (e, site = null) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ top: e.clientY, left: e.clientX });
    setContextItem(site);
  };

  const fetchWebsitesAndProjects = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [websitesData, projectsData, wpSitesData] = await Promise.all([
        apiService.getWebsites(), // No filter here, filter removed from UI
        // Only fetch projects if not already loaded in store
        projects.length === 0 ? apiService.getProjects() : Promise.resolve(projects), // For mapping project names
        wordpressService.getWordPressSites(), // Fetch WordPress sites
      ]);

      if (websitesData && websitesData.error)
        throw new Error(websitesData.error);
      // All websites returned from the API should be shown. The previous
      // implementation removed entries with a status of "pending" or
      // "queued", which made freshly created sites invisible to the user
      // until they were processed. This caused confusion when the backend
      // correctly contained websites but the page appeared empty.  We now
      // keep the list intact and display every website regardless of
      // status.
      const sites = Array.isArray(websitesData) ? websitesData : [];
      setWebsites(sites);

      if (projectsData && projectsData.error)
        throw new Error(projectsData.error);
      // Only update projects in store if we fetched new data
      if (projects.length === 0) {
        setProjects(Array.isArray(projectsData) ? projectsData : []);
      }
      
      // Set WordPress sites
      if (wpSitesData && wpSitesData.success && wpSitesData.data) {
        setWpSites(wpSitesData.data);
      }
    } catch (err) {
      const errorMessage =
        err.data?.error || err.message || "Failed to fetch data.";
      setError(errorMessage);
      setWebsites([]);
      setProjects([]);
      setWpSites([]);
      setFeedback({ open: true, message: errorMessage, severity: "error" });
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchWebsitesAndProjects();
  }, [fetchWebsitesAndProjects]);

  useEffect(() => {
    const idParam = searchParams.get("websiteId");
    if (idParam && websites.length > 0) {
      const site = websites.find((s) => String(s.id) === idParam);
      if (site) {
        handleOpenActionModal(site);
        const params = new URLSearchParams(searchParams);
        params.delete("websiteId");
        setSearchParams(params, { replace: true });
      }
    }
  }, [websites, searchParams, setSearchParams]);

  const handleOpenActionModal = (website = null) => {
    setCurrentWebsiteForModal(website);
    setActionModalOpen(true);
  };

  const handleCloseActionModal = () => {
    if (isModalSaving) return;
    setActionModalOpen(false);
    setCurrentWebsiteForModal(null);
    const params = new URLSearchParams(searchParams);
    params.delete("websiteId");
    setSearchParams(params, { replace: true });
  };

  const handleSaveWebsite = async (arg1, arg2) => {
    setIsModalSaving(true);
    setFeedback({ open: false, message: "" });
    let action;
    let idToUpdate;
    let dataPayload;

    if (arg2 !== undefined) {
      action = "update";
      idToUpdate = arg1;
      dataPayload = arg2;
    } else {
      action = "create";
      idToUpdate = null;
      dataPayload = arg1;
    }

    try {
      let response;
      if (action === "update") {
        if (!idToUpdate) throw new Error("Website ID is missing for update.");
        response = await apiService.updateWebsite(idToUpdate, dataPayload);
      } else {
        response = await apiService.createWebsite(dataPayload);
      }
      if (response && response.error)
        throw new Error(response.error.message || response.error);
      setFeedback({
        open: true,
        message: `Website ${action}d successfully!`,
        severity: "success",
      });
      handleCloseActionModal();
      fetchWebsitesAndProjects();
    } catch (err) {
      const errorMessage =
        err.data?.error || err.message || `Error ${action}ing website.`;
      setFeedback({ open: true, message: errorMessage, severity: "error" });
    } finally {
      setIsModalSaving(false);
    }
  };

  const handleDeleteWebsite = async (websiteId, websiteUrl) => {
    if (
      !window.confirm(
        `Are you sure you want to delete website "${websiteUrl || "N/A"}" (ID: ${websiteId})? This action cannot be undone.`,
      )
    )
      return;
    setIsModalSaving(true); // Or a specific isDeleting state
    setFeedback({
      open: true,
      message: `Deleting website "${websiteUrl}"...`,
      severity: "info",
    });
    try {
      await apiService.deleteWebsite(websiteId);
      setFeedback({
        open: true,
        message: "Website deleted successfully!",
        severity: "success",
      });
      if (actionModalOpen && currentWebsiteForModal?.id === websiteId) {
        handleCloseActionModal();
      }
      fetchWebsitesAndProjects();
    } catch (err) {
      setFeedback({
        open: true,
        message: err.data?.error || err.message || "Failed to delete website.",
        severity: "error",
      });
    } finally {
      setIsModalSaving(false);
    }
  };

  const handleCloseFeedback = (event, reason) => {
    if (reason === "clickaway") return;
    setFeedback((prev) => ({ ...prev, open: false }));
  };

  const getProjectName = (projectId) =>
    projects.find((p) => p.id === projectId)?.name || "N/A";
  
  const getWpSiteForWebsite = (websiteId) => {
    return wpSites.find((wp) => wp.website_id === websiteId);
  };
  
  const handleManageWpPages = (websiteId) => {
    const wpSite = getWpSiteForWebsite(websiteId);
    if (wpSite) {
      navigate(`/wordpress/pages?site_id=${wpSite.id}`);
    }
  };

  const [crawlingIds, setCrawlingIds] = useState(new Set());

  const handleCrawlWebsite = async (websiteId, e) => {
    if (e) e.stopPropagation();
    setCrawlingIds(prev => new Set([...prev, websiteId]));
    try {
      await scrapeWebsite(websiteId);
      setFeedback({ open: true, message: "Website crawled successfully", severity: "success" });
      fetchWebsitesAndProjects(); // Refresh to update last_crawled
    } catch (err) {
      setFeedback({ open: true, message: `Crawl failed: ${err.message || "Unknown error"}`, severity: "error" });
    } finally {
      setCrawlingIds(prev => {
        const next = new Set(prev);
        next.delete(websiteId);
        return next;
      });
    }
  };

  const handleSortRequest = (property) => {
    const isAsc = orderBy === property && order === "asc";
    setOrder(isAsc ? "desc" : "asc");
    setOrderBy(property);
  };

  const sortedWebsites = useMemo(() => {
    return stableSort(websites, getComparator(order, orderBy));
  }, [websites, order, orderBy]);

  const headCells = [
    { id: "url", label: "Website URL", sortable: true },
    { id: "project.name", label: "Project", sortable: true },
    { id: "sitemap", label: "Sitemap URL", sortable: true },
    { id: "status", label: "Status", sortable: true },
    { id: "document_count", label: "Docs", sortable: true, align: "right" },
    { id: "last_crawled", label: "Last Crawled", sortable: true },
    { id: "actions", label: "Actions", sortable: false },
  ];

  return (
    <ProjectStateErrorBoundary>
      <PageLayout
        title="Websites"
        variant="standard"
        viewToggle={{ mode: viewMode, onToggle: (val) => setViewMode(val) }}
        actions={
          <Button
            variant="contained"
            size="small"
            startIcon={<AddIcon />}
            onClick={() => handleOpenActionModal(null)}
            disabled={isLoading || isModalSaving}
          >
            Add Website
          </Button>
        }
        modelStatus
        activeModel={activeModel}
      >
      <Box
        onContextMenu={(e) => handleContextMenu(e, null)}
        sx={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
      >
        <Snackbar
          open={feedback.open}
          autoHideDuration={6000}
          onClose={handleCloseFeedback}
          anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        >
          <AlertSnackbar
            onClose={handleCloseFeedback}
            severity={feedback.severity || "info"}
            sx={{ width: "100%" }}
          >
            {feedback.message}
          </AlertSnackbar>
        </Snackbar>

        {error && (
          <MuiAlert severity="error" sx={{ mb: 2 }}>
            {error}
          </MuiAlert>
        )}
        {isLoading && (
          <ContextualLoader loading message="Loading websites..." showProgress={false} inline />
        )}

        {!isLoading && sortedWebsites.length === 0 && !error && (
          <Typography sx={{ mt: 2, textAlign: "center", fontStyle: "italic" }}>
            No websites found. Add one to get started!
          </Typography>
        )}

        {!isLoading && sortedWebsites.length > 0 && viewMode === "card" && (
          <Grid container spacing={2}>
            {sortedWebsites.map((site) => (
              <Grid item xs={12} sm={6} md={4} lg={3} key={site.id}>
                <Card
                  onContextMenu={(e) => handleContextMenu(e, site)}
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    height: "100%",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 2,
                    "&:hover": { boxShadow: theme.shadows[3] },
                  }}
                >
                  <CardActionArea
                    onClick={() => handleOpenActionModal(site)}
                    sx={{
                      flexGrow: 1,
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "stretch",
                    }}
                  >
                    <CardContent sx={{ flexGrow: 1, p: 1.5 }}>
                      <Grid container spacing={1} alignItems="flex-start">
                        <Grid item xs={9}>
                          <Tooltip title={site.url}>
                            <Typography
                              variant="h6"
                              component="div"
                              gutterBottom
                              noWrap
                              sx={{ fontSize: "1rem", fontWeight: "medium" }}
                            >
                              {site.url}
                            </Typography>
                          </Tooltip>
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ mb: 0.5 }}
                          >
                            Project: {getProjectName(site.project_id)}
                          </Typography>
                          <Tooltip title={site.sitemap || "No sitemap URL"}>
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              noWrap
                              sx={{
                                mb: 0.5,
                                fontStyle: site.sitemap ? "normal" : "italic",
                              }}
                            >
                              Sitemap: {site.sitemap || "N/A"}
                            </Typography>
                          </Tooltip>
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ mb: 0.5 }}
                          >
                            Status: <WebsiteStatusChip status={site.status} />
                          </Typography>
                          <Typography variant="body2" color="text.secondary">
                            Linked Docs:{" "}
                            {site.document_count !== undefined
                              ? site.document_count
                              : "N/A"}
                          </Typography>
                          {getWpSiteForWebsite(site.id) && (
                            <Box sx={{ mt: 1, display: "flex", gap: 1, alignItems: "center" }}>
                              <Chip
                                label="WordPress Connected"
                                color="success"
                                size="small"
                                icon={<AutoAwesomeIcon />}
                              />
                            </Box>
                          )}
                          <Typography
                            variant="caption"
                            color="text.disabled"
                            display="block"
                            sx={{ mt: 1 }}
                          >
                            Last Crawled:{" "}
                            {site.last_crawled
                              ? formatDate(site.last_crawled)
                              : "Never"}
                          </Typography>
                        </Grid>
                        <Grid item xs={3} sx={{ textAlign: "right" }}>
                          {site.client?.logo_path && (
                            <img
                              src={getLogoUrl(site.client.logo_path)}
                              alt={site.client.name}
                              style={{ maxHeight: 60, maxWidth: "100%" }}
                            />
                          )}
                        </Grid>
                      </Grid>
                    </CardContent>
                  </CardActionArea>
                  <Box sx={{ p: 1.5, pt: 0, display: 'flex', gap: 1 }}>
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={<TravelExploreIcon />}
                      disabled={crawlingIds.has(site.id)}
                      onClick={(e) => handleCrawlWebsite(site.id, e)}
                      sx={{ flex: 1 }}
                    >
                      {crawlingIds.has(site.id) ? "Crawling..." : "Crawl"}
                    </Button>
                    {getWpSiteForWebsite(site.id) && (
                      <Button
                        size="small"
                        variant="outlined"
                        startIcon={<AutoAwesomeIcon />}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleManageWpPages(site.id);
                        }}
                        sx={{ flex: 1 }}
                      >
                        Manage Pages
                      </Button>
                    )}
                  </Box>
                </Card>
              </Grid>
            ))}
          </Grid>
        )}

        {!isLoading && sortedWebsites.length > 0 && viewMode === "table" && (
          <Paper elevation={2} sx={{ mb: 1, overflow: "hidden" }}>
            <TableContainer sx={{ maxHeight: "calc(100vh - 200px)" }}>
              <Table stickyHeader size="small">
                <TableHead>
                  <TableRow>
                    {headCells.map((headCell) => (
                      <TableCell
                        key={headCell.id}
                        align={headCell.align || "left"}
                        sortDirection={orderBy === headCell.id ? order : false}
                        sx={{ fontWeight: "bold" }}
                      >
                        {headCell.sortable ? (
                          <TableSortLabel
                            active={orderBy === headCell.id}
                            direction={orderBy === headCell.id ? order : "asc"}
                            onClick={() => handleSortRequest(headCell.id)}
                          >
                            {headCell.label}
                          </TableSortLabel>
                        ) : (
                          headCell.label
                        )}
                      </TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {sortedWebsites.map((site) => (
                    <TableRow
                      key={site.id}
                      hover
                      onClick={() => handleOpenActionModal(site)}
                      onContextMenu={(e) => handleContextMenu(e, site)}
                      sx={{
                        "&:hover": {
                          backgroundColor: theme.palette.action.hover,
                        },
                      }}
                    >
                      <TableCell
                        sx={{
                          maxWidth: 200,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        <Tooltip title={site.url}>
                          <Typography variant="body2">{site.url}</Typography>
                        </Tooltip>
                      </TableCell>
                      <TableCell>{getProjectName(site.project_id)}</TableCell>
                      <TableCell
                        sx={{
                          maxWidth: 150,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        <Tooltip title={site.sitemap || ""}>
                          <Typography variant="body2">
                            {site.sitemap || "-"}
                          </Typography>
                        </Tooltip>
                      </TableCell>
                      <TableCell>
                        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
                          <WebsiteStatusChip status={site.status} />
                          {getWpSiteForWebsite(site.id) && (
                            <Chip
                              label="WP"
                              color="success"
                              size="small"
                              icon={<AutoAwesomeIcon />}
                            />
                          )}
                        </Box>
                      </TableCell>
                      <TableCell align="right">
                        {site.document_count ?? "N/A"}
                      </TableCell>
                      <TableCell>{formatDate(site.last_crawled)}</TableCell>
                      <TableCell>
                        <Box sx={{ display: 'flex', gap: 0.5 }}>
                          <Button
                            size="small"
                            variant="outlined"
                            startIcon={<TravelExploreIcon />}
                            disabled={crawlingIds.has(site.id)}
                            onClick={(e) => handleCrawlWebsite(site.id, e)}
                          >
                            {crawlingIds.has(site.id) ? "..." : "Crawl"}
                          </Button>
                          {getWpSiteForWebsite(site.id) && (
                            <Button
                              size="small"
                              variant="outlined"
                              startIcon={<AutoAwesomeIcon />}
                              onClick={(e) => {
                                e.stopPropagation();
                                handleManageWpPages(site.id);
                              }}
                            >
                              Pages
                            </Button>
                          )}
                        </Box>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
            {sortedWebsites.length > 0 && (
              <Typography
                variant="caption"
                display="block"
                sx={{
                  textAlign: "right",
                  p: 1,
                  color: "text.secondary",
                  borderTop: 1,
                  borderColor: "divider",
                }}
              >
                Total Websites: {sortedWebsites.length}
              </Typography>
            )}
          </Paper>
        )}

      </Box>

      <EntityContextMenu
        anchorPosition={contextMenu}
        onClose={() => { setContextMenu(null); setContextItem(null); }}
        actions={contextItem ? [
          { label: 'Edit', onClick: () => handleOpenActionModal(contextItem) },
          { label: 'Crawl', onClick: () => handleCrawlWebsite(contextItem.id) },
          { label: 'Delete', onClick: () => handleDeleteWebsite(contextItem.id, contextItem.url), color: 'error.main' },
          { label: 'Files', onClick: () => navigate(`/documents?website_id=${contextItem.id}`), dividerBefore: true },
          { label: 'Schedule Task', onClick: () => navigate(`/tasks?website_id=${contextItem.id}`) },
        ] : [
          { label: 'New Website', icon: <AddIcon fontSize="small" />, onClick: () => handleOpenActionModal(null) },
        ]}
      />

      {actionModalOpen && (
        <WebsiteActionModal
          open={actionModalOpen}
          onClose={handleCloseActionModal}
          websiteData={currentWebsiteForModal}
          onSave={handleSaveWebsite}
          onDelete={handleDeleteWebsite}
          isSaving={isModalSaving}
        />
      )}
      </PageLayout>
    </ProjectStateErrorBoundary>
  );
};

export default WebsitesPage;
