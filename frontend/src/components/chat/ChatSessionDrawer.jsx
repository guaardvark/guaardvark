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
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import { listChatSessions, deleteChatSession } from "../../api/chatService";

const DRAWER_WIDTH = 340;

const ChatSessionDrawer = ({
  open,
  onClose,
  projectId,
  currentSessionId,
  onSelectSession,
  onNewChat,
}) => {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listChatSessions(projectId, 100);
      setSessions(data.sessions || []);
    } catch (err) {
      console.error("Failed to load sessions:", err);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (open) {
      loadSessions();
    }
  }, [open, loadSessions]);

  const handleDelete = async (sessionId, e) => {
    e.stopPropagation();
    try {
      await deleteChatSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
      if (sessionId === currentSessionId) {
        onSelectSession(null);
      }
    } catch (err) {
      console.error("Failed to delete session:", err);
    }
  };

  const isAgentSession = (session) => {
    const preview = (session.preview || "").toLowerCase();
    const sid = (session.session_id || "").toLowerCase();
    return (
      preview.includes("agent") ||
      preview.includes("self-test") ||
      preview.includes("monitor") ||
      sid.includes("agent")
    );
  };

  const formatDateTime = (isoString) => {
    if (!isoString) return "";
    const date = new Date(isoString);
    return date.toLocaleDateString([], { month: "short", day: "numeric" }) +
      " " + date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  const formatRelative = (isoString) => {
    if (!isoString) return "";
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays === 1) return "Yesterday";
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString([], { month: "short", day: "numeric" });
  };

  const formatSize = (chars) => {
    if (!chars) return "";
    if (chars < 1000) return `${chars} chars`;
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
      <ListItem key={session.session_id} disablePadding sx={{ px: 1 }}>
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
                {session.preview || "New chat"}
              </Typography>
              <Tooltip title="Delete">
                <IconButton
                  size="small"
                  onClick={(e) => handleDelete(session.session_id, e)}
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
                  color: "text.disabled",
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
                  <AccessTimeIcon sx={{ fontSize: 11, color: "text.disabled" }} />
                  <Typography variant="caption" color="text.disabled" sx={{ fontSize: "0.7rem" }}>
                    {formatRelative(session.last_activity || session.created_at)}
                  </Typography>
                </Box>
              </Tooltip>

              {/* Message count */}
              <Chip
                label={`${session.message_count} msg${session.message_count !== 1 ? "s" : ""}`}
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
                  label="Agent"
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
                  label="Active"
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
            color: "text.disabled",
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
            Chats
          </Typography>
          <Tooltip title="New chat">
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
          placeholder="Search chats..."
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
        ) : filteredSessions.length === 0 ? (
          <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", py: 4, px: 2 }}>
            <ChatBubbleOutlineIcon sx={{ fontSize: 40, color: "text.disabled", mb: 1 }} />
            <Typography color="text.secondary" variant="body2">
              {searchQuery ? "No matching chats" : "No chats yet"}
            </Typography>
          </Box>
        ) : (
          <List disablePadding sx={{ pt: 0.5 }}>
            {renderGroup("Today", groups.today)}
            {renderGroup("Yesterday", groups.yesterday)}
            {renderGroup("This Week", groups.thisWeek)}
            {renderGroup("Older", groups.older)}
          </List>
        )}
      </Box>

      {/* Footer stats */}
      <Divider />
      <Box sx={{ px: 2, py: 1, display: "flex", justifyContent: "space-between" }}>
        <Typography variant="caption" color="text.disabled">
          {sessions.length} chat{sessions.length !== 1 ? "s" : ""}
        </Typography>
        <Typography variant="caption" color="text.disabled">
          {sessions.reduce((sum, s) => sum + (s.message_count || 0), 0)} messages
        </Typography>
      </Box>
    </Drawer>
  );
};

export default ChatSessionDrawer;
