// frontend/src/components/chat/FileDropOverlay.jsx
import React from "react";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import { useUiTranslation } from "../../i18n";

/**
 * Highlight drawn over a chat while files are dragged onto it. It ignores the
 * pointer so drag events keep reaching the zone underneath.
 *
 * @param {boolean} active  show the overlay
 * @param {string}  [label] what a drop will do
 * @param {boolean} [compact] smaller text for the floating chat
 */
const FileDropOverlay = ({ active, label, compact = false }) => {
  const { t } = useUiTranslation();
  if (!active) return null;
  return (
    <Box
      data-testid="chat-drop-overlay"
      sx={(theme) => ({
        position: "absolute",
        inset: compact ? 4 : 8,
        zIndex: 20,
        pointerEvents: "none",
        border: `2px dashed ${theme.palette.primary.main}`,
        borderRadius: compact ? "10px" : 2,
        bgcolor: alpha(theme.palette.primary.main, 0.08),
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 1,
        textAlign: "center",
        px: 2,
      })}
    >
      <UploadFileIcon color="primary" sx={{ fontSize: compact ? 28 : 40 }} />
      <Typography variant={compact ? "caption" : "subtitle1"} color="primary" sx={{ fontWeight: 600 }}>
        {label === undefined ? t("chat.fileDrop.label") : label}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {t("chat.fileDrop.hint")}
      </Typography>
    </Box>
  );
};

export default FileDropOverlay;
