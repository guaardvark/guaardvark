import React, { createContext, useContext, useState } from "react";

const LayoutContext = createContext(null);

const createGridSettings = () => {
  const CARD_TARGET_PIXEL_WIDTH = 350;
  const CARD_ASPECT_RATIO_W_H = 2.5 / 3.5;
  const CARD_TARGET_PIXEL_HEIGHT =
    CARD_TARGET_PIXEL_WIDTH / CARD_ASPECT_RATIO_W_H;
  const CARD_MARGIN_PX = 8;
  const CONTAINER_PADDING_PX = 10;
  const GRID_CONTENT_WIDTH_PX =
    5 * CARD_TARGET_PIXEL_WIDTH + 0 * CARD_MARGIN_PX;
  const RGL_WIDTH_PROP_PX = GRID_CONTENT_WIDTH_PX;
  const COLS_COUNT = Math.round(RGL_WIDTH_PROP_PX / 10);
  const COL_WIDTH_PX = RGL_WIDTH_PROP_PX / COLS_COUNT;
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
    GRID_CONTENT_WIDTH_PX,
    RGL_WIDTH_PROP_PX,
    COLS_COUNT,
    COL_WIDTH_PX,
    ROW_HEIGHT_PX,
    cardGridW,
    cardGridH,
    cardMinGridW,
    cardMinGridH,
  };
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
  if (ctx === undefined)
    throw new Error("useLayout must be used within LayoutProvider");
  return ctx;
};

export default LayoutContext;
