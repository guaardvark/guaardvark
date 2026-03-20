// Lightweight code/text editor modal for Documents page
// Double-click a code file → view/edit here. Full Code Editor page for heavy work.

import React, { useState, useEffect, useRef } from "react";
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
  Tooltip,
} from "@mui/material";
import {
  Close as CloseIcon,
  Code as CodeIcon,
  ContentCopy as CopyIcon,
  Check as CheckIcon,
  Edit as EditIcon,
  Save as SaveIcon,
  Visibility as ViewIcon,
} from "@mui/icons-material";
import Editor from "@monaco-editor/react";
import { getDocumentContent, updateDocument } from "../../api/documentService";
import { getLanguageFromFilename } from "../../utils/languageDetector";

const CodeViewerModal = ({ open, onClose, file, onOpenInCodeEditor }) => {
  const [content, setContent] = useState(null);
  const [originalContent, setOriginalContent] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const editorRef = useRef(null);

  const filename = file?.filename || file?.name || "untitled";
  const language = getLanguageFromFilename(filename);
  const isModified = content !== originalContent;

  useEffect(() => {
    if (!open || !file?.id) return;
    setLoading(true);
    setError(null);
    setContent(null);
    setOriginalContent(null);
    setEditing(false);

    getDocumentContent(file.id).then((result) => {
      if (result.error) {
        setError(result.error);
      } else {
        const text = typeof result === "string" ? result : result.content || result.data || "";
        setContent(text);
        setOriginalContent(text);
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

  const handleSave = async () => {
    if (!file?.id || !isModified) return;
    setSaving(true);
    try {
      await updateDocument(file.id, { content });
      setOriginalContent(content);
    } catch (err) {
      setError(`Save failed: ${err.message}`);
    }
    setSaving(false);
  };

  const handleToggleEdit = () => {
    setEditing(!editing);
  };

  const handleClose = () => {
    if (isModified) {
      if (!window.confirm("You have unsaved changes. Discard them?")) return;
    }
    setEditing(false);
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
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
          py: 1,
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
          {isModified && (
            <Chip label="Modified" size="small" color="warning" sx={{ fontSize: "0.65rem", height: 20 }} />
          )}
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Tooltip title={editing ? "View mode" : "Edit mode"}>
            <IconButton size="small" onClick={handleToggleEdit} disabled={loading || !!error}>
              {editing ? <ViewIcon fontSize="small" /> : <EditIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
          <Tooltip title="Open in Code Editor">
            <IconButton size="small" onClick={handleOpenInEditor} disabled={loading || !!error}>
              <CodeIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Copy to clipboard">
            <IconButton size="small" onClick={handleCopy} disabled={!content}>
              {copied ? <CheckIcon fontSize="small" color="success" /> : <CopyIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
          <IconButton size="small" onClick={handleClose}>
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
            onChange={editing ? (value) => setContent(value) : undefined}
            onMount={(editor) => { editorRef.current = editor; }}
            theme="vs-dark"
            options={{
              readOnly: !editing,
              fontSize: 13,
              wordWrap: "on",
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              lineNumbers: "on",
              renderLineHighlight: editing ? "line" : "none",
              folding: true,
              automaticLayout: true,
              padding: { top: 8, bottom: 8 },
            }}
          />
        )}
      </DialogContent>

      <DialogActions sx={{ px: 2, py: 1, borderTop: 1, borderColor: "divider" }}>
        <Button onClick={handleClose} size="small">
          Close
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          size="small"
          startIcon={saving ? <CircularProgress size={14} /> : <SaveIcon />}
          disabled={!isModified || saving || loading || !!error}
        >
          {saving ? "Saving..." : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default CodeViewerModal;
