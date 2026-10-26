/**
 * SelfAnalysisSources - under a reply about Guaardvark's own code: the files
 * and lines it named, checked against the checkout (each opens in the Code
 * Editor), anything that did not check out, and "Plan a change" to turn the
 * answer into a plan.
 */
import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { ActionButton } from "../settings/ui";
import { usePlanActions } from "./planActions";
import { setPlanMode } from "../../api/planModeService";
import { useAppStore } from "../../stores/useAppStore";
import { useUiTranslation } from "../../i18n";

const SelfAnalysisSources = ({ analysis, sessionId }) => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  const planActions = usePlanActions();
  if (!analysis) return null;
  const valid = analysis.valid || [];
  const invalid = analysis.invalid || [];
  if (valid.length === 0 && invalid.length === 0) return null;

  const open = (ref) => navigate("/code-editor", {
    state: {
      openFile: {
        filename: ref.path.split("/").pop(),
        source: "live_repo",
        filePath: ref.path,
        relativePath: ref.path,
        line: ref.line || undefined,
      },
    },
  });

  const planChange = async () => {
    if (!sessionId) return;
    try {
      const data = await setPlanMode(sessionId, true);
      useAppStore.getState().setSessionPlanMode(sessionId, Boolean(data?.plan_mode));
    } catch {
      // The input still gets the prompt; the person can switch plan mode on.
    }
    planActions?.onKeepPlanning?.(t("chat.selfAnalysis.planPrompt"));
  };

  return (
    <Box data-testid="self-analysis-sources" sx={{ mt: 1, display: "flex", flexWrap: "wrap", gap: 0.5, alignItems: "center" }}>
      <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
        {t("chat.selfAnalysis.checked")}
      </Typography>
      {valid.map((ref) => (
        <Chip key={ref.ref} size="small" variant="outlined" label={ref.ref} onClick={() => open(ref)}
          sx={{ fontFamily: "monospace", fontSize: "0.7rem", height: 20 }} />
      ))}
      {invalid.map((ref) => (
        <Chip key={ref.ref} size="small" color="warning" variant="outlined"
          label={t("chat.selfAnalysis.notFound", { ref: ref.ref })}
          sx={{ fontFamily: "monospace", fontSize: "0.7rem", height: 20 }} />
      ))}
      <ActionButton kind="link" onClick={planChange} tooltip={t("chat.selfAnalysis.planTooltip")}>
        {t("chat.selfAnalysis.plan")}
      </ActionButton>
    </Box>
  );
};

SelfAnalysisSources.propTypes = { analysis: PropTypes.object, sessionId: PropTypes.string };

export default SelfAnalysisSources;
