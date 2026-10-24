// frontend/src/components/dashboard/CSVGenerationCard.jsx
// CSV Generation Dashboard Card - Quick access to CSV generation functionality

import React from "react";
import { Box, Button, IconButton, Tooltip } from "@mui/material";
import { Add, Upload, Refresh } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
import RecentRunsList from "./RecentRunsList";
import { isCsvJob, loadJobRows, useRecentRuns } from "./recentRuns";
import { useUiTranslation } from "../../i18n";

// CSV runs are file-generation jobs that write a .csv (File Generation page, Jobs page).
const loadCsvJobs = () => loadJobRows("file_generation", isCsvJob);

const CSVGenerationCard = React.forwardRef(
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
    const recent = useRecentRuns(loadCsvJobs);

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.csvgen.title")}
        {...props}
        contextMenuActions={[
          { label: t("dashboard.csvgen.newCsv"), onClick: () => navigate("/file-generation") },
        ]}
      >
        {/* Quick Actions */}
        <Box sx={{ mb: 2, display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            variant="contained"
            size="small"
            startIcon={<Add />}
            onClick={() => navigate("/file-generation")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            {t("dashboard.csvgen.newCsv")}
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<Upload />}
            onClick={() => navigate("/file-generation")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            {t("dashboard.csvgen.import")}
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
          emptyText={t("dashboard.csvgen.empty")}
          onOpen={navigate}
          viewAll={{ label: t("dashboard.viewAllJobs"), path: "/tasks" }}
        />
      </DashboardCardWrapper>
    );
  },
);

CSVGenerationCard.displayName = "CSVGenerationCard";
export default CSVGenerationCard;
