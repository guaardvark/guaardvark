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
      variant="outlined"
      sx={{
        width: "100%",
        backgroundColor: "rgba(8, 10, 14, 0.85)",
        backdropFilter: "blur(12px)",
        border: "1px solid rgba(138, 155, 174, 0.25)",
        borderRadius: "8px",
        color: "rgba(255, 255, 255, 0.8)",
        fontFamily: '"Lato", sans-serif',
        '& .MuiAlert-icon': {
          color: 'inherit',
        },
        '& .MuiAlert-action': {
          color: 'rgba(255, 255, 255, 0.5)',
        },
      }}
    >
      {message}
    </MuiAlert>
  </Snackbar>
);

export default AlertSnackbar;
