// frontend/src/components/modals/InterconnectorSettingsModal.jsx
// Modal wrapping InterconnectorSettings for compact SettingsPage

import React from "react";
import { Dialog, DialogTitle, DialogContent } from "@mui/material";
import InterconnectorSettings from "../settings/InterconnectorSettings";
import { useUiTranslation } from "../../i18n";

const InterconnectorSettingsModal = ({ open, onClose }) => {
  const { t } = useUiTranslation();
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("dialogs.interconnectorSettings.title")}</DialogTitle>
      <DialogContent dividers>
        <InterconnectorSettings />
      </DialogContent>
    </Dialog>
  );
};

export default InterconnectorSettingsModal;
