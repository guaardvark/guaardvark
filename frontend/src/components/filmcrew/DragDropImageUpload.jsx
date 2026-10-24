import React, { useCallback, useEffect, useRef, useState } from "react";
import { formatUiError } from "../../utils/uiError";
import {
  Box, Typography, Chip, IconButton, CircularProgress, Alert,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions, Button,
} from "@mui/material";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import ImageIcon from "@mui/icons-material/Image";
import CloseIcon from "@mui/icons-material/Close";
import axios from "axios";
import { useUiTranslation } from "../../i18n";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const DEFAULT_ACCEPT = "image/png,image/jpeg,image/webp,image/gif,image/bmp";

// Small status badge overlaid on a training-pool thumbnail (ref / approved / training / trained).
function StatusBadge({ status }) {
  if (!status?.label) return null;
  return (
    <Chip
      size="small"
      label={status.label}
      color={status.color || "default"}
      sx={{
        position: "absolute",
        left: 2,
        bottom: 2,
        height: 18,
        fontSize: "0.65rem",
        maxWidth: "calc(100% - 4px)",
        "& .MuiChip-label": { px: 0.5, overflow: "hidden", textOverflow: "ellipsis" },
      }}
    />
  );
}

// An already-uploaded reference image, shown as a real thumbnail (visuals beat
// filename chips). Served by index from the subject's ref_image_paths; falls back
// to a filename chip if there's no subject id yet or the image fails to load.
function ExistingThumb({ subjectId, index, name, status }) {
  const [failed, setFailed] = useState(false);
  if (!subjectId || failed) {
    return <Chip icon={<ImageIcon sx={{ fontSize: 16 }} />} label={name} size="small" variant="outlined" />;
  }
  return (
    <Box sx={{ position: "relative", width: 72, height: 72, borderRadius: 1, overflow: "hidden", border: 1, borderColor: "divider" }}>
      <img
        src={`${API_BASE}/cast-library/subjects/${subjectId}/refs/${index}/image`}
        alt={name}
        title={name}
        loading="lazy"
        onError={() => setFailed(true)}
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />
      <StatusBadge status={status} />
    </Box>
  );
}

// Extra pool thumbnails (e.g. approved generated samples not yet promoted into refs).
function ExtraThumb({ src, name, status }) {
  const { t } = useUiTranslation();
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return <Chip icon={<ImageIcon sx={{ fontSize: 16 }} />} label={name || t("filmCrew.upload.sample")} size="small" variant="outlined" />;
  }
  return (
    <Box sx={{ position: "relative", width: 72, height: 72, borderRadius: 1, overflow: "hidden", border: 1, borderColor: "divider" }}>
      <img
        src={src}
        alt={name || t("filmCrew.upload.sample")}
        title={name}
        loading="lazy"
        onError={() => setFailed(true)}
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />
      <StatusBadge status={status} />
    </Box>
  );
}

/**
 * Reusable drag-and-drop image uploader. Native HTML5 — no react-dropzone dep.
 *
 * Two modes:
 *   1. Subject-bound (subjectId given): drops POST straight to
 *      /api/cast-library/subjects/{id}/upload-refs and the parent gets the
 *      updated ref_image_paths via onUploaded.
 *   2. Staging (no subjectId): files are kept in component state until the
 *      parent calls flushTo(subjectId) — used during "Create Subject" where
 *      we don't have an id until the user submits the dialog.
 *
 * Either way the parent never types a path.
 */
/**
 * @param {object[]} [extraItems] — additional pool thumbs (approved gens not yet in refs).
 *   Each: { key, src, name, status?: { label, color } }
 * @param {(path: string, index: number) => ({label, color}|null)} [getPathStatus]
 *   Status chip for each existing ref path (trained / training / ref).
 */
