// SystemStatusCard.jsx   Version 2.0 (uses DashboardCardWrapper + functional backend)

import React, { useState, useEffect } from "react";
import { Button, CircularProgress, Typography, Box, Grid } from "@mui/material";
import AlertSnackbar from "../common/AlertSnackbar";
import DashboardCardWrapper from "./DashboardCardWrapper";
import { useUiTranslation } from "../../i18n";

const SystemStatusCard = React.forwardRef(
  ({ style, isMinimized, onToggleMinimize, cardColor, onCardColorChange, ...props }, ref) => {
    const { t } = useUiTranslation();
    const [status, setStatus] = useState(null);
    const [loading, setLoading] = useState(false);
    const [_error, setError] = useState(null);
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
        if (!res.ok) throw new Error(t("dashboard.systemStatus.fetchFailed"));
        const data = await res.json();
        if (!data) throw new Error(t("dashboard.systemStatus.noData"));
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
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.systemStatus.title")}
        {...props}
      >
        {loading && <CircularProgress sx={{ my: 2 }} />}
        {status && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="body2">
              {t("dashboard.systemStatus.python", { version: status.python_version })}
            </Typography>
            <Typography variant="body2">{t("dashboard.systemStatus.platform", { platform: status.platform })}</Typography>
            <Typography variant="body2">{t("dashboard.systemStatus.dbPath", { path: status.db_path })}</Typography>
            <Typography variant="body2">
              {t("dashboard.systemStatus.dbSize", { size: status.db_size_kb })}
            </Typography>
            <Typography variant="body2">
              {t("dashboard.systemStatus.documents", { count: status.document_count })}
            </Typography>
            <Typography variant="body2">
              {t("dashboard.systemStatus.models", { count: status.model_count })}
            </Typography>
            <Typography variant="body2">
              {t("dashboard.systemStatus.indexStatus", { status: status.index_status })}
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
              {t("dashboard.systemStatus.runSelfTest")}
            </Button>
          </Grid>
          <Grid item>
            <Button
              onClick={() => triggerAction("rebuild-index")}
              variant="contained"
              color="warning"
              className="non-draggable"
            >
              {t("dashboard.systemStatus.rebuildIndex")}
            </Button>
          </Grid>
          <Grid item>
            <Button
              onClick={() => triggerAction("clear-cache")}
              variant="outlined"
              color="secondary"
              className="non-draggable"
            >
              {t("dashboard.systemStatus.clearCache")}
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
