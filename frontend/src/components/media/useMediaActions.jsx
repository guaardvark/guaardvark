// What a click and the file menu do with a picture, video or song on the
// shared file desktop, the same on the Files page and the Media page: the
// details window (how it was made, Regenerate, Adjust & Retry), the full-size
// viewer and Publish. Other files are left to the desktop.

import React, { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { isAudioFile, isImageFile, isVideoFile } from "../documents/fileUtils.jsx";
import ImageLightbox from "../images/ImageLightbox";
import PublishModal from "../modals/PublishModal";
import MediaDetailsModal from "./MediaDetailsModal";
import { getDocumentRecipe, regenerateMedia } from "../../api/mediaRecipeService";
import { getJob, upscaleDocument } from "../../api/upscalingService";
import { useSnackbar } from "../common/SnackbarProvider";
import { useUiTranslation } from "../../i18n";

const API_BASE = "/api/files";

export const isMediaFile = (file) => {
  const name = file?.filename || file?.name || "";
  return isImageFile(name) || isVideoFile(name) || isAudioFile(name);
};

// What /api/upscaling/upscale/document accepts (OpenCV stills, ffmpeg video).
const UPSCALE_EXT = new Set(["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "mp4", "webm", "mov", "mkv", "avi"]);
const isUpscalable = (file) => {
  const name = file?.filename || file?.name || "";
  return name.includes(".") && UPSCALE_EXT.has(name.split(".").pop().toLowerCase());
};
const UPSCALE_POLL_MS = 3000;
const UPSCALE_TERMINAL = new Set(["completed", "failed", "cancelled"]);

const downloadUrl = (file) => `${API_BASE}/document/${file.id}/download?v=${file.updated_at || ""}`;

const documentTarget = (file) => ({
  kind: "document",
  documentId: file.id,
  name: file.title || file.filename || file.name,
  url: downloadUrl(file),
});

/**
 * @param {Object} options
 * @param {() => void} [options.onEdited] - an image was edited (a copy lands beside it)
 * @returns {{onFileClick, fileMenuActions, keyboardSuspended, overlays}} for FileDesktop
 */
const useMediaActions = ({ onEdited } = {}) => {
  const { t } = useUiTranslation();
  const { showMessage } = useSnackbar();
  const navigate = useNavigate();
  // {list, index}: the media files of the folder or view it was opened from.
  const [details, setDetails] = useState(null);
  const [lightbox, setLightbox] = useState(null); // {list, index}
  const [publishDocuments, setPublishDocuments] = useState([]);

  const openDetails = useCallback((file, siblings) => {
    const list = (siblings || []).filter(isMediaFile);
    const index = list.findIndex((f) => f.id === file.id);
    setDetails(index >= 0 ? { list, index } : { list: [file], index: 0 });
  }, []);

  const onFileClick = useCallback((file, siblings) => {
    if (isMediaFile(file)) openDetails(file, siblings);
  }, [openDetails]);

  const upscale = useCallback(async (file) => {
    const name = file.filename || file.name;
    showMessage?.(t("images.upscale.running", { name }), "info");
    try {
      const res = await upscaleDocument(file.id);
      const data = res?.data || {};
      if (!data.job_id) {
        showMessage?.(t("images.upscale.done", { name: data.filename }), "success");
        onEdited?.();
        return;
      }
      const output = data.output_file;
      showMessage?.(t("images.upscale.queued", { name: output }), "info");
      let failures = 0;
      const poll = async () => {
        try {
          const job = (await getJob(data.job_id))?.data || {};
          failures = 0;
          if (!UPSCALE_TERMINAL.has(job.status)) {
            setTimeout(poll, UPSCALE_POLL_MS);
          } else if (job.status === "completed") {
            showMessage?.(t("images.upscale.videoDone", { name: output }), "success");
            onEdited?.();
          } else {
            showMessage?.(t("images.upscale.videoFailed", { name, error: job.error || job.message || job.status }), "error");
          }
        } catch (e) {
          // A job that is gone, or a service that stays down, ends the watch.
          failures += 1;
          if (e?.status === 404 || failures >= 10) {
            showMessage?.(t("images.upscale.videoFailed", { name, error: e?.message || "" }), "error");
          } else {
            setTimeout(poll, UPSCALE_POLL_MS * 2);
          }
        }
      };
      setTimeout(poll, UPSCALE_POLL_MS);
    } catch (e) {
      showMessage?.(e.message, "error");
    }
  }, [t, showMessage, onEdited]);

  const queuedMessage = useCallback((queued) => (
    t("images.details.queued", { page: t(`images.details.queues.${queued.queue}`) })
  ), [t]);

  const fileMenuActions = useCallback((file) => {
    if (!isMediaFile(file)) return [];
    return [
      { label: t("images.contextMenu.details"), onClick: () => openDetails(file) },
      {
        label: t("images.details.regenerate"),
        onClick: async () => {
          try {
            showMessage?.(queuedMessage(await regenerateMedia({ document_id: file.id })), "success");
          } catch (e) {
            showMessage?.(e.message, "error");
          }
        },
      },
      {
        label: t("images.batchCard.adjustRetry"),
        onClick: async () => {
          try {
            const recipe = await getDocumentRecipe(file.id);
            if (!recipe.adjust) {
              showMessage?.(t("images.details.reasons.no_settings"), "info");
              return;
            }
            navigate(`${recipe.adjust.path}?${new URLSearchParams(
              Object.entries(recipe.adjust.query || {}).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]),
            )}`);
          } catch (e) {
            showMessage?.(e.message, "error");
          }
        },
      },
      ...(isUpscalable(file) ? [{ label: t("images.contextMenu.upscale"), onClick: () => upscale(file) }] : []),
      { label: t("images.contextMenu.publish"), onClick: () => setPublishDocuments([file]) },
    ];
  }, [t, openDetails, showMessage, queuedMessage, navigate, upscale]);

  const step = (setter, delta) => setter((prev) => {
    if (!prev) return prev;
    const index = prev.index + delta;
    return index < 0 || index >= prev.list.length ? prev : { ...prev, index };
  });

  const detailsFile = details ? details.list[details.index] : null;
  const lightboxFile = lightbox ? lightbox.list[lightbox.index] : null;

  const openFullSize = useCallback(() => {
    if (!details) return;
    const images = details.list.filter((f) => isImageFile(f.filename || f.name || ""));
    const index = images.findIndex((f) => f.id === detailsFile?.id);
    setDetails(null);
    if (index >= 0) setLightbox({ list: images, index });
  }, [details, detailsFile]);

  const download = (file) => {
    const link = document.createElement("a");
    link.href = downloadUrl(file);
    link.download = file.filename || file.name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const overlays = (
    <>
      <MediaDetailsModal
        open={!!detailsFile}
        target={detailsFile ? documentTarget(detailsFile) : null}
        onClose={() => setDetails(null)}
        onPrev={details?.list.length > 1 ? () => step(setDetails, -1) : undefined}
        onNext={details?.list.length > 1 ? () => step(setDetails, 1) : undefined}
        hasPrev={!!details && details.index > 0}
        hasNext={!!details && details.index < details.list.length - 1}
        onFullSize={detailsFile && isImageFile(detailsFile.filename || "") ? openFullSize : undefined}
      />
      {lightboxFile && (
        <ImageLightbox
          imageUrl={downloadUrl(lightboxFile)}
          imageName={lightboxFile.filename || lightboxFile.name}
          documentId={lightboxFile.id}
          initialEditMode={false}
          onClose={() => setLightbox(null)}
          onPrev={() => step(setLightbox, -1)}
          onNext={() => step(setLightbox, 1)}
          onDownload={() => download(lightboxFile)}
          onPublish={() => setPublishDocuments([lightboxFile])}
          onImageEdited={() => {
            setLightbox(null);
            onEdited?.();
          }}
          hasPrev={lightbox.index > 0}
          hasNext={lightbox.index < lightbox.list.length - 1}
        />
      )}
      <PublishModal
        open={publishDocuments.length > 0}
        documents={publishDocuments}
        onClose={() => setPublishDocuments([])}
        onFeedback={({ message, severity }) => showMessage?.(message, severity)}
      />
    </>
  );

  return { onFileClick, fileMenuActions, keyboardSuspended: !!details || !!lightbox, overlays };
};

export default useMediaActions;
