// frontend/src/components/modals/InterconnectorSettingsModal.jsx
// Modal wrapping InterconnectorSettings for compact SettingsPage

import React from "react";
import { Dialog, DialogTitle, DialogContent } from "@mui/material";
import InterconnectorSettings from "../settings/InterconnectorSettings";
import { useUiTranslation } from "../../i18n";

// startConnecting: opened from Sync's Connect, so the setup starts enabled.
const InterconnectorSettingsModal = ({ open, onClose, startConnecting = false }) => {
  const { t } = useUiTranslation();
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("dialogs.interconnectorSettings.title")}</DialogTitle>
      <DialogContent dividers>
        <InterconnectorSettings startConnecting={startConnecting} />
      </DialogContent>
    </Dialog>
  );
};

export default InterconnectorSettingsModal;
