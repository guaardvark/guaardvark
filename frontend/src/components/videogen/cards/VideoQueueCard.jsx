// The Video Gen Queue card: what is rendering. The batch queue, the open
// batch with its live progress, and its clips.

import React, { memo, useMemo } from "react";
import {
  Box,
  Typography,
  Button,
  Grid,
  Stack,
  Divider,
  Chip,
  IconButton,
  Tooltip,
  Card,
  CardContent,
  CardActions,
  LinearProgress,
} from "@mui/material";
import QualityFlagsPill from "../QualityFlagsPill";
import VlmReviewPill from "../VlmReviewPill";
import ColourMatchPill from "../ColourMatchPill";
import KeyframeMatchPill from "../KeyframeMatchPill";
import ClipReviewHold, { ReviewStatePill } from "../ClipReviewHold";
import RenderFailureNote from "../RenderFailureNote";
import LiveLatentPreview from "../LiveLatentPreview";
import { videoGenStageLabel } from "../stageLabels";
import {
  PlayArrow as PlayIcon,
  Refresh as RefreshIcon,
  Download as DownloadIcon,
  VideoLibrary as VideoIcon,
  DriveFileRenameOutline as RenameIcon,
  Settings as SettingsIcon,
  Close as CloseIcon,
  Pause as PauseIcon,
  OpenInNew as OpenInNewIcon,
} from "@mui/icons-material";
import { formatUiError } from "../../../utils/uiError";
import EntityContextMenu from "../../common/EntityContextMenu";
import { API_BASE, batchStatusLabel, PathFromUrl, encodePathSegments, statusSx } from "../videoGenPage";
import { useUiTranslation } from "../../../i18n";
import useContextMenu from "../../../hooks/useContextMenu";
import { useUnifiedProgress } from "../../../contexts/UnifiedProgressContext";

