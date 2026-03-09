// frontend/src/components/codeeditor/CodeMetricsCard.jsx
// Code metrics and statistics

import React, { useMemo } from "react";
import {
  Box,
  Typography,
  List,
  ListItem,
  ListItemText,
  Chip,
} from "@mui/material";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";

const CodeMetricsCard = React.forwardRef(
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

    const metrics = useMemo(() => {
      if (!currentTab?.content) {
        return {
          lines: 0,
          characters: 0,
          words: 0,
          functions: 0,
          imports: 0,
        };
      }

      const content = currentTab.content;
      const lines = content.split('\n');

      return {
        lines: lines.length,
        characters: content.length,
        words: content.split(/\s+/).filter(word => word.length > 0).length,
        functions: (content.match(/function\s+\w+|const\s+\w+\s*=\s*\(/g) || []).length,
        imports: (content.match(/import\s+.*from|const\s+.*=\s*require/g) || []).length,
      };
    }, [currentTab?.content]);

    return (
      <DashboardCardWrapper
        ref={ref}
        title="Metrics"
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        style={style}
        {...props}
      >
        <Box sx={{ p: 1 }}>
          {currentTab ? (
            <List dense>
              <ListItem sx={{ py: 0.25 }}>
                <ListItemText
                  primary="Lines"
                  secondary={<Chip label={metrics.lines} size="small" />}
                />
              </ListItem>
              <ListItem sx={{ py: 0.25 }}>
                <ListItemText
                  primary="Characters"
                  secondary={<Chip label={metrics.characters} size="small" />}
                />
              </ListItem>
              <ListItem sx={{ py: 0.25 }}>
                <ListItemText
                  primary="Words"
                  secondary={<Chip label={metrics.words} size="small" />}
                />
              </ListItem>
              <ListItem sx={{ py: 0.25 }}>
                <ListItemText
                  primary="Functions"
                  secondary={<Chip label={metrics.functions} size="small" color="primary" />}
                />
              </ListItem>
              <ListItem sx={{ py: 0.25 }}>
                <ListItemText
                  primary="Imports"
                  secondary={<Chip label={metrics.imports} size="small" color="secondary" />}
                />
              </ListItem>
            </List>
          ) : (
            <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', mt: 2 }}>
              No file open
            </Typography>
          )}
        </Box>
      </DashboardCardWrapper>
    );
  }
);

CodeMetricsCard.displayName = "CodeMetricsCard";

export default CodeMetricsCard;