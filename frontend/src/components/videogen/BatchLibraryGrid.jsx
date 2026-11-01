// frontend/src/components/videogen/BatchLibraryGrid.jsx
// The Video Gen library: one stacked-thumbnail card per batch.
//
// Memoized because the page above it re-renders on every keystroke and status
// poll, and the card count grows with every render a person makes. At 314
// batches, drawing them each time held the main thread for about 400 ms, which
// made typed letters appear seconds late. Keep every prop referentially stable.

import React, { memo } from "react";
import { Box, Chip, Grid, IconButton, Tooltip, Typography } from "@mui/material";
import {
  Close as CloseIcon,
  PlayArrow as PlayIcon,
  VideoLibrary as VideoIcon,
} from "@mui/icons-material";
import { useUiTranslation } from "../../i18n";
import { formatVideoStamp } from "../../utils/videoBatchStamp";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

function BatchLibraryGrid({ batches, onOpen, onOpenMenu, onDelete, statusLabel }) {
  const { t } = useUiTranslation();

  return (
    <Grid container spacing={2}>
      {batches.map((b) => {
        // Date and time, as Image Gen's cards show it: cards are named
        // after the first prompt, so two runs of one prompt tell apart here.
        const dateStr = formatVideoStamp(b.start_time || b.end_time);
        const videoCount = b.completed_videos ?? 0;
        const rawName = b.display_name || t("videoGenerator.library.batchName", { id: b.batch_id.slice(0, 8) });
        // Backend trims new names to ~40 chars; legacy batches may hold a
        // full prompt. Cap defensively so a long name can't push the card down.
        const label = rawName.length > 42 ? rawName.slice(0, 41).trimEnd() + '…' : rawName;
        return (
        <Grid item xs={6} sm={4} md={3} key={b.batch_id}>
          <Box
            onClick={() => onOpen(b)}
            onContextMenu={(e) => onOpenMenu(e, b)}
            sx={{
              cursor: 'pointer',
              position: 'relative',
              borderRadius: 2,
              overflow: 'hidden',
              transition: 'transform 0.2s, box-shadow 0.2s',
              '&:hover': {
                transform: 'translateY(-4px)',
                boxShadow: 6,
                '& .batch-overlay': { opacity: 1 },
                '& .batch-delete': { opacity: 1 },
              },
            }}
          >
            {/* Stacked thumbnail effect */}
            <Box sx={{ position: 'relative', aspectRatio: '16/9' }}>
              {/* Background layers for stack effect */}
              {videoCount > 2 && (
                <Box sx={{
                  position: 'absolute', top: -6, left: 6, right: -6, bottom: 6,
                  bgcolor: 'grey.800', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
                }} />
              )}
              {videoCount > 1 && (
                <Box sx={{
                  position: 'absolute', top: -3, left: 3, right: -3, bottom: 3,
                  bgcolor: 'grey.850', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
                }} />
              )}
              {/* Main thumbnail */}
              <Box sx={{
                position: 'relative', width: '100%', height: '100%',
                bgcolor: 'grey.900', borderRadius: 1.5, overflow: 'hidden',
                border: 1, borderColor: 'grey.700',
              }}>
                {videoCount > 0 ? (
                  <Box
                    component="img"
                    src={`${API_BASE}/batch-video/preview/${b.batch_id}`}
                    alt={t("videoGenerator.library.previewAlt")}
                    sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
                    onError={(e) => { e.target.style.display = 'none'; }}
                  />
                ) : (
                  <Box sx={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <VideoIcon sx={{ fontSize: 36, color: 'grey.600' }} />
                  </Box>
                )}
                {/* Hover overlay */}
                <Box className="batch-overlay" sx={{
                  position: 'absolute', inset: 0,
                  bgcolor: 'rgba(0,0,0,0.5)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  opacity: 0, transition: 'opacity 0.2s',
                }}>
                  <PlayIcon sx={{ fontSize: 40, color: 'white' }} />
                </Box>
                {/* Video count badge */}
                <Chip
                  label={t("videoGenerator.library.videoCount", { count: videoCount })}
                  size="small"
                  sx={{
                    position: 'absolute', top: 6, right: 6,
                    height: 20, fontSize: '0.65rem',
                    bgcolor: 'rgba(0,0,0,0.7)', color: 'white',
                    '& .MuiChip-label': { px: 0.75 },
                  }}
                />
                {/* Status indicator */}
                {b.status !== 'completed' && (
                  <Chip
                    label={statusLabel(b.status)}
                    size="small"
                    color={b.status === 'error' ? 'error' : b.status === 'cancelled' ? 'warning' : 'info'}
                    sx={{
                      position: 'absolute', bottom: 6, left: 6,
                      height: 18, fontSize: '0.6rem',
                    }}
                  />
                )}
                {/* Delete batch — reveals on card hover (X per the no-trash-icon rule) */}
                <Tooltip title={t("videoGenerator.library.deleteBatch")}>
                  <IconButton
                    size="small"
                    className="batch-delete"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(b);
                    }}
                    sx={{
                      position: 'absolute', top: 4, left: 4,
                      width: 24, height: 24,
                      bgcolor: 'rgba(0,0,0,0.6)', color: 'white',
                      opacity: 0, transition: 'opacity 0.2s',
                      '&:hover': { bgcolor: 'error.main' },
                    }}
                  >
                    <CloseIcon sx={{ fontSize: 16 }} />
                  </IconButton>
                </Tooltip>
              </Box>
            </Box>
            {/* Batch label */}
            <Box sx={{ pt: 0.75, px: 0.5 }}>
              <Typography variant="caption" noWrap title={dateStr ? `${rawName} · ${dateStr}` : rawName} sx={{ fontWeight: 500, display: 'block' }}>
                {label}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                {dateStr}
              </Typography>
            </Box>
          </Box>
        </Grid>
        );
      })}
    </Grid>
  );
}

export default memo(BatchLibraryGrid);
