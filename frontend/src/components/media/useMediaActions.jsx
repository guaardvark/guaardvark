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
import { useSnackbar } from "../common/SnackbarProvider";
import { useUiTranslation } from "../../i18n";

const API_BASE = "/api/files";

export const isMediaFile = (file) => {
  const name = file?.filename || file?.name || "";
  return isImageFile(name) || isVideoFile(name) || isAudioFile(name);
};

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
      { label: t("images.contextMenu.publish"), onClick: () => setPublishDocuments([file]) },
    ];
  }, [t, openDetails, showMessage, queuedMessage, navigate]);

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
