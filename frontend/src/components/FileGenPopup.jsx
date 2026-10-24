// frontend/src/components/FileGenPopup.jsx
// Offer to write a chat request out as a file. A plain card in the corner,
// worded as the small thing it is; "Always create" stores the tool so later
// requests of this kind start without the card (undo on the Approvals page).

import React from "react";
import { Box, Fade, Paper, Typography } from "@mui/material";
import { ActionButton } from "./settings/ui";
import { translate, useUiTranslation } from "../i18n";

// The whole question per kind of file, so a language can word each one.
const kindTitles = () => ({
  codegen: translate("fileGeneration.popup.titleCodeFile"),
  generate_file: translate("fileGeneration.popup.titleFile"),
  generate_csv: translate("fileGeneration.popup.titleCsvFile"),
  generate_bulk_csv: translate("fileGeneration.popup.titleCsvFile"),
  generate_wordpress_content: translate("fileGeneration.popup.titleWordPressCsv"),
  generate_enhanced_wordpress_content: translate("fileGeneration.popup.titleWordPressCsv"),
});

const FileGenPopup = ({ open, fileData, onConfirm, onAlways, onDismiss }) => {
  const { t } = useUiTranslation();
  if (!open || !fileData) return null;

  const { filename, isBulkRequest, quantity, toolName } = fileData;
  const title = kindTitles()[toolName] || t("fileGeneration.popup.titleFile");
  const detail = isBulkRequest
    ? quantity
      ? t("fileGeneration.popup.bulkRows", { quantity })
      : t("fileGeneration.popup.bulkRowsSeveral")
    : null;

  return (
    <Fade in={open}>
      <Paper
        variant="outlined"
        role="dialog"
        aria-label={t("fileGeneration.popup.dialogLabel")}
        data-testid="file-gen-card"
        sx={{
          // Above the chat input bar, which would otherwise draw over the card.
          position: "fixed",
          bottom: 112,
          right: 24,
          zIndex: 1500,
          width: 420,
          maxWidth: "calc(100vw - 48px)",
          p: 1.75,
          borderRadius: 1.5,
          bgcolor: "background.paper",
          boxShadow: 3,
        }}
      >
        <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
          {title}
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontFamily: "monospace", fontSize: "0.8rem", wordBreak: "break-all" }}
        >
          {filename}
        </Typography>
        {detail && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            {detail}
          </Typography>
        )}
        <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap", mt: 1.5 }}>
          <ActionButton kind="primary" onClick={onConfirm}>
            {t("fileGeneration.popup.create")}
          </ActionButton>
          {onAlways && (
            <ActionButton
              kind="neutral"
              tooltip={t("fileGeneration.popup.alwaysCreateTooltip")}
              onClick={onAlways}
            >
              {t("fileGeneration.popup.alwaysCreate")}
            </ActionButton>
          )}
          <ActionButton kind="link" onClick={onDismiss}>
            {t("fileGeneration.popup.notNow")}
          </ActionButton>
        </Box>
      </Paper>
    </Fade>
  );
};

export default FileGenPopup;
