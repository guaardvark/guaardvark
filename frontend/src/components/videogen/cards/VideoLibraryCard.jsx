// The Video Gen Results card: what has been made, one stacked thumbnail per batch.

import React, { memo } from "react";
import {
  Box,
  Typography,
  Button,
  Stack,
  IconButton,
  Card,
  CardContent,
} from "@mui/material";
import BatchLibraryGrid from "../BatchLibraryGrid";
import {
  Refresh as RefreshIcon,
  VideoLibrary as VideoIcon,
} from "@mui/icons-material";
import EntityContextMenu from "../../common/EntityContextMenu";
import { batchStatusLabel, statusSx } from "../videoGenPage";
import { useUiTranslation } from "../../../i18n";
import useContextMenu from "../../../hooks/useContextMenu";

function VideoLibraryCard({
  batchMenuActions,
  batches,
  confirmCancelBatch,
  fetchBatches,
  handleRetryBatch,
  openBatch,
  requestDeleteBatch,
}) {
  const { t } = useUiTranslation();
  const batchMenu = useContextMenu();
  return (
          <Box sx={statusSx}>
    {/* Batch History — Stacked Thumbnail Gallery */}
    <Card sx={{
      boxShadow: 2,
      borderRadius: 2
    }}>
      <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            {t("videoGenerator.library.title")}
          </Typography>
          <IconButton size="small" onClick={fetchBatches}>
            <RefreshIcon />
          </IconButton>
        </Stack>
        <Box sx={{ maxHeight: 520, overflowY: 'auto', pr: 0.5 }}>
        <BatchLibraryGrid
          batches={batches}
          onOpen={openBatch}
          onOpenMenu={batchMenu.open}
          onDelete={requestDeleteBatch}
          statusLabel={batchStatusLabel}
        />
        </Box>
        <EntityContextMenu
          anchorPosition={batchMenu.anchorPosition}
          onClose={batchMenu.close}
          actions={batchMenu.isOpen ? batchMenuActions(batchMenu.payload) : []}
        />
        {batches.length === 0 && (
          <Box sx={{ textAlign: 'center', py: 4 }}>
            <VideoIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
            <Typography variant="body2" color="text.secondary">
              {t("videoGenerator.library.empty")}
            </Typography>
          </Box>
        )}
      </CardContent>
    </Card>

    {/* Legacy batch controls — keep for running/pending batches */}
    {batches.filter(b => b.status === "running" || b.status === "pending" || b.status === "processing").map((b) => (
      <Box key={`ctrl-${b.batch_id}`} sx={{ mt: 1 }}>
        <Button size="small" color="warning" variant="outlined" onClick={() => confirmCancelBatch(b)}>
          {t("videoGenerator.library.cancelNamed", { name: b.display_name || b.batch_id.slice(0, 8) })}
        </Button>
      </Box>
    ))}
    {/* Retry for failed batches that have persisted original config */}
    {batches.filter(b => b.status === "error" && b.can_retry).map((b) => (
      <Box key={`retry-${b.batch_id}`} sx={{ mt: 1 }}>
        <Button
          size="small"
          color="primary"
          variant="contained"
          startIcon={<RefreshIcon />}
          onClick={() => handleRetryBatch(b.batch_id)}
        >
          {t("videoGenerator.library.retryNamed", { name: b.display_name || b.batch_id.slice(0, 8) })}
        </Button>
      </Box>
    ))}
          </Box>
  );
}

export default memo(VideoLibraryCard);
