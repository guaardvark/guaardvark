// Folder window wrapper component
// Based on DashboardCardWrapper but adapted for folder windows
// - Draggable/resizable window chrome
// - Title bar with folder name
// - Close button, minimize/maximize
// - Double-click header to minimize/maximize
// - Theme-based alternate background color

import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Paper,
  Box,
  Typography,
  Divider,
  IconButton,
  Chip,
} from "@mui/material";
import { Close as CloseIcon } from "@mui/icons-material";
import { useTheme, alpha } from "@mui/material/styles";

const FolderWindowWrapper = React.forwardRef(
  ({ title, children, isMinimized, onToggleMinimize, onClose, onDrop, onDragOver, titleBarActions, isRepository, ...props }, ref) => {
    const [clickTimeout, setClickTimeout] = useState(null);
    const [lastClickTime, setLastClickTime] = useState(0);
    const [clickCount, setClickCount] = useState(0);

    // Destructure react-grid-layout props to prevent them from spreading to DOM
    const {
      i,
      x,
      y,
      w,
      h,
      minW,
      maxW,
      minH,
      maxH,
      isDraggable,
      isResizable,
      isBounded,
      static: staticProp,
      moved,
      resizeHandles,
      className,
      style,
      // Filter out legacy color props so they don't spread to DOM
      windowColor: _wc,
      onWindowColorChange: _owcc,
      ...restProps
    } = props;

    const theme = useTheme();

    // Theme-based alternate color for the window header
    const headerBg = alpha(theme.palette.primary.main, 0.08);
    const textColor = theme.palette.text.primary;

    // Handle double-click detection for minimize/maximize
    const handleMouseDown = useCallback((e) => {
      const currentTime = Date.now();

      setLastClickTime(prevTime => {
        const timeDiff = currentTime - prevTime;

        setClickTimeout(prevTimeout => {
          if (prevTimeout) {
            clearTimeout(prevTimeout);
          }

          setClickCount(prevCount => {
            if (timeDiff < 500 && prevCount === 1) {
              // Double-click detected
              if (onToggleMinimize) {
                onToggleMinimize();
              }
              return 0;
            } else {
              // Single click - set timeout to reset
              const newTimeout = setTimeout(() => {
                setClickCount(0);
                setLastClickTime(0);
              }, 500);
              setClickTimeout(newTimeout);
              return 1;
            }
          });

          return null;
        });

        return prevTime === 0 || timeDiff >= 500 ? currentTime : prevTime;
      });
    }, [onToggleMinimize]);

    // Cleanup timeout on unmount
    useEffect(() => {
      return () => {
        if (clickTimeout) {
          clearTimeout(clickTimeout);
        }
      };
    }, [clickTimeout]);

    // Conditionally render content based on isMinimized
    const windowContent = isMinimized ? null : (
      <>
        <Divider sx={{ mb: 0.5 }} />
        <Box
          sx={{
            flexGrow: 1,
            overflowY: "auto",
            overflowX: "hidden",
            px: 0.5,
            display: "flex",
            flexDirection: "column",
            height: "100%",
          }}
        >
          {children}
        </Box>
      </>
    );

    return (
      <Paper
        ref={ref}
        style={style}
        className={`folder-window ${className || ""} ${isMinimized ? 'minimized' : ''}`}
        elevation={3}
        onDrop={onDrop}
        onDragOver={onDragOver || ((e) => e.preventDefault())}
        sx={{
          display: "flex",
          flexDirection: "column",
          height: isMinimized ? "auto" : "100%",
          minHeight: isMinimized ? "50px" : "200px",
          maxWidth: isMinimized ? "350px" : "none",
          p: 0.5,
          overflow: "hidden",
          cursor: isDraggable !== false ? "grab" : "default",
          userSelect: "none",
          borderRadius: "8px",
          transition: theme.transitions.create(['height', 'min-height'], {
            duration: theme.transitions.duration.standard,
          }),
          '&.minimized': {
            '& .folder-window-drag-handle': {
              cursor: 'pointer',
              '&:hover': {
                backgroundColor: 'rgba(0, 0, 0, 0.04)',
              },
            },
          },
          backgroundColor: alpha(theme.palette.background.paper, 0.95),
          border: `1px solid ${alpha(theme.palette.primary.main, 0.15)}`,
          ...restProps.sx,
        }}
        {...restProps}
      >
        {/* Window Header/Title Bar - Always visible */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            px: 1,
            mb: isMinimized ? 0 : 1,
            minHeight: "40px",
            cursor: "grab",
            userSelect: "none",
            outline: "none",
            WebkitTapHighlightColor: "transparent",
            "&:active": {
              cursor: "grabbing",
              backgroundColor: "transparent",
            },
            backgroundColor: headerBg,
            borderRadius: "4px",
            "&:hover": {
              backgroundColor: alpha(theme.palette.primary.main, 0.14),
            },
            "&:focus": {
              outline: "none",
            },
          }}
          onMouseDown={handleMouseDown}
          className="folder-window-drag-handle" // Drag handle for react-grid-layout
        >
          {/* Window title */}
          <Typography
            variant="h6"
            component="span"
            sx={{
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              color: textColor,
              userSelect: "none",
              pointerEvents: "none",
              fontSize: "0.77rem",
              fontWeight: "medium",
              flexGrow: 1,
            }}
          >
            {title}
          </Typography>

          {isRepository && (
            <Chip
              label="CODE REPO"
              size="small"
              sx={{
                height: 16,
                fontSize: '0.6rem',
                fontWeight: 'bold',
                ml: 1,
                backgroundColor: 'rgba(0,0,0,0.1)',
                color: textColor,
                border: `1px solid ${textColor}`,
                '& .MuiChip-label': { px: 0.5 }
              }}
            />
          )}

          {/* Custom title bar actions */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            {titleBarActions}

            {/* Close Button */}
            {onClose && (
              <IconButton
                onClick={onClose}
                size="small"
                className="non-draggable"
                sx={{
                  width: "20px",
                  height: "20px",
                  padding: 0,
                  color: textColor,
                  "&:hover": {
                    backgroundColor: "rgba(255, 0, 0, 0.1)",
                  },
                }}
              >
                <CloseIcon sx={{ fontSize: "16px" }} />
              </IconButton>
            )}
          </Box>
        </Box>

        {/* Window Content */}
        {windowContent}
      </Paper>
    );
  }
);

FolderWindowWrapper.displayName = "FolderWindowWrapper";

FolderWindowWrapper.propTypes = {
  title: PropTypes.string.isRequired,
  children: PropTypes.node.isRequired,
  isMinimized: PropTypes.bool,
  onToggleMinimize: PropTypes.func,
  onClose: PropTypes.func,
  onDrop: PropTypes.func,
  onDragOver: PropTypes.func,
  titleBarActions: PropTypes.node,
  isRepository: PropTypes.bool,
};

export default FolderWindowWrapper;
