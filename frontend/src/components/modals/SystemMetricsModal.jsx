import React, { useEffect, useState, useRef, useCallback } from "react";
import {
  Box,
  Typography,
  IconButton,
  Paper,
  Divider,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { useSnackbar } from "../common/SnackbarProvider";
import * as apiService from "../../api";
import AlertSnackbar from "../common/AlertSnackbar";
import { METRICS_POLL_INTERVAL_MS } from "../../config";
import { translate, useUiTranslation } from "../../i18n";

const MIN_WIDTH = 210;
const MIN_HEIGHT = 220;
const DEFAULT_WIDTH = 290;
const DEFAULT_HEIGHT = 440;
const DOUBLE_CLICK_MS = 400;
const MAX_HISTORY_POINTS = 60; // 30s at 500ms polling

const getBarColor = (val, isTemp = false) => {
  if (val === null || val === undefined || isNaN(val)) return "#6b7280";
  // Temp threshold: <=50°C good, <=75°C warn, >75°C high
  const normVal = isTemp ? (val / 90) * 100 : val;
  if (normVal <= 50) return "#10b981"; // Emerald green
  if (normVal <= 80) return "#f59e0b"; // Amber
  return "#ef4444"; // Rose red
};

const TelemetryGraph = ({ history, title, primaryColor, secondaryColor, scale }) => {
  const { t } = useUiTranslation();
  if (!history || history.length < 2) return null;

  const width = 240;
  const height = Math.max(36, Math.round(44 * scale));

  const pointsUtl = history.map((pt, idx) => {
    const x = (idx / (MAX_HISTORY_POINTS - 1)) * width;
    const y = height - (Math.min(100, Math.max(0, pt.utl ?? 0)) / 100) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const pointsMem = history.map((pt, idx) => {
    const x = (idx / (MAX_HISTORY_POINTS - 1)) * width;
    const y = height - (Math.min(100, Math.max(0, pt.mem ?? 0)) / 100) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const pathUtl = `M ${pointsUtl.join(" L ")}`;
  const areaUtl = `M 0,${height} L ${pointsUtl.join(" L ")} L ${width},${height} Z`;

  const pathMem = `M ${pointsMem.join(" L ")}`;
  const areaMem = `M 0,${height} L ${pointsMem.join(" L ")} L ${width},${height} Z`;

  const gradUtlId = `grad-utl-${title.replace(/\s+/g, "")}`;
  const gradMemId = `grad-mem-${title.replace(/\s+/g, "")}`;

  return (
    <Box sx={{ mt: 1.5 }}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 0.5 }}>
        <Typography
          sx={{
            fontSize: `${0.55 * scale}rem`,
            fontFamily: "monospace",
            fontWeight: 700,
            color: "text.secondary",
            letterSpacing: "0.1em",
          }}
        >
          {t("dialogs.systemMetrics.history", { device: title })}
        </Typography>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: primaryColor }} />
            <Typography sx={{ fontSize: `${0.5 * scale}rem`, fontFamily: "monospace", color: primaryColor, fontWeight: 700 }}>
              {t("dialogs.systemMetrics.utilization")}
            </Typography>
          </Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: secondaryColor }} />
            <Typography sx={{ fontSize: `${0.5 * scale}rem`, fontFamily: "monospace", color: secondaryColor, fontWeight: 700 }}>
              {t("dialogs.systemMetrics.memory")}
            </Typography>
          </Box>
        </Box>
      </Box>

      <Box
        sx={{
          position: "relative",
          width: "100%",
          height: height,
          bgcolor: "rgba(0, 0, 0, 0.35)",
          borderRadius: "6px",
          overflow: "hidden",
          border: "1px solid rgba(255, 255, 255, 0.08)",
        }}
      >
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ width: "100%", height: "100%", display: "block" }}>
          <defs>
            <linearGradient id={gradUtlId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={primaryColor} stopOpacity="0.45" />
              <stop offset="100%" stopColor={primaryColor} stopOpacity="0.0" />
            </linearGradient>
            <linearGradient id={gradMemId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={secondaryColor} stopOpacity="0.25" />
              <stop offset="100%" stopColor={secondaryColor} stopOpacity="0.0" />
            </linearGradient>
          </defs>

          {/* Grid Lines */}
          <line x1="0" y1={height * 0.25} x2={width} y2={height * 0.25} stroke="rgba(255,255,255,0.06)" strokeDasharray="2 2" />
          <line x1="0" y1={height * 0.5} x2={width} y2={height * 0.5} stroke="rgba(255,255,255,0.06)" strokeDasharray="2 2" />
          <line x1="0" y1={height * 0.75} x2={width} y2={height * 0.75} stroke="rgba(255,255,255,0.06)" strokeDasharray="2 2" />

          {/* Fills */}
          <path d={areaMem} fill={`url(#${gradMemId})`} />
          <path d={areaUtl} fill={`url(#${gradUtlId})`} />

          {/* Stroke Lines */}
          <path d={pathMem} fill="none" stroke={secondaryColor} strokeWidth="1.2" strokeDasharray="3 2" opacity="0.85" />
          <path d={pathUtl} fill="none" stroke={primaryColor} strokeWidth="1.8" />
        </svg>

        {/* Time Axis Markers */}
        <Box
          sx={{
            position: "absolute",
            bottom: 2,
            left: 4,
            right: 4,
            display: "flex",
            justifyContent: "space-between",
            pointerEvents: "none",
          }}
        >
          <Typography sx={{ fontSize: "0.45rem", fontFamily: "monospace", color: "rgba(255,255,255,0.3)" }}>{t("dialogs.systemMetrics.axisStart")}</Typography>
          <Typography sx={{ fontSize: "0.45rem", fontFamily: "monospace", color: "rgba(255,255,255,0.3)" }}>{t("dialogs.systemMetrics.axisMiddle")}</Typography>
          <Typography sx={{ fontSize: "0.45rem", fontFamily: "monospace", color: "rgba(255,255,255,0.4)", fontWeight: 700 }}>{t("dialogs.systemMetrics.now")}</Typography>
        </Box>
      </Box>
    </Box>
  );
};

