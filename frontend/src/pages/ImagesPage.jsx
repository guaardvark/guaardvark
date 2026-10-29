// frontend/src/pages/ImagesPage.jsx
// The media workspace: one page with five tabs, each its own route (see
// constants/mediaTabs.js). The Media Library tab is the shared file desktop
// (components/documents/FileDesktop) on the Images folder, so it moves,
// renames, imports and sorts exactly as the Files page does, plus:
// - a click on a picture, clip or song opens its details (MediaDetailsModal):
//   the settings it was made with, Regenerate and Adjust & Retry
// - the file menu leads with Details, Regenerate, Adjust & Retry and Publish
// - Videos (Video Gen batches and Film Crew films) and Songs and sounds below
// Under software (workspace) chrome the Studio tool strip is the tab strip,
// so the duplicate row is hidden.

import React, { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Card,
  CardActionArea,
  IconButton,
  Tabs,
  Tab,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Snackbar,
  Alert as MuiAlert,
  Chip,
  Grid,
} from '@mui/material';
import {
  MovieCreation as VideoIcon,
  PlayArrow as PlayArrowIcon,
  Download as DownloadIcon,
  Close as CloseIcon,
  Refresh as RefreshIcon,
  Videocam as VideocamIcon,
  MusicNote as MusicNoteIcon,
  GraphicEq as SoundIcon,
  OpenInNew as OpenInNewIcon,
} from '@mui/icons-material';
import BatchImageGeneratorPage from './BatchImageGeneratorPage';
import VideoGeneratorPage from './VideoGeneratorPage';
import UpscalingPage from './UpscalingPage';
import InfographicGenerator from '../components/images/InfographicGenerator';
import FileDesktop, { FileDesktopActions } from '../components/documents/FileDesktop';
import { isImageFile, isVideoFile, isAudioFile } from '../components/documents/fileUtils.jsx';
import ImageLightbox from '../components/images/ImageLightbox';
import MediaDetailsModal from '../components/media/MediaDetailsModal';
import EntityContextMenu from '../components/common/EntityContextMenu';
import useContextMenu from '../hooks/useContextMenu';
import { getDocumentRecipe, regenerateMedia } from '../api/mediaRecipeService';
import FilmCard from '../components/images/FilmCard';
import { fallbackBatchName } from '../utils/batchNames';
import PublishModal from '../components/modals/PublishModal';
import PageLayout from '../components/layout/PageLayout';
import { MEDIA_TABS, MEDIA_LIBRARY_TAB, mediaTabIndexForPath } from '../constants/mediaTabs';
import { useAppStore } from '../stores/useAppStore';
import { NAV_CHROME } from '../config/navCatalog';
import { useSnackbar } from '../components/common/SnackbarProvider';
import { useStatus } from '../contexts/StatusContext';
import { uiLocale, useUiTranslation } from '../i18n';

// Folder rows store paths without the leading slash; create, move and copy
// look the folder up by its stored path.
const IMAGES_ROOT_STORED_PATH = 'Images';
const WINDOWS_STATE_ENDPOINT = '/api/state/images-windows';
const API_BASE = '/api/files';
const VIDEO_API_BASE = '/api/batch-video';
const FILMS_API = '/api/production/films';
// The Files page's indexing, bulk import, code editor and repository review
// stay there; the media library is for pictures, clips and songs.
const MEDIA_FEATURES = Object.freeze({});
const LIBRARY_REFRESH_EVENTS = ['images-updated'];
const VIEW_KEY = 'mediaDesktopView';

const readView = () => {
  try {
    return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'icons';
  } catch (e) {
    return 'icons';
  }
};

const isMediaFile = (file) => {
  const name = file?.filename || file?.name || '';
  return isImageFile(name) || isVideoFile(name) || isAudioFile(name);
};

