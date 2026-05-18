// frontend/src/components/codeeditor/OutputCard.jsx
// Problems panel + placeholder for future terminal/output integration

import React, { useState, useMemo } from "react";
import {
  Box,
  Typography,
  Tabs,
  Tab,
  Chip,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
} from "@mui/material";
import {
  Error as ErrorIcon,
  Warning as WarningIcon,
  Info as InfoIcon,
  CheckCircle as SuccessIcon,
  Terminal as TerminalIcon,
} from "@mui/icons-material";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";

const OutputCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      _openTabs,
      currentTab,
      ...props
    },
    ref
  ) => {
    const [activeTab, setActiveTab] = useState(0);

    // Analyze code for problems — debounced via useMemo to avoid re-scanning on every render
    const problems = useMemo(() => {
      if (!currentTab?.content) return [];
      const results = [];
      const lines = currentTab.content.split('\n');
      const filePath = currentTab.filePath || 'untitled';

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmed = line.trim();
        if (trimmed.startsWith('//') || trimmed.startsWith('#')) continue;

        if (line.includes('console.log')) {
          results.push({ type: 'warning', message: 'console.log statement', line: i + 1, file: filePath });
        }
        if (line.includes('debugger')) {
          results.push({ type: 'warning', message: 'debugger statement', line: i + 1, file: filePath });
        }
        if (line.includes('TODO') || line.includes('FIXME')) {
          results.push({ type: 'info', message: trimmed, line: i + 1, file: filePath });
        }
      }
      return results;
    }, [currentTab?.content, currentTab?.filePath]);

    const getIconForType = (type) => {
      switch (type) {
        case 'error':
          return <ErrorIcon fontSize="small" sx={{ color: 'error.main' }} />;
        case 'warn':
        case 'warning':
          return <WarningIcon fontSize="small" sx={{ color: 'warning.main' }} />;
        case 'info':
          return <InfoIcon fontSize="small" sx={{ color: 'info.main' }} />;
        default:
          return <SuccessIcon fontSize="small" sx={{ color: 'text.secondary' }} />;
      }
    };

    return (
      <DashboardCardWrapper
        ref={ref}
        title="Output"
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        style={style}
        {...props}
      >
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <Tabs
            value={activeTab}
            onChange={(e, v) => setActiveTab(v)}
            sx={{
              borderBottom: 1,
              borderColor: 'divider',
              minHeight: 'auto',
              '& .MuiTab-root': {
                minHeight: 'auto',
                py: 0.75,
                fontSize: '0.7rem',
                textTransform: 'none'
              }
            }}
          >
            <Tab
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  Problems
                  {problems.length > 0 && (
                    <Chip
                      label={problems.length}
                      size="small"
                      color={problems.some(p => p.type === 'error') ? 'error' : 'warning'}
                      sx={{ height: 16, fontSize: '0.6rem', '& .MuiChip-label': { px: 0.5 } }}
                    />
                  )}
                </Box>
              }
            />
            <Tab
              icon={<TerminalIcon sx={{ fontSize: '0.85rem' }} />}
              iconPosition="start"
              label="Terminal"
              sx={{ '& .MuiTab-iconWrapper': { mr: 0.5 } }}
            />
          </Tabs>

          <Box sx={{ flex: 1, overflow: 'auto', p: 0, minHeight: 0 }}>
            {/* Problems Tab */}
            {activeTab === 0 && (
              <Box sx={{ height: '100%', overflow: 'auto' }}>
                {problems.length === 0 ? (
                  <Box sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    minHeight: '80px',
                    p: 1
                  }}>
                    <Typography variant="body2" color="success.main" sx={{ fontSize: '0.7rem', display: 'flex', alignItems: 'center', gap: 0.5 }}>
                      <SuccessIcon fontSize="small" />
                      No problems detected
                    </Typography>
                  </Box>
                ) : (
                  <List dense sx={{ p: 0 }}>
                    {problems.map((problem, index) => (
                      <ListItem
                        key={`${problem.file}-${problem.line}-${index}`}
                        sx={{
                          py: 0.5,
                          px: 1,
                          borderBottom: '1px solid',
                          borderColor: 'divider',
                          '&:hover': { bgcolor: 'action.hover', cursor: 'pointer' },
                          '&:last-child': { borderBottom: 'none' }
                        }}
                      >
                        <ListItemIcon sx={{ minWidth: 24 }}>
                          {getIconForType(problem.type)}
                        </ListItemIcon>
                        <ListItemText
                          primary={
                            <Typography variant="body2" sx={{ fontSize: '0.7rem', lineHeight: 1.4 }}>
                              {problem.message}
                            </Typography>
                          }
                          secondary={
                            <Typography variant="caption" sx={{ fontSize: '0.6rem', color: 'text.secondary' }}>
                              Ln {problem.line}
                            </Typography>
                          }
                        />
                      </ListItem>
                    ))}
                  </List>
                )}
              </Box>
            )}

            {/* Terminal Tab — placeholder for future integration */}
            {activeTab === 1 && (
              <Box
                sx={{
                  p: 1,
                  bgcolor: 'grey.900',
                  color: 'grey.500',
                  fontFamily: 'monospace',
                  fontSize: '0.7rem',
                  height: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: 0
                }}
              >
                <Typography variant="body2" sx={{ color: 'grey.500', fontSize: '0.7rem' }}>
                  Terminal integration coming soon
                </Typography>
              </Box>
            )}
          </Box>
        </Box>
      </DashboardCardWrapper>
    );
  }
);

OutputCard.displayName = "OutputCard";

export default React.memo(OutputCard, (prev, next) => (
  prev.currentTab?.content === next.currentTab?.content &&
  prev.currentTab?.filePath === next.currentTab?.filePath &&
  prev.isMinimized === next.isMinimized &&
  prev.cardColor === next.cardColor
));
