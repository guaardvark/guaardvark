// frontend/src/components/dashboard/DashboardCardWrapper.jsx
// Version 3.0: Added double-click minimize/maximize functionality
// - Double-click header to minimize/maximize
// - Minimized cards show as 300px wide bars
// - Enhanced dragging support for minimized cards
// - Better UX for minimize/maximize transitions

import React, { useEffect, useRef, useMemo, useCallback, useState } from "react";
import PropTypes from "prop-types";
import {
  Paper,
  Box,
  Typography,
  Divider,
  IconButton,
  InputBase,
  Tooltip,
} from "@mui/material";
import { ThemeProvider, useTheme } from "@mui/material/styles";
import { flattenOver, foregroundFor, themeOnColor } from "../../theme/onColor";
import CloseIcon from "@mui/icons-material/Close";
import UnfoldLessIcon from "@mui/icons-material/UnfoldLess";
import UnfoldMoreIcon from "@mui/icons-material/UnfoldMore";
import { toHexColor } from "../../utils/colorHex";
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
  videogen: "/video",
  approvals: "/approvals",
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
 *
 * `route` is the page the title opens, for a card core has no page listed for
 * (an extension's).
 *
 * A card the person names (a note) passes `onRename`: its title is then a name,
 * not a link to a page. Double-clicking the name of an open card edits it in
 * place (Enter or leaving the box keeps it, Escape does not); everywhere else
 * on the bar, and anywhere on a minimized card's bar, a double-click minimizes
 * or expands as usual. `onClose` adds a close button, `showMinimizeButton` a
 * minimize button beside it, and `keepMounted` keeps the body in the page
 * while minimized, for a body that holds unsaved state of its own.
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
      onRename,
      titlePlaceholder,
      route: routeProp,
      onClose,
      closeLabel,
      showMinimizeButton = false,
      keepMounted = false,
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

      sx: sxProp, // merged over the frame's own styles below

      ...restProps // Collect any other valid DOM/MUI props (like data-grid)
    } = props;

    const theme = useTheme();
    const cardId = restProps.id || i;
    // The page the title opens: the card's own, or the built-in one for a core card.
    const cardRoute = routeProp || getCardRoute(cardId);
    const colorInputRef = useRef(null);

    // A custom card colour: header text and the card body's theme follow it
    // (black or white ink by WCAG contrast); without one the app theme stands.
    // A translucent colour (a note's glass tint) is read as it looks over the page.
    const inkBase = useMemo(
      () => (cardColor ? flattenOver(cardColor, theme.palette.background.default) : null),
      [cardColor, theme.palette.background.default],
    );
    const colorData = useMemo(() => {
      const fg = inkBase ? foregroundFor(inkBase) : null;
      if (!fg) {
        return { isLight: false, oppositeColor: theme.palette.text.primary };
      }
      return { isLight: fg.mode === "light", oppositeColor: fg.text.primary };
    }, [inkBase, theme.palette.text.primary]);
    const bodyTheme = useMemo(() => themeOnColor(inkBase), [inkBase]);
    const onCardColor = (node) => (bodyTheme ? <ThemeProvider theme={bodyTheme}>{node}</ThemeProvider> : node);

    const _isLightColor = colorData.isLight;
    const getOppositeColor = colorData.oppositeColor;

    // Renaming in place (cards with onRename).
    const [renaming, setRenaming] = useState(false);
    const [nameDraft, setNameDraft] = useState(title || "");
    const renameCancelled = useRef(false);
    useEffect(() => {
      if (!renaming) setNameDraft(title || "");
    }, [title, renaming]);
    const startRename = () => {
      // An Escape that unmounted the last name box may have left this set.
      renameCancelled.current = false;
      setNameDraft(title || "");
      setRenaming(true);
    };
    const commitRename = () => {
      setRenaming(false);
      if (nameDraft !== (title || "")) onRename(nameDraft);
    };
    const cancelRename = () => {
      renameCancelled.current = true;
      setNameDraft(title || "");
      setRenaming(false);
    };


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
        if (onRename && !isMinimized && e.target.closest?.("[data-card-title]")) {
          // Open the name box once this press is released: a press moves focus
          // to what was pressed, which would close a box opened during it.
          window.addEventListener("mouseup", () => startRename(), { once: true });
        } else if (onToggleMinimize) {
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
    }, [onToggleMinimize, onRename, isMinimized, title]);

    // Cleanup timeout on unmount
    useEffect(() => {
      return () => {
        if (clickState.current.timeout) {
          clearTimeout(clickState.current.timeout);
        }
      };
    }, []);

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
      const route = cardRoute;
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
    const cardContent = isMinimized && !keepMounted ? null : onCardColor(
      <>
        {!isMinimized && <Divider sx={{ mb: 0.5 }} />}
        <Box
          sx={{
            flexGrow: 1,
            overflowY: "auto",
            overflowX: "hidden",
            px: 0.5, // Reduced horizontal padding from 1 to 0.5
            display: isMinimized ? "none" : "flex",
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
            ...sxProp,
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
            {/* A name the person gives the card: part of the drag handle, edited in place. */}
            {onRename && renaming && (
              <InputBase
                autoFocus
                className="non-draggable"
                value={nameDraft}
                placeholder={titlePlaceholder}
                inputProps={{ "aria-label": titlePlaceholder }}
                onMouseDown={(e) => e.stopPropagation()}
                onChange={(e) => setNameDraft(e.target.value)}
                onBlur={() => {
                  if (renameCancelled.current) {
                    renameCancelled.current = false;
                    return;
                  }
                  commitRename();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    e.currentTarget.blur();
                  } else if (e.key === "Escape") {
                    e.preventDefault();
                    e.stopPropagation();
                    cancelRename();
                  }
                }}
                // The caret goes after the name. The drag handle clears the page
                // selection one frame after the press ends, so place it after that frame.
                onFocus={(e) => {
                  const input = e.target;
                  requestAnimationFrame(() => {
                    const end = input.value.length;
                    input.setSelectionRange(end, end);
                  });
                }}
                sx={{
                  flex: 1,
                  minWidth: 0,
                  fontSize: "0.77rem",
                  fontWeight: 600,
                  color: getOppositeColor,
                  "& input": { p: 0, color: "inherit", userSelect: "text" },
                }}
              />
            )}
            {onRename && !renaming && (
              <Typography
                component="span"
                data-card-title=""
                sx={{
                  minWidth: 0,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  color: getOppositeColor,
                  userSelect: "none",
                  fontSize: "0.77rem",
                  fontWeight: 600,
                  px: 0.4,
                  opacity: title ? 1 : 0.3,
                  fontStyle: title ? "normal" : "italic",
                }}
              >
                {title || titlePlaceholder}
              </Typography>
            )}

            {/* Otherwise a plain title; the card's page is in its right-click menu. */}
            {!onRename && (
              <Typography
                variant="h6"
                component="span"
                sx={{
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  color: getOppositeColor,
                  userSelect: "none",
                  fontSize: "0.62rem",
                  fontWeight: "medium",
                  px: 0.4,
                  py: 0.15,
                  lineHeight: 1.2,
                }}
              >
                {title}
              </Typography>
            )}

            {/* Custom title bar actions */}
            {titleBarActions && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                {onCardColor(titleBarActions)}
              </Box>
            )}

            {/* The rest of the bar is empty and drags the card. */}
            <Box sx={{ flex: 1, minWidth: "24px" }} />

            <Box sx={{ position: "relative" }}>
              {/* The colour picker opens from the right-click menu. */}
              <input
                ref={colorInputRef}
                type="color"
                value={cardColor ? toHexColor(cardColor) : "#1976d2"}
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

            {showMinimizeButton && onToggleMinimize && (
              <Tooltip title={isMinimized ? t("dashboard.cardWrapper.expand") : t("dashboard.cardWrapper.minimize")}>
                <IconButton
                  aria-label={isMinimized ? t("dashboard.cardWrapper.expand") : t("dashboard.cardWrapper.minimize")}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleMinimize();
                  }}
                  className="non-draggable"
                  size="small"
                  sx={{ width: 20, height: 20, p: 0, ml: 0.5, color: getOppositeColor, opacity: 0.4, "&:hover": { opacity: 1 } }}
                >
                  {isMinimized ? <UnfoldMoreIcon sx={{ fontSize: 16 }} /> : <UnfoldLessIcon sx={{ fontSize: 16 }} />}
                </IconButton>
              </Tooltip>
            )}
            {onHideCard && !onClose && (
              <Tooltip title={t("dashboard.cardWrapper.hideCard")}>
                <IconButton
                  aria-label={t("dashboard.cardWrapper.hideCard")}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    onHideCard();
                  }}
                  className="non-draggable"
                  size="small"
                  sx={{ width: 18, height: 18, p: 0, ml: 0.5, color: getOppositeColor, opacity: 0.35, "&:hover": { opacity: 1 } }}
                >
                  <CloseIcon sx={{ fontSize: 13 }} />
                </IconButton>
              </Tooltip>
            )}
            {onClose && (
              <Tooltip title={closeLabel || t("common.close")}>
                <IconButton
                  aria-label={closeLabel || t("common.close")}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    onClose();
                  }}
                  className="non-draggable"
                  size="small"
                  sx={{
                    width: 20,
                    height: 20,
                    p: 0,
                    ml: 0.5,
                    color: getOppositeColor,
                    opacity: 0.4,
                    "&:hover": { opacity: 1, backgroundColor: "rgba(255,0,0,0.1)" },
                  }}
                >
                  <CloseIcon sx={{ fontSize: 16 }} />
                </IconButton>
              </Tooltip>
            )}
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
  title: PropTypes.string,
  children: PropTypes.node.isRequired,
  cardColor: PropTypes.string,
  onCardColorChange: PropTypes.func,
  isMinimized: PropTypes.bool,
  onToggleMinimize: PropTypes.func,
  minimizedContent: PropTypes.node,
  contextMenuActions: PropTypes.array,
  contextMenuArea: PropTypes.oneOf(["card", "header"]),
  onHideCard: PropTypes.func,
  route: PropTypes.string,
  onRename: PropTypes.func,
  titlePlaceholder: PropTypes.string,
  onClose: PropTypes.func,
  closeLabel: PropTypes.string,
  showMinimizeButton: PropTypes.bool,
  keepMounted: PropTypes.bool,
};

export default DashboardCardWrapper;
