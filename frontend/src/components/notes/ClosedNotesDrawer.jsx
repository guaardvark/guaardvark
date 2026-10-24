// Closed notes list. Same drawer shape as ChatSessionDrawer: search, date groups,
// click to reopen, hover to delete. Deletion is confirmed by the page.

import React, { useMemo, useState } from "react";
import {
  Drawer,
  Box,
  Typography,
  List,
  ListItem,
  ListItemButton,
  IconButton,
  TextField,
  InputAdornment,
  Tooltip,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import StickyNote2OutlinedIcon from "@mui/icons-material/StickyNote2Outlined";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import { useUiTranslation, translate, uiLocale } from "../../i18n";

const DRAWER_WIDTH = 340;

const stripHtml = (html) =>
  (html || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();

const formatRelative = (isoString) => {
  if (!isoString) return "";
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);
  if (diffMins < 1) return translate("notes.closed.justNow");
  if (diffMins < 60) return translate("notes.closed.minutesAgo", { count: diffMins });
  if (diffHours < 24) return translate("notes.closed.hoursAgo", { count: diffHours });
  if (diffDays === 1) return translate("notes.closed.yesterday");
  if (diffDays < 7) return translate("notes.closed.daysAgo", { count: diffDays });
  return date.toLocaleDateString(uiLocale(), { month: "short", day: "numeric" });
};

const groupClosed = (entries) => {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today - 86400000);
  const weekAgo = new Date(today - 7 * 86400000);
  const groups = { today: [], yesterday: [], thisWeek: [], older: [] };
  entries.forEach((note) => {
    const d = new Date(note.closedAt || 0);
    if (d >= today) groups.today.push(note);
    else if (d >= yesterday) groups.yesterday.push(note);
    else if (d >= weekAgo) groups.thisWeek.push(note);
    else groups.older.push(note);
  });
  return groups;
};

const ClosedNotesDrawer = ({ open, onClose, closedNotes, onReopen, onDeleteRequest }) => {
  const { t } = useUiTranslation();
  const [searchQuery, setSearchQuery] = useState("");

  const entries = useMemo(() => {
    return Object.entries(closedNotes || {})
      .map(([id, note]) => ({ id, ...note }))
      .sort((a, b) => new Date(b.closedAt || 0) - new Date(a.closedAt || 0));
  }, [closedNotes]);

  const filtered = entries.filter((note) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      (note.title || "").toLowerCase().includes(q) ||
      stripHtml(note.content).toLowerCase().includes(q)
    );
  });

  const groups = groupClosed(filtered);

  const renderNote = (note) => {
    const preview = stripHtml(note.content);
    return (
      <ListItem key={note.id} disablePadding sx={{ px: 1 }}>
        <ListItemButton
          onClick={() => onReopen(note.id)}
          sx={{ borderRadius: 1.5, mb: 0.5, py: 1.25, px: 1.5 }}
        >
          <Box sx={{ width: "100%", minWidth: 0 }}>
            <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1 }}>
              <StickyNote2OutlinedIcon
                sx={{ fontSize: 16, mt: 0.4, color: "text.disabled", flexShrink: 0 }}
              />
              <Typography variant="body2" noWrap sx={{ flex: 1, color: "text.secondary" }}>
                {note.title || t("notes.untitled")}
              </Typography>
              <Tooltip title={t("notes.actions.delete")}>
                <IconButton
                  size="small"
                  aria-label={t("notes.closed.deleteNamed", { title: note.title || t("notes.untitled") })}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteRequest(note.id);
                  }}
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
            {preview && (
              <Typography
                variant="caption"
                noWrap
                sx={{ display: "block", color: "text.secondary", pl: 3, mt: 0.25 }}
              >
                {preview}
              </Typography>
            )}
            {note.closedAt && (
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.25, pl: 3, mt: 0.5 }}>
                <AccessTimeIcon sx={{ fontSize: 11, color: "text.secondary" }} />
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                  {formatRelative(note.closedAt)}
                </Typography>
              </Box>
            )}
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
            px: 2.5,
            pt: 1.5,
            pb: 0.5,
            display: "block",
            color: "text.secondary",
            letterSpacing: 1,
            fontSize: "0.65rem",
          }}
        >
          {label}
        </Typography>
        {items.map(renderNote)}
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
      <Box sx={{ p: 2, pb: 1 }}>
        <Typography variant="h6" sx={{ fontWeight: 600, mb: 1.5 }}>
          {t("notes.closed.heading")}
        </Typography>
        <TextField
          size="small"
          placeholder={t("notes.closed.searchPlaceholder")}
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
        />
      </Box>
      <List sx={{ flex: 1, overflow: "auto", pt: 0 }}>
        {filtered.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ px: 2.5, pt: 2 }}>
            {entries.length === 0 ? t("notes.closed.empty") : t("notes.closed.noMatches")}
          </Typography>
        ) : (
          <>
            {renderGroup(t("notes.closed.groups.today"), groups.today)}
            {renderGroup(t("notes.closed.groups.yesterday"), groups.yesterday)}
            {renderGroup(t("notes.closed.groups.thisWeek"), groups.thisWeek)}
            {renderGroup(t("notes.closed.groups.older"), groups.older)}
          </>
        )}
      </List>
    </Drawer>
  );
};

export default ClosedNotesDrawer;
