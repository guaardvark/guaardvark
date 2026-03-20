// Lightweight code/text viewer modal for Documents page
// Double-click a code file → view here. "Edit in Code Editor" escalates to full page.

import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Typography,
  IconButton,
  Chip,
  CircularProgress,
} from "@mui/material";
import {
  Close as CloseIcon,
  Code as CodeIcon,
  ContentCopy as CopyIcon,
  Check as CheckIcon,
} from "@mui/icons-material";
import Editor from "@monaco-editor/react";
import { getDocumentContent } from "../../api/documentService";
import { getLanguageFromFilename } from "../../utils/languageDetector";

const CodeViewerModal = ({ open, onClose, file, onOpenInCodeEditor }) => {
  const [content, setContent] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  const filename = file?.filename || file?.name || "untitled";
  const language = getLanguageFromFilename(filename);

  useEffect(() => {
    if (!open || !file?.id) return;
    setLoading(true);
    setError(null);
    setContent(null);

    getDocumentContent(file.id).then((result) => {
      if (result.error) {
        setError(result.error);
      } else {
        setContent(typeof result === "string" ? result : result.content || result.data || "");
      }
      setLoading(false);
    });
  }, [open, file?.id]);

  const handleCopy = async () => {
    if (!content) return;
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleOpenInEditor = () => {
    if (onOpenInCodeEditor && file) {
      onOpenInCodeEditor(file, content);
    }
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      PaperProps={{
        sx: {
          height: "80vh",
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
          py: 1.5,
          px: 2,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}>
          <CodeIcon fontSize="small" color="primary" />
          <Typography variant="subtitle1" noWrap sx={{ fontWeight: 500 }}>
            {filename}
          </Typography>
          <Chip label={language} size="small" variant="outlined" sx={{ fontSize: "0.7rem" }} />
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <IconButton size="small" onClick={handleCopy} disabled={!content}>
            {copied ? <CheckIcon fontSize="small" color="success" /> : <CopyIcon fontSize="small" />}
          </IconButton>
          <IconButton size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent sx={{ p: 0, flex: 1, overflow: "hidden" }}>
        {loading && (
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}>
            <CircularProgress size={32} />
          </Box>
        )}
        {error && (
          <Box sx={{ p: 3, textAlign: "center" }}>
            <Typography color="error">{error}</Typography>
          </Box>
        )}
        {!loading && !error && content !== null && (
          <Editor
            height="100%"
            language={language}
            value={content}
            theme="vs-dark"
            options={{
              readOnly: true,
              fontSize: 13,
              wordWrap: "on",
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              lineNumbers: "on",
              renderLineHighlight: "none",
              folding: true,
              automaticLayout: true,
              padding: { top: 8, bottom: 8 },
            }}
          />
        )}
      </DialogContent>

      <DialogActions sx={{ px: 2, py: 1, borderTop: 1, borderColor: "divider" }}>
        <Button onClick={onClose} size="small">
          Close
        </Button>
        <Button
          onClick={handleOpenInEditor}
          variant="contained"
          size="small"
          startIcon={<CodeIcon />}
          disabled={loading || !!error}
        >
          Edit in Code Editor
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default CodeViewerModal;
