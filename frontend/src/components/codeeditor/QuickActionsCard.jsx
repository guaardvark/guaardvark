// frontend/src/components/codeeditor/QuickActionsCard.jsx
// Quick action buttons for code editor

import React, { useCallback } from "react";
import {
  Box,
  Button,
  Stack,
  Tooltip,
} from "@mui/material";
import {
  PlayArrow,
  FormatAlignLeft,
  BugReport,
  Build,
} from "@mui/icons-material";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";
import { useUiTranslation } from "../../i18n";

const QuickActionsCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      openTabs,
      activeTabIndex,
      ...props
    },
    ref
  ) => {
    const { t } = useUiTranslation();
    const _currentTab = openTabs?.[activeTabIndex];

    const handleAction = useCallback((action) => {
      console.log(`${action} action not implemented yet`);
    }, []);

    return (
      <DashboardCardWrapper
        ref={ref}
        title={t("codeEditor.quickActions.title")}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        style={style}
        {...props}
      >
        <Box sx={{ p: 1 }}>
          <Stack spacing={1}>
            <Tooltip title={t("codeEditor.quickActions.runUnavailable")}>
              <span>
                <Button
                  fullWidth
                  variant="contained"
                  startIcon={<PlayArrow />}
                  onClick={() => handleAction('run')}
                  disabled={true}
                >
                  {t("codeEditor.quickActions.run")}
                </Button>
              </span>
            </Tooltip>

            <Tooltip title={t("codeEditor.quickActions.formatUnavailable")}>
              <span>
                <Button
                  fullWidth
                  variant="outlined"
                  startIcon={<FormatAlignLeft />}
                  onClick={() => handleAction('format')}
                  disabled={true}
                >
                  {t("codeEditor.quickActions.format")}
                </Button>
              </span>
            </Tooltip>

            <Tooltip title={t("codeEditor.quickActions.debugUnavailable")}>
              <span>
                <Button
                  fullWidth
                  variant="outlined"
                  startIcon={<BugReport />}
                  onClick={() => handleAction('debug')}
                  disabled={true}
                >
                  {t("codeEditor.quickActions.debug")}
                </Button>
              </span>
            </Tooltip>

            <Tooltip title={t("codeEditor.quickActions.buildUnavailable")}>
              <span>
                <Button
                  fullWidth
                  variant="outlined"
                  startIcon={<Build />}
                  onClick={() => handleAction('build')}
                  disabled={true}
                >
                  {t("codeEditor.quickActions.build")}
                </Button>
              </span>
            </Tooltip>
          </Stack>
        </Box>
      </DashboardCardWrapper>
    );
  }
);

QuickActionsCard.displayName = "QuickActionsCard";

export default QuickActionsCard;