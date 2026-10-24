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
import { translate, useUiTranslation } from "../../i18n";

/** Dispatch on window to open the shortcuts reference from elsewhere (a tip's "Show me"). */
export const SHOW_SHORTCUTS_EVENT = "guaardvark:show-shortcuts";

// Real shortcuts wired elsewhere in the app — keep this list in sync with the
// handlers that own each key (referenced in comments) rather than inventing new
// ones. `?` itself is owned here.
const shortcutGroups = () => {
  const ctrl = translate("layoutCommon.shortcuts.keys.ctrl");
  const shift = translate("layoutCommon.shortcuts.keys.shift");
  return [
    {
      group: translate("layoutCommon.shortcuts.groups.global"),
      items: [
        { keys: ["?"], desc: translate("layoutCommon.shortcuts.showShortcuts") },
        { keys: [ctrl, shift, "C"], desc: translate("layoutCommon.shortcuts.toggleFloatingChat") },
        // VoiceSessionProvider (contexts/VoiceSessionContext.jsx)
        { keys: [ctrl, shift, translate("layoutCommon.shortcuts.keys.space")], desc: translate("layoutCommon.shortcuts.voiceMic") },
      ],
    },
    {
      group: translate("layoutCommon.shortcuts.groups.chat"),
      items: [
        { keys: [translate("layoutCommon.shortcuts.keys.enter")], desc: translate("layoutCommon.shortcuts.sendMessage") },
        { keys: [shift, translate("layoutCommon.shortcuts.keys.enter")], desc: translate("layoutCommon.shortcuts.newline") },
        { keys: ["↑"], desc: translate("layoutCommon.shortcuts.previousMessage") },
        { keys: ["↓"], desc: translate("layoutCommon.shortcuts.nextMessage") },
      ],
    },
    {
      // pages/CodeEditorPage.jsx and components/codeeditor/CodeEditorCard.jsx
      group: translate("layoutCommon.shortcuts.groups.codeEditor"),
      items: [
        { keys: [ctrl, "S"], desc: translate("layoutCommon.shortcuts.saveFile") },
        { keys: [ctrl, shift, "S"], desc: translate("layoutCommon.shortcuts.saveSession") },
        { keys: [ctrl, shift, "O"], desc: translate("layoutCommon.shortcuts.findSymbol") },
        { keys: [ctrl, "1–5"], desc: translate("layoutCommon.shortcuts.jumpToPanel") },
        { keys: [ctrl, shift, "E"], desc: translate("layoutCommon.shortcuts.explainSelection") },
      ],
    },
    {
      group: translate("layoutCommon.shortcuts.groups.systemMap"),
      items: [
        { keys: ["/"], desc: translate("layoutCommon.shortcuts.focusSearch") },
        { keys: [ctrl, "K"], desc: translate("layoutCommon.shortcuts.focusSearch") },
        { keys: ["R"], desc: translate("layoutCommon.shortcuts.resetView") },
        { keys: [translate("layoutCommon.shortcuts.keys.esc")], desc: translate("layoutCommon.shortcuts.clearSearch") },
      ],
    },
    {
      group: translate("layoutCommon.shortcuts.groups.videoEditor"),
      items: [
        { keys: [translate("layoutCommon.shortcuts.keys.space")], desc: translate("layoutCommon.shortcuts.playPause") },
        { keys: [ctrl, "Z"], desc: translate("layoutCommon.shortcuts.undo") },
        { keys: [translate("layoutCommon.shortcuts.keys.delete")], desc: translate("layoutCommon.shortcuts.removeOverlay") },
      ],
    },
  ];
};

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
  const { t } = useUiTranslation();
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
    const onShow = () => setOpen(true);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener(SHOW_SHORTCUTS_EVENT, onShow);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener(SHOW_SHORTCUTS_EVENT, onShow);
    };
  }, []);

  const groups = shortcutGroups();

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        <Box display="flex" alignItems="center" justifyContent="space-between">
          <Box display="flex" alignItems="center">
            <KeyboardIcon sx={{ mr: 1 }} />
            {t("layoutCommon.shortcuts.title")}
          </Box>
          <IconButton onClick={handleClose} size="small" aria-label={t("layoutCommon.shortcuts.close")}>
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogContent dividers>
        {groups.map((group, gi) => (
          <Box key={group.group} sx={{ mb: gi < groups.length - 1 ? 2 : 0 }}>
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
