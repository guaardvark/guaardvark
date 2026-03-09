// frontend/src/components/modals/InterconnectorSettingsModal.jsx
// Modal wrapping InterconnectorSettings for compact SettingsPage

import React from "react";
import { Dialog, DialogTitle, DialogContent } from "@mui/material";
import InterconnectorSettings from "../settings/InterconnectorSettings";

const InterconnectorSettingsModal = ({ open, onClose }) => {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Interconnector</DialogTitle>
      <DialogContent dividers>
        <InterconnectorSettings />
      </DialogContent>
    </Dialog>
  );
};

export default InterconnectorSettingsModal;
