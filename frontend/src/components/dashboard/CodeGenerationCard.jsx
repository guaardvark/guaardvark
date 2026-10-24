// frontend/src/components/dashboard/CodeGenerationCard.jsx
// Code Generation Dashboard Card - Quick access to code generation functionality

import React from "react";
import { Box, Button, IconButton, Tooltip } from "@mui/material";
import { Add, PlayArrow, Refresh } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
import RecentRunsList from "./RecentRunsList";
import { loadJobRows, useRecentRuns } from "./recentRuns";
import { useUiTranslation } from "../../i18n";

const loadCodeJobs = () => loadJobRows("code_generation");

const CodeGenerationCard = React.forwardRef(
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
    const recent = useRecentRuns(loadCodeJobs);

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.codegen.title")}
        {...props}
        contextMenuActions={[
          { label: t("dashboard.codegen.newCode"), onClick: () => navigate("/code-editor") },
        ]}
      >
        {/* Quick Actions */}
        <Box sx={{ mb: 2, display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            variant="contained"
            size="small"
            startIcon={<Add />}
            onClick={() => navigate("/code-editor")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            {t("dashboard.codegen.newCode")}
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<PlayArrow />}
            onClick={() => navigate("/code-editor")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            {t("dashboard.codegen.debug")}
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
          emptyText={t("dashboard.codegen.empty")}
          onOpen={navigate}
          viewAll={{ label: t("dashboard.viewAllJobs"), path: "/tasks" }}
        />
      </DashboardCardWrapper>
    );
  },
);

CodeGenerationCard.displayName = "CodeGenerationCard";
export default CodeGenerationCard;
