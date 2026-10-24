// frontend/src/components/dashboard/ImageGenerationCard.jsx
// Image Generation Dashboard Card - Quick access to image generation functionality

import React from "react";
import { Box, Button, IconButton, Tooltip } from "@mui/material";
import { Add, PlayArrow, Refresh } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
import RecentRunsList from "./RecentRunsList";
import { loadImageBatchRows, useRecentRuns } from "./recentRuns";
import { useUiTranslation } from "../../i18n";

// The Image Gen tab; BatchImageGeneratorPage reads ?mode= to pick its input mode.
export const IMAGE_GEN_PATH = "/batch-images";
export const IMAGE_GEN_BATCH_PATH = "/batch-images?mode=bulk";

const ImageGenerationCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      ...props
    },
    ref,
  ) => {
    const { t } = useUiTranslation();
    const navigate = useNavigate();
    const recent = useRecentRuns(loadImageBatchRows);

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.imggen.title")}
        {...props}
        contextMenuActions={[
          { label: t("dashboard.imggen.newImages"), onClick: () => navigate(IMAGE_GEN_PATH) },
          { label: t("dashboard.imggen.batchMode"), onClick: () => navigate(IMAGE_GEN_BATCH_PATH) },
        ]}
      >
        {/* Quick Actions */}
        <Box sx={{ mb: 2, display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            variant="contained"
            size="small"
            startIcon={<Add />}
            onClick={() => navigate(IMAGE_GEN_PATH)}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            {t("dashboard.imggen.newImages")}
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<PlayArrow />}
            onClick={() => navigate(IMAGE_GEN_BATCH_PATH)}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            {t("dashboard.imggen.batchMode")}
          </Button>
          <Tooltip title={t("dashboard.refreshData")}>
            <IconButton
              size="small"
              onClick={recent.refresh}
              className="non-draggable"
            >
              <Refresh fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>

        <RecentRunsList
          rows={recent.rows}
          loading={recent.loading}
          error={recent.error}
          emptyText={t("dashboard.imggen.empty")}
          onOpen={navigate}
          viewAll={{ label: t("dashboard.imggen.viewAll"), path: IMAGE_GEN_PATH }}
        />
      </DashboardCardWrapper>
    );
  },
);

ImageGenerationCard.displayName = "ImageGenerationCard";
export default ImageGenerationCard;
