import React from "react";
import { Box, Typography, CircularProgress } from "@mui/material";
import { useHealth } from "../../contexts/HealthContext";
import { useUiTranslation, Trans } from "../../i18n";

/**
 * App-wide banner shown when the health poll confirms the Flask backend is
 * unreachable. The HealthProvider keeps polling every 10s, so the banner clears
 * itself automatically once the backend comes back — no manual reload needed.
 *
 * This replaces the old failure mode where a backend blip surfaced only as a wall
 * of misleading "HTTP 500" console errors with no user-facing signal.
 */
const BackendOfflineBanner = () => {
  useUiTranslation();
  const { isBackendOffline } = useHealth();

  if (!isBackendOffline) return null;

  return (
    <Box
      role="alert"
      sx={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: (theme) => theme.zIndex.snackbar + 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 1.5,
        py: 0.75,
        px: 2,
        bgcolor: "error.dark",
        color: "error.contrastText",
        boxShadow: 3,
      }}
    >
      <CircularProgress size={14} color="inherit" />
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        <Trans i18nKey="layoutCommon.backendOffline.message" components={{ code: <code /> }} />
      </Typography>
    </Box>
  );
};

export default BackendOfflineBanner;
