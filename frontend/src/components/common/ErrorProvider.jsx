import React, { createContext, useContext, useState, useCallback } from "react";
import {
  Snackbar,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
} from "@mui/material";

const ErrorContext = createContext(null);

export const ErrorProvider = ({ children }) => {
  const [error, setError] = useState({ open: false, message: "", details: "" });
  const [dialogOpen, setDialogOpen] = useState(false);

  const showError = useCallback((message, details = "") => {
    setError({ open: true, message, details });
  }, []);

  const handleClose = useCallback((event, reason) => {
    if (reason === "clickaway") return;
    setError((prev) => ({ ...prev, open: false }));
  }, []);

  const openDetails = useCallback(() => {
    setDialogOpen(true);
    setError((prev) => ({ ...prev, open: false }));
  }, []);

  const closeDialog = useCallback(() => {
    setDialogOpen(false);
  }, []);

  return (
    <ErrorContext.Provider value={{ showError }}>
      {children}
      <Snackbar
        open={error.open}
        autoHideDuration={6000}
        onClose={handleClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          onClose={handleClose}
          severity="error"
          variant="outlined"
          sx={{
            width: "100%",
            cursor: "pointer",
            backgroundColor: "rgba(8, 10, 14, 0.85)",
            backdropFilter: "blur(12px)",
            border: "1px solid rgba(244, 67, 54, 0.4)",
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
          onClick={openDetails}
        >
          {error.message}
        </Alert>
      </Snackbar>
      <Dialog open={dialogOpen} onClose={closeDialog} fullWidth maxWidth="sm">
        <DialogTitle>Error Details</DialogTitle>
        <DialogContent dividers>
          <pre style={{ whiteSpace: "pre-wrap" }}>
            {error.details || error.message}
          </pre>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeDialog}>Close</Button>
        </DialogActions>
      </Dialog>
    </ErrorContext.Provider>
  );
};

export const useError = () => {
  const ctx = useContext(ErrorContext);
  if (ctx === undefined)
    throw new Error("useError must be used within ErrorProvider");
  return ctx;
};

export default ErrorContext;