const SystemMetricsModal = ({ open, onClose }) => {
  const { t } = useUiTranslation();
  const { showMessage } = useSnackbar();
  const [metrics, setMetrics] = useState(null);
  const [gpuHistory, setGpuHistory] = useState([]);
  const [cpuHistory, setCpuHistory] = useState([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [position, setPosition] = useState({ x: 100, y: 100 });
  const [size, setSize] = useState({ w: DEFAULT_WIDTH, h: DEFAULT_HEIGHT });
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [resizeStart, setResizeStart] = useState({ x: 0, y: 0, w: 0, h: 0 });
  const lastClickRef = useRef(0);
  const modalRef = useRef(null);
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
          throw new Error(data?.error || translate("dialogs.systemMetrics.fetchFailed"));
        }
        if (isMounted) {
          setMetrics(data);

          // Update GPU history buffer
          if (data.gpu_percent !== null || data.gpu_mem !== null) {
            setGpuHistory((prev) => {
              const updated = [
                ...prev,
                { utl: data.gpu_percent ?? 0, mem: data.gpu_mem ?? 0 },
              ];
              return updated.slice(-MAX_HISTORY_POINTS);
            });
          }

          // Update CPU history buffer
          if (data.cpu_percent !== null || data.cpu_mem !== null) {
            setCpuHistory((prev) => {
              const updated = [
                ...prev,
                { utl: data.cpu_percent ?? 0, mem: data.cpu_mem ?? 0 },
              ];
              return updated.slice(-MAX_HISTORY_POINTS);
            });
          }
        }
      } catch (err) {
        console.error("SystemMetricsModal:", err);
        if (!err.message.includes("fetch")) {
          showMessage(translate("dialogs.systemMetrics.fetchFailedWithError", { error: err.message }), "error");
        }
      }
    };

    if (open) {
      fetchMetrics();
      const id = setInterval(fetchMetrics, Math.max(METRICS_POLL_INTERVAL_MS, 500));
      return () => {
        isMounted = false;
        clearInterval(id);
      };
    }
  }, [open, showMessage]);

  const scale = Math.max(0.7, Math.min(1.8, size.w / DEFAULT_WIDTH));
  const barHeight = Math.max(7, Math.round(9 * scale));

  const MetricRow = ({ label, value, isTemp = false }) => {
    const safeValue = value !== null && value !== undefined && !isNaN(value) ? value : null;
    const color = getBarColor(safeValue, isTemp);
    const unit = isTemp ? "°C" : "%";

    return (
      <Box sx={{ display: "flex", alignItems: "center", mb: 1, gap: 1 }}>
        <Typography
          sx={{
            width: 34,
            fontSize: `${0.65 * scale}rem`,
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
            height: barHeight,
            bgcolor: "rgba(255, 255, 255, 0.08)",
            borderRadius: `${barHeight / 2}px`,
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
              borderRadius: `${barHeight / 2}px`,
              boxShadow: `0 0 10px ${color}88`,
              transition: "width 0.35s cubic-bezier(0.4, 0, 0.2, 1)",
            }}
          />
        </Box>
        <Typography
          sx={{
            width: 42,
            textAlign: "right",
            fontSize: `${0.65 * scale}rem`,
            fontFamily: "monospace",
            fontWeight: 700,
            color: color,
            flexShrink: 0,
          }}
        >
          {safeValue !== null ? `${Math.round(safeValue)}${unit}` : t("dialogs.systemMetrics.notAvailable")}
        </Typography>
      </Box>
    );
  };

  const handleHeaderMouseDown = useCallback((e) => {
    if (e.target.closest(".close-button")) return;

    const now = Date.now();
    if (now - lastClickRef.current < DOUBLE_CLICK_MS) {
      setCollapsed((prev) => !prev);
      lastClickRef.current = 0;
      return;
    }
    lastClickRef.current = now;

    if (e.target.closest(".metric-content")) return;
    setIsDragging(true);
    const rect = modalRef.current.getBoundingClientRect();
    setDragOffset({ x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);

  const handleResizeMouseDown = useCallback((e) => {
    e.stopPropagation();
    setIsResizing(true);
    setResizeStart({ x: e.clientX, y: e.clientY, w: size.w, h: size.h });
  }, [size]);

  useEffect(() => {
    if (!isDragging && !isResizing) return;

    const handleMouseMove = (e) => {
      if (isDragging) {
        const newX = e.clientX - dragOffset.x;
        const newY = e.clientY - dragOffset.y;
        setPosition({
          x: Math.max(10, Math.min(newX, window.innerWidth - size.w - 10)),
          y: Math.max(10, Math.min(newY, window.innerHeight - (collapsed ? 40 : size.h) - 10)),
        });
      }
      if (isResizing) {
        const maxW = Math.max(MIN_WIDTH, window.innerWidth - position.x - 20);
        const maxH = Math.max(MIN_HEIGHT, window.innerHeight - position.y - 20);
        const newW = Math.max(MIN_WIDTH, Math.min(maxW, resizeStart.w + (e.clientX - resizeStart.x)));
        const newH = Math.max(MIN_HEIGHT, Math.min(maxH, resizeStart.h + (e.clientY - resizeStart.y)));
        setSize({ w: newW, h: newH });
      }
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      setIsResizing(false);
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging, isResizing, dragOffset, resizeStart, position.x, position.y, size.w, size.h, collapsed]);

  if (!open) return null;

  return (
    <>
      <Paper
        ref={modalRef}
        elevation={12}
        sx={{
          position: "fixed",
          top: position.y,
          left: position.x,
          width: size.w,
          height: collapsed ? "auto" : size.h,
          zIndex: 1500,
          userSelect: "none",
          borderRadius: "12px",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          bgcolor: "rgba(15, 17, 23, 0.88)",
          backdropFilter: "blur(16px)",
          border: "1px solid rgba(255, 255, 255, 0.1)",
          boxShadow: "0 12px 36px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.1)",
        }}
      >
        {/* Header */}
        <Box
          onMouseDown={handleHeaderMouseDown}
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            px: 1.5,
            py: 1,
            cursor: isDragging ? "grabbing" : "grab",
            flexShrink: 0,
            background: "linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(0,0,0,0) 100%)",
            "&:hover": { bgcolor: "rgba(255, 255, 255, 0.03)" },
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            {/* Live Indicator Pulse */}
            <Box
              sx={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                bgcolor: "#10b981",
                boxShadow: "0 0 8px #10b981",
                animation: "pulse 2s infinite ease-in-out",
                "@keyframes pulse": {
                  "0%": { opacity: 0.4, transform: "scale(0.85)" },
                  "50%": { opacity: 1, transform: "scale(1.15)" },
                  "100%": { opacity: 0.4, transform: "scale(0.85)" },
                },
              }}
            />
            <Typography
              noWrap
              sx={{
                fontSize: `${0.75 * scale}rem`,
                fontFamily: "monospace",
                fontWeight: 700,
                letterSpacing: "0.08em",
                color: "text.primary",
                textTransform: "uppercase",
              }}
            >
              {size.w >= 220 ? t("dialogs.systemMetrics.title") : t("dialogs.systemMetrics.titleShort")}
            </Typography>
          </Box>
          <IconButton
            onClick={onClose}
            className="close-button"
            sx={{
              p: 0.25,
              ml: "auto",
              color: "text.secondary",
              "&:hover": { color: "#ef4444", bgcolor: "rgba(239, 68, 68, 0.1)" },
            }}
          >
            <CloseIcon sx={{ fontSize: Math.max(12, Math.round(16 * scale)) }} />
          </IconButton>
        </Box>

        {/* Content */}
        {!collapsed && (
          <>
            <Divider sx={{ borderColor: "rgba(255, 255, 255, 0.08)" }} />
            <Box
              className="metric-content"
              sx={{
                p: Math.max(1, 1.5 * scale),
                flexGrow: 1,
                overflow: "auto",
                cursor: "default",
              }}
              onMouseDown={(e) => e.stopPropagation()}
            >
              {!metrics ? (
                <Box sx={{ textAlign: "center", py: 2 }}>
                  <Typography
                    variant="caption"
                    sx={{
                      fontSize: `${0.7 * scale}rem`,
                      fontFamily: "monospace",
                      color: "text.secondary",
                    }}
                  >
                    {t("dialogs.systemMetrics.connecting")}
                  </Typography>
                </Box>
              ) : (
                <Box>
                  {/* GPU Section */}
                  {(metrics.gpu_percent !== null || metrics.gpu_mem !== null || metrics.gpu_temp !== null) && (
                    <Box sx={{ mb: 2 }}>
                      <Box
                        sx={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          mb: 1,
                          borderBottom: "1px solid rgba(139, 92, 246, 0.2)",
                          pb: 0.25,
                        }}
                      >
                        <Typography
                          sx={{
                            fontSize: `${0.6 * scale}rem`,
                            fontFamily: "monospace",
                            fontWeight: 700,
                            color: "#8b5cf6",
                            letterSpacing: "0.12em",
                          }}
                        >
                          {t("dialogs.systemMetrics.gpuTelemetry")}
                        </Typography>
                        {metrics.gpu_mem_used_gb !== null && metrics.gpu_mem_used_gb !== undefined && (
                          <Typography
                            sx={{
                              fontSize: `${0.58 * scale}rem`,
                              fontFamily: "monospace",
                              fontWeight: 700,
                              color: "rgba(255, 255, 255, 0.6)",
                              letterSpacing: "0.05em",
                            }}
                          >
                            {metrics.gpu_mem_used_gb.toFixed(2)}GB / {metrics.gpu_mem_total_gb ? `${Math.round(metrics.gpu_mem_total_gb)}GB` : ""}
                          </Typography>
                        )}
                      </Box>
                      <MetricRow label={t("dialogs.systemMetrics.memory")} value={metrics?.gpu_mem ?? null} />
                      <MetricRow label={t("dialogs.systemMetrics.utilization")} value={metrics?.gpu_percent ?? null} />
                      <MetricRow label={t("dialogs.systemMetrics.temperature")} value={metrics?.gpu_temp ?? null} isTemp />
                    </Box>
                  )}

                  {/* CPU Section */}
                  <Box sx={{ mb: 1 }}>
                    <Box
                      sx={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        mb: 1,
                        borderBottom: "1px solid rgba(59, 130, 246, 0.2)",
                        pb: 0.25,
                      }}
                    >
                      <Typography
                        sx={{
                          fontSize: `${0.6 * scale}rem`,
                          fontFamily: "monospace",
                          fontWeight: 700,
                          color: "#3b82f6",
                          letterSpacing: "0.12em",
                        }}
                      >
                        {t("dialogs.systemMetrics.cpuTelemetry")}
                      </Typography>
                      {metrics.cpu_mem_used_gb !== null && metrics.cpu_mem_used_gb !== undefined && (
                        <Typography
                          sx={{
                            fontSize: `${0.58 * scale}rem`,
                            fontFamily: "monospace",
                            fontWeight: 700,
                            color: "rgba(255, 255, 255, 0.6)",
                            letterSpacing: "0.05em",
                          }}
                        >
                          {metrics.cpu_mem_used_gb.toFixed(1)}GB / {metrics.cpu_mem_total_gb ? `${Math.round(metrics.cpu_mem_total_gb)}GB` : ""}
                        </Typography>
                      )}
                    </Box>
                    <MetricRow label={t("dialogs.systemMetrics.memory")} value={metrics?.cpu_mem ?? null} />
                    <MetricRow label={t("dialogs.systemMetrics.utilization")} value={metrics?.cpu_percent ?? null} />
                    <MetricRow label={t("dialogs.systemMetrics.temperature")} value={metrics?.cpu_temp ?? null} isTemp />
                  </Box>

                  {/* Live Telemetry History Graphs */}
                  <Divider sx={{ borderColor: "rgba(255, 255, 255, 0.08)", my: 1.5 }} />

                  {/* GPU History Sparkline */}
                  {gpuHistory.length > 1 && (
                    <TelemetryGraph
                      history={gpuHistory}
                      title="GPU"
                      primaryColor="#8b5cf6"
                      secondaryColor="#ec4899"
                      scale={scale}
                    />
                  )}

                  {/* CPU History Sparkline */}
                  {cpuHistory.length > 1 && (
                    <TelemetryGraph
                      history={cpuHistory}
                      title="CPU"
                      primaryColor="#3b82f6"
                      secondaryColor="#10b981"
                      scale={scale}
                    />
                  )}

                  {/* GPU Tools Status Warning */}
                  {metrics.gpu_tools_available === false && (
                    <Box
                      sx={{
                        p: 0.75,
                        bgcolor: "rgba(245, 158, 11, 0.1)",
                        borderRadius: 1,
                        border: "1px solid rgba(245, 158, 11, 0.3)",
                        mt: 1,
                      }}
                    >
                      <Typography
                        sx={{
                          fontSize: `${0.6 * scale}rem`,
                          fontFamily: "monospace",
                          color: "#f59e0b",
                          textAlign: "center",
                          fontWeight: 600,
                        }}
                      >
                        {t("dialogs.systemMetrics.noGpuTool")}
                      </Typography>
                    </Box>
                  )}
                </Box>
              )}
            </Box>

            {/* Resize handle */}
            <Box
              onMouseDown={handleResizeMouseDown}
              sx={{
                position: "absolute",
                bottom: 0,
                right: 0,
                width: 18,
                height: 18,
                cursor: "se-resize",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                "&::after": {
                  content: '""',
                  position: "absolute",
                  bottom: 4,
                  right: 4,
                  width: 8,
                  height: 8,
                  borderRight: "2px solid rgba(255, 255, 255, 0.3)",
                  borderBottom: "2px solid rgba(255, 255, 255, 0.3)",
                  borderRadius: "1px",
                },
                "&:hover::after": {
                  borderRightColor: "#3b82f6",
                  borderBottomColor: "#3b82f6",
                },
              }}
            />
          </>
        )}
      </Paper>

      <AlertSnackbar
        open={snackbar.open}
        onClose={handleCloseSnackbar}
        severity={snackbar.severity}
        message={snackbar.message}
      />
    </>
  );
};

export default SystemMetricsModal;