const DragDropImageUpload = React.forwardRef(function DragDropImageUpload(
  {
    subjectId,
    existingPaths = [],
    onUploaded,
    accept = DEFAULT_ACCEPT,
    helperText,
    extraItems = [],
    getPathStatus,
  },
  ref,
) {
  const { t } = useUiTranslation();
  const [staged, setStaged] = useState([]); // [{file, previewUrl}] when no subjectId
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [skipped, setSkipped] = useState([]);
  const [dragOver, setDragOver] = useState(false);
  // Index (into existingPaths) pending a delete-confirm, or null. Deleting an
  // already-uploaded reference removes it from the subject AND from disk.
  const [confirmIdx, setConfirmIdx] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const inputRef = useRef(null);

  const confirmDeleteExisting = useCallback(async () => {
    if (confirmIdx == null || !subjectId) return;
    setDeleting(true);
    setError(null);
    try {
      const res = await axios.delete(
        `${API_BASE}/cast-library/subjects/${subjectId}/refs/${confirmIdx}`,
      );
      // Parent refreshes off the returned ref_image_paths (re-indexes thumbs).
      if (onUploaded) onUploaded(res.data?.ref_image_paths || []);
      setConfirmIdx(null);
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || t("filmCrew.upload.deleteFailed"));
    } finally {
      setDeleting(false);
    }
  }, [confirmIdx, subjectId, onUploaded]);

  const reset = useCallback(() => {
    staged.forEach((s) => URL.revokeObjectURL(s.previewUrl));
    setStaged([]);
    setSkipped([]);
    setError(null);
  }, [staged]);

  // Revoke object URLs on unmount so closing the Create Subject dialog
  // mid-staging doesn't permanently leak preview blobs. The ref-based
  // closure captures whatever's staged at unmount time.
  const stagedRef = useRef(staged);
  stagedRef.current = staged;
  useEffect(() => {
    return () => {
      stagedRef.current.forEach((s) => URL.revokeObjectURL(s.previewUrl));
    };
  }, []);

  const sendToServer = useCallback(
    async (files, targetSubjectId) => {
      if (!files.length || !targetSubjectId) return null;
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      setUploading(true);
      setError(null);
      try {
        const res = await axios.post(
          `${API_BASE}/cast-library/subjects/${targetSubjectId}/upload-refs`,
          fd,
          { headers: { "Content-Type": "multipart/form-data" } },
        );
        if (res.data?.skipped?.length) setSkipped(res.data.skipped);
        if (onUploaded) onUploaded(res.data?.subject?.ref_image_paths || []);
        return res.data;
      } catch (e) {
        setError(formatUiError(e.response?.data?.error) || t("filmCrew.upload.uploadFailed"));
        throw e;
      } finally {
        setUploading(false);
      }
    },
    [onUploaded],
  );

  // Parent can call ref.current.flushTo(newId) once the Subject row has
  // been created — sends everything that was staged in subject-less mode.
  React.useImperativeHandle(ref, () => ({
    async flushTo(newId) {
      const files = staged.map((s) => s.file);
      if (!files.length) return null;
      const result = await sendToServer(files, newId);
      reset();
      return result;
    },
    hasStagedFiles: () => staged.length > 0,
  }));

  const handleFiles = useCallback(
    async (fileList) => {
      if (uploading) return;  // refuse concurrent drops mid-upload
      const files = Array.from(fileList || []);
      if (!files.length) return;

      if (subjectId) {
        await sendToServer(files, subjectId);
      } else {
        // Stage with object-URL previews until the parent has an id.
        const additions = files.map((f) => ({ file: f, previewUrl: URL.createObjectURL(f) }));
        setStaged((prev) => [...prev, ...additions]);
      }
    },
    [subjectId, sendToServer, uploading],
  );

  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    if (uploading) return;
    handleFiles(e.dataTransfer?.files);
  };

  const removeStaged = (idx) => {
    setStaged((prev) => {
      const next = [...prev];
      const removed = next.splice(idx, 1)[0];
      if (removed) URL.revokeObjectURL(removed.previewUrl);
      return next;
    });
  };

  const poolCount = existingPaths.length + (extraItems?.length || 0) + staged.length;

  return (
    <Box>
      <Box
        data-testid="drag-drop-zone"
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        sx={{
          border: "2px dashed",
          borderColor: dragOver ? "primary.main" : "divider",
          borderRadius: 1,
          p: 3,
          textAlign: "center",
          cursor: "pointer",
          bgcolor: dragOver ? "action.hover" : "background.paper",
          transition: "background-color 120ms, border-color 120ms",
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          multiple
          hidden
          onChange={(e) => handleFiles(e.target.files)}
        />
        {uploading ? (
          <CircularProgress size={24} />
        ) : (
          <>
            <CloudUploadIcon sx={{ fontSize: 36, color: "text.secondary", mb: 1 }} />
            <Typography variant="body2" color="text.secondary">
              {t("filmCrew.upload.dropHint")}
            </Typography>
            {helperText && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                {helperText}
              </Typography>
            )}
            {poolCount > 0 && (
              <Typography variant="caption" sx={{ display: "block", mt: 1 }}>
                {t("filmCrew.upload.ready", { count: poolCount })}
              </Typography>
            )}
          </>
        )}
      </Box>

      {error && (
        <Alert severity="error" sx={{ mt: 1 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {skipped.length > 0 && (
        <Alert severity="warning" sx={{ mt: 1 }} onClose={() => setSkipped([])}>
          {t("filmCrew.upload.skipped", { files: skipped.map((s) => `${s.name} (${s.reason})`).join("; ") })}
        </Alert>
      )}

      {staged.length > 0 && (
        <Box 
          sx={{ 
            maxHeight: 140, 
            overflowY: 'auto', 
            border: '1px solid', 
            borderColor: 'divider', 
            borderRadius: 1, 
            p: 1, 
            mt: 1,
            bgcolor: 'action.hover',
            width: '100%'
          }}
        >
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
            {t("filmCrew.upload.staged", { count: staged.length })}
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, justifyContent: 'flex-start' }}>
            {staged.map((s, idx) => (
              <Box key={idx} sx={{ position: "relative", width: 72, height: 72 }}>
                <img
                  src={s.previewUrl}
                  alt={s.file.name}
                  style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 4 }}
                />
                <IconButton
                  size="small"
                  onClick={(e) => { e.stopPropagation(); removeStaged(idx); }}
                  sx={{
                    position: "absolute", top: -8, right: -8,
                    bgcolor: "background.paper",
                    "&:hover": { bgcolor: "background.paper" },
                  }}
                >
                  <CloseIcon sx={{ fontSize: 14 }} />
                </IconButton>
              </Box>
            ))}
          </Box>
        </Box>
      )}

      {(existingPaths.length > 0 || (extraItems && extraItems.length > 0)) && (
        <Box 
          sx={{ 
            maxHeight: 220, 
            overflowY: 'auto', 
            border: '1px solid', 
            borderColor: 'divider', 
            borderRadius: 1, 
            p: 1, 
            mt: 1,
            bgcolor: 'action.hover',
            width: '100%'
          }}
        >
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
            {t("filmCrew.upload.trainingSet", {
              count: existingPaths.length + (extraItems?.length || 0),
              approved: (extraItems?.length || 0) > 0
                ? ` + ${t("filmCrew.upload.approvedGenerated", { count: extraItems.length })}`
                : '',
            })}
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, justifyContent: 'flex-start' }}>
            {existingPaths.map((p, idx) => {
              const status = typeof getPathStatus === "function" ? getPathStatus(p, idx) : null;
              return (
                <Box key={`ref-${idx}`} sx={{ position: "relative", display: "inline-flex" }}>
                  <ExistingThumb
                    subjectId={subjectId}
                    index={idx}
                    name={p.split("/").pop()}
                    status={status}
                  />
                  {subjectId && (
                    <IconButton
                      size="small"
                      aria-label={t("filmCrew.upload.deleteAria")}
                      onClick={(e) => { e.stopPropagation(); setConfirmIdx(idx); }}
                      sx={{
                        position: "absolute", top: -8, right: -8,
                        bgcolor: "background.paper",
                        "&:hover": { bgcolor: "background.paper" },
                      }}
                    >
                      <CloseIcon sx={{ fontSize: 14 }} />
                    </IconButton>
                  )}
                </Box>
              );
            })}
            {(extraItems || []).map((item) => (
              <Box key={item.key} sx={{ position: "relative", display: "inline-flex" }}>
                <ExtraThumb src={item.src} name={item.name} status={item.status} />
              </Box>
            ))}
          </Box>
        </Box>
      )}

      <Dialog open={confirmIdx != null} onClose={() => !deleting && setConfirmIdx(null)}>
        <DialogTitle>{t("filmCrew.upload.deleteTitle")}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {t("filmCrew.upload.deleteText")}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmIdx(null)} disabled={deleting}>{t("filmCrew.upload.cancel")}</Button>
          <Button onClick={confirmDeleteExisting} color="error" variant="contained" disabled={deleting}>
            {deleting ? t("filmCrew.upload.deleting") : t("filmCrew.upload.delete")}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
});

export default DragDropImageUpload;
