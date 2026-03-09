import React from "react";
import { Box, Typography, Tooltip, useTheme } from "@mui/material";
import FiberManualRecordIcon from "@mui/icons-material/FiberManualRecord";
import { useStatus } from "../../contexts/StatusContext";

const statusColor = (loaded, theme) =>
  loaded ? theme.palette.success.main : theme.palette.error.main;
const statusText = (loaded) => (loaded ? "Loaded" : "Not loaded");

const ModelStatusBar = () => {
  const theme = useTheme();
  const { modelStatus } = useStatus();

  if (!modelStatus) return null;

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
      {/* Text Model */}
      <Tooltip title={`Text Model: ${modelStatus.text_model || "N/A"}`}>
        <Typography
          variant="caption"
          sx={{ color: "text.secondary", fontWeight: 500 }}
        >
          {modelStatus.text_model || "Text: N/A"}
        </Typography>
      </Tooltip>
      {/* Vision Model */}
      <Tooltip
        title={`Vision Model: ${modelStatus.vision_model || "N/A"} (${statusText(modelStatus.vision_loaded)})`}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <FiberManualRecordIcon
            sx={{
              fontSize: 14,
              color: statusColor(modelStatus.vision_loaded, theme),
            }}
          />
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            {modelStatus.vision_model || "Vision"}
          </Typography>
        </Box>
      </Tooltip>
      {/* Image Gen Model */}
      <Tooltip
        title={`Image Gen Model: ${modelStatus.image_gen_model || "N/A"} (${statusText(modelStatus.image_gen_loaded)})`}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <FiberManualRecordIcon
            sx={{
              fontSize: 14,
              color: statusColor(modelStatus.image_gen_loaded, theme),
            }}
          />
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            {modelStatus.image_gen_model || "ImageGen"}
          </Typography>
        </Box>
      </Tooltip>
    </Box>
  );
};

export default ModelStatusBar;
