import React from "react";
import { Snackbar, Alert as MuiAlert } from "@mui/material";

// Reusable Snackbar+Alert component for displaying feedback messages
const AlertSnackbar = ({
  open,
  onClose,
  severity = "info",
  message,
  autoHideDuration = 4000,
  anchorOrigin = { vertical: "bottom", horizontal: "center" },
  ...props
}) => (
  <Snackbar
    open={open}
    autoHideDuration={autoHideDuration}
    onClose={onClose}
    anchorOrigin={anchorOrigin}
    {...props}
  >
    <MuiAlert
      onClose={onClose}
      severity={severity}
      elevation={6}
      variant="filled"
      sx={{ width: "100%" }}
    >
      {message}
    </MuiAlert>
  </Snackbar>
);

export default AlertSnackbar;
