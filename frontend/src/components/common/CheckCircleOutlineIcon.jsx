import React from "react";
import { createSvgIcon } from "@mui/material/utils";

// @mui/icons-material 9 dropped the `CheckCircleOutline` export, and its
// `CheckCircleOutlineOutlined` draws a shorter tick. This keeps the glyph the
// app has always shown.
const CheckCircleOutlineIcon = createSvgIcon(
  <path d="M16.59 7.58 10 14.17l-3.59-3.58L5 12l5 5 8-8zM12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2m0 18c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8" />,
  "CheckCircleOutline",
);

export default CheckCircleOutlineIcon;
