// In-app Word document viewer. Fetches the .docx and converts it to HTML
// client-side with mammoth — no server round-trip beyond the file itself,
// no external services. Mirrors PdfViewerModal's shell (title bar, open-in-
// new-tab, 90vh dialog) so the two feel like one viewer family.
import React, { useEffect, useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  Typography,
  IconButton,
  Tooltip,
  CircularProgress,
} from "@mui/material";
import {
  Close as CloseIcon,
  Description as DocIcon,
  Download as DownloadIcon,
} from "@mui/icons-material";
import { useUiTranslation } from "../../i18n";

const API_BASE = '/api/files';

const DocxViewerModal = ({ open, onClose, file }) => {
  const { t } = useUiTranslation();
  const filename = file?.filename || file?.name || "document.docx";
  const fileUrl = file?.id ? `${API_BASE}/document/${file.id}/download?v=${file.updated_at || ''}` : "";
  const [html, setHtml] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open || !fileUrl) return;
    let cancelled = false;
    setHtml(null);
    setError(null);
    (async () => {
      try {
        const mammoth = await import("mammoth/mammoth.browser");
        const resp = await fetch(fileUrl);
        if (!resp.ok) throw Object.assign(new Error(), { status: resp.status });
        const buf = await resp.arrayBuffer();
        const result = await mammoth.convertToHtml({ arrayBuffer: buf });
        if (!cancelled) setHtml(result.value);
      } catch (e) {
        if (!cancelled) setError({ status: e.status, message: e.message });
      }
    })();
    return () => { cancelled = true; };
  }, [open, fileUrl]);

  const handleDownload = () => {
    if (fileUrl) window.open(fileUrl, '_blank');
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{ sx: { height: "90vh", display: "flex", flexDirection: "column" } }}
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
          <DocIcon fontSize="small" color="info" />
          <Typography variant="subtitle1" noWrap sx={{ fontWeight: 500 }}>
            {filename}
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Tooltip title={t("documents.docxViewer.downloadOriginal")}>
            <span>
              <IconButton size="small" onClick={handleDownload} disabled={!fileUrl}>
                <DownloadIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <IconButton size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent sx={{ flex: 1, overflow: "auto", bgcolor: "background.paper" }}>
        {error ? (
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}>
            <Typography color="error">
              {error.status
                ? t("documents.docxViewer.fetchFailed", { status: error.status })
                : error.message || t("documents.docxViewer.renderFailed")}
            </Typography>
          </Box>
        ) : html === null ? (
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}>
            <CircularProgress size={28} />
          </Box>
        ) : (
          <Box
            sx={{
              maxWidth: "72ch",
              mx: "auto",
              py: 3,
              lineHeight: 1.7,
              "& img": { maxWidth: "100%" },
              "& table": { borderCollapse: "collapse", width: "100%" },
              "& td, & th": { border: "1px solid", borderColor: "divider", p: 1 },
            }}
            dangerouslySetInnerHTML={{ __html: html }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
};

export default DocxViewerModal;
