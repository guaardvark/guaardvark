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
import Editor from "../../utils/monaco";
import { getDocumentContent, getRepoFileContent, updateDocument } from "../../api/documentService";
import { getLanguageFromFilename } from "../../utils/languageDetector";
import copyText from "../../utils/copyText";
import { useUiTranslation } from "../../i18n";

// startEditing opens straight into edit mode (the Files page's Edit action);
// a live-repo file stays in view mode, as its toggle is disabled.
const CodeViewerModal = ({ open, onClose, file, onOpenInCodeEditor, startEditing = false }) => {
  const { t } = useUiTranslation();
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
  const isLiveRepoFile = file?.source_type === "live_repo";
  const isModified = content !== originalContent;

  useEffect(() => {
    if (!open || !file?.id) return;
    setLoading(true);
    setError(null);
    setContent(null);
    setOriginalContent(null);
    setEditing(Boolean(startEditing) && !isLiveRepoFile);

    const loader = isLiveRepoFile
      ? getRepoFileContent(file.relative_path || "")
      : getDocumentContent(file.id);
    loader.then((result) => {
      if (result.error) {
        setError({ detail: result.error });
      } else {
        const text = typeof result === "string" ? result : result.content || result.data || "";
        setContent(text);
        setOriginalContent(text);
      }
      setLoading(false);
    });
  }, [open, file?.id, file?.relative_path, isLiveRepoFile, startEditing]);

  const handleCopy = async () => {
    if (!content) return;
    // copyText falls back to execCommand: navigator.clipboard is absent over plain http on the LAN.
    if (await copyText(content)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleOpenInEditor = () => {
    if (onOpenInCodeEditor && file) {
      onOpenInCodeEditor(file, content);
    }
    onClose();
  };

  const handleSave = async () => {
    if (!file?.id || !isModified) return;
    if (isLiveRepoFile) {
      setError({ code: "readOnly" });
      return;
    }
    setSaving(true);
    try {
      await updateDocument(file.id, { content });
      setOriginalContent(content);
    } catch (err) {
      setError({ code: "saveFailed", detail: err.message });
    }
    setSaving(false);
  };

  const handleToggleEdit = () => {
    if (isLiveRepoFile) {
      setError({ code: "reviewFirst" });
      return;
    }
    setEditing(!editing);
  };

  const handleClose = () => {
    if (isModified) {
      if (!window.confirm(t("documents.codeViewer.discardConfirm"))) return;
    }
    setEditing(false);
    onClose();
  };

  // Our own errors are kept as codes and worded here, so they follow a language change.
  const errorText = !error
    ? null
    : error.code === "readOnly"
      ? t("documents.codeViewer.liveRepoReadOnly")
      : error.code === "saveFailed"
        ? t("documents.codeViewer.saveFailed", { error: error.detail })
        : error.code === "reviewFirst"
          ? t("documents.codeViewer.liveRepoReviewFirst")
          : error.detail;

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
          {isLiveRepoFile && (
            <Chip label={t("documents.codeViewer.liveRepoChip")} size="small" color="primary" variant="outlined" sx={{ fontSize: "0.65rem", height: 20 }} />
          )}
          {isModified && (
            <Chip label={t("documents.codeViewer.modified")} size="small" color="warning" sx={{ fontSize: "0.65rem", height: 20 }} />
          )}
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Tooltip title={editing ? t("documents.codeViewer.viewMode") : t("documents.codeViewer.editMode")}>
            <span>
              <IconButton size="small" onClick={handleToggleEdit} disabled={loading || !!error || isLiveRepoFile}>
                {editing ? <ViewIcon fontSize="small" /> : <EditIcon fontSize="small" />}
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title={t("documents.codeViewer.openInCodeEditor")}>
            <span>
              <IconButton size="small" onClick={handleOpenInEditor} disabled={loading || !!error}>
                <CodeIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title={t("documents.codeViewer.copy")}>
            <span>
              <IconButton size="small" onClick={handleCopy} disabled={!content}>
                {copied ? <CheckIcon fontSize="small" color="success" /> : <CopyIcon fontSize="small" />}
              </IconButton>
            </span>
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
            <Typography color="error">{errorText}</Typography>
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
          {t("documents.actions.close")}
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          size="small"
          startIcon={saving ? <CircularProgress size={14} /> : <SaveIcon />}
          disabled={!isModified || saving || loading || !!error}
        >
          {saving ? t("documents.codeViewer.saving") : t("documents.codeViewer.save")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default CodeViewerModal;
