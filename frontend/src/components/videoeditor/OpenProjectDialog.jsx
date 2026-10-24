// frontend/src/components/videoeditor/OpenProjectDialog.jsx
//
// "Open project" gallery for the Video Editor named-projects feature.
// When `open` is true it fetches listProjects() and shows a searchable grid
// of project cards (poster thumbnail or placeholder, name, edited date,
// clip-count chip). Clicking a card opens it; a per-card trash button
// deletes with an inline confirm step (no window.confirm). Search filters
// by name, case-insensitive. The card matching currentId is highlighted.
import React, { useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Grid,
  Card,
  CardActionArea,
  CardMedia,
  CardContent,
  Typography,
  TextField,
  InputAdornment,
  Chip,
  IconButton,
  Tooltip,
  CircularProgress,
  Stack,
  Alert,
} from "@mui/material";
import {
  MovieFilter as PosterPlaceholderIcon,
  Search as SearchIcon,
  Delete as DeleteIcon,
  Check as ConfirmIcon,
  Close as CancelIcon,
} from "@mui/icons-material";
import {
  listProjects,
  deleteProject,
  getVideoEditorErrorMessage,
} from "../../api/videoEditorService";
import { useUiTranslation, translate, uiLocale } from "../../i18n";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

const posterUrl = (posterDocumentId) =>
  posterDocumentId != null
    ? `${API_BASE}/files/document/${posterDocumentId}/download`
    : null;

// Short, friendly "edited" label. Falls back to a locale date for older edits.
const formatEdited = (value) => {
  if (!value) return translate("videoEditor.openProject.never");
  const then = new Date(value);
  if (Number.isNaN(then.getTime())) return translate("videoEditor.openProject.unknown");
  const diffMs = Date.now() - then.getTime();
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return translate("videoEditor.openProject.justNow");
  const min = Math.floor(sec / 60);
  if (min < 60) return translate("videoEditor.openProject.minutesAgo", { minutes: min });
  const hr = Math.floor(min / 60);
  if (hr < 24) return translate("videoEditor.openProject.hoursAgo", { hours: hr });
  const days = Math.floor(hr / 24);
  if (days < 7) return translate("videoEditor.openProject.daysAgo", { days });
  return then.toLocaleDateString(uiLocale());
};

