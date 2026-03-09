import React, { useEffect, useState, useRef, useCallback } from "react";
import {
  Box,
  Typography,
  IconButton,
  Paper,
  Divider,
  useTheme,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { useSnackbar } from "../common/SnackbarProvider";
import * as apiService from "../../api";
import AlertSnackbar from "../common/AlertSnackbar";
import { METRICS_POLL_INTERVAL_MS } from "../../config";

const MIN_WIDTH = 80;
const MIN_HEIGHT = 60;
const DEFAULT_WIDTH = 260;
const DEFAULT_HEIGHT = 420;
const DOUBLE_CLICK_MS = 400;

const SystemMetricsModal = ({ open, onClose }) => {
  const theme = useTheme();
  const { showMessage } = useSnackbar();
  const [metrics, setMetrics] = useState(null);
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
          throw new Error(data?.error || "Failed to fetch system metrics.");
        }
        if (isMounted) setMetrics(data);
      } catch (err) {
        console.error("SystemMetricsModal:", err);
        // Only show error message if it's not a network error (to avoid spam)
        if (!err.message.includes('fetch')) {
          showMessage(`Failed to fetch system metrics: ${err.message}`, "error");
        }
      }
    };
    
    if (open) {
      fetchMetrics();
      const id = setInterval(fetchMetrics, Math.max(METRICS_POLL_INTERVAL_MS, 10000));
      return () => {
        isMounted = false;
        clearInterval(id);
      };
    }
  }, [open, showMessage]);

  const getBarColor = (val) => {
    if (val === null || val === undefined || isNaN(val))
      return theme.palette.text.secondary;
    if (val <= 33) return theme.palette.success.main;
    if (val <= 66) return theme.palette.warning.main;
    return theme.palette.error.main;
  };

  // Scale factor: 1.0 at default width, scales proportionally
  const scale = Math.max(0.4, Math.min(2.5, size.w / DEFAULT_WIDTH));
  const showLabels = size.w >= 130;
  const showValues = size.w >= 110;

  const MetricRow = ({ label, value }) => {
    const safeValue = (value !== null && value !== undefined && !isNaN(value)) ? value : null;
    const barH = Math.max(3, Math.round(6 * scale));
    const fontSize = Math.max(0.5, 0.75 * scale);
    const gap = Math.max(2, Math.round(6 * scale));

    return (
      <Box sx={{ mb: `${gap}px` }}>
        {showLabels && (
          <Typography
            variant="body2"
            noWrap
            sx={{
              fontSize: `${fontSize}rem`,
              fontWeight: "medium",
              color: getBarColor(safeValue),
              mb: 0.5 * scale,
              lineHeight: 1.2,
            }}
          >
            {label}
          </Typography>
        )}
        <Box sx={{ display: "flex", alignItems: "center" }}>
          <Box
            sx={{
              flexGrow: 1,
              height: barH,
              bgcolor: theme.palette.grey[300],
              position: "relative",
              borderRadius: barH / 2,
              overflow: "hidden",
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
                borderRadius: barH / 2,
                transition: "width 0.3s ease",
              }}
            />
          </Box>
          {showValues && (
            <Typography
              variant="body2"
              sx={{
                fontSize: `${fontSize}rem`,
                ml: 0.5,
                flexShrink: 0,
                textAlign: "right",
                color: getBarColor(safeValue),
                fontWeight: "medium",
                lineHeight: 1.2,
              }}
            >
              {safeValue !== null ? Math.round(safeValue) : "—"}
            </Typography>
          )}
        </Box>
      </Box>
    );
  };

  // Double-click collapse/expand on header
  const handleHeaderMouseDown = useCallback((e) => {
    if (e.target.closest('.close-button')) return;

    const now = Date.now();
    if (now - lastClickRef.current < DOUBLE_CLICK_MS) {
      setCollapsed((prev) => !prev);
      lastClickRef.current = 0;
      return; // don't start drag on double-click
    }
    lastClickRef.current = now;

    // Start drag
    if (e.target.closest('.metric-content')) return;
    setIsDragging(true);
    const rect = modalRef.current.getBoundingClientRect();
    setDragOffset({ x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);

  // Resize handle
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
          x: Math.max(0, Math.min(newX, window.innerWidth - 100)),
          y: Math.max(0, Math.min(newY, window.innerHeight - 40)),
        });
      }
      if (isResizing) {
        const newW = Math.max(MIN_WIDTH, resizeStart.w + (e.clientX - resizeStart.x));
        const newH = Math.max(MIN_HEIGHT, resizeStart.h + (e.clientY - resizeStart.y));
        setSize({ w: newW, h: newH });
      }
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      setIsResizing(false);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, isResizing, dragOffset, resizeStart]);

  if (!open) return null;

  return (
    <>
      <Paper
        ref={modalRef}
        elevation={4}
        sx={{
          position: "fixed",
          top: position.y,
          left: position.x,
          width: size.w,
          height: collapsed ? "auto" : size.h,
          zIndex: 1500,
          userSelect: "none",
          borderRadius: "8px",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          border: `1px solid ${theme.palette.divider}`,
          boxShadow: `0 8px 24px rgba(0, 0, 0, 0.3)`,
        }}
      >
        {/* Header */}
        <Box
          onMouseDown={handleHeaderMouseDown}
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            px: 1,
            py: 0.25,
            cursor: isDragging ? "grabbing" : "grab",
            flexShrink: 0,
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          {size.w >= 100 && (
            <Typography
              variant="caption"
              noWrap
              sx={{
                fontSize: `${Math.max(0.55, 0.75 * scale)}rem`,
                fontWeight: 600,
                color: "text.secondary",
                letterSpacing: "0.02em",
                lineHeight: 1.2,
              }}
            >
              {size.w >= 180 ? "System Metrics" : "Metrics"}
            </Typography>
          )}
          <IconButton
            onClick={onClose}
            className="close-button"
            sx={{
              p: 0.25,
              ml: "auto",
              color: "text.secondary",
              "&:hover": { color: "error.main" },
            }}
          >
            <CloseIcon sx={{ fontSize: Math.max(10, Math.round(14 * scale)) }} />
          </IconButton>
        </Box>

        {/* Content */}
        {!collapsed && (
          <>
            <Divider />
            <Box
              className="metric-content"
              sx={{
                p: Math.max(0.5, 1.5 * scale),
                flexGrow: 1,
                overflow: "auto",
                cursor: "default",
              }}
              onMouseDown={(e) => e.stopPropagation()}
            >
              {!metrics ? (
                <Box sx={{ textAlign: "center", py: 1 }}>
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: `${Math.max(0.5, 0.75 * scale)}rem` }}>
                    Loading...
                  </Typography>
                </Box>
              ) : (
                <Box>
                  {/* GPU Section */}
                  {(metrics.gpu_percent !== null || metrics.gpu_mem !== null || metrics.gpu_temp !== null) && (
                    <Box sx={{ mb: Math.max(0.5, 1.5 * scale) }}>
                      {showLabels && (
                        <Typography
                          variant="caption"
                          sx={{
                            fontSize: `${Math.max(0.5, 0.7 * scale)}rem`,
                            fontWeight: 700,
                            color: "text.secondary",
                            textTransform: "uppercase",
                            letterSpacing: "0.06em",
                            mb: 0.25 * scale,
                            display: "block",
                            lineHeight: 1.2,
                          }}
                        >
                          GPU
                        </Typography>
                      )}
                      <MetricRow label="Memory" value={metrics?.gpu_mem ?? null} />
                      <MetricRow label="Utilization" value={metrics?.gpu_percent ?? null} />
                      <MetricRow label="Temperature" value={metrics?.gpu_temp ?? null} />
                    </Box>
                  )}

                  {/* CPU Section */}
                  <Box sx={{ mb: Math.max(0.5, 1.5 * scale) }}>
                    {showLabels && (
                      <Typography
                        variant="caption"
                        sx={{
                          fontSize: `${Math.max(0.5, 0.7 * scale)}rem`,
                          fontWeight: 700,
                          color: "text.secondary",
                          textTransform: "uppercase",
                          letterSpacing: "0.06em",
                          mb: 0.25 * scale,
                          display: "block",
                          lineHeight: 1.2,
                        }}
                      >
                        CPU
                      </Typography>
                    )}
                    <MetricRow label="Memory" value={metrics?.cpu_mem ?? null} />
                    <MetricRow label="Utilization" value={metrics?.cpu_percent ?? null} />
                    <MetricRow label="Temperature" value={metrics?.cpu_temp ?? null} />
                  </Box>

                  {/* GPU Tools Status */}
                  {metrics.gpu_tools_available === false && showLabels && (
                    <Box sx={{
                      p: 0.5 * scale,
                      bgcolor: theme.palette.warning.main + "22",
                      borderRadius: 1,
                      border: `1px solid ${theme.palette.warning.main}44`,
                    }}>
                      <Typography
                        variant="body2"
                        sx={{
                          fontSize: `${Math.max(0.45, 0.65 * scale)}rem`,
                          color: "warning.main",
                          textAlign: "center",
                        }}
                      >
                        GPU tools unavailable
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
                width: 16,
                height: 16,
                cursor: "se-resize",
                "&::after": {
                  content: '""',
                  position: "absolute",
                  bottom: 3,
                  right: 3,
                  width: 8,
                  height: 8,
                  borderRight: `2px solid ${theme.palette.text.secondary}`,
                  borderBottom: `2px solid ${theme.palette.text.secondary}`,
                  opacity: 0.4,
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