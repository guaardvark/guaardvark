// frontend/src/components/settings/ExportChatsButton.jsx
// "Export Chats" control for the Settings Data card: writes every stored chat
// session to a timestamped folder under data/outputs and reports where it went.

import React, { useState } from "react";
import { Button, CircularProgress, Tooltip } from "@mui/material";
import { exportChatSessions } from "../../api/chatService";
import { useUiTranslation } from "../../i18n";

/**
 * Button that exports all chat sessions through the backend.
 *
 * @param {object} props
 * @param {(message: string, severity?: string) => void} props.showMessage - Snackbar reporter from SettingsPage.
 * @param {boolean} [props.disabled] - Disable while the page is loading or another data job runs.
 */
const ExportChatsButton = ({ showMessage, disabled = false }) => {
  const { t } = useUiTranslation();
  const [isExporting, setIsExporting] = useState(false);

  const handleClick = async () => {
    setIsExporting(true);
    try {
      const result = await exportChatSessions();
      const where = result.relative_directory || result.directory;
      showMessage(
        t("settingsSections.exportChats.exported", { count: result.sessions, messages: result.messages, where }),
        "success",
      );
    } catch (err) {
      showMessage(t("settingsSections.exportChats.failed", { error: err.message }), "error");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <Tooltip title={isExporting ? t("settingsSections.exportChats.inProgress") : t("settingsSections.exportChats.tooltip")}>
      <span>
        <Button
          variant="outlined"
          size="small"
          onClick={handleClick}
          disabled={disabled || isExporting}
          aria-label={t("settingsSections.exportChats.ariaLabel")}
        >
          {isExporting ? <CircularProgress size={16} /> : t("settingsSections.exportChats.button")}
        </Button>
      </span>
    </Tooltip>
  );
};

export default ExportChatsButton;
