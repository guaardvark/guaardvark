// frontend/src/components/settings/ExportChatsButton.jsx
// "Export Chats" control for the Settings Data card: writes every stored chat
// session to a timestamped folder under data/outputs and reports where it went.

import React, { useState } from "react";
import { Button, CircularProgress, Tooltip } from "@mui/material";
import { exportChatSessions } from "../../api/chatService";

/**
 * Button that exports all chat sessions through the backend.
 *
 * @param {object} props
 * @param {(message: string, severity?: string) => void} props.showMessage - Snackbar reporter from SettingsPage.
 * @param {boolean} [props.disabled] - Disable while the page is loading or another data job runs.
 */
const ExportChatsButton = ({ showMessage, disabled = false }) => {
  const [isExporting, setIsExporting] = useState(false);

  const handleClick = async () => {
    setIsExporting(true);
    try {
      const result = await exportChatSessions();
      const where = result.relative_directory || result.directory;
      showMessage(
        `Exported ${result.sessions} chat${result.sessions === 1 ? "" : "s"} (${result.messages} messages) to outputs/${where}`,
        "success",
      );
    } catch (err) {
      showMessage(`Chat export failed: ${err.message}`, "error");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <Tooltip title={isExporting ? "Export in progress..." : "Write every chat session to data/outputs/chat-exports"}>
      <span>
        <Button
          variant="outlined"
          size="small"
          onClick={handleClick}
          disabled={disabled || isExporting}
          aria-label="Export chats"
        >
          {isExporting ? <CircularProgress size={16} /> : "Export Chats"}
        </Button>
      </span>
    </Tooltip>
  );
};

export default ExportChatsButton;
