// frontend/src/components/dashboard/DashboardCardWrapper.jsx
// Version 3.0: Added double-click minimize/maximize functionality
// - Double-click header to minimize/maximize
// - Minimized cards show as 300px wide bars
// - Enhanced dragging support for minimized cards
// - Better UX for minimize/maximize transitions

import React, { useEffect, useRef, useMemo, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Paper,
  Box,
  Typography,
  Divider,
  IconButton,
  Tooltip,
} from "@mui/material";
import { ThemeProvider, useTheme } from "@mui/material/styles";
import { foregroundFor, themeOnColor } from "../../theme/onColor";
import DragIndicatorIcon from "@mui/icons-material/DragIndicator";
import { useNavigate } from "react-router-dom";
import useContextMenu from "../../hooks/useContextMenu";
import EntityContextMenu from "../common/EntityContextMenu";
import { useUiTranslation } from "../../i18n";

// Page each dashboard card's title opens.
const CARD_ROUTES = {
  project: "/projects",
  website: "/websites",
  tasks: "/tasks",
  chat: "/chat",
  clients: "/clients",
  csvgen: "/file-generation",
  codegen: "/code-editor",
  imggen: "/batch-images",
  files: "/documents",
  family: "/settings",
  autoresearch: "/autoresearch",
  gpu: "/plugins",
};

const getCardRoute = (cardId) => CARD_ROUTES[cardId] || "/";

/**
 * Card frame shared by the dashboard, the code editor, the video editor, sticky
 * notes and document windows.
 *
 * Right-click is opt-in: pass `contextMenuActions` (an EntityContextMenu action
 * list, may be empty) and the card gets a menu of its generic actions (open page,
 * minimize, colour, hide when `onHideCard` is given) followed by those actions.
 * `contextMenuArea="header"` limits it to the title bar, for cards whose body has
 * menus of its own.
 */
