// frontend/src/components/dashboard/GpuStatusCard.jsx
// Dashboard card showing real-time GPU VRAM usage, loaded models, and quality tier control.

import React, { useState, useEffect, useRef, useCallback } from "react";
import DashboardCardWrapper from "./DashboardCardWrapper";
import {
  Box,
  Typography,
  LinearProgress,
  Chip,
  IconButton,
  Tooltip,
  ToggleButton,
  ToggleButtonGroup,
  Stack,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import MemoryIcon from "@mui/icons-material/Memory";
import { io } from "socket.io-client";
import { SOCKET_URL } from "../../api/apiClient";
import { getGpuStatus, setGpuTier, evictGpuModel } from "../../api/gpuService";
import { translate, useUiTranslation } from "../../i18n";

// Color map for model types
const MODEL_COLORS = {
  ollama_llm: "#5c9cf5",
  ollama_embedding: "#6dd58c",
  sd_pipeline: "#b388ff",
  video_pipeline: "#ff8a65",
  whisper: "#ffd54f",
};

const tierOptions = () => [
  { value: "speed", menuLabel: translate("dashboard.gpu.tierMenu.speed") },
  { value: "balanced", menuLabel: translate("dashboard.gpu.tierMenu.balanced") },
  { value: "quality", menuLabel: translate("dashboard.gpu.tierMenu.quality") },
];

const stateLabels = () => ({
  loaded: translate("dashboard.gpu.states.loaded"),
  loading: translate("dashboard.gpu.states.loading"),
  unloading: translate("dashboard.gpu.states.unloading"),
  unloaded: translate("dashboard.gpu.states.unloaded"),
});

const GpuStatusCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      ...props
    },
    ref,
  ) => {
    const { t } = useUiTranslation();
    const theme = useTheme();
    const [status, setStatus] = useState(null);
    const [tier, setTier] = useState("balanced");
    const [error, setError] = useState(null);
    const socketRef = useRef(null);
    const pollRef = useRef(null);

    // Fetch initial status
    const fetchStatus = useCallback(async () => {
      try {
        const data = await getGpuStatus();
        setStatus(data);
        setTier(data.quality_tier || "balanced");
        setError(null);
      } catch (e) {
        setError(t("dashboard.gpu.unavailable"));
      }
    }, []);

    // Socket.IO subscription for live updates
    useEffect(() => {
      fetchStatus();

      try {
        const socket = io(SOCKET_URL, {
          reconnection: true,
          reconnectionAttempts: 3,
          transports: ["polling", "websocket"],
        });
        socketRef.current = socket;

        socket.on("connect", () => {
          socket.emit("subscribe_gpu");
        });

        socket.on("gpu:status", (data) => {
          setStatus(data);
          setTier(data.quality_tier || "balanced");
          setError(null);
        });
      } catch {
        // Socket not available — fall back to polling
      }

      // Fallback polling every 5s
      pollRef.current = setInterval(fetchStatus, 5000);

      return () => {
        if (socketRef.current) {
          socketRef.current.disconnect();
          socketRef.current = null;
        }
        if (pollRef.current) clearInterval(pollRef.current);
      };
    }, [fetchStatus]);

    const handleTierChange = async (_e, newTier) => {
      if (!newTier) return;
      try {
        await setGpuTier(newTier);
        setTier(newTier);
        fetchStatus();
      } catch {
        // Tier change failed
      }
    };

    const handleEvict = async (slotId) => {
      try {
        await evictGpuModel(slotId);
        fetchStatus();
      } catch {
        // Eviction failed
      }
    };

    // vram numbers are null when the GPU query failed (no GPU, driver down):
    // nothing here may divide by them or print them as 0.
    const vram = status?.vram?.total_mb > 0 ? status.vram : null;
    const models = status?.models || [];
    const usedPct = vram ? Math.round((vram.used_mb / vram.total_mb) * 100) : 0;
    const usedGb = vram ? (vram.used_mb / 1024).toFixed(1) : "?";
    const totalGb = vram ? (vram.total_mb / 1024).toFixed(1) : "?";
    const loadedModels = models.filter((m) => m.state === "loaded");

    const menuActions = [
      { label: t("common.refresh"), onClick: fetchStatus },
      ...tierOptions().map((option, idx) => ({
        label: option.menuLabel,
        checked: tier === option.value,
        onClick: () => handleTierChange(null, option.value),
        dividerBefore: idx === 0,
      })),
      ...loadedModels.map((m, idx) => ({
        label: t("dashboard.gpu.evict", { model: m.slot_id }),
        onClick: () => handleEvict(m.slot_id),
        dividerBefore: idx === 0,
      })),
    ];

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.gpu.title")}
        titleBarActions={
          <Tooltip title={t("common.refresh")}>
            <IconButton size="small" onClick={fetchStatus} sx={{ color: "inherit" }}>
              <MemoryIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        }
        {...props}
        contextMenuActions={menuActions}
      >
        <Box sx={{ p: 1.5, overflow: "auto", height: "100%" }}>
          {error && (
            <Typography variant="caption" color="error">
              {error}
            </Typography>
          )}

          {/* VRAM Bar */}
          {vram && (
            <Box sx={{ mb: 1.5 }}>
              <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                <Typography variant="caption" color="text.secondary">
                  VRAM
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {usedGb} / {totalGb} GB ({usedPct}%)
                </Typography>
              </Box>
              <LinearProgress
                variant="determinate"
                value={usedPct}
                sx={{
                  height: 10,
                  borderRadius: 1,
                  bgcolor: theme.palette.action.hover,
                  "& .MuiLinearProgress-bar": {
                    bgcolor:
                      usedPct > 90
                        ? theme.palette.error.main
                        : usedPct > 70
                          ? theme.palette.warning.main
                          : theme.palette.primary.main,
                    borderRadius: 1,
                  },
                }}
              />
              {/* Segmented breakdown */}
              {models.length > 0 && (
                <Box sx={{ display: "flex", mt: 0.5, height: 6, borderRadius: 0.5, overflow: "hidden" }}>
                  {models
                    .filter((m) => m.state === "loaded" || m.state === "loading")
                    .map((m) => {
                      const pct = vram.total_mb > 0 ? (m.vram_mb / vram.total_mb) * 100 : 0;
                      return (
                        <Tooltip key={m.slot_id} title={`${m.slot_id}: ${(m.vram_mb / 1024).toFixed(1)} GB`}>
                          <Box
                            sx={{
                              width: `${pct}%`,
                              bgcolor: MODEL_COLORS[m.model_type] || "#888",
                              minWidth: pct > 0 ? 4 : 0,
                            }}
                          />
                        </Tooltip>
                      );
                    })}
                  {/* Untracked / system */}
                  {vram && status?.untracked_vram_mb > 0 && (
                    <Tooltip title={t("dashboard.gpu.systemOther", { size: (status.untracked_vram_mb / 1024).toFixed(1) })}>
                      <Box
                        sx={{
                          width: `${(status.untracked_vram_mb / vram.total_mb) * 100}%`,
                          bgcolor: theme.palette.grey[600],
                          minWidth: 4,
                        }}
                      />
                    </Tooltip>
                  )}
                </Box>
              )}
            </Box>
          )}

          {/* Quality Tier */}
          <Box sx={{ mb: 1.5 }}>
            <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block" }}>
              {t("dashboard.gpu.qualityTier")}
            </Typography>
            <ToggleButtonGroup
              value={tier}
              exclusive
              onChange={handleTierChange}
              size="small"
              fullWidth
              sx={{ height: 28 }}
            >
              <ToggleButton value="speed" sx={{ fontSize: "0.7rem", py: 0 }}>
                {t("dashboard.gpu.tiers.speed")}
              </ToggleButton>
              <ToggleButton value="balanced" sx={{ fontSize: "0.7rem", py: 0 }}>
                {t("dashboard.gpu.tiers.balanced")}
              </ToggleButton>
              <ToggleButton value="quality" sx={{ fontSize: "0.7rem", py: 0 }}>
                {t("dashboard.gpu.tiers.quality")}
              </ToggleButton>
            </ToggleButtonGroup>
          </Box>

          {/* Model List */}
          <Stack spacing={0.5}>
            {models
              .filter((m) => m.state !== "unloaded")
              .map((m) => (
                <Box
                  key={m.slot_id}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    px: 1,
                    py: 0.5,
                    borderRadius: 1,
                    bgcolor: theme.palette.action.hover,
                  }}
                >
                  <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, minWidth: 0, flex: 1 }}>
                    <Box
                      sx={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        bgcolor: MODEL_COLORS[m.model_type] || "#888",
                        flexShrink: 0,
                      }}
                    />
                    <Typography
                      variant="caption"
                      noWrap
                      sx={{ fontFamily: "monospace", fontSize: "0.7rem" }}
                    >
                      {m.slot_id}
                    </Typography>
                  </Box>
                  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                    <Chip
                      label={`${(m.vram_mb / 1024).toFixed(1)}G`}
                      size="small"
                      sx={{ height: 18, fontSize: "0.65rem", bgcolor: "transparent" }}
                    />
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.6rem" }}>
                      {stateLabels()[m.state] || m.state}
                    </Typography>
                    {m.state === "loaded" && (
                      <Tooltip title={t("dashboard.gpu.evictFromGpu")}>
                        <IconButton
                          size="small"
                          onClick={() => handleEvict(m.slot_id)}
                          sx={{ p: 0.25 }}
                        >
                          <DeleteOutlineIcon sx={{ fontSize: 14 }} />
                        </IconButton>
                      </Tooltip>
                    )}
                  </Box>
                </Box>
              ))}
            {models.filter((m) => m.state !== "unloaded").length === 0 && (
              <Typography variant="caption" color="text.secondary" sx={{ textAlign: "center", py: 1 }}>
                {t("dashboard.gpu.noModels")}
              </Typography>
            )}
          </Stack>
        </Box>
      </DashboardCardWrapper>
    );
  },
);

GpuStatusCard.displayName = "GpuStatusCard";

export default GpuStatusCard;
