// Video Dashboard Card - the latest clips as thumbnails that play on hover.

import React from "react";
import { Box, Button, IconButton, Tooltip } from "@mui/material";
import { Add, PermMedia, Refresh } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
import MediaThumbGrid from "./MediaThumbGrid";
import { useRecentMedia } from "./recentMedia";
import { useUiTranslation } from "../../i18n";

export const VIDEO_GEN_PATH = "/video";
export const MEDIA_LIBRARY_PATH = "/images";

const buttonSx = { minWidth: "100px", textTransform: "none", fontSize: "0.75rem", py: 0.5 };

const VideoGenerationCard = React.forwardRef(
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
    const latest = useRecentMedia("video");

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.videogen.title")}
        {...props}
        contextMenuActions={[
          { label: t("dashboard.videogen.newVideo"), onClick: () => navigate(VIDEO_GEN_PATH) },
          { label: t("dashboard.videogen.library"), onClick: () => navigate(MEDIA_LIBRARY_PATH) },
        ]}
      >
        <Box sx={{ mb: 2, display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            variant="contained"
            size="small"
            startIcon={<Add />}
            onClick={() => navigate(VIDEO_GEN_PATH)}
            sx={buttonSx}
            className="non-draggable"
          >
            {t("dashboard.videogen.newVideo")}
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<PermMedia />}
            onClick={() => navigate(MEDIA_LIBRARY_PATH)}
            sx={buttonSx}
            className="non-draggable"
          >
            {t("dashboard.videogen.library")}
          </Button>
          <Tooltip title={t("dashboard.refreshData")}>
            <IconButton size="small" onClick={() => latest.refresh()} className="non-draggable">
              <Refresh fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>

        <MediaThumbGrid
          video
          items={latest.items}
          loading={latest.loading}
          error={latest.error}
          emptyText={t("dashboard.videogen.empty")}
          onChanged={() => latest.refresh()}
        />
      </DashboardCardWrapper>
    );
  },
);

VideoGenerationCard.displayName = "VideoGenerationCard";
export default VideoGenerationCard;
