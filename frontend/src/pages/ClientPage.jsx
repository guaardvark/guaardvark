// frontend/src/pages/ClientPage.jsx
// Version 1.12: Aligned UI with Projects/Websites pages (header, view toggle, table view, styling).
// - Added card/table view toggle.
// - Implemented table view with sorting and actions.
// - Displayed notes excerpt in card and table views.
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from Dean (user/owner).

import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  Box,
  Typography,
  Button,
  Alert as MuiAlert,
  Snackbar,
  Grid,
  Card,
  CardActionArea,
  CardContent,
  Tooltip,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import EditIcon from "@mui/icons-material/Edit"; // For table actions
import CloseIcon from "@mui/icons-material/Close"; // For table actions
import LinkIcon from "@mui/icons-material/Link"; // For table actions
import { useTheme } from "@mui/material/styles";
import { useNavigate, useSearchParams } from "react-router-dom"; // For navigation and modal linking

import * as apiService from "../api";
import ClientActionModal from "../components/modals/ClientActionModal";
import LinkingModal from "../components/modals/LinkingModal";
import PageLayout from "../components/layout/PageLayout";
import EntityContextMenu from "../components/common/EntityContextMenu";
import { useStatus } from "../contexts/StatusContext";
import { getLogoUrl } from "../config/logoConfig";
import { ContextualLoader } from "../components/common/LoadingStates";

const logger = {
  info: (message, ...args) =>
    console.log(`[ClientPage INFO] ${message}`, ...args),
  error: (message, ...args) =>
    console.error(`[ClientPage ERROR] ${message}`, ...args),
};

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

// Sorting functions
import { descendingComparator, getComparator, stableSort } from "../utils/sortUtils";

const formatDate = (dateString) => {
  if (!dateString) return "-";
  try {
    return new Date(dateString).toLocaleDateString();
  } catch (e) {
    return dateString;
  }
};

