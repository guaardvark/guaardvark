// frontend/src/pages/WordPressSitesPage.jsx
// WordPress Sites Management Page
// Manages WordPress site registrations and connections

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
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Chip,
  Stack,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import EditIcon from "@mui/icons-material/Edit";
import DeleteIcon from "@mui/icons-material/Delete";
import RefreshIcon from "@mui/icons-material/Refresh";
import * as wordpressService from "../api/wordpressService";
import { getClients, getProjects } from "../api";
import WordPressSiteModal from "../components/modals/WordPressSiteModal";
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import { ContextualLoader } from "../components/common/LoadingStates";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

import { getComparator, stableSort } from "../utils/sortUtils";

const formatDate = (dateString) => {
  if (!dateString) return "-";
  try {
    return new Date(dateString).toLocaleString();
  } catch (e) {
    return dateString;
  }
};

const StatusChip = ({ status }) => {
  if (!status) return null;
  const normalized = status.toLowerCase();
  let color = "default";
  switch (normalized) {
    case "active":
      color = "success";
      break;
    case "error":
    case "inactive":
      color = "error";
      break;
    default:
      color = "warning";
  }
  return (
    <Chip label={status} color={color} size="small" sx={{ textTransform: "capitalize" }} />
  );
};

function WordPressSitesPage() {
  const { activeModel } = useStatus();

  const [sites, setSites] = useState([]);
  const [clients, setClients] = useState([]);
  const [projects, setProjects] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState({
    open: false,
    message: "",
    severity: "info",
  });

  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [testDialogOpen, setTestDialogOpen] = useState(false);
  const [currentSite, setCurrentSite] = useState(null);
  const [isTesting, setIsTesting] = useState(false);

  const [viewMode, setViewMode] = useState("card");
  const [order, setOrder] = useState("asc");
  const [orderBy, setOrderBy] = useState("url");

  const fetchSites = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await wordpressService.getWordPressSites();
      if (response.success && response.data) {
        setSites(response.data);
      } else {
        throw new Error(response.error || "Failed to fetch WordPress sites");
      }
    } catch (err) {
      console.error("Error fetching WordPress sites:", err);
      setError(err.message || "Failed to load WordPress sites");
      setFeedback({
        open: true,
        message: err.message || "Failed to load WordPress sites",
        severity: "error",
      });
    } finally {
      setIsLoading(false);
    }
  }, []);

  const fetchClientsAndProjects = useCallback(async () => {
    try {
      const [clientsData, projectsData] = await Promise.all([
        getClients(),
        getProjects(),
      ]);
      if (Array.isArray(clientsData)) setClients(clientsData);
      if (Array.isArray(projectsData)) setProjects(projectsData);
    } catch (err) {
      console.error("Error fetching clients/projects:", err);
    }
  }, []);

  useEffect(() => {
    fetchSites();
    fetchClientsAndProjects();
  }, [fetchSites, fetchClientsAndProjects]);

  const handleCreate = () => {
    setCurrentSite(null);
    setEditDialogOpen(true);
  };

  const handleEdit = (site) => {
    setCurrentSite(site);
    setEditDialogOpen(true);
  };

  const handleDelete = (site) => {
    setCurrentSite(site);
    setDeleteDialogOpen(true);
  };

  const handleTestConnection = async (site) => {
    setCurrentSite(site);
    setTestDialogOpen(true);
    setIsTesting(true);
    try {
      const response = await wordpressService.testWordPressConnection(site.id);
      if (response.success) {
        setFeedback({
          open: true,
          message: "Connection test successful!",
          severity: "success",
        });
        fetchSites(); // Refresh to get updated status
      } else {
        setFeedback({
          open: true,
          message: `Connection test failed: ${response.error || response.message}`,
          severity: "error",
        });
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: `Connection test error: ${err.message}`,
        severity: "error",
      });
    } finally {
      setIsTesting(false);
      setTimeout(() => setTestDialogOpen(false), 2000);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!currentSite) return;
    try {
      const response = await wordpressService.deleteWordPressSite(currentSite.id);
      if (response.success) {
        setFeedback({
          open: true,
          message: "WordPress site deleted successfully",
          severity: "success",
        });
        fetchSites();
      } else {
        throw new Error(response.error || "Failed to delete site");
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || "Failed to delete WordPress site",
        severity: "error",
      });
    } finally {
      setDeleteDialogOpen(false);
      setCurrentSite(null);
    }
  };

  const handleSave = async (siteData) => {
    try {
      let response;
      if (currentSite) {
        response = await wordpressService.updateWordPressSite(currentSite.id, siteData);
      } else {
        response = await wordpressService.registerWordPressSite(siteData);
      }

      if (response.success) {
        setFeedback({
          open: true,
          message: currentSite
            ? "WordPress site updated successfully"
            : "WordPress site registered successfully",
          severity: "success",
        });
        fetchSites();
        setEditDialogOpen(false);
        setCurrentSite(null);
      } else {
        throw new Error(response.error || "Failed to save site");
      }
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || "Failed to save WordPress site",
        severity: "error",
      });
    }
  };

  const sortedSites = useMemo(() => {
    return stableSort(sites, getComparator(order, orderBy));
  }, [sites, order, orderBy]);

  const handleRequestSort = (property) => {
    const isAsc = orderBy === property && order === "asc";
    setOrder(isAsc ? "desc" : "asc");
    setOrderBy(property);
  };

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
        }}
      >
        <ContextualLoader loading message="Loading WordPress sites..." showProgress={false} inline />
      </Box>
    );
  }

  return (
    <PageLayout
      title="WordPress Sites"
      variant="standard"
      viewToggle={{
        mode: viewMode,
        onToggle: (val) => setViewMode(val),
      }}
      actions={
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={handleCreate}
          size="small"
        >
          Register Site
        </Button>
      }
      modelStatus={!!activeModel}
      activeModel={activeModel}
    >
      {error && (
        <AlertSnackbar severity="error" sx={{ mb: 2 }}>
          {error}
        </AlertSnackbar>
      )}

      {/* Card View */}
      {viewMode === "card" && (
        <Grid container spacing={2}>
          {sortedSites.map((site) => (
            <Grid item xs={12} sm={6} md={4} key={site.id}>
              <Card
                sx={{
                  height: "100%",
                  display: "flex",
                  flexDirection: "column",
                  "&:hover": { boxShadow: 6 },
                }}
              >
                <CardActionArea onClick={() => handleEdit(site)} sx={{ flexGrow: 1 }}>
                  <CardContent>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 1 }}>
                      <Typography variant="h6" noWrap sx={{ flex: 1 }}>
                        {site.site_name || site.url}
                      </Typography>
                      <StatusChip status={site.status} />
                    </Box>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                      {site.url}
                    </Typography>
                    {site.client && (
                      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                        Client: {site.client.name}
                      </Typography>
                    )}
                    {site.last_pull_at && (
                      <Typography variant="caption" color="text.secondary">
                        Last pulled: {formatDate(site.last_pull_at)}
                      </Typography>
                    )}
                    <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                      Pages: {site.page_count || 0}
                    </Typography>
                  </CardContent>
                </CardActionArea>
                <Box sx={{ p: 1, display: "flex", justifyContent: "flex-end", gap: 1 }}>
                  <Tooltip title="Test Connection">
                    <IconButton size="small" onClick={() => handleTestConnection(site)}>
                      <RefreshIcon />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Edit">
                    <IconButton size="small" onClick={() => handleEdit(site)}>
                      <EditIcon />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Delete">
                    <IconButton size="small" onClick={() => handleDelete(site)} color="error">
                      <DeleteIcon />
                    </IconButton>
                  </Tooltip>
                </Box>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      {/* Table View */}
      {viewMode === "table" && (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>
                  <TableSortLabel
                    active={orderBy === "url"}
                    direction={orderBy === "url" ? order : "asc"}
                    onClick={() => handleRequestSort("url")}
                  >
                    URL
                  </TableSortLabel>
                </TableCell>
                <TableCell>
                  <TableSortLabel
                    active={orderBy === "site_name"}
                    direction={orderBy === "site_name" ? order : "asc"}
                    onClick={() => handleRequestSort("site_name")}
                  >
                    Name
                  </TableSortLabel>
                </TableCell>
                <TableCell>Client</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Pages</TableCell>
                <TableCell>Last Pull</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {sortedSites.map((site) => (
                <TableRow key={site.id} hover>
                  <TableCell>{site.url}</TableCell>
                  <TableCell>{site.site_name || "-"}</TableCell>
                  <TableCell>{site.client?.name || "-"}</TableCell>
                  <TableCell>
                    <StatusChip status={site.status} />
                  </TableCell>
                  <TableCell>{site.page_count || 0}</TableCell>
                  <TableCell>{formatDate(site.last_pull_at)}</TableCell>
                  <TableCell align="right">
                    <Stack direction="row" spacing={1} justifyContent="flex-end">
                      <Tooltip title="Test Connection">
                        <IconButton size="small" onClick={() => handleTestConnection(site)}>
                          <RefreshIcon />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Edit">
                        <IconButton size="small" onClick={() => handleEdit(site)}>
                          <EditIcon />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton size="small" onClick={() => handleDelete(site)} color="error">
                          <DeleteIcon />
                        </IconButton>
                      </Tooltip>
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {sites.length === 0 && !isLoading && (
        <Box sx={{ textAlign: "center", py: 8 }}>
          <Typography variant="h6" color="text.secondary" gutterBottom>
            No WordPress sites registered
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Register a WordPress site to start pulling and processing content
          </Typography>
          <Button variant="contained" startIcon={<AddIcon />} onClick={handleCreate}>
            Register First Site
          </Button>
        </Box>
      )}

      {/* Modals */}
      <WordPressSiteModal
        open={editDialogOpen}
        onClose={() => {
          setEditDialogOpen(false);
          setCurrentSite(null);
        }}
        site={currentSite}
        clients={clients}
        projects={projects}
        onSave={handleSave}
      />

      {/* Delete Confirmation Dialog */}
      {deleteDialogOpen && currentSite && (
        <Box
          sx={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            bgcolor: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1300,
          }}
          onClick={() => setDeleteDialogOpen(false)}
        >
          <Paper
            sx={{ p: 3, maxWidth: 400 }}
            onClick={(e) => e.stopPropagation()}
          >
            <Typography variant="h6" gutterBottom>
              Delete WordPress Site?
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Are you sure you want to delete {currentSite.url}? This action cannot be undone.
            </Typography>
            <Stack direction="row" spacing={2} justifyContent="flex-end">
              <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
              <Button variant="contained" color="error" onClick={handleDeleteConfirm}>
                Delete
              </Button>
            </Stack>
          </Paper>
        </Box>
      )}

      {/* Test Connection Feedback */}
      {testDialogOpen && (
        <Snackbar
          open={testDialogOpen}
          autoHideDuration={isTesting ? null : 3000}
          anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        >
          <AlertSnackbar severity={isTesting ? "info" : "success"}>
            {isTesting ? "Testing connection..." : "Connection test completed"}
          </AlertSnackbar>
        </Snackbar>
      )}

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

export default WordPressSitesPage;

