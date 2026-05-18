import React, { useCallback, useEffect, useRef, useState } from "react";
import { Box, Typography, Chip, IconButton, CircularProgress, Alert } from "@mui/material";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import ImageIcon from "@mui/icons-material/Image";
import CloseIcon from "@mui/icons-material/Close";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const DEFAULT_ACCEPT = "image/png,image/jpeg,image/webp,image/gif,image/bmp";

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
const DragDropImageUpload = React.forwardRef(function DragDropImageUpload(
  { subjectId, existingPaths = [], onUploaded, accept = DEFAULT_ACCEPT, helperText },
  ref,
) {
  const [staged, setStaged] = useState([]); // [{file, previewUrl}] when no subjectId
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [skipped, setSkipped] = useState([]);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

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
        setError(e.response?.data?.error || "Upload failed.");
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

  const total = existingPaths.length + staged.length;

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
              Drop reference images here or click to pick
            </Typography>
            {helperText && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                {helperText}
              </Typography>
            )}
            {total > 0 && (
              <Typography variant="caption" sx={{ display: "block", mt: 1 }}>
                {total} image{total === 1 ? "" : "s"} ready
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
          Skipped: {skipped.map((s) => `${s.name} (${s.reason})`).join("; ")}
        </Alert>
      )}

      {staged.length > 0 && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, mt: 1 }}>
          {staged.map((s, idx) => (
            <Box key={idx} sx={{ position: "relative", width: 64, height: 64 }}>
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
      )}

      {existingPaths.length > 0 && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mt: 1 }}>
          {existingPaths.map((p, idx) => (
            <Chip
              key={idx}
              icon={<ImageIcon sx={{ fontSize: 16 }} />}
              label={p.split("/").pop()}
              size="small"
              variant="outlined"
            />
          ))}
        </Box>
      )}
    </Box>
  );
});

export default DragDropImageUpload;
