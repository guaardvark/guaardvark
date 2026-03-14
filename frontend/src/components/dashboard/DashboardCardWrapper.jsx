// frontend/src/components/dashboard/DashboardCardWrapper.jsx
// Version 3.0: Added double-click minimize/maximize functionality
// - Double-click header to minimize/maximize
// - Minimized cards show as 300px wide bars
// - Enhanced dragging support for minimized cards
// - Better UX for minimize/maximize transitions

import React, { useState, useEffect, useRef, useMemo, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Paper,
  Box,
  Typography,
  Divider,
  IconButton,
  Tooltip,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { useNavigate } from "react-router-dom";

const DashboardCardWrapper = React.forwardRef(
  ({ title, children, cardColor, onCardColorChange, isMinimized, onToggleMinimize, titleBarActions, ...props }, ref) => {
    const routerNavigate = useNavigate();
    const [clickTimeout, setClickTimeout] = useState(null);
    const [lastClickTime, setLastClickTime] = useState(0);
    const [clickCount, setClickCount] = useState(0);
    // Destructure known react-grid-layout props AND custom props
    // to prevent them from being spread onto the Paper component.
    const {
      i,
      x,
      y,
      w,
      h, // Basic layout props
      minW,
      maxW,
      minH,
      maxH, // Size constraints
      isDraggable,
      isResizable,
      isBounded,
      static: staticProp, // Interaction props
      moved, // Layout state prop
      resizeHandles, // Resizing configuration
      className, // RGL might add its own classes
      style, // RGL passes style, which we want to keep

      // Code editor specific props that should not be passed to DOM
      projectId,
      openTabs,
      setOpenTabs,
      activeTabIndex,
      setActiveTabIndex,
      fileTree,
      setFileTree,
      chatMessages,
      setChatMessages,
      searchResults,
      setSearchResults,
      rulesCutoffEnabled,
      currentTab,
      headerActions,

      ...restProps // Collect any other valid DOM/MUI props (like sx, data-grid)
    } = props;

    const theme = useTheme();
    const cardId = restProps.id || i;
    const colorInputRef = useRef(null);

    // Optimized color calculations - parse hex once and compute both properties
    const colorData = useMemo(() => {
      if (!cardColor) {
        return {
          isLight: false,
          oppositeColor: theme.palette.text.primary
        };
      }

      // Parse hex once for efficiency
      let hex = cardColor.replace("#", "");
      if (hex.length === 3) {
        hex = hex.split("").map((h) => h + h).join("");
      }
      const r = parseInt(hex.substring(0, 2), 16);
      const g = parseInt(hex.substring(2, 4), 16);
      const b = parseInt(hex.substring(4, 6), 16);

      // Calculate luminance and opposite color in one pass
      const yiq = (r * 299 + g * 587 + b * 114) / 1000;
      const isLight = yiq > 186;
      // Use proper contrast: black text for light backgrounds, white text for dark backgrounds
      const oppositeColor = isLight ? 'rgba(0, 0, 0, 0.87)' : 'rgba(255, 255, 255, 0.95)';

      return { isLight, oppositeColor };
    }, [cardColor, theme.palette.text.primary]);

    const isLightColor = colorData.isLight;
    const getOppositeColor = colorData.oppositeColor;


    // Handle mouse down to implement custom double-click detection
    const handleMouseDown = useCallback((e) => {
      const currentTime = Date.now();
      const timeDiff = currentTime - lastClickTime;

      // console.log('MouseDown on card:', title, 'timeDiff:', timeDiff, 'clickCount:', clickCount);

      // Clear any existing timeout
      if (clickTimeout) {
        clearTimeout(clickTimeout);
        setClickTimeout(null);
      }

      if (timeDiff < 500 && clickCount === 1) {
        // This is a double-click (second click within 500ms)
        // console.log('Double-click detected via mousedown on card:', title);
        setClickCount(0);
        setLastClickTime(0);

        if (onToggleMinimize) {
          onToggleMinimize();
        }
      } else {
        // This is a single click, start the timer
        setLastClickTime(currentTime);
        setClickCount(1);

        // Set timeout to reset if no second click comes
        const newTimeout = setTimeout(() => {
          setClickCount(0);
          setLastClickTime(0);
        }, 500);
        setClickTimeout(newTimeout);
      }
    }, [clickTimeout, lastClickTime, clickCount, onToggleMinimize, title]);

    // Cleanup timeout on unmount
    useEffect(() => {
      return () => {
        if (clickTimeout) {
          clearTimeout(clickTimeout);
        }
      };
    }, []);

    // Handle single click on title to navigate — only if mouse didn't move >5px (not a drag)
    const dragDetectRef = useRef(false);
    const mouseStartRef = useRef(null);
    const handleTitleMouseDown = useCallback((e) => {
      dragDetectRef.current = false;
      mouseStartRef.current = { x: e.clientX, y: e.clientY };
    }, []);
    const handleTitleMouseMove = useCallback((e) => {
      if (!mouseStartRef.current) return;
      const dx = e.clientX - mouseStartRef.current.x;
      const dy = e.clientY - mouseStartRef.current.y;
      if (Math.sqrt(dx * dx + dy * dy) > 5) dragDetectRef.current = true;
    }, []);
    const handleTitleClick = useCallback((e) => {
      e.stopPropagation();
      // Don't navigate if this was a drag, or on CodeEditorPage
      if (dragDetectRef.current) return;
      if (window.location.pathname !== '/code-editor') {
        const route = getCardRoute(cardId);
        if (route && route !== "/") {
          routerNavigate(route);
        }
      }
    }, [cardId]);

    const handleColorChange = (e) => {
      const value = e.target.value;
      if (onCardColorChange) {
        onCardColorChange(value);
      }
    };

    const openPicker = () => {
      if (colorInputRef.current) {
        colorInputRef.current.click();
      }
    };

    // Get the route for the card title link
    const getCardRoute = (cardId) => {
      const routeMap = {
        project: "/projects",
        website: "/websites",
        tasks: "/tasks",
        chat: "/chat",
        clients: "/clients",
        csvgen: "/csv-generation",
        codegen: "/code-generation",
        imggen: "/batch-image-generation",
        files: "/documents",
        family: "/settings",
        autoresearch: "/settings",
      };
      return routeMap[cardId] || "/";
    };

    // Conditionally render content based on isMinimized
    const cardContent = isMinimized ? null : (
      <>
        <Divider sx={{ mb: 0.5 }} /> {/* Reduced margin bottom from 1 to 0.5 */}
        <Box
          sx={{
            flexGrow: 1,
            overflowY: "auto",
            overflowX: "hidden",
            px: 0.5, // Reduced horizontal padding from 1 to 0.5
            display: "flex",
            flexDirection: "column",
            height: "100%",
            color: getOppositeColor, // Use opposite color for better contrast
          }}
        >
          {children}
        </Box>
      </>
    );

    return (
      <>
        <Paper
          ref={ref}
          style={style} // Apply styles passed from react-grid-layout
          className={`draggable-card ${className || ""} ${isMinimized ? 'minimized' : ''}`}
          elevation={2} // Set elevation for shadow, similar to chat bubbles
          sx={{
            display: "flex",
            flexDirection: "column",
            height: isMinimized ? "auto" : "100%",
            minHeight: isMinimized ? "50px" : "200px",
            p: 0.5, // Reduced base padding from 1 to 0.5
            overflow: "hidden", // Hide overflow
            cursor: isDraggable !== false ? "grab" : "default",
            userSelect: "none",
            borderRadius: "5px 5px 5px 5px",
            transition: theme.transitions.create(['height', 'min-height'], {
              duration: theme.transitions.duration.standard,
            }),
            '&.minimized': {
              '& .card-header-buttons': {
                cursor: 'pointer',
                '&:hover': {
                  backgroundColor: 'rgba(0, 0, 0, 0.04)',
                },
              },
            },
            ...(cardColor && {
              backgroundColor: cardColor,
              color: getOppositeColor, // Use opposite color for better contrast
            }),
            ...restProps.sx,
          }}
          {...restProps}
        >
          {/* Card Header Area - Always visible */}
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              px: 1,
              mb: isMinimized ? 0 : 1,
              minHeight: "40px", // Ensure consistent header height
              cursor: "grab",
              userSelect: "none", // Prevent text selection on double-click
              outline: "none", // Remove focus outline
              WebkitTapHighlightColor: "transparent", // Remove mobile tap highlight
              "&:active": {
                cursor: "grabbing",
                backgroundColor: "transparent", // Remove click highlight
              },
              "&:hover": {
                backgroundColor: "rgba(0, 0, 0, 0.04)",
                borderRadius: "4px",
              },
              "&:focus": {
                outline: "none", // Remove focus outline
              },
            }}
            onMouseDown={handleMouseDown}
            className="card-header-buttons" // Draggable handle for react-grid-layout
          >
            {/* Title text - clickable to navigate to linked page */}
            <Typography
              variant="h6"
              component="span"
              onMouseDown={handleTitleMouseDown}
              onMouseMove={handleTitleMouseMove}
              onClick={handleTitleClick}
              className="non-draggable"
              sx={{
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                color: getOppositeColor,
                userSelect: "none",
                fontSize: "0.62rem",
                fontWeight: "medium",
                cursor: "pointer",
                px: 0.4,
                py: 0.15,
                lineHeight: 1.2,
                borderRadius: "3px",
                border: "1px solid transparent",
                transition: "border-color 0.15s ease",
                "&:hover": {
                  borderColor: `color-mix(in srgb, ${getOppositeColor} 75%, transparent)`,
                },
              }}
            >
              {title}
            </Typography>

            {/* Custom title bar actions */}
            {titleBarActions && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                {titleBarActions}
              </Box>
            )}

            <Box sx={{ position: "relative" }}>
              {/* Color Picker Button - smaller */}
              <Tooltip title="Change card color">
                <IconButton
                  onClick={openPicker}
                  sx={{
                    width: "8px",
                    height: "8px",
                    minWidth: "8px",
                    minHeight: "8px",
                    padding: 0,
                    borderRadius: "50%",
                    backgroundColor: cardColor || theme.palette.primary.main,
                    border: `1px solid ${getOppositeColor}`,
                    transition: "all 0.2s ease",
                    "&:hover": {
                      transform: "scale(1.3)",
                      boxShadow: `0 0 3px ${cardColor || theme.palette.primary.main}`,
                    },
                  }}
                  className="non-draggable" // Prevent dragging when clicking color picker
                >
                  <Box
                    sx={{
                      width: "2px",
                      height: "2px",
                      borderRadius: "50%",
                      backgroundColor: getOppositeColor,
                    }}
                  />
                </IconButton>
              </Tooltip>
              
              {/* Hidden file input for color picker */}
              <input
                ref={colorInputRef}
                type="color"
                value={cardColor || "#1976d2"}
                onChange={handleColorChange}
                style={{
                  position: "absolute",
                  opacity: 0,
                  pointerEvents: "none",
                  width: "1px",
                  height: "1px",
                }}
              />
            </Box>
          </Box>

          {/* Card Content */}
          {cardContent}
        </Paper>
      </>
    );
  }
);

DashboardCardWrapper.displayName = "DashboardCardWrapper";

DashboardCardWrapper.propTypes = {
  title: PropTypes.string.isRequired,
  children: PropTypes.node.isRequired,
  cardColor: PropTypes.string,
  onCardColorChange: PropTypes.func,
  isMinimized: PropTypes.bool,
  onToggleMinimize: PropTypes.func,
};

export default DashboardCardWrapper;
