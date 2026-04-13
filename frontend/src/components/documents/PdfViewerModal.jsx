import React from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  Typography,
  IconButton,
  Tooltip,
} from "@mui/material";
import {
  Close as CloseIcon,
  PictureAsPdf as PdfIcon,
  OpenInNew as OpenInNewIcon,
} from "@mui/icons-material";

const API_BASE = '/api/files';

const PdfViewerModal = ({ open, onClose, file }) => {
  const filename = file?.filename || file?.name || "document.pdf";
  const fileUrl = file?.id ? `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}` : "";

  const handleOpenInNewTab = () => {
    if (fileUrl) {
      window.open(fileUrl, '_blank');
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      PaperProps={{
        sx: {
          height: "90vh",
          display: "flex",
          flexDirection: "column",
        },
      }}
    >
      <DialogTitle
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          py: 1,
          px: 2,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}>
          <PdfIcon fontSize="small" color="error" />
          <Typography variant="subtitle1" noWrap sx={{ fontWeight: 500 }}>
            {filename}
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Tooltip title="Open in new tab">
            <IconButton size="small" onClick={handleOpenInNewTab} disabled={!fileUrl}>
              <OpenInNewIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <IconButton size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent sx={{ p: 0, flex: 1, overflow: "hidden", bgcolor: "#525659" }}>
        {fileUrl ? (
          <iframe
            src={fileUrl}
            title={filename}
            width="100%"
            height="100%"
            style={{ border: "none" }}
          />
        ) : (
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}>
            <Typography color="text.secondary">No PDF file selected</Typography>
          </Box>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default PdfViewerModal;
