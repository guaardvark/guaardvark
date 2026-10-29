// frontend/src/pages/DocumentsPage.jsx
// The Files page: the whole library on the shared desktop (FileDesktop), with
// folders as windows, the files beside them, and everything done to them.

import React, { useRef, useState } from "react";
import PageLayout from "../components/layout/PageLayout";
import FileDesktop, { FileDesktopActions, FILES_FEATURES } from "../components/documents/FileDesktop";
import { useStatus } from "../contexts/StatusContext";
import { useUiTranslation } from '../i18n';

export { indexingMessage } from "../components/documents/FileDesktop";

const VIEW_KEY = 'filesDesktopView';

const readView = () => {
  try {
    return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'icons';
  } catch (e) {
    return 'icons';
  }
};

const DocumentsPage = () => {
  const { t } = useUiTranslation();
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const desktopRef = useRef(null);
  const [viewMode, setViewMode] = useState(readView);

  const changeView = (mode) => {
    setViewMode(mode);
    try {
      localStorage.setItem(VIEW_KEY, mode);
    } catch (e) {
      // Storage refused: the view still changes for this visit.
    }
  };

  return (
    <PageLayout
      title={t('documents.page.title')}
      variant="grid"
      actions={<FileDesktopActions desktopRef={desktopRef} viewMode={viewMode} onViewModeChange={changeView} />}
      modelStatus
      activeModel={isLoadingModel ? t('documents.page.modelLoading') : modelError ? t('documents.page.modelError') : activeModel}
    >
      <FileDesktop
        ref={desktopRef}
        rootPath="/"
        stateEndpoint="/api/state/documents-windows-v2"
        features={FILES_FEATURES}
        viewMode={viewMode}
      />
    </PageLayout>
  );
};

export default DocumentsPage;
