// frontend/src/pages/ImagesPage.jsx
// The media workspace: one page with five tabs, each its own route (see
// constants/mediaTabs.js). The Media Library tab is the Files page's desktop
// (components/documents/FileDesktop) on the whole library showing only
// pictures, videos and sound: the same folders, windows, moves, menus and
// views (Icons, Thumbnails, List; Folders or All items), opening in
// Thumbnails. A click on a picture, video or song opens its details
// (MediaDetailsModal): how it was made, Regenerate and Adjust & Retry.
// Under software (workspace) chrome the Studio tool strip is the tab strip,
// so the duplicate row is hidden.

import React, { useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Box, Tabs, Tab } from '@mui/material';
import BatchImageGeneratorPage from './BatchImageGeneratorPage';
import VideoGeneratorPage from './VideoGeneratorPage';
import UpscalingPage from './UpscalingPage';
import InfographicGenerator from '../components/images/InfographicGenerator';
import FileDesktop, { FileDesktopActions, FILES_FEATURES } from '../components/documents/FileDesktop';
import { MEDIA_VIEW_DEFAULTS, useDesktopView } from '../components/documents/desktopView';
import useMediaActions from '../components/media/useMediaActions';
import PageLayout from '../components/layout/PageLayout';
import { MEDIA_TABS, MEDIA_LIBRARY_TAB, mediaTabIndexForPath } from '../constants/mediaTabs';
import { useAppStore } from '../stores/useAppStore';
import { NAV_CHROME } from '../config/navCatalog';
import { useUiTranslation } from '../i18n';

const WINDOWS_STATE_ENDPOINT = '/api/state/images-windows';
const LIBRARY_REFRESH_EVENTS = ['images-updated'];
// A new key: the Thumbnails view is new, so the Media page opens in it once.
const VIEW_KEY = 'mediaDesktopView.v2';

const ImagesPage = () => {
  const { t } = useUiTranslation();

  // Every tab is its own route, so the route is the single source of truth for
  // which one is showing — no local tab state to drift out of sync.
  const location = useLocation();
  const navigate = useNavigate();
  const activeTab = mediaTabIndexForPath(location.pathname);
  const activeTabPath = MEDIA_TABS[activeTab].path;
  const navChrome = useAppStore((state) => state.navChrome);
  const showMediaTabs = navChrome !== NAV_CHROME.SOFTWARE;

  // The library desktop, driven from the header.
  const desktopRef = useRef(null);
  const [view, setView] = useDesktopView(VIEW_KEY, MEDIA_VIEW_DEFAULTS);
  const media = useMediaActions({ onEdited: () => desktopRef.current?.refresh() });

  return (
    <PageLayout
      title={t('images.library.pageTitle')}
      variant="grid"
      noPadding
      actions={activeTab === MEDIA_LIBRARY_TAB
        ? <FileDesktopActions desktopRef={desktopRef} view={view} onViewChange={setView} />
        : null}
    >
      {showMediaTabs && (
        <Box sx={{ borderBottom: 1, borderColor: 'divider', flexShrink: 0 }}>
          <Tabs
            value={activeTab}
            onChange={(e, v) => navigate(MEDIA_TABS[v].path)}
            variant="scrollable"
            scrollButtons="auto"
          >
            {MEDIA_TABS.map((tab) => (
              <Tab key={tab.path} label={tab.label} />
            ))}
          </Tabs>
        </Box>
      )}

      {/* Media Library Tab */}
      {activeTab === MEDIA_LIBRARY_TAB && (
        <Box sx={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflow: 'hidden' }}>
          <FileDesktop
            ref={desktopRef}
            rootPath="/"
            mediaOnly
            stateEndpoint={WINDOWS_STATE_ENDPOINT}
            features={FILES_FEATURES}
            view={view}
            onViewChange={setView}
            onFileClick={media.onFileClick}
            fileMenuActions={media.fileMenuActions}
            keyboardSuspended={media.keyboardSuspended}
            refreshOnEvents={LIBRARY_REFRESH_EVENTS}
          />
          {media.overlays}
        </Box>
      )}

      {/* Image Gen Tab */}
      {activeTabPath === '/batch-images' && (
        <Box sx={{ flexGrow: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 2, p: 1 }}>
          <BatchImageGeneratorPage embedded />
        </Box>
      )}

      {/* Infographic Tab */}
      {activeTabPath === '/infographic' && (
        <Box sx={{ flexGrow: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
          <InfographicGenerator />
        </Box>
      )}

      {/* Video Gen Tab */}
      {activeTabPath === '/video' && (
        <Box sx={{ flexGrow: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
          <VideoGeneratorPage embedded />
        </Box>
      )}

      {activeTabPath === '/upscaling' && (
        <Box sx={{ flexGrow: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
          <UpscalingPage embedded />
        </Box>
      )}
    </PageLayout>
  );
};

export default ImagesPage;
