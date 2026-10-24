// ChatSessionDrawer.jsx — Unified chat session browser
import React, { useEffect, useState, useCallback } from "react";
import {
  Drawer,
  Box,
  Typography,
  List,
  ListItem,
  ListItemButton,
  IconButton,
  Chip,
  TextField,
  InputAdornment,
  CircularProgress,
  Divider,
  Tooltip,
  Alert,
  Button,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import { listChatSessions, deleteChatSession } from "../../api/chatService";
import EntityContextMenu from "../common/EntityContextMenu";
import ConfirmActionDialog from "../settings/ui/ConfirmActionDialog";
import useContextMenu from "../../hooks/useContextMenu";
import { formatUiError } from "../../utils/uiError";
import { useUiTranslation, uiLocale } from "../../i18n";

const DRAWER_WIDTH = 340;

const ChatSessionDrawer = ({
  open,
  onClose,
  projectId,
  currentSessionId,
  onSelectSession,
  onNewChat,
}) => {
  const { t } = useUiTranslation();
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);
  // Why the list could not be loaded; shown instead of "No chats yet".
  const [loadError, setLoadError] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const rowMenu = useContextMenu();

  const loadSessions = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await listChatSessions(projectId, 100);
      setSessions(data.sessions || []);
    } catch (err) {
      console.error("Failed to load sessions:", err);
      setLoadError({ detail: formatUiError(err) });
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (open) {
      loadSessions();
    }
  }, [open, loadSessions]);

  // The chat waiting for the person to confirm its deletion.
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const askDelete = (session, e) => {
    e?.stopPropagation();
    setDeleteError(null);
    setPendingDelete(session);
  };

  const confirmDelete = async () => {
    const sessionId = pendingDelete?.session_id;
    if (!sessionId) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteChatSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
      if (sessionId === currentSessionId) {
        onSelectSession(null);
      }
      setPendingDelete(null);
    } catch (err) {
      console.error("Failed to delete session:", err);
      setDeleteError({ detail: formatUiError(err) });
    } finally {
      setDeleting(false);
    }
  };

  // The session's stored mode, as the list route serves it (the value the
  // /agent and /chat commands set). A list without it is a chat session.
  const isAgentSession = (session) => (session.mode || "chat") === "agent";

  const formatDateTime = (isoString) => {
    if (!isoString) return "";
    const date = new Date(isoString);
    return date.toLocaleDateString(uiLocale(), { month: "short", day: "numeric" }) +
      " " + date.toLocaleTimeString(uiLocale(), { hour: "2-digit", minute: "2-digit" });
  };

  const formatRelative = (isoString) => {
    if (!isoString) return "";
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return t("chat.sessions.justNow");
    if (diffMins < 60) return t("chat.sessions.minutesAgo", { count: diffMins });
    if (diffHours < 24) return t("chat.sessions.hoursAgo", { count: diffHours });
    if (diffDays === 1) return t("chat.sessions.yesterdayRelative");
    if (diffDays < 7) return t("chat.sessions.daysAgo", { count: diffDays });
    return date.toLocaleDateString(uiLocale(), { month: "short", day: "numeric" });
  };

  const formatSize = (chars) => {
    if (!chars) return "";
    if (chars < 1000) return t("chat.sessions.chars", { count: chars });
    if (chars < 100000) return `${(chars / 1000).toFixed(1)}K`;
    return `${(chars / 1000).toFixed(0)}K`;
  };

  const filteredSessions = sessions.filter((s) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      (s.preview || "").toLowerCase().includes(q) ||
      (s.response_preview || "").toLowerCase().includes(q) ||
      (s.session_id || "").toLowerCase().includes(q)
    );
  });

  // Group by last_activity (or created_at fallback) for better ordering
  const groupSessions = (sessions) => {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today - 86400000);
    const weekAgo = new Date(today - 7 * 86400000);

    const groups = { today: [], yesterday: [], thisWeek: [], older: [] };
    sessions.forEach((s) => {
      const d = new Date(s.last_activity || s.created_at);
      if (d >= today) groups.today.push(s);
      else if (d >= yesterday) groups.yesterday.push(s);
      else if (d >= weekAgo) groups.thisWeek.push(s);
      else groups.older.push(s);
    });
    // Sort within each group by last_activity descending
    Object.values(groups).forEach((g) =>
      g.sort((a, b) =>
        new Date(b.last_activity || b.created_at) - new Date(a.last_activity || a.created_at)
      )
    );
    return groups;
  };

  const groups = groupSessions(filteredSessions);

  const renderSession = (session) => {
    const isCurrent = session.session_id === currentSessionId;
    const isAgent = isAgentSession(session);
    const hasResponse = !!session.response_preview;

    return (
      <ListItem
        key={session.session_id}
        disablePadding
        sx={{ px: 1 }}
        onContextMenu={(e) => rowMenu.open(e, session)}
      >
        <ListItemButton
          selected={isCurrent}
          onClick={() => {
            onSelectSession(session.session_id);
            onClose();
          }}
          sx={{
            borderRadius: 1.5,
            mb: 0.5,
            py: 1.25,
            px: 1.5,
            "&.Mui-selected": {
              bgcolor: "action.selected",
              borderLeft: "3px solid",
              borderColor: "primary.main",
            },
          }}
        >
          <Box sx={{ width: "100%", minWidth: 0 }}>
            {/* Row 1: Icon + Preview + Delete */}
            <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1 }}>
              {isAgent ? (
                <SmartToyIcon sx={{ fontSize: 16, mt: 0.4, color: "warning.main", flexShrink: 0 }} />
              ) : (
                <ChatBubbleOutlineIcon sx={{ fontSize: 16, mt: 0.4, color: "text.disabled", flexShrink: 0 }} />
              )}
              <Typography
                variant="body2"
                noWrap
                sx={{
                  flex: 1,
                  fontWeight: isCurrent ? 600 : 400,
                  color: isCurrent ? "text.primary" : "text.secondary",
                }}
              >
                {session.preview || t("chat.sessions.newChat")}
              </Typography>
              <Tooltip title={t("chat.sessions.delete")}>
                <IconButton
                  size="small"
                  onClick={(e) => askDelete(session, e)}
                  sx={{
                    opacity: 0,
                    transition: "opacity 0.15s",
                    ".MuiListItemButton-root:hover &": { opacity: 0.5 },
                    "&:hover": { opacity: "1 !important", color: "error.main" },
                    mt: -0.5,
                    flexShrink: 0,
                    p: 0.25,
                  }}
                >
                  <DeleteOutlineIcon sx={{ fontSize: 15 }} />
                </IconButton>
              </Tooltip>
            </Box>

            {/* Row 2: Assistant response preview */}
            {hasResponse && (
              <Typography
                variant="caption"
                noWrap
                sx={{
                  display: "block",
                  color: "text.secondary",
                  pl: 3,
                  mt: 0.25,
                  fontStyle: "italic",
                }}
              >
                {session.response_preview}
              </Typography>
            )}

            {/* Row 3: Metadata chips */}
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, pl: 3, mt: 0.5, flexWrap: "wrap" }}>
              {/* Date/time */}
              <Tooltip title={formatDateTime(session.last_activity || session.created_at)}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
                  <AccessTimeIcon sx={{ fontSize: 11, color: "text.secondary" }} />
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                    {formatRelative(session.last_activity || session.created_at)}
                  </Typography>
                </Box>
              </Tooltip>

              {/* Message count */}
              <Chip
                label={t("chat.sessions.messageCount", { count: session.message_count })}
                size="small"
                variant="outlined"
                sx={{
                  height: 18,
                  fontSize: "0.65rem",
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />

              {/* Content size */}
              {session.total_chars > 0 && (
                <Chip
                  label={formatSize(session.total_chars)}
                  size="small"
                  variant="outlined"
                  sx={{
                    height: 18,
                    fontSize: "0.65rem",
                    "& .MuiChip-label": { px: 0.5 },
                    borderColor: "divider",
                  }}
                />
              )}

              {/* Agent badge */}
              {isAgent && (
                <Chip
                  label={t("chat.sessions.agent")}
                  size="small"
                  color="warning"
                  sx={{
                    height: 18,
                    fontSize: "0.65rem",
                    "& .MuiChip-label": { px: 0.5 },
                  }}
                />
              )}

              {/* Active indicator */}
              {isCurrent && (
                <Chip
                  label={t("chat.sessions.active")}
                  size="small"
                  color="primary"
                  sx={{
                    height: 18,
                    fontSize: "0.65rem",
                    "& .MuiChip-label": { px: 0.5 },
                  }}
                />
              )}
            </Box>
          </Box>
        </ListItemButton>
      </ListItem>
    );
  };

  const renderGroup = (label, items) => {
    if (items.length === 0) return null;
    return (
      <React.Fragment key={label}>
        <Typography
          variant="overline"
          sx={{
            px: 2.5, pt: 1.5, pb: 0.5,
            display: "block",
            color: "text.secondary",
            letterSpacing: 1,
            fontSize: "0.65rem",
          }}
        >
          {label}
        </Typography>
        {items.map(renderSession)}
      </React.Fragment>
    );
  };

  return (
    <Drawer
      anchor="left"
      open={open}
      onClose={onClose}
      variant="temporary"
      sx={{
        "& .MuiDrawer-paper": {
          width: DRAWER_WIDTH,
          bgcolor: "background.paper",
          borderRight: 1,
          borderColor: "divider",
        },
      }}
    >
      {/* Header */}
      <Box sx={{ p: 2, pb: 1 }}>
        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1.5 }}>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            {t("chat.sessions.title")}
          </Typography>
          <Tooltip title={t("chat.sessions.newChat")}>
            <IconButton
              size="small"
              onClick={() => {
                onNewChat();
                onClose();
              }}
              sx={{
                bgcolor: "action.hover",
                "&:hover": { bgcolor: "primary.main", color: "primary.contrastText" },
              }}
            >
              <AddIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
        <TextField
          size="small"
          placeholder={t("chat.sessions.search")}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          fullWidth
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon sx={{ fontSize: 18, color: "text.disabled" }} />
              </InputAdornment>
            ),
          }}
          sx={{
            "& .MuiOutlinedInput-root": {
              borderRadius: 2,
              fontSize: "0.85rem",
            },
          }}
        />
      </Box>

      <Divider />

      {/* Session List */}
      <Box sx={{ flex: 1, overflowY: "auto", pb: 2 }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={28} />
          </Box>
        ) : loadError ? (
          <Box sx={{ px: 2, py: 2 }}>
            <Alert
              severity="error"
              action={<Button color="inherit" size="small" onClick={loadSessions}>{t("chat.sessions.retry")}</Button>}
            >
              {loadError.detail || t("chat.sessions.loadFailed")}
            </Alert>
          </Box>
        ) : filteredSessions.length === 0 ? (
          <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", py: 4, px: 2 }}>
            <ChatBubbleOutlineIcon sx={{ fontSize: 40, color: "text.disabled", mb: 1 }} />
            <Typography color="text.secondary" variant="body2">
              {searchQuery ? t("chat.sessions.noMatches") : t("chat.sessions.empty")}
            </Typography>
          </Box>
        ) : (
          <List disablePadding sx={{ pt: 0.5 }}>
            {renderGroup(t("chat.sessions.today"), groups.today)}
            {renderGroup(t("chat.sessions.yesterday"), groups.yesterday)}
            {renderGroup(t("chat.sessions.thisWeek"), groups.thisWeek)}
            {renderGroup(t("chat.sessions.older"), groups.older)}
          </List>
        )}
        <EntityContextMenu
          anchorPosition={rowMenu.anchorPosition}
          onClose={rowMenu.close}
          actions={
            rowMenu.payload
              ? [
                  {
                    label: t("chat.sessions.open"),
                    onClick: () => {
                      onSelectSession(rowMenu.payload.session_id);
                      onClose();
                    },
                  },
                  {
                    label: t("chat.sessions.delete"),
                    onClick: () => askDelete(rowMenu.payload),
                    color: "error.main",
                    dividerBefore: true,
                  },
                ]
              : []
          }
        />
      </Box>

      <ConfirmActionDialog
        open={Boolean(pendingDelete)}
        title={t("chat.sessions.deleteTitle")}
        description={
          pendingDelete
            ? t("chat.sessions.deleteDescription", { name: pendingDelete.preview || t("chat.sessions.newChat") })
            : null
        }
        facts={
          pendingDelete
            ? [
                { label: t("chat.sessions.messages"), value: pendingDelete.message_count ?? 0 },
                { label: t("chat.sessions.lastActive"), value: formatDateTime(pendingDelete.last_activity || pendingDelete.created_at) },
              ]
            : []
        }
        confirmLabel={t("chat.sessions.deleteConfirm")}
        busy={deleting}
        extra={
          deleteError ? (
            <Alert severity="error" sx={{ mt: 1 }}>
              {deleteError.detail
                ? t("chat.sessions.deleteFailed", { reason: deleteError.detail })
                : t("chat.sessions.deleteFailedUnknown")}
            </Alert>
          ) : null
        }
        onConfirm={confirmDelete}
        onClose={() => { setPendingDelete(null); setDeleteError(null); }}
      />

      {/* Footer stats */}
      <Divider />
      <Box sx={{ px: 2, py: 1, display: "flex", justifyContent: "space-between" }}>
        <Typography variant="caption" color="text.secondary">
          {t("chat.sessions.chatCount", { count: sessions.length })}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {t("chat.sessions.totalMessages", {
            count: sessions.reduce((sum, s) => sum + (s.message_count || 0), 0),
          })}
        </Typography>
      </Box>
    </Drawer>
  );
};

export default ChatSessionDrawer;
