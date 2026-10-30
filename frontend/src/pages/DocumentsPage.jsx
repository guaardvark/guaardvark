// frontend/src/pages/DocumentsPage.jsx
// The Files page: the whole library on the shared desktop (FileDesktop), with
// folders as windows, the files beside them, and everything done to them. The
// Media page is the same desktop showing only media; both have the Icons,
// Thumbnails and List views, and a picture, video or song opens its details.

import React, { useRef } from "react";
import PageLayout from "../components/layout/PageLayout";
import FileDesktop, { FileDesktopActions, FILES_FEATURES } from "../components/documents/FileDesktop";
import { FILES_VIEW_DEFAULTS, useDesktopView } from "../components/documents/desktopView";
import useMediaActions from "../components/media/useMediaActions";
import { useStatus } from "../contexts/StatusContext";
import { useUiTranslation } from '../i18n';

export { indexingMessage } from "../components/documents/FileDesktop";

const VIEW_KEY = 'filesDesktopView.v2';
// The icons/list choice kept before the view had more to it.
const LEGACY_VIEW_KEY = 'filesDesktopView';
const LIBRARY_REFRESH_EVENTS = ['images-updated'];

const DocumentsPage = () => {
  const { t } = useUiTranslation();
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const desktopRef = useRef(null);
  const [view, setView] = useDesktopView(VIEW_KEY, FILES_VIEW_DEFAULTS, LEGACY_VIEW_KEY);
  const media = useMediaActions({ onEdited: () => desktopRef.current?.refresh() });

  return (
    <PageLayout
      title={t('documents.page.title')}
      variant="grid"
      actions={<FileDesktopActions desktopRef={desktopRef} view={view} onViewChange={setView} />}
      modelStatus
      activeModel={isLoadingModel ? t('documents.page.modelLoading') : modelError ? t('documents.page.modelError') : activeModel}
    >
      <FileDesktop
        ref={desktopRef}
        rootPath="/"
        stateEndpoint="/api/state/documents-windows-v2"
        features={FILES_FEATURES}
        view={view}
        onViewChange={setView}
        onFileClick={media.onFileClick}
        fileMenuActions={media.fileMenuActions}
        keyboardSuspended={media.keyboardSuspended}
        refreshOnEvents={LIBRARY_REFRESH_EVENTS}
      />
      {media.overlays}
    </PageLayout>
  );
};

export default DocumentsPage;