const OpenProjectDialog = ({ open, onClose, onOpenProject, currentId }) => {
  const { t } = useUiTranslation();
  const [loading, setLoading] = useState(false);
  const [projects, setProjects] = useState([]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState(null);
  // id of the project pending delete-confirm, or null
  const [confirmingId, setConfirmingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listProjects();
      setProjects(Array.isArray(data?.projects) ? data.projects : []);
    } catch (err) {
      setError(getVideoEditorErrorMessage(err, t("videoEditor.openProject.listFailed")));
      setProjects([]);
    } finally {
      setLoading(false);
    }
  };

  // Fetch each time the dialog opens; reset transient UI state on close.
  useEffect(() => {
    if (open) {
      setSearch("");
      setConfirmingId(null);
      setDeletingId(null);
      refresh();
    }
  }, [open]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return projects;
    return projects.filter((p) => (p?.name || "").toLowerCase().includes(needle));
  }, [projects, search]);

  const handleOpen = (id) => {
    onOpenProject?.(id);
    onClose?.();
  };

  const handleDelete = async (id) => {
    setDeletingId(id);
    setError(null);
    try {
      await deleteProject(id);
      setConfirmingId(null);
      await refresh();
    } catch (err) {
      setError(getVideoEditorErrorMessage(err, t("videoEditor.openProject.deleteFailed")));
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>{t("videoEditor.openProject.title")}</DialogTitle>
      <DialogContent dividers>
        <TextField
          fullWidth
          size="small"
          placeholder={t("videoEditor.openProject.search")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ mb: 2 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
        />

        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
            <CircularProgress />
          </Box>
        ) : filtered.length === 0 ? (
          <Box sx={{ textAlign: "center", py: 6 }}>
            <PosterPlaceholderIcon sx={{ fontSize: 48, color: "text.disabled", mb: 1 }} />
            <Typography variant="body2" color="text.secondary">
              {projects.length === 0
                ? t("videoEditor.openProject.none")
                : t("videoEditor.openProject.noMatch")}
            </Typography>
          </Box>
        ) : (
          <Grid container spacing={2}>
            {filtered.map((project) => {
              const isCurrent = project.id === currentId;
              const isConfirming = confirmingId === project.id;
              const isDeleting = deletingId === project.id;
              const poster = posterUrl(project.posterDocumentId);
              return (
                <Grid item xs={12} sm={6} md={4} key={project.id}>
                  <Card
                    elevation={0}
                    sx={{
                      position: "relative",
                      border: 2,
                      borderColor: isCurrent ? "primary.main" : "divider",
                      borderRadius: 1.5,
                      overflow: "hidden",
                    }}
                  >
                    <CardActionArea
                      onClick={() => handleOpen(project.id)}
                      disabled={isDeleting}
                    >
                      {poster ? (
                        <CardMedia
                          component="img"
                          height={120}
                          image={poster}
                          alt={project.name}
                          sx={{ objectFit: "cover", bgcolor: "action.hover" }}
                        />
                      ) : (
                        <Box
                          sx={{
                            height: 120,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            bgcolor: "action.hover",
                          }}
                        >
                          <PosterPlaceholderIcon
                            sx={{ fontSize: 40, color: "text.disabled" }}
                          />
                        </Box>
                      )}
                      <CardContent sx={{ py: 1, px: 1.5 }}>
                        <Typography
                          variant="subtitle2"
                          fontWeight={600}
                          noWrap
                          title={project.name}
                        >
                          {project.name}
                          {isCurrent && (
                            <Typography
                              component="span"
                              variant="caption"
                              color="primary"
                              sx={{ ml: 0.75 }}
                            >
                              {t("videoEditor.openProject.current")}
                            </Typography>
                          )}
                        </Typography>
                        <Stack
                          direction="row"
                          alignItems="center"
                          justifyContent="space-between"
                          spacing={1}
                          sx={{ mt: 0.5 }}
                        >
                          <Typography variant="caption" color="text.secondary" noWrap>
                            {t("videoEditor.openProject.edited", { when: formatEdited(project.updatedAt) })}
                          </Typography>
                          <Chip
                            size="small"
                            label={t("videoEditor.openProject.clipCount", { count: project.clipCount ?? 0 })}
                            sx={{ height: 20, fontSize: "0.7rem" }}
                          />
                        </Stack>
                      </CardContent>
                    </CardActionArea>

                    {/* Delete control: trash → inline confirm/cancel */}
                    <Box sx={{ position: "absolute", top: 4, right: 4 }}>
                      {isConfirming ? (
                        <Stack direction="row" spacing={0.25}>
                          <Tooltip title={t("videoEditor.openProject.confirmDelete")}>
                            <span>
                              <IconButton
                                size="small"
                                color="error"
                                disabled={isDeleting}
                                onClick={() => handleDelete(project.id)}
                                sx={{ bgcolor: "background.paper" }}
                                aria-label={t("videoEditor.openProject.confirmDelete")}
                              >
                                {isDeleting ? (
                                  <CircularProgress size={16} />
                                ) : (
                                  <ConfirmIcon fontSize="small" />
                                )}
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Tooltip title={t("videoEditor.openProject.cancel")}>
                            <span>
                              <IconButton
                                size="small"
                                disabled={isDeleting}
                                onClick={() => setConfirmingId(null)}
                                sx={{ bgcolor: "background.paper" }}
                                aria-label={t("videoEditor.openProject.cancelDelete")}
                              >
                                <CancelIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                        </Stack>
                      ) : (
                        <Tooltip title={t("videoEditor.openProject.delete")}>
                          <IconButton
                            size="small"
                            onClick={() => setConfirmingId(project.id)}
                            sx={{ bgcolor: "background.paper" }}
                            aria-label={t("videoEditor.openProject.delete")}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      )}
                    </Box>
                  </Card>
                </Grid>
              );
            })}
          </Grid>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} color="inherit">
          {t("videoEditor.openProject.close")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default OpenProjectDialog;
