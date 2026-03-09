import React, { useEffect, useState } from "react";
import { Box, Typography, useTheme } from "@mui/material";
import { useSnackbar } from "../common/SnackbarProvider";
import * as apiService from "../../api";
import AlertSnackbar from "../common/AlertSnackbar";
import { METRICS_POLL_INTERVAL_MS } from "../../config";

const SystemMetricsBar = () => {
  const theme = useTheme();
  const { showMessage } = useSnackbar();
  const [metrics, setMetrics] = useState(null);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: "",
    severity: "error",
  });
  const handleCloseSnackbar = () =>
    setSnackbar((prev) => ({ ...prev, open: false }));

  useEffect(() => {
    let isMounted = true;
    const fetchMetrics = async () => {
      try {
        const data = await apiService.getSystemMetrics();
        if (!data || data.error) {
          throw new Error(data?.error || "Failed to fetch system metrics.");
        }
        if (isMounted) setMetrics(data);
      } catch (err) {
        console.error("SystemMetricsBar:", err);
        // Only show error message if it's not a network error (to avoid spam)
        if (!err.message.includes('fetch')) {
          showMessage(`Failed to fetch system metrics: ${err.message}`, "error");
        }
      }
    };
    fetchMetrics();
    const id = setInterval(fetchMetrics, Math.max(METRICS_POLL_INTERVAL_MS, 10000));
    return () => {
      isMounted = false;
      clearInterval(id);
    };
  }, [showMessage]);

  const getBarColor = (val) => {
    if (val === null || val === undefined || isNaN(val))
      return theme.palette.text.secondary;
    if (val <= 33) return theme.palette.success.main;
    if (val <= 66) return theme.palette.warning.main;
    return theme.palette.error.main;
  };

  const MetricRow = ({ label, value }) => {
    const safeValue = (value !== null && value !== undefined && !isNaN(value)) ? value : null;
    
    return (
      <Box sx={{ mb: "2px" }}>
        <Typography
          variant="caption"
          sx={{
            fontSize: "0.55rem",
            textAlign: "center",
            color: getBarColor(safeValue),
          }}
        >
          {label}
        </Typography>
        <Box sx={{ display: "flex", alignItems: "center" }}>
          <Box
            sx={{
              flexGrow: 1,
              height: 4,
              mx: 0.5,
              bgcolor: theme.palette.grey[300],
              position: "relative",
              borderRadius: 1,
            }}
          >
            <Box
              sx={{
                position: "absolute",
                top: 0,
                left: 0,
                height: "100%",
                width: `${Math.min(100, safeValue || 0)}%`,
                bgcolor: getBarColor(safeValue),
                borderRadius: 1,
                transition: "width 0.3s ease",
              }}
            />
          </Box>
          <Typography
            variant="caption"
            sx={{
              fontSize: "0.55rem",
              width: 18,
              textAlign: "right",
              color: getBarColor(safeValue),
            }}
          >
            {safeValue !== null ? Math.round(safeValue) : "N/A"}
          </Typography>
        </Box>
      </Box>
    );
  };

  if (!metrics) {
    return (
      <Box
        sx={{
          borderTop: `1px solid ${theme.palette.divider}`,
          backgroundColor: "rgba(20,20,20,0.9)",
          p: "4px",
          textAlign: "center",
        }}
      >
        <Typography
          variant="caption"
          sx={{ fontSize: "0.6rem", color: theme.palette.text.secondary }}
        >
          Loading metrics...
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        borderTop: `1px solid ${theme.palette.divider}`,
        backgroundColor: "rgba(20,20,20,0.9)",
        p: "2px",
      }}
    >
      {/* GPU Section */}
      {(metrics.gpu_percent !== null || metrics.gpu_mem !== null || metrics.gpu_temp !== null) && (
        <>
          <Typography
            variant="caption"
            sx={{ fontSize: "0.67rem", fontWeight: "bold", color: theme.palette.text.primary }}
          >
            GPU
          </Typography>
          <MetricRow label="Memory" value={metrics?.gpu_mem ?? null} />
          <MetricRow label="Utilization" value={metrics?.gpu_percent ?? null} />
          <MetricRow label="Temp" value={metrics?.gpu_temp ?? null} />
        </>
      )}
      
      {/* CPU Section */}
      <Typography
        variant="caption"
        sx={{ fontSize: "0.67rem", fontWeight: "bold", mt: 0.5, color: theme.palette.text.primary }}
      >
        CPU
      </Typography>
      <MetricRow label="Memory" value={metrics?.cpu_mem ?? null} />
      <MetricRow label="Utilization" value={metrics?.cpu_percent ?? null} />
      <MetricRow label="Temp" value={metrics?.cpu_temp ?? null} />
      
      {/* GPU Tools Status */}
      {metrics.gpu_tools_available === false && (
        <Typography
          variant="caption"
          sx={{ 
            fontSize: "0.5rem", 
            color: theme.palette.warning.main,
            display: "block",
            textAlign: "center",
            mt: 0.5
          }}
        >
          No GPU tools
        </Typography>
      )}
      
      <AlertSnackbar
        open={snackbar.open}
        onClose={handleCloseSnackbar}
        severity={snackbar.severity}
        message={snackbar.message}
      />
    </Box>
  );
};

export default SystemMetricsBar;