const DashboardCardWrapper = React.forwardRef(
  (
    {
      title,
      children,
      cardColor,
      onCardColorChange,
      isMinimized,
      onToggleMinimize,
      titleBarActions,
      minimizedContent,
      contextMenuActions,
      contextMenuArea = "card",
      onHideCard,
      ...props
    },
    ref,
  ) => {
    const { t } = useUiTranslation();
    const routerNavigate = useNavigate();
    const clickState = useRef({ timeout: null, lastTime: 0, count: 0 });
    const menu = useContextMenu();
    // Destructure known react-grid-layout props AND custom props
    // to prevent them from being spread onto the Paper component.
    const {
      i,
      x: _x,
      y: _y,
      w: _w,
      h: _h, // Basic layout props
      minW: _minW,
      maxW: _maxW,
      minH: _minH,
      maxH: _maxH, // Size constraints
      isDraggable: _isDraggable,
      isResizable: _isResizable,
      isBounded: _isBounded,
      static: _staticProp, // Interaction props
      moved: _moved, // Layout state prop
      resizeHandles: _resizeHandles, // Resizing configuration
      className, // RGL might add its own classes
      style, // RGL passes style, which we want to keep

      // Code editor specific props that should not be passed to DOM
      projectId: _projectId,
      openTabs: _openTabs,
      setOpenTabs: _setOpenTabs,
      activeTabIndex: _activeTabIndex,
      setActiveTabIndex: _setActiveTabIndex,
      fileTree: _fileTree,
      setFileTree: _setFileTree,
      chatMessages: _chatMessages,
      setChatMessages: _setChatMessages,
      searchResults: _searchResults,
      setSearchResults: _setSearchResults,
      rulesCutoffEnabled: _rulesCutoffEnabled,
      currentTab: _currentTab,

      ...restProps // Collect any other valid DOM/MUI props (like sx, data-grid)
    } = props;

    const theme = useTheme();
    const cardId = restProps.id || i;
    const colorInputRef = useRef(null);

    // A custom card colour: header text and the card body's theme follow it
    // (black or white ink by WCAG contrast); without one the app theme stands.
    const colorData = useMemo(() => {
      const fg = cardColor ? foregroundFor(cardColor) : null;
      if (!fg) {
        return { isLight: false, oppositeColor: theme.palette.text.primary };
      }
      return { isLight: fg.mode === "light", oppositeColor: fg.text.primary };
    }, [cardColor, theme.palette.text.primary]);
    const bodyTheme = useMemo(() => themeOnColor(cardColor), [cardColor]);
    const onCardColor = (node) => (bodyTheme ? <ThemeProvider theme={bodyTheme}>{node}</ThemeProvider> : node);

    const _isLightColor = colorData.isLight;
    const getOppositeColor = colorData.oppositeColor;


    // Handle mouse down to implement custom double-click detection
    // Uses refs instead of state to avoid stale closures on rapid clicks
    const handleMouseDown = useCallback((e) => {
      if (e.button !== 0) return;
      const cs = clickState.current;
      const now = Date.now();
      const timeDiff = now - cs.lastTime;

      if (cs.timeout) {
        clearTimeout(cs.timeout);
        cs.timeout = null;
      }

      if (timeDiff < 400 && cs.count === 1) {
        // Double-click detected
        cs.count = 0;
        cs.lastTime = 0;
        if (onToggleMinimize) {
          onToggleMinimize();
        }
      } else {
        // First click — wait for potential second
        cs.lastTime = now;
        cs.count = 1;
        cs.timeout = setTimeout(() => {
          cs.count = 0;
          cs.lastTime = 0;
        }, 400);
      }
    }, [onToggleMinimize]);

    // Cleanup timeout on unmount
    useEffect(() => {
      return () => {
        if (clickState.current.timeout) {
          clearTimeout(clickState.current.timeout);
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

    const menuEnabled = Array.isArray(contextMenuActions);
    const handleContextMenu = menuEnabled ? (e) => menu.open(e) : undefined;

    const buildMenuActions = () => {
      const route = getCardRoute(cardId);
      const own = contextMenuActions.filter(Boolean);
      return [
        route !== "/" && { label: t("dashboard.cardWrapper.openPage"), onClick: () => routerNavigate(route) },
        onToggleMinimize && { label: isMinimized ? t("dashboard.cardWrapper.expand") : t("dashboard.cardWrapper.minimize"), onClick: onToggleMinimize },
        onCardColorChange && { label: t("dashboard.cardWrapper.changeColour"), onClick: openPicker },
        onCardColorChange && cardColor && { label: t("dashboard.cardWrapper.resetColour"), onClick: () => onCardColorChange(null) },
        onHideCard && { label: t("dashboard.cardWrapper.hideCard"), onClick: onHideCard },
        ...own.map((action, idx) => (idx === 0 ? { ...action, dividerBefore: true } : action)),
      ];
    };

    // Conditionally render content based on isMinimized
    const cardContent = isMinimized ? null : onCardColor(
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
            maxHeight: "100%",
            minHeight: isMinimized ? "unset" : "200px",
            p: 0.5, // Reduced base padding from 1 to 0.5
            overflow: "hidden", // Hide overflow
            // Body uses a normal cursor — only the header (.card-header-buttons,
            // the draggableHandle on every card page) drags, so the whole-card
            // "grab" cursor was misleading. Header keeps its own grab cursor.
            cursor: "default",
            userSelect: "none",
            borderRadius: "5px 5px 5px 5px",
            transition: theme.transitions.create(['height', 'min-height'], {
              duration: theme.transitions.duration.standard,
            }),
            '&.minimized': {
              cursor: 'pointer',
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
          onContextMenu={contextMenuArea === "card" ? handleContextMenu : undefined}
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
            onContextMenu={contextMenuArea === "header" ? handleContextMenu : undefined}
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
                {onCardColor(titleBarActions)}
              </Box>
            )}

            {/* Drag grip — visible affordance for the draggable area */}
            <Box sx={{ flex: 1, display: 'flex', justifyContent: 'center', alignItems: 'center', minWidth: '24px' }}>
              <DragIndicatorIcon sx={{ fontSize: '0.75rem', opacity: 0.25, color: getOppositeColor }} />
            </Box>

            <Box sx={{ position: "relative" }}>
              {/* Color Picker Button - smaller */}
              <Tooltip title={t("dashboard.cardWrapper.changeColor")}>
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

          {/* Compact summary shown in header area when minimized */}
          {isMinimized && minimizedContent && (
            <Box sx={{ px: 1, py: 0.5 }}>{onCardColor(minimizedContent)}</Box>
          )}

          {/* Card Content */}
          {cardContent}

          {menuEnabled && (
            <EntityContextMenu
              anchorPosition={menu.anchorPosition}
              onClose={menu.close}
              actions={menu.isOpen ? buildMenuActions() : []}
            />
          )}
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
  minimizedContent: PropTypes.node,
  contextMenuActions: PropTypes.array,
  contextMenuArea: PropTypes.oneOf(["card", "header"]),
  onHideCard: PropTypes.func,
};

export default DashboardCardWrapper;
