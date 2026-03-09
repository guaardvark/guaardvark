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
  Save,
} from "@mui/icons-material";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";

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
    const currentTab = openTabs?.[activeTabIndex];

    const handleAction = useCallback((action) => {
      console.log(`${action} action not implemented yet`);
    }, []);

    return (
      <DashboardCardWrapper
        ref={ref}
        title="Actions"
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        style={style}
        {...props}
      >
        <Box sx={{ p: 1 }}>
          <Stack spacing={1}>
            <Tooltip title="Code execution not implemented">
              <span>
                <Button
                  fullWidth
                  variant="contained"
                  startIcon={<PlayArrow />}
                  onClick={() => handleAction('run')}
                  disabled={true}
                >
                  Run
                </Button>
              </span>
            </Tooltip>

            <Tooltip title="Code formatting not implemented">
              <span>
                <Button
                  fullWidth
                  variant="outlined"
                  startIcon={<FormatAlignLeft />}
                  onClick={() => handleAction('format')}
                  disabled={true}
                >
                  Format
                </Button>
              </span>
            </Tooltip>

            <Tooltip title="Debugging not implemented">
              <span>
                <Button
                  fullWidth
                  variant="outlined"
                  startIcon={<BugReport />}
                  onClick={() => handleAction('debug')}
                  disabled={true}
                >
                  Debug
                </Button>
              </span>
            </Tooltip>

            <Tooltip title="Build system not implemented">
              <span>
                <Button
                  fullWidth
                  variant="outlined"
                  startIcon={<Build />}
                  onClick={() => handleAction('build')}
                  disabled={true}
                >
                  Build
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