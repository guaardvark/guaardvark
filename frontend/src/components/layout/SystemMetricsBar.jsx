import React, { useEffect, useState } from "react";
import { Box, Typography, useTheme } from "@mui/material";
import { useSnackbar } from "../common/SnackbarProvider";
import * as apiService from "../../api";
import AlertSnackbar from "../common/AlertSnackbar";
import { METRICS_POLL_INTERVAL_MS } from "../../config";
import { useUiTranslation } from "../../i18n";

const getBarColor = (val, isTemp = false) => {
  if (val === null || val === undefined || isNaN(val)) return "#6b7280";
  const normVal = isTemp ? (val / 90) * 100 : val;
  if (normVal <= 50) return "#10b981"; // Emerald green
  if (normVal <= 80) return "#f59e0b"; // Amber
  return "#ef4444"; // Rose red
};

const SystemMetricsBar = () => {
  const { t } = useUiTranslation();
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
        if (!err.message.includes("fetch")) {
          showMessage(t("layout.systemMetrics.fetchFailed", { message: err.message }), "error");
        }
      }
    };
    fetchMetrics();
    const id = setInterval(fetchMetrics, Math.max(METRICS_POLL_INTERVAL_MS, 500));
    return () => {
      isMounted = false;
      clearInterval(id);
    };
  }, [showMessage]);

  const MetricRow = ({ label, value, isTemp = false }) => {
    const safeValue = value !== null && value !== undefined && !isNaN(value) ? value : null;
    const color = getBarColor(safeValue, isTemp);
    const unit = isTemp ? "°C" : "%";

    return (
      <Box sx={{ display: "flex", alignItems: "center", mb: 0.75, gap: 0.75 }}>
        <Typography
          sx={{
            width: 28,
            fontSize: "0.6rem",
            fontFamily: "monospace",
            fontWeight: 700,
            color: "text.secondary",
            letterSpacing: "0.05em",
            flexShrink: 0,
          }}
        >
          {label}
        </Typography>
        <Box
          sx={{
            flexGrow: 1,
            height: 7,
            bgcolor: "rgba(255, 255, 255, 0.08)",
            borderRadius: "4px",
            position: "relative",
            overflow: "hidden",
            border: "1px solid rgba(255, 255, 255, 0.05)",
          }}
        >
          <Box
            sx={{
              position: "absolute",
              top: 0,
              left: 0,
              height: "100%",
              width: `${Math.min(100, Math.max(0, safeValue || 0))}%`,
              background: `linear-gradient(90deg, ${color}cc 0%, ${color} 100%)`,
              borderRadius: "4px",
              boxShadow: `0 0 8px ${color}66`,
              transition: "width 0.35s cubic-bezier(0.4, 0, 0.2, 1)",
            }}
          />
        </Box>
        <Typography
          sx={{
            width: 36,
            textAlign: "right",
            fontSize: "0.6rem",
            fontFamily: "monospace",
            fontWeight: 700,
            color: color,
            flexShrink: 0,
          }}
        >
          {safeValue !== null ? `${Math.round(safeValue)}${unit}` : t("layout.notAvailable")}
        </Typography>
      </Box>
    );
  };

  if (!metrics) {
    return (
      <Box
        sx={{
          borderTop: `1px solid ${theme.palette.divider}`,
          backgroundColor: "rgba(15, 17, 23, 0.9)",
          p: "8px",
          textAlign: "center",
        }}
      >
        <Typography
          sx={{ fontSize: "0.6rem", fontFamily: "monospace", color: "text.secondary" }}
        >
          {t("layout.systemMetrics.connecting")}
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        borderTop: `1px solid rgba(255, 255, 255, 0.08)`,
        backgroundColor: "rgba(15, 17, 23, 0.9)",
        backdropFilter: "blur(12px)",
        p: "8px",
      }}
    >
      {/* GPU Section */}
      {(metrics.gpu_percent !== null || metrics.gpu_mem !== null || metrics.gpu_temp !== null) && (
        <Box sx={{ mb: 1.5 }}>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              mb: 0.5,
              borderBottom: "1px solid rgba(139, 92, 246, 0.2)",
              pb: 0.25,
            }}
          >
            <Typography
              sx={{
                fontSize: "0.6rem",
                fontFamily: "monospace",
                fontWeight: 700,
                color: "#8b5cf6",
                letterSpacing: "0.1em",
              }}
            >
              GPU
            </Typography>
            {metrics.gpu_mem_used_gb !== null && metrics.gpu_mem_used_gb !== undefined && (
              <Typography
                sx={{
                  fontSize: "0.55rem",
                  fontFamily: "monospace",
                  fontWeight: 700,
                  color: "rgba(255, 255, 255, 0.6)",
                }}
              >
                {metrics.gpu_mem_used_gb.toFixed(2)}GB / {metrics.gpu_mem_total_gb ? `${Math.round(metrics.gpu_mem_total_gb)}GB` : ""}
              </Typography>
            )}
          </Box>
          <MetricRow label={t("layout.systemMetrics.memory")} value={metrics?.gpu_mem ?? null} />
          <MetricRow label={t("layout.systemMetrics.utilization")} value={metrics?.gpu_percent ?? null} />
          <MetricRow label={t("layout.systemMetrics.temperature")} value={metrics?.gpu_temp ?? null} isTemp />
        </Box>
      )}

      {/* CPU Section */}
      <Box sx={{ mb: 0.5 }}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            mb: 0.5,
            borderBottom: "1px solid rgba(59, 130, 246, 0.2)",
            pb: 0.25,
          }}
        >
          <Typography
            sx={{
              fontSize: "0.6rem",
              fontFamily: "monospace",
              fontWeight: 700,
              color: "#3b82f6",
              letterSpacing: "0.1em",
            }}
          >
            CPU
          </Typography>
          {metrics.cpu_mem_used_gb !== null && metrics.cpu_mem_used_gb !== undefined && (
            <Typography
              sx={{
                fontSize: "0.55rem",
                fontFamily: "monospace",
                fontWeight: 700,
                color: "rgba(255, 255, 255, 0.6)",
              }}
            >
              {metrics.cpu_mem_used_gb.toFixed(1)}GB / {metrics.cpu_mem_total_gb ? `${Math.round(metrics.cpu_mem_total_gb)}GB` : ""}
            </Typography>
          )}
        </Box>
        <MetricRow label={t("layout.systemMetrics.memory")} value={metrics?.cpu_mem ?? null} />
        <MetricRow label={t("layout.systemMetrics.utilization")} value={metrics?.cpu_percent ?? null} />
        <MetricRow label={t("layout.systemMetrics.temperature")} value={metrics?.cpu_temp ?? null} isTemp />
      </Box>

      {/* GPU Tools Status */}
      {metrics.gpu_tools_available === false && (
        <Typography
          sx={{
            fontSize: "0.55rem",
            fontFamily: "monospace",
            color: "#f59e0b",
            display: "block",
            textAlign: "center",
            mt: 0.5,
          }}
        >
          {t("layout.systemMetrics.noGpuTools")}
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
