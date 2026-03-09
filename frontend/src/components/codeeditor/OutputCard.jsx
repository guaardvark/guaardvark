// frontend/src/components/codeeditor/OutputCard.jsx
// Console output and execution results

import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Box,
  Typography,
  Tabs,
  Tab,
  Paper,
  IconButton,
  Tooltip,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Chip,
} from "@mui/material";
import {
  Clear as ClearIcon,
  Error as ErrorIcon,
  Warning as WarningIcon,
  Info as InfoIcon,
  CheckCircle as SuccessIcon,
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
      openTabs,
      currentTab,
      ...props
    },
    ref
  ) => {
    const [activeTab, setActiveTab] = useState(0);
    const [consoleOutput, setConsoleOutput] = useState([]);
    const [problems, setProblems] = useState([]);
    const consoleEndRef = useRef(null);

    const handleTabChange = (event, newValue) => {
      setActiveTab(newValue);
    };

    // Auto-scroll console to bottom
    const scrollToBottom = useCallback(() => {
      if (consoleEndRef.current) {
        consoleEndRef.current.scrollIntoView({ behavior: 'smooth' });
      }
    }, []);

    useEffect(() => {
      scrollToBottom();
    }, [consoleOutput, scrollToBottom]);

    // Capture console output
    useEffect(() => {
      const originalLog = console.log;
      const originalError = console.error;
      const originalWarn = console.warn;
      const originalInfo = console.info;

      console.log = (...args) => {
        setConsoleOutput(prev => [...prev, {
          type: 'log',
          message: args.map(arg =>
            typeof arg === 'object' ? JSON.stringify(arg, null, 2) : String(arg)
          ).join(' '),
          timestamp: new Date().toLocaleTimeString()
        }]);
        originalLog.apply(console, args);
      };

      console.error = (...args) => {
        setConsoleOutput(prev => [...prev, {
          type: 'error',
          message: args.map(arg =>
            typeof arg === 'object' ? JSON.stringify(arg, null, 2) : String(arg)
          ).join(' '),
          timestamp: new Date().toLocaleTimeString()
        }]);
        originalError.apply(console, args);
      };

      console.warn = (...args) => {
        setConsoleOutput(prev => [...prev, {
          type: 'warn',
          message: args.map(arg =>
            typeof arg === 'object' ? JSON.stringify(arg, null, 2) : String(arg)
          ).join(' '),
          timestamp: new Date().toLocaleTimeString()
        }]);
        originalWarn.apply(console, args);
      };

      console.info = (...args) => {
        setConsoleOutput(prev => [...prev, {
          type: 'info',
          message: args.map(arg =>
            typeof arg === 'object' ? JSON.stringify(arg, null, 2) : String(arg)
          ).join(' '),
          timestamp: new Date().toLocaleTimeString()
        }]);
        originalInfo.apply(console, args);
      };

      return () => {
        console.log = originalLog;
        console.error = originalError;
        console.warn = originalWarn;
        console.info = originalInfo;
      };
    }, []);

    // Analyze code for problems when currentTab changes
    useEffect(() => {
      // Use setTimeout to defer state update to avoid updating during render
      const timeoutId = setTimeout(() => {
        if (currentTab?.content) {
          const newProblems = [];
          const lines = currentTab.content.split('\n');

          lines.forEach((line, index) => {
            // Check for common issues
            if (line.includes('console.log') && !line.trim().startsWith('//')) {
              newProblems.push({
                type: 'warning',
                message: 'Console.log statement found',
                line: index + 1,
                file: currentTab.filePath || 'untitled'
              });
            }

            if (line.includes('debugger') && !line.trim().startsWith('//')) {
              newProblems.push({
                type: 'warning',
                message: 'Debugger statement found',
                line: index + 1,
                file: currentTab.filePath || 'untitled'
              });
            }

            // Check for TODO comments
            if (line.includes('TODO') || line.includes('FIXME')) {
              newProblems.push({
                type: 'info',
                message: line.trim(),
                line: index + 1,
                file: currentTab.filePath || 'untitled'
              });
            }
          });

          setProblems(newProblems);
        } else {
          setProblems([]);
        }
      }, 0);

      return () => clearTimeout(timeoutId);
    }, [currentTab?.content, currentTab?.filePath]);

    const clearConsole = () => {
      setConsoleOutput([]);
    };

    const getIconForType = (type) => {
      switch (type) {
        case 'error':
          return <ErrorIcon fontSize="small" sx={{ color: 'error.main' }} />;
        case 'warn':
          return <WarningIcon fontSize="small" sx={{ color: 'warning.main' }} />;
        case 'info':
          return <InfoIcon fontSize="small" sx={{ color: 'info.main' }} />;
        default:
          return <SuccessIcon fontSize="small" sx={{ color: 'text.secondary' }} />;
      }
    };

    const titleBarActions = null;

    return (
      <DashboardCardWrapper
        ref={ref}
        title="Output"
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        titleBarActions={titleBarActions}
        style={style}
        {...props}
      >
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <Tabs
            value={activeTab}
            onChange={handleTabChange}
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
                  Console
                  {consoleOutput.length > 0 && (
                    <Chip
                      label={consoleOutput.length}
                      size="small"
                      sx={{
                        height: 16,
                        fontSize: '0.6rem',
                        '& .MuiChip-label': { px: 0.5 }
                      }}
                    />
                  )}
                </Box>
              }
            />
            <Tab label="Terminal" />
            <Tab
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  Problems
                  {problems.length > 0 && (
                    <Chip
                      label={problems.length}
                      size="small"
                      color={problems.some(p => p.type === 'error') ? 'error' : 'warning'}
                      sx={{
                        height: 16,
                        fontSize: '0.6rem',
                        '& .MuiChip-label': { px: 0.5 }
                      }}
                    />
                  )}
                </Box>
              }
            />
          </Tabs>

          <Box sx={{ flex: 1, overflow: 'auto', p: 0, minHeight: 0 }}>
            {/* Console Tab */}
            {activeTab === 0 && (
              <Paper
                elevation={0}
                sx={{
                  p: 0.5,
                  bgcolor: 'grey.900',
                  color: 'common.white',
                  fontFamily: 'monospace',
                  fontSize: '0.7rem',
                  height: '100%',
                  overflow: 'auto',
                  borderRadius: 0
                }}
              >
                {consoleOutput.length === 0 ? (
                  <Box sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    minHeight: '100px',
                    color: 'grey.500'
                  }}>
                    <Typography variant="body2" sx={{ fontSize: '0.7rem' }}>
                      No console output yet
                    </Typography>
                  </Box>
                ) : (
                  consoleOutput.map((output, index) => (
                    <Box
                      key={`console-${output.timestamp}-${index}`}
                      sx={{
                        py: 0.5,
                        px: 0.75,
                        borderBottom: index < consoleOutput.length - 1 ? '1px solid rgba(255,255,255,0.08)' : 'none',
                        '&:hover': { bgcolor: 'rgba(255,255,255,0.03)' }
                      }}
                    >
                      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.75 }}>
                        {getIconForType(output.type)}
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <Typography
                            variant="body2"
                            component="div"
                            sx={{
                              fontSize: '0.7rem',
                              lineHeight: 1.4,
                              color: output.type === 'error' ? 'error.light' :
                                     output.type === 'warn' ? 'warning.light' :
                                     output.type === 'info' ? 'info.light' : 'common.white',
                              whiteSpace: 'pre-wrap',
                              wordBreak: 'break-word'
                            }}
                          >
                            {output.message}
                          </Typography>
                          <Typography variant="caption" sx={{ color: 'grey.600', fontSize: '0.6rem' }}>
                            {output.timestamp}
                          </Typography>
                        </Box>
                      </Box>
                    </Box>
                  ))
                )}
                <div ref={consoleEndRef} />
              </Paper>
            )}

            {/* Terminal Tab */}
            {activeTab === 1 && (
              <Paper
                elevation={0}
                sx={{
                  p: 0.5,
                  bgcolor: 'grey.900',
                  color: 'common.white',
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
              </Paper>
            )}

            {/* Problems Tab */}
            {activeTab === 2 && (
              <Box sx={{ height: '100%', overflow: 'auto' }}>
                {problems.length === 0 ? (
                  <Box sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    minHeight: '100px',
                    p: 1
                  }}>
                    <Typography variant="body2" color="success.main" sx={{ fontSize: '0.7rem', display: 'flex', alignItems: 'center', gap: 0.5 }}>
                      <SuccessIcon fontSize="small" />
                      No problems found
                    </Typography>
                  </Box>
                ) : (
                  <List dense sx={{ p: 0 }}>
                    {problems.map((problem, index) => (
                      <ListItem
                        key={`problem-${problem.file}-${problem.line}-${index}`}
                        sx={{
                          py: 0.75,
                          px: 1,
                          borderBottom: '1px solid',
                          borderColor: 'divider',
                          '&:hover': { bgcolor: 'action.hover' },
                          '&:last-child': { borderBottom: 'none' }
                        }}
                      >
                        <ListItemIcon sx={{ minWidth: 28 }}>
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
                              {problem.file} [Ln {problem.line}]
                            </Typography>
                          }
                        />
                      </ListItem>
                    ))}
                  </List>
                )}
              </Box>
            )}
          </Box>
        </Box>
      </DashboardCardWrapper>
    );
  }
);

OutputCard.displayName = "OutputCard";

export default OutputCard;