const ClientPage = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { activeModel } = useStatus();

  const [clients, setClients] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState({
    open: false,
    message: "",
    severity: "info",
  });

  const [actionModalOpen, setActionModalOpen] = useState(false);
  const [selectedClientForModal, setSelectedClientForModal] = useState(null);
  const [isModalSaving, setIsModalSaving] = useState(false);
  const [isLinkingModalOpen, setIsLinkingModalOpen] = useState(false);

  const [viewMode, setViewMode] = useState("card"); // 'card' or 'table'
  const [order, setOrder] = useState("asc");
  const [orderBy, setOrderBy] = useState("name");

  // Context menu state
  const [contextMenu, setContextMenu] = useState(null);
  const [contextItem, setContextItem] = useState(null);

  const handleContextMenu = (e, client = null) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ top: e.clientY, left: e.clientX });
    setContextItem(client);
  };

  const fetchClients = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await apiService.getClients();
      if (data && data.error) throw new Error(data.error.message || data.error);
      if (data && data.length > 0) {
        console.log('ClientPage - fetchClients received FULL first client:', data[0]);
      }
      setClients(Array.isArray(data) ? data : []);
    } catch (err) {
      const errorMessage =
        err.data?.error || err.message || "Failed to fetch clients.";
      setError(errorMessage);
      logger.error("Error fetching clients:", err);
      setFeedback({
        open: true,
        message: `Error loading clients: ${errorMessage}`,
        severity: "error",
      });
      setClients([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchClients();
  }, [fetchClients]);

  useEffect(() => {
    const idParam = searchParams.get("clientId");
    if (idParam && clients.length > 0) {
      const client = clients.find((c) => String(c.id) === idParam);
      if (client) {
        setSelectedClientForModal(client);
        setActionModalOpen(true);
        const params = new URLSearchParams(searchParams);
        params.delete("clientId");
        setSearchParams(params, { replace: true });
      }
    }
  }, [clients, searchParams, setSearchParams]);

  const handleOpenActionModal = (client = null) => {
    setSelectedClientForModal(client);
    setActionModalOpen(true);
    if (isLinkingModalOpen) setIsLinkingModalOpen(false);
  };

  const handleCloseActionModal = () => {
    if (isModalSaving) return;
    setActionModalOpen(false);
    // Delay clearing selectedClient to allow modal to fade out smoothly
    setTimeout(() => setSelectedClientForModal(null), 150);
    const params = new URLSearchParams(searchParams);
    params.delete("clientId");
    setSearchParams(params, { replace: true });
  };

  const handleSaveClient = async (clientDataFromModal, logoFile) => {
    setIsModalSaving(true);
    setFeedback({ open: false, message: "" });
    const action = clientDataFromModal.id ? "update" : "create";
    const payload = {
      name: clientDataFromModal.name,
      notes: clientDataFromModal.notes || null,
      email: clientDataFromModal.email || null,
      phone: clientDataFromModal.phone || null,
      location: clientDataFromModal.location || null,
      // RAG Enhancement fields
      industry: clientDataFromModal.industry || null,
      target_audience: clientDataFromModal.target_audience || null,
      unique_selling_points: clientDataFromModal.unique_selling_points || null,
      competitor_urls: clientDataFromModal.competitor_urls || [],
      brand_voice_examples: clientDataFromModal.brand_voice_examples || null,
      keywords: clientDataFromModal.keywords || [],
      content_goals: clientDataFromModal.content_goals || null,
      regulatory_constraints: clientDataFromModal.regulatory_constraints || null,
      geographic_coverage: clientDataFromModal.geographic_coverage || null,
    };

    try {
      let result;
      if (action === "update") {
        result = await apiService.updateClient(clientDataFromModal.id, payload);
      } else {
        result = await apiService.createClient(payload);
      }
      if (logoFile) {
        await apiService.uploadClientLogo(
          result.id || clientDataFromModal.id,
          logoFile,
        );
      }
      if (result && result.error)
        throw new Error(result.error.message || result.error);
      setFeedback({
        open: true,
        message: `Client ${action}d successfully!`,
        severity: "success",
      });
      handleCloseActionModal();
      await fetchClients();
    } catch (err) {
      const errorMessage =
        err.data?.error || err.message || `Error ${action}ing client.`;
      logger.error(`Error ${action}ing client:`, err);
      setFeedback({ open: true, message: errorMessage, severity: "error" });
    } finally {
      setIsModalSaving(false);
    }
  };

  const handleDeleteClient = async (clientId, clientName, event) => {
    if (event) event.stopPropagation(); // Prevent card/row click
    if (
      !window.confirm(
        `Are you sure you want to delete client "${clientName || "this client"}" (ID: ${clientId})? This may affect associated projects.`,
      )
    )
      return;

    setIsModalSaving(true); // Use general saving flag or a specific deleting flag
    setFeedback({ open: false, message: "" });
    try {
      await apiService.deleteClient(clientId);
      setFeedback({
        open: true,
        message: `Client "${clientName}" deleted successfully!`,
        severity: "info",
      });
      if (actionModalOpen && selectedClientForModal?.id === clientId) {
        handleCloseActionModal();
      }
      await fetchClients();
    } catch (err) {
      const errorMessage =
        err.data?.error || err.message || "Error deleting client.";
      logger.error("Error deleting client:", err);
      setFeedback({ open: true, message: errorMessage, severity: "error" });
    } finally {
      setIsModalSaving(false);
    }
  };

  const handleOpenLinkerModal = (client, event) => {
    if (event) event.stopPropagation(); // Prevent card/row click
    logger.info(
      "ClientPage: handleOpenLinkerModal called with client:",
      client,
    );
    if (!client?.id) {
      setFeedback({
        open: true,
        message: "Client data is missing. Cannot open linking modal.",
        severity: "warning",
      });
      logger.error("Client data or ID missing for LinkingModal", client);
      return;
    }
    setSelectedClientForModal(client); // Set the client for whom we are linking projects
    if (actionModalOpen) setActionModalOpen(false); // Close action modal if it was open
    setIsLinkingModalOpen(true);
  };

  const handleCloseLinkingModal = () => setIsLinkingModalOpen(false);

  const handleLinkingSuccess = () => {
    fetchClients();
    setFeedback({
      open: true,
      message: "Project links updated successfully!",
      severity: "success",
    });
    setIsLinkingModalOpen(false);
  };

  const handleCloseFeedback = (event, reason) => {
    if (reason === "clickaway") return;
    setFeedback((prev) => ({ ...prev, open: false }));
  };

  const getProjectsForLinking = useCallback(async (searchTerm = "") => {
    /* ... (same as v1.11) ... */
    try {
      const queryParams = searchTerm ? { name: searchTerm } : {};
      const projects = await apiService.getProjects(queryParams);
      if (projects && projects.error)
        throw new Error(projects.error.message || projects.error);
      return Array.isArray(projects)
        ? projects.map((p) => ({ id: p.id, name: p.name }))
        : [];
    } catch (error) {
      logger.error("Error fetching projects for linking:", error);
      setFeedback({
        open: true,
        message: `Error fetching projects: ${error.message}`,
        severity: "error",
      });
      return [];
    }
  }, []);

  const updateClientProjectLinks = useCallback(
    async (
      primaryEntityType,
      primaryEntityId,
      linkableEntityType,
      targetProjectIdsToLink,
    ) => {
      /* ... (same as v1.11) ... */
      const actualClientId = primaryEntityId;
      const actualProjectIdsToLink = targetProjectIdsToLink;
      logger.info(
        `Updating links for ${primaryEntityType} ${actualClientId} with ${linkableEntityType} IDs:`,
        actualProjectIdsToLink,
      );
      setIsModalSaving(true);
      try {
        const currentlyLinkedProjects =
          await apiService.getProjectsForClient(actualClientId);
        if (
          currentlyLinkedProjects &&
          currentlyLinkedProjects.error &&
          typeof currentlyLinkedProjects.error === "string" &&
          currentlyLinkedProjects.error.includes("Client ID is required")
        ) {
          throw new Error(
            `Invalid Client ID (${actualClientId}) received in updateClientProjectLinks.`,
          );
        }
        const currentlyLinkedIds = Array.isArray(currentlyLinkedProjects)
          ? currentlyLinkedProjects.map((p) => p.id)
          : [];
        const toLinkPromises = actualProjectIdsToLink
          .filter((id) => !currentlyLinkedIds.includes(id))
          .map((projectId) =>
            apiService.updateProject(projectId, { client_id: actualClientId }),
          );
        const toUnlinkPromises = currentlyLinkedIds
          .filter((id) => !actualProjectIdsToLink.includes(id))
          .map((projectId) =>
            apiService.updateProject(projectId, { client_id: null }),
          );
        await Promise.all([...toLinkPromises, ...toUnlinkPromises]);
      } catch (error) {
        logger.error(
          `Error updating ${linkableEntityType} links for ${primaryEntityType} ${actualClientId}:`,
          error,
        );
        setFeedback({
          open: true,
          message: `Error updating links: ${error.message || "An unknown error occurred."}`,
          severity: "error",
        });
        throw error;
      } finally {
        setIsModalSaving(false);
      }
    },
    [],
  );

  const handleSortRequest = (property) => {
    const isAsc = orderBy === property && order === "asc";
    setOrder(isAsc ? "desc" : "asc");
    setOrderBy(property);
  };

  const sortedClients = useMemo(() => {
    return stableSort(clients, getComparator(order, orderBy));
  }, [clients, order, orderBy]);

  const headCells = [
    { id: "logo", label: "Logo", sortable: false },
    { id: "name", label: "Client Name", sortable: true },
    { id: "email", label: "Email", sortable: true },
    { id: "phone", label: "Phone", sortable: false },
    {
      id: "project_count",
      label: "Projects #",
      sortable: true,
      align: "right",
    },
    { id: "notes", label: "Notes", sortable: false },
    { id: "updated_at", label: "Last Updated", sortable: true },
    { id: "actions", label: "Actions", sortable: false, align: "right" },
  ];

  return (
    <PageLayout
      title="Clients"
      variant="standard"
      viewToggle={{ mode: viewMode, onToggle: (val) => setViewMode(val) }}
      actions={
        <Button
          variant="contained"
          size="small"
          startIcon={<AddIcon />}
          onClick={() => handleOpenActionModal(null)}
          disabled={
            isLoading ||
            actionModalOpen ||
            isLinkingModalOpen ||
            isModalSaving
          }
        >
          New
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

        {error && !isLoading && (
          <MuiAlert
            severity="error"
            sx={{ my: 2 }}
            onClose={() => setError(null)}
          >
            {error}
          </MuiAlert>
        )}
        {isLoading && (
          <ContextualLoader loading message="Loading clients..." showProgress={false} inline />
        )}

        {!isLoading && sortedClients.length === 0 && !error && (
          <Typography sx={{ mt: 2, fontStyle: "italic", textAlign: "center" }}>
            No clients found. Add one to get started!
          </Typography>
        )}

        {!isLoading && sortedClients.length > 0 && viewMode === "card" && (
          <Grid container spacing={2}>
            {sortedClients.map((client) => (
              <Grid item xs={12} sm={6} md={4} lg={3} key={client.id}>
                <Card
                  onContextMenu={(e) => handleContextMenu(e, client)}
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
                    onClick={() => handleOpenActionModal(client)}
                    sx={{
                      flexGrow: 1,
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "stretch",
                      p: 1.5,
                    }}
                  >
                    <CardContent sx={{ flexGrow: 1, p: 0 }}>
                      <Grid container spacing={1} alignItems="flex-start">
                        <Grid item xs={9}>
                          <Typography
                            variant="h6"
                            component="div"
                            gutterBottom
                            noWrap
                            title={client.name}
                            sx={{ fontSize: "1rem", fontWeight: "medium" }}
                          >
                            {client.name}
                          </Typography>
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ mb: 0.5 }}
                            noWrap
                            title={client.email || "N/A"}
                          >
                            Email: {client.email || "N/A"}
                          </Typography>
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ mb: 1 }}
                            noWrap
                            title={client.phone || "N/A"}
                          >
                            Phone: {client.phone || "N/A"}
                          </Typography>
                          <Tooltip title={client.notes || "No notes."}>
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              sx={{
                                mb: 1,
                                fontStyle: client.notes ? "normal" : "italic",
                                height: "3em",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                display: "-webkit-box",
                                WebkitLineClamp: 2,
                                WebkitBoxOrient: "vertical",
                              }}
                            >
                              {client.notes || "No notes"}
                            </Typography>
                          </Tooltip>
                          <Typography variant="body2" color="text.primary">
                            Projects:{" "}
                            {client.project_count !== undefined
                              ? client.project_count
                              : "N/A"}
                          </Typography>
                        </Grid>
                        <Grid item xs={3} sx={{ textAlign: "right" }}>
                          {client.logo_path && (
                            <img
                              src={getLogoUrl(client.logo_path)}
                              alt={client.name}
                              style={{ maxHeight: 60, maxWidth: "100%" }}
                            />
                          )}
                        </Grid>
                      </Grid>
                    </CardContent>
                  </CardActionArea>
                </Card>
              </Grid>
            ))}
          </Grid>
        )}

        {!isLoading && sortedClients.length > 0 && viewMode === "table" && (
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
                  {sortedClients.map((client) => (
                    <TableRow
                      key={client.id}
                      hover
                      onClick={() => handleOpenActionModal(client)} // Row click opens edit modal
                      onContextMenu={(e) => handleContextMenu(e, client)}
                      sx={{
                        "&:hover": {
                          backgroundColor: theme.palette.action.hover,
                        },
                      }}
                    >
                      <TableCell>
                        {client.logo_path ? (
                          <img
                            src={getLogoUrl(client.logo_path)}
                            alt={client.name}
                            style={{
                              width: 32,
                              height: 32,
                              objectFit: "cover",
                              borderRadius: 4,
                            }}
                          />
                        ) : (
                          "-"
                        )}
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" noWrap title={client.name}>
                          {client.name}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography
                          variant="body2"
                          noWrap
                          title={client.email || ""}
                        >
                          {client.email || "-"}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography
                          variant="body2"
                          noWrap
                          title={client.phone || ""}
                        >
                          {client.phone || "-"}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        {client.project_count ?? 0}
                      </TableCell>
                      <TableCell sx={{ maxWidth: 200 }}>
                        <Tooltip title={client.notes || ""}>
                          <Typography variant="body2" noWrap>
                            {client.notes || "-"}
                          </Typography>
                        </Tooltip>
                      </TableCell>
                      <TableCell>{formatDate(client.updated_at)}</TableCell>
                      <TableCell align="right" sx={{ pr: 2 }}>
                        <Tooltip title="Link Projects">
                          <IconButton
                            size="small"
                            onClick={(e) => handleOpenLinkerModal(client, e)}
                            disabled={isModalSaving}
                          >
                            <LinkIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Delete Client">
                          <IconButton
                            size="small"
                            onClick={(e) =>
                              handleDeleteClient(client.id, client.name, e)
                            }
                            disabled={isModalSaving}
                          >
                            <CloseIcon
                              fontSize="small"
                              sx={{ color: "text.secondary" }}
                            />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
            {sortedClients.length > 0 && (
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
                Total Clients: {sortedClients.length}
              </Typography>
            )}
          </Paper>
        )}

      {actionModalOpen && (
        <ClientActionModal
          open={actionModalOpen}
          onClose={handleCloseActionModal}
          clientData={selectedClientForModal}
          onSave={handleSaveClient}
          onDelete={(clientId) =>
            handleDeleteClient(clientId, selectedClientForModal?.name)
          } // Pass name for confirm dialog
          onOpenLinker={handleOpenLinkerModal} // Pass the function to open linker from action modal
          isSaving={isModalSaving}
        />
      )}

      </Box>

      <EntityContextMenu
        anchorPosition={contextMenu}
        onClose={() => { setContextMenu(null); setContextItem(null); }}
        actions={contextItem ? [
          { label: 'Edit', onClick: () => handleOpenActionModal(contextItem) },
          { label: 'Delete', onClick: () => handleDeleteClient(contextItem.id, contextItem.name), color: 'error.main' },
          { label: 'Files', onClick: () => navigate(`/documents?client_id=${contextItem.id}`), dividerBefore: true },
          { label: 'Schedule Task', onClick: () => navigate(`/tasks?client_id=${contextItem.id}`) },
        ] : [
          { label: 'New Client', icon: <AddIcon fontSize="small" />, onClick: () => handleOpenActionModal(null) },
        ]}
      />

      {isLinkingModalOpen && selectedClientForModal && (
        <LinkingModal
          open={isLinkingModalOpen}
          onClose={handleCloseLinkingModal}
          primaryEntityType="client"
          primaryEntityId={selectedClientForModal.id}
          primaryEntityName={selectedClientForModal.name}
          onLinksUpdated={handleLinkingSuccess}
          linkableTypesConfig={[
            {
              entityType: "project",
              singularLabel: "Project",
              pluralLabel: "Projects",
              apiServiceFunction: getProjectsForLinking, // Provides list of all projects to link
            },
          ]}
          apiGetLinkedItems={apiService.getCurrentlyLinkedItems} // Fetches projects currently linked to this client
          apiUpdateLinks={updateClientProjectLinks} // Handles linking/unlinking logic
        />
      )}
    </PageLayout>
  );
};

export default ClientPage;
