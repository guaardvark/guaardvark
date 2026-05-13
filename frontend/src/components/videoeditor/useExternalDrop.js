// Accepts files dropped from the OS file browser and uploads them as Documents.
// Returns a stable `onDrop` handler + a `uploading` flag. The dropped files
// land under data/uploads/Videos/ (folder name is configurable) via the
// existing /api/files/upload pipeline.

import { useCallback, useState } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export function useExternalDrop({ folderName = "Videos", onUploaded }) {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState(null);

  const handleDrop = useCallback(async (event) => {
    event.preventDefault();
    event.stopPropagation();

    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length === 0) return;

    setUploading(true);
    setError(null);
    const uploaded = [];

    try {
      for (const [idx, file] of files.entries()) {
        const form = new FormData();
        form.append("file", file);
        form.append("folder_name", folderName);
        const res = await axios.post(`${API_BASE}/files/upload`, form, {
          headers: { "Content-Type": "multipart/form-data" },
          onUploadProgress: (e) => {
            if (e.total) {
              const filePct = (e.loaded / e.total) * 100;
              setProgress(((idx + filePct / 100) / files.length) * 100);
            }
          },
        });
        const doc = res.data?.data || res.data?.document || res.data;
        if (doc?.id) uploaded.push(doc);
      }
      if (onUploaded) onUploaded(uploaded);
    } catch (e) {
      console.error("useExternalDrop: upload failed:", e);
      setError(e.response?.data?.error?.message || e.message || "Upload failed");
    } finally {
      setUploading(false);
      setProgress(0);
    }
  }, [folderName, onUploaded]);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  return { onDrop: handleDrop, onDragOver: handleDragOver, uploading, progress, error };
}
