import React, { createContext, useContext, useState, useCallback } from "react";
import { Snackbar, Alert } from "@mui/material";
import { GuaardvarkLogo } from "../branding";

const SnackbarContext = createContext(null);

export const SnackbarProvider = ({ children }) => {
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: "",
    severity: "info",
  });

  const showMessage = useCallback((message, severity = "info") => {
    setSnackbar({ open: true, message, severity });
  }, []);

  const handleClose = useCallback((event, reason) => {
    if (reason === "clickaway") return;
    setSnackbar((prev) => ({ ...prev, open: false }));
  }, []);

  return (
    <SnackbarContext.Provider
      value={{ showMessage, closeSnackbar: handleClose }}
    >
      {children}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={handleClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          onClose={handleClose}
          severity={snackbar.severity || "info"}
          variant="filled"
          icon={<GuaardvarkLogo size={20} variant={snackbar.severity || "info"} />}
          sx={{ width: "100%" }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </SnackbarContext.Provider>
  );
};

export const useSnackbar = () => {
  const ctx = useContext(SnackbarContext);
  if (ctx === undefined)
    throw new Error("useSnackbar must be used within a SnackbarProvider");
  return ctx;
};
