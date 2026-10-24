// Accepts files dropped from the OS file browser and uploads them as Documents
// through /api/files/upload. Each file goes into the top-level Files folder for
// its kind (Videos, Audio, Images), created when missing; those are the folders
// generated media is registered under.

import { useCallback, useState } from "react";
import axios from "axios";
import { ensureFolderPath, filesFolderApi } from "../../utils/droppedFiles";
import { translate } from "../../i18n";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Route each dropped file to the right library folder (and tag its kind) by
// extension, so the Bin can accept video, audio, and image in one drop.
const EXT_KIND = {
  video: ["mp4", "mov", "webm", "mkv", "avi", "m4v"],
  audio: ["mp3", "wav", "flac", "m4a", "aac", "ogg"],
  image: ["jpg", "jpeg", "png", "webp", "gif", "bmp"],
};
export const FOLDER_FOR_KIND = { video: "Videos", audio: "Audio", image: "Images" };

export function kindForFile(name = "", fallback = "video") {
  const ext = (name.split(".").pop() || "").toLowerCase();
  for (const [k, exts] of Object.entries(EXT_KIND)) if (exts.includes(ext)) return k;
  return fallback;
}

/**
 * Upload dropped files, each into the Files folder for its kind.
 *
 * @param {File[]} files
 * @param {object} opts
 * @param {{get: Function, post: Function}} opts.http  axios or a compatible client
 * @param {string} [opts.apiBase]  API root, e.g. "/api"
 * @param {string} [opts.fallbackFolder]  folder for a kind with no folder of its own
 * @param {(pct: number) => void} [opts.onProgress]
 * @param {boolean} [opts.autoIndex]  false keeps the files out of the vision index
 *   (inputs to a render, not documents to search); the route's default is true
 * @returns {Promise<Array<object>>} the created Documents, each with its `kind`
 */
export async function uploadDroppedMedia(files, { http, apiBase = API_BASE, fallbackFolder = "Videos", onProgress, autoIndex = true } = {}) {
  const folderApi = filesFolderApi(http, `${apiBase}/files`);
  const folderCache = new Map();
  const uploaded = [];
  for (const [idx, file] of files.entries()) {
    const kind = kindForFile(file.name);
    const folderPath = await ensureFolderPath(FOLDER_FOR_KIND[kind] || fallbackFolder, "/", folderApi, folderCache);
    const form = new FormData();
    form.append("file", file);
    form.append("folder_path", folderPath);
    if (!autoIndex) form.append("auto_index", "false");
    const res = await http.post(`${apiBase}/files/upload`, form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) => {
        if (e.total && onProgress) onProgress(((idx + e.loaded / e.total) / files.length) * 100);
      },
    });
    const doc = res.data?.data || res.data?.document || res.data;
    if (doc?.id) uploaded.push({ ...doc, kind });
  }
  return uploaded;
}

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

    try {
      const uploaded = await uploadDroppedMedia(files, {
        http: axios,
        fallbackFolder: folderName,
        onProgress: setProgress,
      });
      if (onUploaded) onUploaded(uploaded);
    } catch (e) {
      console.error("useExternalDrop: upload failed:", e);
      setError(e.response?.data?.error?.message || e.message || translate("videoEditor.drop.uploadFailed"));
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