function VideoQueueCard({
  batchStatus,
  confirmCancelBatch,
  confirmDeleteVideo,
  currentResults,
  handleAdjustRetry,
  handleApproveClip,
  handleCancelBatch,
  handlePauseBatch,
  handleResumeBatch,
  handleClearCompletedQueue,
  handleCombineFrames,
  handleDeleteBatch,
  handleDownloadBatch,
  handleRenameVideo,
  handleRerenderClip,
  handleRetryBatch,
  openResultPlayer,
  queue,
  resultMenuActions,
  setConfirmAction,
}) {
  const { t } = useUiTranslation();
  const resultMenu = useContextMenu();
  // Live per-step progress for the currently-rendering video. The batch bar only
  // moves when a whole clip finishes; THIS shows "denoising 12/50" inside the
  // active clip, fed by the ComfyUI ws progress bridge (process_type=video_render,
  // process_id=item_id). Single GPU = at most one active render, so we just take
  // the freshest non-terminal video_render process (preferring this batch's).
  const { getProcessesByType, activeProcesses, getPreviewUrl } = useUnifiedProgress();
  const activeStep = useMemo(() => {
    if (!batchStatus || batchStatus.status !== "running") return null;
    const live = (getProcessesByType("video_render") || []).filter((p) =>
      !["complete", "end", "error", "cancelled"].includes(p.status)
    );
    if (!live.length) return null;
    const mine = live.filter((p) => p.additional_data?.batch_id === batchStatus.batch_id);
    const pool = mine.length ? mine : live;
    return pool.reduce((a, b) => (b.timestamp > a.timestamp ? b : a));
  }, [batchStatus, getProcessesByType, activeProcesses]);
  return (
          <Box sx={statusSx}>
    {/* Batch Queue panel — live view of what's running and what's stacked behind it */}
    {queue.length > 0 && (
      <Card sx={{ mb: 3, boxShadow: 2, borderRadius: 2 }}>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between", mb: 2 }}>
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              {t("videoGenerator.queue.title")}
            </Typography>
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
              {queue.some((q) => ["completed", "error", "cancelled"].includes(q.status)) && (
                <Button
                  size="small"
                  variant="outlined"
                  color="inherit"
                  onClick={handleClearCompletedQueue}
                  sx={{ textTransform: "none", borderRadius: 1 }}
                >
                  {t("videoGenerator.queue.clearCompleted")}
                </Button>
              )}
              <Chip
                label={t("videoGenerator.queue.active", { count: queue.filter(q => q.status === 'queued' || q.status === 'running' || q.status === 'pending').length })}
                size="small"
                color="primary"
                variant="outlined"
              />
            </Stack>
          </Stack>
          <Stack spacing={1}>
            {queue.map((q, idx) => {
              const slotTag = `#${idx + 1}`;
              const pct = q.total_videos > 0
                ? Math.round(((q.completed_videos + q.failed_videos) / q.total_videos) * 100)
                : 0;
              const chipColor =
                q.status === 'running' ? 'primary' :
                q.status === 'queued' ? 'default' :
                q.status === 'completed' ? 'success' :
                q.status === 'cancelled' ? 'warning' :
                q.status === 'paused' ? 'warning' :
                q.status === 'error' ? 'error' : 'default';
              const cancellable = q.status === 'queued' || q.status === 'running' || q.status === 'paused';
              const pausing = Boolean(q.pause_requested);
              const pausable = (q.status === 'queued' || q.status === 'running') && !pausing;
              const resumable = q.status === 'paused' || pausing;
              return (
                <Box
                  key={q.batch_id}
                  sx={{
                    p: 1.5,
                    border: '1px solid',
                    borderColor: q.is_running ? 'primary.main' : 'divider',
                    borderRadius: 1,
                    bgcolor: q.is_running ? 'action.hover' : 'transparent',
                  }}
                >
                  <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
                    <Chip
                      label={slotTag}
                      size="small"
                      variant="outlined"
                      sx={{ minWidth: 44, fontFamily: 'monospace' }}
                    />
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Typography variant="body2" noWrap title={q.batch_id}>
                        {q.display_name || q.batch_id}
                      </Typography>
                      <Typography variant="caption" sx={{ color: "text.secondary" }}>
                        {t("videoGenerator.queue.videos", { done: q.completed_videos + q.failed_videos, total: q.total_videos })}
                        {q.failed_videos > 0 ? ` ${t("videoGenerator.queue.failed", { count: q.failed_videos })}` : ''}
                      </Typography>
                    </Box>
                    <Chip
                      label={(pausing ? t("videoGenerator.queue.pausing") : batchStatusLabel(q.status)).toUpperCase()}
                      size="small"
                      color={chipColor}
                    />
                    {pausable && (
                      <Tooltip
                        title={q.status === 'running' ? t("videoGenerator.queue.pauseRunning") : t("videoGenerator.queue.pause")}
                        arrow
                      >
                        <IconButton
                          size="small"
                          onClick={() => handlePauseBatch(q.batch_id)}
                          aria-label={t("videoGenerator.queue.pauseAria")}
                          data-testid={`batch-pause-${q.batch_id}`}
                        >
                          <PauseIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    {resumable && (
                      <Tooltip title={pausing ? t("videoGenerator.queue.keepGoing") : t("videoGenerator.queue.resume")} arrow>
                        <IconButton
                          size="small"
                          onClick={() => handleResumeBatch(q.batch_id)}
                          aria-label={t("videoGenerator.queue.resumeAria")}
                          data-testid={`batch-resume-${q.batch_id}`}
                        >
                          <PlayIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    {cancellable && (
                      <Tooltip
                        title={q.status === 'running' ? t("videoGenerator.queue.cancelRunning") : t("videoGenerator.queue.remove")}
                        arrow
                      >
                        <IconButton
                          size="small"
                          onClick={() => confirmCancelBatch(q)}
                          aria-label={t("videoGenerator.queue.cancelAria")}
                        >
                          <CloseIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                  </Stack>
                  {q.status === 'running' && (
                    <LinearProgress
                      variant="determinate"
                      value={pct}
                      sx={{ mt: 1, height: 4, borderRadius: 2 }}
                    />
                  )}
                  {q.error && (
                    <Typography variant="caption" color="error" sx={{ mt: 0.5, display: 'block' }}>
                      {formatUiError(q.error)}
                    </Typography>
                  )}
                </Box>
              );
            })}
          </Stack>
        </CardContent>
      </Card>
    )}

    {/* Active batch status */}
    {batchStatus ? (
      <Card sx={{ 
        mb: 3,
        boxShadow: 2,
        borderRadius: 2
      }}>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography 
            variant="h6" 
            sx={{ 
              fontWeight: 600,
              mb: 2,
              color: 'text.primary'
            }}
          >
            {t("videoGenerator.progress.title")}
          </Typography>

          <Box sx={{ mb: 2 }}>
            <Typography variant="body2" sx={{ color: "text.secondary" }}>
              {t("videoGenerator.progress.batchId", { id: batchStatus.batch_id })}
            </Typography>
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", mt: 1, flexWrap: "wrap" }}>
              <Chip
                label={batchStatusLabel(batchStatus.status).toUpperCase()}
                color={batchStatus.status === 'running' ? 'primary' :
                       batchStatus.status === 'completed' ? 'success' :
                       batchStatus.status === 'error' ? 'error' :
                       batchStatus.status === 'cancelled' ? 'warning' : 'default'}
                size="small"
              />
              {batchStatus.stage && (
                <Chip
                  label={videoGenStageLabel(batchStatus.stage)}
                  size="small"
                  variant="outlined"
                  color={batchStatus.stage === "gpu_wait" ? "warning" : "default"}
                />
              )}
              {(batchStatus.status === 'running' || batchStatus.status === 'pending' || batchStatus.status === 'queued') && (
                <Button
                  size="small"
                  color="warning"
                  variant="outlined"
                  onClick={() => setConfirmAction({
                    title: t("videoGenerator.confirm.cancelBatchTitle"),
                    description: t("videoGenerator.confirm.cancelBatchText"),
                    facts: [{ label: t("videoGenerator.confirm.batch"), value: batchStatus.display_name || batchStatus.batch_id.slice(0, 8) }],
                    keeps: t("videoGenerator.confirm.cancelBatchKeeps"),
                    confirmLabel: t("videoGenerator.confirm.cancelBatch"),
                    run: () => handleCancelBatch(batchStatus.batch_id),
                  })}
                >
                  {t("videoGenerator.progress.cancel")}
                </Button>
              )}
            </Stack>
          </Box>

          <Box sx={{ mb: 2 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
              <Typography variant="body2">
                {t("videoGenerator.progress.progress", { done: batchStatus.completed_videos || 0, total: batchStatus.total_videos || 0 })}
                {batchStatus.stage === "gpu_wait" ? ` — ${t("videoGenerator.progress.waitingForGpu")}` : ""}
              </Typography>
              <Typography variant="body2">
                {typeof batchStatus.progress_pct === "number"
                  ? Math.round(batchStatus.progress_pct)
                  : Math.round(((batchStatus.completed_videos || 0) / (batchStatus.total_videos || 1)) * 100)}%
              </Typography>
            </Box>
            <LinearProgress
              variant="determinate"
              value={
                typeof batchStatus.progress_pct === "number"
                  ? batchStatus.progress_pct
                  : ((batchStatus.completed_videos || 0) / (batchStatus.total_videos || 1)) * 100
              }
            />
          </Box>

          {/* Live current-step (per-clip) progress from the ComfyUI ws bridge */}
          {activeStep && (
            <Box sx={{ mb: 2 }}>
              <LiveLatentPreview src={getPreviewUrl(activeStep.job_id)} />
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography variant="caption" sx={{ color: "text.secondary", textTransform: 'capitalize' }}>
                  {activeStep.message || t("videoGenerator.progress.rendering")}
                </Typography>
                <Typography variant="caption" sx={{ color: "text.secondary" }}>
                  {activeStep.progress || 0}%
                </Typography>
              </Box>
              <LinearProgress
                variant="determinate"
                value={activeStep.progress || 0}
                color="secondary"
                sx={{ height: 4, borderRadius: 2 }}
              />
            </Box>
          )}

          {(batchStatus.status === 'completed' || batchStatus.status === 'error' || batchStatus.status === 'cancelled') && (
            // Buttons start at their label's width and share what is left. A
            // button that does not fit moves to the next row, and a label wider
            // than the whole card wraps inside its button, so labels never
            // spill onto a neighbour.
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2, '& > .MuiButton-root': { maxWidth: '100%' } }}>
              {batchStatus.status === 'completed' && (
                <Button
                  startIcon={<DownloadIcon />}
                  variant="contained"
                  sx={{ flex: '2 1 auto' }}
                  onClick={() => handleDownloadBatch(batchStatus.batch_id)}
                >
                  {t("videoGenerator.menu.downloadAll")}
                </Button>
              )}
              <Button
                startIcon={<SettingsIcon />}
                variant="outlined"
                sx={{ flex: '1 1 auto' }}
                onClick={() => handleAdjustRetry(batchStatus.batch_id)}
              >
                {t("videoGenerator.menu.adjustRetry")}
              </Button>
              <Button
                startIcon={<CloseIcon />}
                variant="outlined"
                color="error"
                sx={{ flex: '1 1 auto' }}
                onClick={() => setConfirmAction({
                  title: t("videoGenerator.confirm.deleteBatchTitle", { name: batchStatus.display_name || batchStatus.batch_id.slice(0, 8) }),
                  description: t("videoGenerator.confirm.deleteBatchText"),
                  facts: [{ label: t("videoGenerator.confirm.videos"), value: String(batchStatus.completed_videos ?? batchStatus.total_videos ?? t("videoGenerator.confirm.all")) }],
                  keeps: t("videoGenerator.confirm.deleteBatchKeeps"),
                  confirmLabel: t("videoGenerator.confirm.deleteBatch"),
                  run: () => handleDeleteBatch(batchStatus.batch_id),
                })}
              >
                {t("videoGenerator.progress.delete")}
              </Button>
            </Box>
          )}

          <Divider sx={{ my: 2 }} />

          <Grid container spacing={2}>
            {currentResults.map((res, idx) => {
              const videoUrl = res.video_path
                ? `${API_BASE}/batch-video/video/${batchStatus.batch_id}/${encodePathSegments(PathFromUrl(res.video_path))}`
                : null;
              const thumbUrl = res.thumbnail_path
                ? `${API_BASE}/batch-video/video/${batchStatus.batch_id}/${encodePathSegments(PathFromUrl(res.thumbnail_path))}`
                : null;
              const liveThumb = (!thumbUrl && activeStep && res.item_id === activeStep.job_id)
                ? getPreviewUrl(activeStep.job_id)
                : null;
              return (
              <Grid key={res.item_id} size={{ xs: 12, sm: 6 }}>
                <Card
                  variant="outlined"
                  onContextMenu={(e) => resultMenu.open(e, { res, idx, videoUrl })}
                >
                  <CardContent sx={{ pb: 1 }}>
                    <Box
                      sx={{
                        position: "relative",
                        width: "100%",
                        aspectRatio: "16/9",
                        borderRadius: 1,
                        overflow: "hidden",
                        mb: 1,
                        bgcolor: "grey.900",
                        cursor: videoUrl ? "pointer" : "default",
                      }}
                      onClick={() => {
                        if (!videoUrl) return;
                        openResultPlayer(res, idx, videoUrl);
                      }}
                    >
                      {thumbUrl || liveThumb ? (
                        <Box
                          component="img"
                          src={thumbUrl || liveThumb}
                          alt={t("videoGenerator.results.thumbAlt")}
                          sx={{ width: "100%", height: "100%", objectFit: "cover" }}
                        />
                      ) : (
                        <Box sx={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
                          <VideoIcon color="action" sx={{ fontSize: 40 }} />
                        </Box>
                      )}
                      {videoUrl && (
                        <Box sx={{
                          position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
                          display: "flex", alignItems: "center", justifyContent: "center",
                          bgcolor: "rgba(0,0,0,0.3)", opacity: 0, transition: "opacity 0.2s",
                          "&:hover": { opacity: 1 },
                        }}>
                          <PlayIcon sx={{ fontSize: 48, color: "white" }} />
                        </Box>
                      )}
                    </Box>
                    <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", flexWrap: "wrap" }}>
                      {/* A held clip is not "Ready": its pill says it waits for a person. */}
                      {res.review?.state !== "needs_review" && (
                        <Chip
                          label={res.success ? t("videoGenerator.results.ready") : t("videoGenerator.results.error")}
                          color={res.success ? "success" : "error"}
                          size="small"
                        />
                      )}
                      {res.success && <ReviewStatePill review={res.review} />}
                      {res.frame_paths?.length > 0 && (
                        <Chip label={`${res.frame_paths.length}f`} size="small" variant="outlined" />
                      )}
                      <VlmReviewPill review={res.metadata?.quality?.vlm_review} />
                      <KeyframeMatchPill quality={res.metadata?.quality} />
                      <ColourMatchPill quality={res.metadata?.quality} />
                      <QualityFlagsPill quality={res.metadata?.quality} />
                    </Stack>
                    {!res.success && <RenderFailureNote failure={res.failure} error={res.error} />}
                    {!res.success && ["pending", "queued", "running"].includes(batchStatus?.status) && (
                      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
                        {t("videoGenerator.results.batchContinues")}
                      </Typography>
                    )}
                    {res.success && (
                      <ClipReviewHold
                        review={res.review}
                        onApprove={() => handleApproveClip(batchStatus.batch_id, res.item_id)}
                        onRerender={() => handleRerenderClip(batchStatus.batch_id, res.item_id)}
                      />
                    )}
                  </CardContent>
                  <CardActions sx={{ pt: 0 }}>
                    {videoUrl && (
                      <Button
                        size="small"
                        variant="contained"
                        startIcon={<PlayIcon />}
                        onClick={() => openResultPlayer(res, idx, videoUrl)}
                      >
                        {t("videoGenerator.menu.play")}
                      </Button>
                    )}
                    {videoUrl && (
                      <Tooltip title={t("videoGenerator.menu.openInNewTab")}>
                        <IconButton size="small" onClick={() => window.open(videoUrl, "_blank")}>
                          <OpenInNewIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    {res.video_path && (
                      <>
                        <IconButton
                          size="small"
                          onClick={() => handleRenameVideo(batchStatus.batch_id, PathFromUrl(res.video_path))}
                        >
                          <RenameIcon fontSize="small" />
                        </IconButton>
                        <IconButton
                          size="small"
                          onClick={() => confirmDeleteVideo(batchStatus.batch_id, res)}
                        >
                          <CloseIcon fontSize="small" />
                        </IconButton>
                      </>
                    )}
                    {!res.video_path && res.frame_paths?.length > 0 && (
                      <Button
                        size="small"
                        onClick={() => handleCombineFrames(batchStatus.batch_id, res.item_id)}
                      >
                        {t("videoGenerator.menu.combineFrames")}
                      </Button>
                    )}
                  </CardActions>
                </Card>
              </Grid>
              );
            })}
          </Grid>
          <EntityContextMenu
            anchorPosition={resultMenu.anchorPosition}
            onClose={resultMenu.close}
            actions={resultMenu.isOpen ? resultMenuActions(resultMenu.payload) : []}
          />

          {(batchStatus.status === 'error' || batchStatus.status === 'cancelled') && (
            <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
              {batchStatus.retry_data ? (
                <Button
                  startIcon={<RefreshIcon />}
                  variant="contained"
                  color="primary"
                  onClick={() => handleRetryBatch(batchStatus.batch_id)}
                >
                  {t("videoGenerator.progress.retryBatch")}
                </Button>
              ) : (
                // No saved config (legacy batch) — honest disabled state, not placebo.
                // Disabled buttons swallow hover events, so wrap in a span for the Tooltip.
                <Tooltip title={t("videoGenerator.progress.noRetryData")}>
                  <span>
                    <Button startIcon={<RefreshIcon />} variant="outlined" color="primary" disabled>
                      {t("videoGenerator.progress.retryBatch")}
                    </Button>
                  </span>
                </Tooltip>
              )}
            </Box>
          )}
        </CardContent>
      </Card>
    ) : (
      <Card sx={{
        mb: 3,
        boxShadow: 2,
        borderRadius: 2
      }}>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography 
            variant="h6" 
            sx={{ 
              fontWeight: 600,
              mb: 2,
              color: 'text.primary'
            }}
          >
            {t("videoGenerator.progress.statusTitle")}
          </Typography>
          <Typography variant="body2" sx={{ color: "text.secondary", textAlign: 'center', py: 3 }}>
            {t("videoGenerator.progress.none")}
          </Typography>
        </CardContent>
      </Card>
    )}

          </Box>
  );
}

export default memo(VideoQueueCard);
