// SystemStatusCard.jsx   Version 2.0 (uses DashboardCardWrapper + functional backend)

import React, { useState, useEffect } from "react";
import { Button, CircularProgress, Typography, Box, Grid } from "@mui/material";
import AlertSnackbar from "../common/AlertSnackbar";
import DashboardCardWrapper from "./DashboardCardWrapper";

const SystemStatusCard = React.forwardRef(
  ({ style, isMinimized, onToggleMinimize, ...props }, ref) => {
    const [status, setStatus] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [actionResponse, setActionResponse] = useState(null);
    const [snackbar, setSnackbar] = useState({
      open: false,
      message: "",
      severity: "error",
    });

    const handleCloseSnackbar = () =>
      setSnackbar((prev) => ({ ...prev, open: false }));

    const fetchStatus = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch("/api/meta/status");
        if (!res.ok) throw new Error("Failed to fetch system status.");
        const data = await res.json();
        if (!data) throw new Error("No status data returned.");
        setStatus(data);
      } catch (err) {
        setError(err.message);
        setSnackbar({ open: true, message: err.message, severity: "error" });
      } finally {
        setLoading(false);
      }
    };

    const triggerAction = async (endpoint) => {
      setLoading(true);
      setActionResponse(null);
      try {
        const res = await fetch(`/api/meta/${endpoint}`, { method: "POST" });
        const data = await res.json();
        setActionResponse(data);
      } catch (err) {
        setActionResponse({ error: err.message });
      } finally {
        fetchStatus();
      }
    };

    useEffect(() => {
      fetchStatus();
    }, []);

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        title="System Diagnostics"
        {...props}
      >
        {loading && <CircularProgress sx={{ my: 2 }} />}
        {status && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="body2">
              Python: {status.python_version}
            </Typography>
            <Typography variant="body2">Platform: {status.platform}</Typography>
            <Typography variant="body2">DB Path: {status.db_path}</Typography>
            <Typography variant="body2">
              DB Size: {status.db_size_kb} KB
            </Typography>
            <Typography variant="body2">
              Documents: {status.document_count}
            </Typography>
            <Typography variant="body2">
              Models: {status.model_count}
            </Typography>
            <Typography variant="body2">
              Index Status: {status.index_status}
            </Typography>
          </Box>
        )}

        <Grid container spacing={2}>
          <Grid item>
            <Button
              onClick={() => triggerAction("selftest")}
              variant="contained"
              className="non-draggable"
            >
              Run Self-Test
            </Button>
          </Grid>
          <Grid item>
            <Button
              onClick={() => triggerAction("rebuild-index")}
              variant="contained"
              color="warning"
              className="non-draggable"
            >
              Rebuild Index
            </Button>
          </Grid>
          <Grid item>
            <Button
              onClick={() => triggerAction("clear-cache")}
              variant="outlined"
              color="secondary"
              className="non-draggable"
            >
              Clear Cache
            </Button>
          </Grid>
        </Grid>

        {actionResponse && (
          <Box sx={{ mt: 2 }}>
            <AlertSnackbar
              open={!!actionResponse}
              onClose={() => setActionResponse(null)}
              severity={actionResponse.error ? "error" : "success"}
              message={JSON.stringify(actionResponse, null, 2)}
              autoHideDuration={6000}
            />
          </Box>
        )}

        <AlertSnackbar
          open={snackbar.open}
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          message={snackbar.message}
        />
      </DashboardCardWrapper>
    );
  },
);

SystemStatusCard.displayName = "SystemStatusCard";
export default SystemStatusCard;
