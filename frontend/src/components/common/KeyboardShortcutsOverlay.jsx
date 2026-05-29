// frontend/src/components/common/KeyboardShortcutsOverlay.jsx
// Global keyboard-shortcuts reference. Pressing `?` (Shift+/) anywhere outside
// a text input opens a modal listing the app's real shortcuts, grouped by area.
// Closes on Escape, backdrop click, or the close button (standard MUI Dialog).
/* eslint-env browser */
import React, { useState, useEffect, useCallback } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  Typography,
  IconButton,
  Divider,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import KeyboardIcon from "@mui/icons-material/Keyboard";

// Real shortcuts wired elsewhere in the app — keep this list in sync with the
// handlers that own each key (referenced in comments) rather than inventing new
// ones. `?` itself is owned here.
const SHORTCUT_GROUPS = [
  {
    group: "Global",
    items: [
      { keys: ["?"], desc: "Show this keyboard shortcuts reference" },
      { keys: ["Ctrl", "Shift", "C"], desc: "Toggle the floating chat panel" },
    ],
  },
  {
    group: "Chat",
    items: [
      { keys: ["Enter"], desc: "Send message" },
      { keys: ["Shift", "Enter"], desc: "Insert a newline" },
      { keys: ["↑"], desc: "Recall previous message" },
      { keys: ["↓"], desc: "Recall next message" },
    ],
  },
  {
    group: "System Map",
    items: [
      { keys: ["/"], desc: "Focus search" },
      { keys: ["Ctrl", "K"], desc: "Focus search" },
      { keys: ["R"], desc: "Reset the view" },
      { keys: ["Esc"], desc: "Clear search / selection" },
    ],
  },
  {
    group: "Video Editor",
    items: [
      { keys: ["Space"], desc: "Play / pause the preview" },
      { keys: ["Ctrl", "Z"], desc: "Undo the last timeline change" },
      { keys: ["Delete"], desc: "Remove the selected text overlay" },
    ],
  },
];

function KeyCombo({ keys }) {
  return (
    <Box sx={{ display: "flex", gap: 0.5, flexShrink: 0 }}>
      {keys.map((k, i) => (
        <Box
          key={i}
          component="kbd"
          sx={{
            px: 0.75,
            py: 0.25,
            minWidth: 22,
            textAlign: "center",
            fontSize: "0.72rem",
            fontFamily: "monospace",
            lineHeight: 1.4,
            color: "text.primary",
            bgcolor: "action.hover",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
          }}
        >
          {k}
        </Box>
      ))}
    </Box>
  );
}

const KeyboardShortcutsOverlay = () => {
  const [open, setOpen] = useState(false);

  const handleClose = useCallback(() => setOpen(false), []);

  useEffect(() => {
    const onKeyDown = (e) => {
      // `?` is Shift+/ on most layouts; accept either e.key === "?" directly.
      if (e.key !== "?") return;
      // Ignore when typing in a field or an editable surface.
      const el = document.activeElement;
      const tag = el?.tagName;
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        el?.isContentEditable
      ) {
        return;
      }
      // Don't hijack browser/OS combos.
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      e.preventDefault();
      setOpen((prev) => !prev);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        <Box display="flex" alignItems="center" justifyContent="space-between">
          <Box display="flex" alignItems="center">
            <KeyboardIcon sx={{ mr: 1 }} />
            Keyboard shortcuts
          </Box>
          <IconButton onClick={handleClose} size="small" aria-label="Close shortcuts">
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogContent dividers>
        {SHORTCUT_GROUPS.map((group, gi) => (
          <Box key={group.group} sx={{ mb: gi < SHORTCUT_GROUPS.length - 1 ? 2 : 0 }}>
            <Typography
              variant="overline"
              sx={{ color: "text.secondary", letterSpacing: 1 }}
            >
              {group.group}
            </Typography>
            <Divider sx={{ mb: 1 }} />
            {group.items.map((item, ii) => (
              <Box
                key={ii}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 2,
                  py: 0.5,
                }}
              >
                <Typography variant="body2" sx={{ color: "text.primary" }}>
                  {item.desc}
                </Typography>
                <KeyCombo keys={item.keys} />
              </Box>
            ))}
          </Box>
        ))}
      </DialogContent>
    </Dialog>
  );
};

export default KeyboardShortcutsOverlay;
