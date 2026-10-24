import React from "react";
import { Box, Typography, Tooltip, useTheme } from "@mui/material";
import FiberManualRecordIcon from "@mui/icons-material/FiberManualRecord";
import { useStatus } from "../../contexts/StatusContext";
import { translate, useUiTranslation } from "../../i18n";

const statusColor = (loaded, theme) =>
  loaded ? theme.palette.success.main : theme.palette.error.main;
const statusText = (loaded) =>
  loaded ? translate("layout.modelStatus.loaded") : translate("layout.modelStatus.notLoaded");

const ModelStatusBar = () => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const { modelStatus } = useStatus();

  if (!modelStatus) return null;

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
      {/* Text Model */}
      <Tooltip title={t("layout.modelStatus.textModel", { model: modelStatus.text_model || t("layout.notAvailable") })}>
        <Typography
          variant="caption"
          sx={{ color: "text.secondary", fontWeight: 500 }}
        >
          {modelStatus.text_model || t("layout.modelStatus.textNotAvailable")}
        </Typography>
      </Tooltip>
      {/* Vision Model */}
      <Tooltip
        title={t("layout.modelStatus.visionModel", {
          model: modelStatus.vision_model || t("layout.notAvailable"),
          status: statusText(modelStatus.vision_loaded),
        })}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <FiberManualRecordIcon
            sx={{
              fontSize: 14,
              color: statusColor(modelStatus.vision_loaded, theme),
            }}
          />
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            {modelStatus.vision_model || t("layout.modelStatus.vision")}
          </Typography>
        </Box>
      </Tooltip>
      {/* Image Gen Model */}
      <Tooltip
        title={t("layout.modelStatus.imageGenModel", {
          model: modelStatus.image_gen_model || t("layout.notAvailable"),
          status: statusText(modelStatus.image_gen_loaded),
        })}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <FiberManualRecordIcon
            sx={{
              fontSize: 14,
              color: statusColor(modelStatus.image_gen_loaded, theme),
            }}
          />
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            {modelStatus.image_gen_model || t("layout.modelStatus.imageGen")}
          </Typography>
        </Box>
      </Tooltip>
    </Box>
  );
};

export default ModelStatusBar;
