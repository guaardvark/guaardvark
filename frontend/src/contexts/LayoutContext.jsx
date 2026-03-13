import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import { useAppStore } from "../stores/useAppStore";
import { spacing } from "../theme/tokens";

const LayoutContext = createContext(null);

const CONTAINER_PADDING_PX = 4;

const createGridSettings = () => {
  const CARD_TARGET_PIXEL_WIDTH = 350;
  const CARD_ASPECT_RATIO_W_H = 2.5 / 3.5;
  const CARD_TARGET_PIXEL_HEIGHT =
    CARD_TARGET_PIXEL_WIDTH / CARD_ASPECT_RATIO_W_H;
  const CARD_MARGIN_PX = 8;
  // Use a reference width for computing grid column count and unit sizes.
  // The actual rendered width comes from useDashboardWidth() and is passed
  // as the ReactGridLayout `width` prop — it adapts to any screen size.
  const REFERENCE_WIDTH_PX = 1750;
  const COLS_COUNT = Math.round(REFERENCE_WIDTH_PX / 10);
  const COL_WIDTH_PX = REFERENCE_WIDTH_PX / COLS_COUNT;
  const ROW_HEIGHT_PX = 10;
  const cardGridW = Math.round(CARD_TARGET_PIXEL_WIDTH / COL_WIDTH_PX);
  const cardGridH = Math.round(CARD_TARGET_PIXEL_HEIGHT / ROW_HEIGHT_PX);
  const minResizablePixelW = 100;
  const minResizablePixelH = 100;
  const cardMinGridW = Math.max(
    1,
    Math.round(minResizablePixelW / COL_WIDTH_PX),
  );
  const cardMinGridH = Math.max(
    1,
    Math.round(minResizablePixelH / ROW_HEIGHT_PX),
  );

  return {
    CARD_TARGET_PIXEL_WIDTH,
    CARD_ASPECT_RATIO_W_H,
    CARD_TARGET_PIXEL_HEIGHT,
    CARD_MARGIN_PX,
    CONTAINER_PADDING_PX,
    // RGL_WIDTH_PROP_PX kept for backward compatibility with other pages
    // (ImagesPage, DocumentsPage, CodeEditorPage, FileManager, etc.)
    // The dashboard itself uses useDashboardWidth() for responsive sizing.
    RGL_WIDTH_PROP_PX: REFERENCE_WIDTH_PX,
    GRID_CONTENT_WIDTH_PX: REFERENCE_WIDTH_PX,
    COLS_COUNT,
    COL_WIDTH_PX,
    ROW_HEIGHT_PX,
    cardGridW,
    cardGridH,
    cardMinGridW,
    cardMinGridH,
  };
};

/**
 * Hook that returns the available dashboard width in pixels,
 * accounting for the sidebar and minimal padding.
 * Listens to window resize events.
 */
export const useDashboardWidth = () => {
  const sidebarExpanded = useAppStore((state) => state.sidebarExpanded);
  const sidebarWidth = sidebarExpanded ? spacing.sidebarExpanded : spacing.sidebarCollapsed;

  const calcWidth = useCallback(() => {
    return window.innerWidth - sidebarWidth - CONTAINER_PADDING_PX * 2;
  }, [sidebarWidth]);

  const [width, setWidth] = useState(calcWidth);

  useEffect(() => {
    const handleResize = () => setWidth(calcWidth());
    // Recalc immediately when sidebar changes
    setWidth(calcWidth());
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [calcWidth]);

  return width;
};

export const LayoutProvider = ({ children }) => {
  const [gridSettings, setGridSettings] = useState(createGridSettings());
  const [showFooter, setShowFooter] = useState(true);

  const value = {
    gridSettings,
    setGridSettings,
    showFooter,
    setShowFooter,
    headerHeight: 64,
    footerHeight: 48,
  };

  return (
    <LayoutContext.Provider value={value}>{children}</LayoutContext.Provider>
  );
};

export const useLayout = () => {
  const ctx = useContext(LayoutContext);
  if (!ctx)
    throw new Error("useLayout must be used within LayoutProvider");
  return ctx;
};

export default LayoutContext;