const ImagesPage = () => {
  const { t } = useUiTranslation();
  const { showMessage } = useSnackbar();
  const { _activeModel, _isLoadingModel, _modelError } = useStatus();

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
  const [viewMode, setViewMode] = useState(readView);
  const changeView = useCallback((mode) => {
    setViewMode(mode);
    try {
      localStorage.setItem(VIEW_KEY, mode);
    } catch (e) {
      // Storage refused: the view still changes for this visit.
    }
  }, []);

  // Lightbox (Full size from the details view)
  const [lightbox, setLightbox] = useState(null);
  const [publishDocuments, setPublishDocuments] = useState([]);

  // Details view (MediaDetailsModal): {target, list, index}. `list` is the
  // folder the item was opened from, for stepping with the arrows.
  const [details, setDetails] = useState(null);
  const videoMenu = useContextMenu();

  // Feedback
  const [feedback, setFeedback] = useState({ open: false, message: '', severity: 'info' });

  // Video Library tab state
  const [videoBatches, setVideoBatches] = useState([]);
  const [videoBatchDetails, setVideoBatchDetails] = useState({});
  const [films, setFilms] = useState([]);
  const [videoLoading, setVideoLoading] = useState(false);
  const [videoPlayer, setVideoPlayer] = useState(null); // { url, title, downloadName, batchId, playlist, currentIndex }
  const [videoDeleteConfirm, setVideoDeleteConfirm] = useState(null);
  // Songs and sound effects (/api/media/audio), newest first.
  const [audioItems, setAudioItems] = useState([]);

  // ──────────────────── Video Library Data ────────────────────

  const fetchVideoBatches = useCallback(async () => {
    try {
      setVideoLoading(true);
      const res = await fetch(`${VIDEO_API_BASE}/list`);
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          const batches = data.data.batches || [];
          // Sort by start_time descending (newest first)
          batches.sort((a, b) => {
            const ta = a.start_time ? new Date(a.start_time).getTime() : 0;
            const tb = b.start_time ? new Date(b.start_time).getTime() : 0;
            return tb - ta;
          });
          setVideoBatches(batches);

          // Fetch details for any batch that may have produced videos —
          // cancelled/error batches often have partial results worth showing.
          const detailPromises = batches
            .filter(b => ['completed', 'partial', 'cancelled', 'error'].includes(b.status))
            .map(async (b) => {
              try {
                const statusRes = await fetch(`${VIDEO_API_BASE}/status/${b.batch_id}`);
                if (statusRes.ok) {
                  const statusData = await statusRes.json();
                  if (statusData.success) {
                    return { batch_id: b.batch_id, ...statusData.data };
                  }
                }
              } catch {
                // One batch's status fetch failed — skip it, others continue
              }
              return null;
            });
          const details = await Promise.all(detailPromises);
          const detailMap = {};
          details.forEach(d => {
            if (d) detailMap[d.batch_id] = d;
          });
          setVideoBatchDetails(detailMap);
        }
      }
    } catch (err) {
      console.error('Failed to fetch video batches:', err);
    } finally {
      setVideoLoading(false);
    }
  }, []);

  // Film Crew films are filed in Documents, not in Video Gen batches, so they
  // come from their own list and share the Videos grid with the batches.
  const fetchFilms = useCallback(async () => {
    try {
      const res = await fetch(FILMS_API);
      if (res.ok) {
        const data = await res.json();
        setFilms(data.films || []);
      }
    } catch (err) {
      console.error('Failed to fetch films:', err);
    }
  }, []);

  const fetchAudio = useCallback(async () => {
    try {
      const res = await fetch('/api/media/audio?limit=60');
      if (res.ok) {
        const data = await res.json();
        setAudioItems(data.items || []);
      }
    } catch (err) {
      console.error('Failed to fetch songs:', err);
    }
  }, []);

  const refreshVideos = useCallback(() => {
    fetchVideoBatches();
    fetchFilms();
    fetchAudio();
  }, [fetchVideoBatches, fetchFilms, fetchAudio]);

  // Load video batches when switching to Video Library tab
  useEffect(() => {
    if (activeTab === MEDIA_LIBRARY_TAB) {
      refreshVideos();
    }
  }, [activeTab, refreshVideos]);

  // Films and batches in one grid, newest first.
  const videoCards = useMemo(() => {
    const cards = [
      ...films.map((film, index) => ({ kind: 'film', time: film.created_at, film, index })),
      ...videoBatches.map((batch) => ({ kind: 'batch', time: batch.start_time, batch })),
    ];
    const ms = (t) => (t ? new Date(t).getTime() : 0);
    return cards.sort((a, b) => ms(b.time) - ms(a.time));
  }, [films, videoBatches]);

  const handlePlayVideo = useCallback((batchId, videoPath, startIndex) => {
    const details = videoBatchDetails[batchId];
    const playlist = (details?.results || []).filter(r => r.success && r.video_path);
    const idx = startIndex ?? playlist.findIndex(r => r.video_path === videoPath);
    const target = playlist[Math.max(0, idx)] || { video_path: videoPath };
    setVideoPlayer({
      url: `${VIDEO_API_BASE}/video/${batchId}/${target.video_path}`,
      title: target.video_path?.split('/').pop() || null,
      batchId,
      playlist,
      currentIndex: Math.max(0, idx),
    });
  }, [videoBatchDetails]);

  const handleDownloadVideoBatch = useCallback((batchId) => {
    window.open(`${VIDEO_API_BASE}/download/${batchId}`, '_blank');
  }, []);

  const handlePlayFilm = useCallback((index) => {
    const playlist = films.map((f) => ({
      item_id: `film-${f.production_id}`,
      url: f.url,
      title: f.name,
      download_name: `${f.name}.mp4`,
      thumbnail_url: f.poster_url,
    }));
    const target = playlist[index];
    if (!target) return;
    setVideoPlayer({
      url: target.url,
      title: target.title,
      downloadName: target.download_name,
      batchId: null,
      playlist,
      currentIndex: index,
    });
  }, [films]);

  const handleDownloadFilm = useCallback((film) => {
    const a = document.createElement('a');
    a.href = film.url;
    a.download = `${film.name}.mp4`;
    a.click();
  }, []);

  const handleDeleteVideoBatch = useCallback(async (batchId) => {
    try {
      const res = await fetch(`${VIDEO_API_BASE}/delete/${batchId}`, { method: 'DELETE' });
      if (res.ok) {
        setVideoBatches(prev => prev.filter(b => b.batch_id !== batchId));
        setVideoBatchDetails(prev => {
          const next = { ...prev };
          delete next[batchId];
          return next;
        });
        showMessage?.(t('images.video.batchDeleted'), 'success');
      } else {
        showMessage?.(t('images.video.deleteFailed'), 'error');
      }
    } catch (err) {
      showMessage?.(t('images.video.deleteFailed'), 'error');
    }
    setVideoDeleteConfirm(null);
  }, [showMessage]);

  const _getVideoStatusColor = useCallback((status) => {
    switch (status) {
      case 'completed': return 'success';
      case 'running': case 'generating': return 'info';
      case 'error': case 'failed': return 'error';
      case 'partial': return 'warning';
      default: return 'default';
    }
  }, []);

  const formatVideoTime = useCallback((isoString) => {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleString(uiLocale(), {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      });
    } catch { return ''; }
  }, []);

  // ──────────────────── Lightbox ────────────────────

  const handlePublishFromLightbox = useCallback(() => {
    const file = lightbox?.fileList?.[lightbox.fileIndex];
    if (file) setPublishDocuments([file]);
  }, [lightbox]);

  const handleImageDoubleClick = useCallback((item, fileIndex, fileList) => {
    const imageUrl = `${API_BASE}/document/${item.id}/download?v=${item.updated_at || Date.now()}`;
    setLightbox({
      url: imageUrl,
      name: item.filename || item.name,
      fileIndex: fileIndex >= 0 ? fileIndex : 0,
      fileList: fileList || [item],
    });
  }, []);

  // ──────────────────── Details ────────────────────

  const documentTarget = (item) => ({
    kind: 'document',
    documentId: item.id,
    name: item.filename || item.name,
    url: `${API_BASE}/document/${item.id}/download?v=${item.updated_at || ''}`,
  });

  const openDocumentDetails = useCallback((item, _fileIndex, fileList) => {
    const list = fileList && fileList.length ? fileList : [item];
    const index = Math.max(0, list.findIndex(f => f.id === item.id));
    setDetails({ target: documentTarget(list[index] || item), list, index });
  }, []);

  const stepDetails = useCallback((delta) => {
    setDetails((prev) => {
      if (!prev?.list) return prev;
      const index = prev.index + delta;
      if (index < 0 || index >= prev.list.length) return prev;
      const item = prev.list[index];
      // Audio rows come from /api/media/audio, not the file browser.
      const target = prev.audio
        ? { kind: 'document', documentId: item.document_id, name: item.title, url: item.url }
        : documentTarget(item);
      return { ...prev, index, target };
    });
  }, []);

  const openClipDetails = useCallback((batchId, startIndex = 0) => {
    const playlist = (videoBatchDetails[batchId]?.results || []).filter(r => r.success && r.video_path);
    if (!playlist.length) return;
    setDetails({
      target: {
        kind: 'clip',
        batchId,
        index: startIndex,
        clips: playlist.map(r => ({
          itemId: r.item_id,
          url: `${VIDEO_API_BASE}/video/${batchId}/${r.video_path}`,
          name: r.video_path.split('/').pop(),
        })),
      },
    });
  }, [videoBatchDetails]);

  const detailsFullSize = useCallback(() => {
    if (!details || details.target.kind !== 'document') return;
    const list = details.list || [];
    const item = list[details.index];
    setDetails(null);
    if (item) handleImageDoubleClick(item, details.index, list);
  }, [details, handleImageDoubleClick]);

  const queuedMessage = useCallback((queued) => (
    t('images.details.queued', { page: t(`images.details.queues.${queued.queue}`) })
  ), [t]);
  const regenerateClip = useCallback(async (batchId) => {
    const first = (videoBatchDetails[batchId]?.results || []).find(r => r.success && r.video_path);
    if (!first) return;
    try {
      showMessage?.(queuedMessage(await regenerateMedia({ batch_id: batchId, item_id: first.item_id })), 'success');
    } catch (e) {
      showMessage?.(e.message, 'error');
    }
  }, [videoBatchDetails, showMessage, queuedMessage]);

  const videoMenuActions = (batch) => {
    const results = (videoBatchDetails[batch.batch_id]?.results || []).filter(r => r.success && r.video_path);
    const hasClips = results.length > 0;
    return [
      hasClips && { label: t('images.contextMenu.details'), onClick: () => openClipDetails(batch.batch_id) },
      hasClips && { label: t('images.video.playAll'), onClick: () => handlePlayVideo(batch.batch_id, results[0].video_path, 0) },
      hasClips && { label: t('images.details.regenerate'), onClick: () => regenerateClip(batch.batch_id), dividerBefore: true },
      hasClips && {
        label: t('images.batchCard.adjustRetry'),
        onClick: () => navigate(`/video?${new URLSearchParams({ retry: batch.batch_id, clip: results[0].item_id })}`),
      },
      { label: t('images.shared.download'), onClick: () => handleDownloadVideoBatch(batch.batch_id), dividerBefore: true },
      { label: t('images.shared.delete'), onClick: () => setVideoDeleteConfirm(batch.batch_id), color: 'error.main' },
    ];
  };

  const audioMenu = useContextMenu();

  const audioTarget = (item) => ({ kind: 'document', documentId: item.document_id, name: item.title, url: item.url });

  const openAudioDetails = useCallback((index) => {
    if (!audioItems[index]) return;
    setDetails({ target: audioTarget(audioItems[index]), list: audioItems, index, audio: true });
  }, [audioItems]);

  const audioMenuActions = (item) => [
    { label: t('images.contextMenu.details'), onClick: () => openAudioDetails(audioItems.indexOf(item)) },
    {
      label: t('images.details.regenerate'),
      dividerBefore: true,
      onClick: async () => {
        try {
          showMessage?.(queuedMessage(await regenerateMedia({ document_id: item.document_id })), 'success');
        } catch (e) {
          showMessage?.(e.message, 'error');
        }
      },
    },
    {
      label: t('images.batchCard.adjustRetry'),
      onClick: () => navigate(`/audio?${new URLSearchParams({ retry_doc: String(item.document_id) })}`),
    },
    {
      label: t('images.shared.download'),
      dividerBefore: true,
      onClick: () => {
        const a = document.createElement('a');
        a.href = item.url;
        a.download = '';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      },
    },
  ];

  const handleLightboxPrev = useCallback(() => {
    if (!lightbox || lightbox.fileIndex <= 0) return;
    const newIndex = lightbox.fileIndex - 1;
    const file = lightbox.fileList[newIndex];
    setLightbox({
      ...lightbox,
      url: `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`,
      name: file.filename || file.name,
      fileIndex: newIndex,
    });
  }, [lightbox]);

  const handleLightboxNext = useCallback(() => {
    if (!lightbox || lightbox.fileIndex >= lightbox.fileList.length - 1) return;
    const newIndex = lightbox.fileIndex + 1;
    const file = lightbox.fileList[newIndex];
    setLightbox({
      ...lightbox,
      url: `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`,
      name: file.filename || file.name,
      fileIndex: newIndex,
    });
  }, [lightbox]);

  const handleLightboxDownload = useCallback(() => {
    if (!lightbox) return;
    const file = lightbox.fileList[lightbox.fileIndex];
    if (!file) return;
    const link = document.createElement('a');
    link.href = `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`;
    link.download = file.filename || file.name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [lightbox]);

  // ──────────────────── Library desktop ────────────────────

  // A click on a picture, clip or song in the library opens its details.
  const handleFileClick = useCallback((file, siblings) => {
    if (!isMediaFile(file)) return;
    openDocumentDetails(file, 0, (siblings || []).filter(isMediaFile));
  }, [openDocumentDetails]);

  // First in a file's right-click menu.
  const fileMenuActions = useCallback((file) => [
    { label: t('images.contextMenu.details'), onClick: () => openDocumentDetails(file) },
    {
      label: t('images.details.regenerate'),
      onClick: async () => {
        try {
          showMessage?.(queuedMessage(await regenerateMedia({ document_id: file.id })), 'success');
        } catch (e) {
          showMessage?.(e.message, 'error');
        }
      },
    },
    {
      label: t('images.batchCard.adjustRetry'),
      onClick: async () => {
        try {
          const recipe = await getDocumentRecipe(file.id);
          if (!recipe.adjust) {
            showMessage?.(t('images.details.reasons.no_settings'), 'info');
            return;
          }
          navigate(`${recipe.adjust.path}?${new URLSearchParams(
            Object.entries(recipe.adjust.query || {}).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]),
          )}`);
        } catch (e) {
          showMessage?.(e.message, 'error');
        }
      },
    },
    { label: t('images.contextMenu.publish'), onClick: () => setPublishDocuments([file]) },
  ], [t, openDocumentDetails, showMessage, queuedMessage, navigate]);


  // ──────────────────── Render ────────────────────

  return (
    <PageLayout
      title={t('images.library.pageTitle')}
      variant="grid"
      noPadding
      actions={activeTab === MEDIA_LIBRARY_TAB
        ? <FileDesktopActions desktopRef={desktopRef} viewMode={viewMode} onViewModeChange={changeView} />
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
      {activeTab === MEDIA_LIBRARY_TAB && (<>
        {/* The desktop keeps most of the first screen and Videos and Songs follow
            below it. As a shrinking flex item it was squeezed to nothing once the
            sections under it grew (300 video batches), hiding folders and files. */}
        <Box sx={{ display: 'flex', flexDirection: 'column', flex: '0 0 auto', height: 'calc(100% - 100px)', minHeight: 360, overflow: 'hidden', borderBottom: 1, borderColor: 'divider' }}>
          <FileDesktop
            ref={desktopRef}
            rootPath={IMAGES_ROOT_STORED_PATH}
            stateEndpoint={WINDOWS_STATE_ENDPOINT}
            features={MEDIA_FEATURES}
            viewMode={viewMode}
            onFileClick={handleFileClick}
            fileMenuActions={fileMenuActions}
            keyboardSuspended={!!details || !!lightbox}
            refreshOnEvents={LIBRARY_REFRESH_EVENTS}
          />
        </Box>

        {/* Video Batches Section — stacked thumbnails within Media Library */}
        {/* (still inside activeTab === MEDIA_LIBRARY_TAB conditional) */}
        {videoCards.length > 0 && (
          <Box sx={{ px: 2, pb: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: 1 }}>
                <VideocamIcon sx={{ fontSize: 20 }} />
                {t('images.video.heading')}
                <Chip label={videoCards.length} size="small" variant="outlined" sx={{ height: 20, fontSize: '0.65rem' }} />
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.5 }}>
                <Button size="small" variant="outlined" startIcon={<VideoIcon />} onClick={() => navigate('/video')}>
                  {t('images.shared.generate')}
                </Button>
                <IconButton size="small" onClick={refreshVideos} disabled={videoLoading}>
                  <RefreshIcon fontSize="small" />
                </IconButton>
              </Box>
            </Box>
            <Grid container spacing={2}>
              {videoCards.map((card) => {
                if (card.kind === 'film') {
                  return (
                    <Grid item xs={6} sm={4} md={3} lg={2} key={`film-${card.film.production_id}`}>
                      <FilmCard
                        film={card.film}
                        index={card.index}
                        dateStr={formatVideoTime(card.film.created_at)}
                        onPlay={handlePlayFilm}
                        onDownload={handleDownloadFilm}
                      />
                    </Grid>
                  );
                }
                const { batch } = card;
                const details = videoBatchDetails[batch.batch_id];
                const results = details?.results?.filter(r => r.success && r.video_path) || [];
                const firstResult = results[0];
                const videoCount = batch.completed_videos ?? 0;
                const displayName = batch.display_name || fallbackBatchName(batch.batch_id);

                return (
                  <Grid item xs={6} sm={4} md={3} lg={2} key={batch.batch_id}>
                    <Box
                      onClick={() => {
                        if (firstResult) openClipDetails(batch.batch_id);
                      }}
                      onContextMenu={(e) => videoMenu.open(e, batch)}
                      sx={{
                        cursor: firstResult ? 'pointer' : 'default',
                        position: 'relative',
                        borderRadius: 2,
                        overflow: 'visible',
                        transition: 'transform 0.2s, box-shadow 0.2s',
                        '&:hover': {
                          transform: 'translateY(-3px)',
                          boxShadow: 4,
                          '& .batch-play': { opacity: 1 },
                        },
                      }}
                    >
                      <Box sx={{ position: 'relative', aspectRatio: '16/9' }}>
                        {/* Stack layers */}
                        {videoCount > 2 && (
                          <Box sx={{
                            position: 'absolute', top: -5, left: 5, right: -5, bottom: 5,
                            bgcolor: 'grey.800', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
                          }} />
                        )}
                        {videoCount > 1 && (
                          <Box sx={{
                            position: 'absolute', top: -2, left: 2, right: -2, bottom: 2,
                            bgcolor: 'grey.850', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
                          }} />
                        )}
                        {/* Main thumbnail */}
                        <Box sx={{
                          position: 'relative', width: '100%', height: '100%',
                          bgcolor: 'grey.900', borderRadius: 1.5, overflow: 'hidden',
                          border: 1, borderColor: 'grey.700',
                        }}>
                          {firstResult?.thumbnail_path ? (
                            <Box
                              component="img"
                              src={`${VIDEO_API_BASE}/video/${batch.batch_id}/${firstResult.thumbnail_path}`}
                              alt={displayName}
                              sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
                              onError={(e) => { e.target.style.display = 'none'; }}
                            />
                          ) : (
                            <Box sx={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                              <VideocamIcon sx={{ fontSize: 32, color: 'grey.600' }} />
                            </Box>
                          )}
                          {/* Hover overlay */}
                          <Box className="batch-play" sx={{
                            position: 'absolute', inset: 0,
                            bgcolor: 'rgba(0,0,0,0.45)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            opacity: 0, transition: 'opacity 0.2s',
                          }}>
                            <PlayArrowIcon sx={{ fontSize: 36, color: 'white' }} />
                          </Box>
                          {/* Count badge */}
                          <Chip
                            label={`${videoCount}`}
                            size="small"
                            sx={{
                              position: 'absolute', top: 4, right: 4,
                              height: 18, fontSize: '0.6rem', minWidth: 24,
                              bgcolor: 'rgba(0,0,0,0.7)', color: 'white',
                              '& .MuiChip-label': { px: 0.5 },
                            }}
                          />
                          {batch.status !== 'completed' && (
                            <Chip label={batch.status} size="small"
                              color={batch.status === 'error' ? 'error' : 'info'}
                              sx={{ position: 'absolute', bottom: 4, left: 4, height: 16, fontSize: '0.55rem' }}
                            />
                          )}
                        </Box>
                      </Box>
                      <Box sx={{ pt: 0.5, px: 0.25 }}>
                        <Typography variant="caption" noWrap sx={{ fontWeight: 500, display: 'block', fontSize: '0.7rem' }}>
                          {displayName}
                        </Typography>
                        {batch.start_time && (
                          <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.6rem' }}>
                            {formatVideoTime(batch.start_time)}
                          </Typography>
                        )}
                      </Box>
                      {/* Action icons on hover */}
                      <Box sx={{
                        position: 'absolute', top: -8, right: -8,
                        display: 'flex', gap: 0.25,
                        opacity: 0, transition: 'opacity 0.2s',
                        '.MuiBox-root:hover > &': { opacity: 1 },
                      }}>
                        <IconButton size="small" onClick={(e) => { e.stopPropagation(); handleDownloadVideoBatch(batch.batch_id); }}
                          sx={{ bgcolor: 'background.paper', boxShadow: 2, p: 0.25, '&:hover': { bgcolor: 'primary.dark' } }}>
                          <DownloadIcon sx={{ fontSize: 14 }} />
                        </IconButton>
                        <IconButton size="small" onClick={(e) => { e.stopPropagation(); setVideoDeleteConfirm(batch.batch_id); }}
                          sx={{ bgcolor: 'background.paper', boxShadow: 2, p: 0.25, '&:hover': { bgcolor: 'error.dark' } }}>
                          <CloseIcon sx={{ fontSize: 14 }} />
                        </IconButton>
                      </Box>
                    </Box>
                  </Grid>
                );
              })}
            </Grid>
          </Box>
        )}


        {/* Songs and sound effects — same details view as pictures and clips */}
        {audioItems.length > 0 && (
          <Box sx={{ px: 2, pb: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: 1 }}>
                <MusicNoteIcon sx={{ fontSize: 20 }} />
                {t('images.audio.heading')}
                <Chip label={audioItems.length} size="small" variant="outlined" sx={{ height: 20, fontSize: '0.65rem' }} />
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.5 }}>
                <Button size="small" variant="outlined" startIcon={<MusicNoteIcon />} onClick={() => navigate('/audio')}>
                  {t('images.shared.generate')}
                </Button>
                <IconButton size="small" onClick={fetchAudio}>
                  <RefreshIcon fontSize="small" />
                </IconButton>
              </Box>
            </Box>
            <Grid container spacing={1.5}>
              {audioItems.map((item, index) => (
                <Grid item xs={12} sm={6} md={4} lg={3} key={item.document_id}>
                  <Card
                    variant="outlined"
                    onContextMenu={(e) => audioMenu.open(e, item)}
                    sx={{ '&:hover': { boxShadow: 3, bgcolor: 'action.hover' } }}
                  >
                    <CardActionArea onClick={() => openAudioDetails(index)} sx={{ p: 1.25, display: 'flex', alignItems: 'center', gap: 1.25, justifyContent: 'flex-start' }}>
                      <Box sx={{ width: 40, height: 40, borderRadius: 1, bgcolor: 'grey.900', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                        {item.kind === 'sound'
                          ? <SoundIcon sx={{ color: 'grey.400' }} />
                          : <MusicNoteIcon sx={{ color: 'grey.400' }} />}
                      </Box>
                      <Box sx={{ minWidth: 0, flex: 1 }}>
                        <Typography variant="body2" noWrap title={item.title} sx={{ fontWeight: 500 }}>
                          {item.title}
                        </Typography>
                        <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
                          {[t(`images.details.kinds.${item.kind}`), item.duration_s ? t('images.details.seconds', { value: Math.round(item.duration_s) }) : null, formatVideoTime(item.created_at)]
                            .filter(Boolean).join(' · ')}
                        </Typography>
                      </Box>
                    </CardActionArea>
                  </Card>
                </Grid>
              ))}
            </Grid>
          </Box>
        )}
      </>)}

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

      <EntityContextMenu
        anchorPosition={videoMenu.anchorPosition}
        onClose={videoMenu.close}
        actions={videoMenu.payload ? videoMenuActions(videoMenu.payload) : []}
      />
      <EntityContextMenu
        anchorPosition={audioMenu.anchorPosition}
        onClose={audioMenu.close}
        actions={audioMenu.payload ? audioMenuActions(audioMenu.payload) : []}
      />

      <MediaDetailsModal
        open={!!details}
        target={details?.target}
        onClose={() => setDetails(null)}
        onPrev={details?.list?.length > 1 ? () => stepDetails(-1) : undefined}
        onNext={details?.list?.length > 1 ? () => stepDetails(1) : undefined}
        hasPrev={!!details?.list && details.index > 0}
        hasNext={!!details?.list && details.index < details.list.length - 1}
        onFullSize={details?.target?.kind === 'document' ? detailsFullSize : undefined}
      />

      {/* Lightbox */}
      {lightbox && (
        <ImageLightbox
          imageUrl={lightbox.url}
          imageName={lightbox.name}
          documentId={lightbox.fileList[lightbox.fileIndex]?.id}
          initialEditMode={lightbox.editMode || false}
          onClose={() => setLightbox(null)}
          onPrev={handleLightboxPrev}
          onNext={handleLightboxNext}
          onDownload={handleLightboxDownload}
          onPublish={handlePublishFromLightbox}
          onImageEdited={() => {
            setLightbox(null);
            // A copy lands beside the original, which may be inside an open
            // folder window; those grids refetch only on their refresh key.
            desktopRef.current?.refresh();
          }}
          hasPrev={lightbox.fileIndex > 0}
          hasNext={lightbox.fileIndex < lightbox.fileList.length - 1}
        />
      )}

      <PublishModal
        open={publishDocuments.length > 0}
        documents={publishDocuments}
        onClose={() => setPublishDocuments([])}
        onFeedback={({ message, severity }) => showMessage?.(message, severity)}
      />

      {/* Feedback Snackbar */}
      <Snackbar
        open={feedback.open}
        autoHideDuration={4000}
        onClose={() => setFeedback(prev => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <MuiAlert
          elevation={6}
          variant="filled"
          onClose={() => setFeedback(prev => ({ ...prev, open: false }))}
          severity={feedback.severity}
        >
          {feedback.message}
        </MuiAlert>
      </Snackbar>

      {/* Video Player Dialog with Prev/Next + Thumbnail Strip */}
      <Dialog
        open={!!videoPlayer}
        onClose={() => setVideoPlayer(null)}
        maxWidth="md"
        fullWidth
        PaperProps={{ sx: { bgcolor: 'grey.900', borderRadius: 2, overflow: 'hidden' } }}
      >
        {videoPlayer && (() => {
          const { playlist, currentIndex, batchId } = videoPlayer;
          const title = videoPlayer.title
            || (videoPlayer.titleNumber ? t('images.video.numbered', { number: videoPlayer.titleNumber }) : t('images.video.untitled'));
          const hasPrev = currentIndex > 0;
          const hasNext = currentIndex < playlist.length - 1;
          const navigateTo = (idx) => {
            const r = playlist[idx];
            setVideoPlayer(prev => ({
              ...prev,
              url: r.url || `${VIDEO_API_BASE}/video/${batchId}/${r.video_path}`,
              currentIndex: idx,
              title: r.title || r.video_path?.split('/').pop() || null,
              titleNumber: idx + 1,
              downloadName: r.download_name,
            }));
          };
          return (
            <>
              <DialogTitle sx={{ color: 'grey.300', display: 'flex', justifyContent: 'space-between', alignItems: 'center', py: 1, px: 2 }}>
                <Typography variant="subtitle2" noWrap sx={{ flex: 1, mr: 2, color: 'grey.400' }}>
                  {title}
                  <Typography component="span" variant="caption" sx={{ ml: 1, color: 'grey.600' }}>
                    {currentIndex + 1} / {playlist.length}
                  </Typography>
                </Typography>
                <IconButton size="small" onClick={() => setVideoPlayer(null)} sx={{ color: 'grey.400' }}>
                  <CloseIcon />
                </IconButton>
              </DialogTitle>
              <DialogContent sx={{ p: 0, position: 'relative' }}>
                <Box sx={{ position: 'relative', bgcolor: 'black' }}>
                  <video
                    key={videoPlayer.url}
                    src={videoPlayer.url}
                    controls
                    autoPlay
                    loop
                    style={{ width: '100%', display: 'block', maxHeight: '65vh' }}
                  />
                  {/* Prev overlay */}
                  {hasPrev && (
                    <IconButton
                      onClick={() => navigateTo(currentIndex - 1)}
                      sx={{
                        position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)',
                        bgcolor: 'rgba(0,0,0,0.5)', color: 'white', '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' },
                      }}
                    >
                      <PlayArrowIcon sx={{ transform: 'rotate(180deg)' }} />
                    </IconButton>
                  )}
                  {/* Next overlay */}
                  {hasNext && (
                    <IconButton
                      onClick={() => navigateTo(currentIndex + 1)}
                      sx={{
                        position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
                        bgcolor: 'rgba(0,0,0,0.5)', color: 'white', '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' },
                      }}
                    >
                      <PlayArrowIcon />
                    </IconButton>
                  )}
                </Box>

                {/* Thumbnail strip */}
                {playlist.length > 1 && (
                  <Box sx={{
                    display: 'flex', gap: 0.5, px: 1, py: 1,
                    overflowX: 'auto', bgcolor: 'grey.900',
                    '&::-webkit-scrollbar': { height: 4 },
                    '&::-webkit-scrollbar-thumb': { bgcolor: 'grey.700', borderRadius: 2 },
                  }}>
                    {playlist.map((r, idx) => {
                      const thumbUrl = r.thumbnail_url || (r.thumbnail_path
                        ? `${VIDEO_API_BASE}/video/${batchId}/${r.thumbnail_path}`
                        : null);
                      return (
                        <Box
                          key={r.item_id || idx}
                          onClick={() => navigateTo(idx)}
                          sx={{
                            flexShrink: 0, width: 80, height: 45,
                            borderRadius: 1, overflow: 'hidden', cursor: 'pointer',
                            border: 2, borderColor: idx === currentIndex ? 'primary.main' : 'transparent',
                            opacity: idx === currentIndex ? 1 : 0.6,
                            transition: 'opacity 0.2s, border-color 0.2s',
                            '&:hover': { opacity: 1 },
                            bgcolor: 'grey.800',
                          }}
                        >
                          {thumbUrl ? (
                            <Box component="img" src={thumbUrl} alt={t('images.video.numbered', { number: idx + 1 })}
                              sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
                              onError={(e) => { e.target.style.display = 'none'; }}
                            />
                          ) : (
                            <Box sx={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                              <VideocamIcon sx={{ fontSize: 16, color: 'grey.500' }} />
                            </Box>
                          )}
                        </Box>
                      );
                    })}
                  </Box>
                )}
              </DialogContent>
              <DialogActions sx={{ justifyContent: 'space-between', px: 2, py: 1 }}>
                <Box sx={{ display: 'flex', gap: 1 }}>
                  <Button size="small" disabled={!hasPrev} onClick={() => navigateTo(currentIndex - 1)}>
                    {t('images.video.previous')}
                  </Button>
                  <Button size="small" disabled={!hasNext} onClick={() => navigateTo(currentIndex + 1)}>
                    {t('images.video.next')}
                  </Button>
                </Box>
                <Box sx={{ display: 'flex', gap: 1 }}>
                  <Button size="small" onClick={() => window.open(videoPlayer.url, '_blank')} startIcon={<OpenInNewIcon />}>
                    {t('images.video.open')}
                  </Button>
                  <Button size="small" onClick={() => {
                    const a = document.createElement('a');
                    a.href = videoPlayer.url;
                    a.download = videoPlayer.downloadName || title;
                    a.click();
                  }} startIcon={<DownloadIcon />}>
                    {t('images.shared.download')}
                  </Button>
                </Box>
              </DialogActions>
            </>
          );
        })()}
      </Dialog>

      {/* Video Delete Confirmation Dialog */}
      <Dialog open={Boolean(videoDeleteConfirm)} onClose={() => setVideoDeleteConfirm(null)}>
        <DialogTitle>{t('images.video.deleteTitle')}</DialogTitle>
        <DialogContent>
          <Typography>
            {t('images.video.deleteConfirm')}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setVideoDeleteConfirm(null)}>{t('images.shared.cancel')}</Button>
          <Button
            onClick={() => handleDeleteVideoBatch(videoDeleteConfirm)}
            variant="contained"
            color="error"
          >
            {t('images.shared.delete')}
          </Button>
        </DialogActions>
      </Dialog>
    </PageLayout>
  );
};

export default ImagesPage;
