// Self-improvement: Guaardvark checks its own code and stages fixes for review.
// The switches, the self-check, pending fixes, the run log and the code keeps
// that autoresearch stages, in one place.
import React, { useCallback, useEffect, useState } from "react";
import { Paper, Stack, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import PageLayout from "../components/layout/PageLayout";
import { useSnackbar } from "../components/common/SnackbarProvider";
import { ActionButton } from "../components/settings/ui";
import SelfImprovementControls from "../components/selfImprovement/SelfImprovementControls";
import SelfImprovementRuns from "../components/selfImprovement/SelfImprovementRuns";
import CodeKeepsTable from "../components/selfImprovement/CodeKeepsTable";
import SelfEditsList from "../components/selfImprovement/SelfEditsList";
import { ragAutoresearchService } from "../api/ragAutoresearchService";
import { selfImprovementService } from "../api/selfImprovementService";
import { useUiTranslation } from "../i18n";

const SelfImprovementPage = () => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  const { showMessage } = useSnackbar();
  const [codeKeeps, setCodeKeeps] = useState([]);

  const fetchCodeKeeps = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.getStatus();
      setCodeKeeps(Array.isArray(data?.code_keeps) ? data.code_keeps : []);
    } catch {
      setCodeKeeps([]);
    }
  }, []);

  useEffect(() => {
    fetchCodeKeeps();
  }, [fetchCodeKeeps]);

  const handleCodeKeep = async (fixId, action) => {
    try {
      if (action === "approve") {
        await selfImprovementService.approveFix(fixId);
        showMessage(t("selfImprovement.codeKeeps.approved"), "success");
      } else {
        await selfImprovementService.rejectFix(fixId);
        showMessage(t("selfImprovement.codeKeeps.rejected"), "info");
      }
      fetchCodeKeeps();
    } catch (e) {
      showMessage(
        action === "approve"
          ? t("selfImprovement.codeKeeps.approveFailed", { error: e.message })
          : t("selfImprovement.codeKeeps.rejectFailed", { error: e.message }),
        "error",
      );
    }
  };

  return (
    <PageLayout
      title={t("selfImprovement.page.title")}
      variant="standard"
      actions={
        <Stack direction="row" spacing={1} sx={{ display: { xs: "none", sm: "flex" } }}>
          <ActionButton kind="link" onClick={() => navigate("/system-map")}>
            {t("selfImprovement.page.systemMap")}
          </ActionButton>
          <ActionButton kind="link" onClick={() => navigate("/settings")}>
            {t("selfImprovement.page.uncleClaudeSettings")}
          </ActionButton>
        </Stack>
      }
    >
      <Stack spacing={2}>
        <Typography variant="body2" color="text.secondary">
          {t("selfImprovement.page.intro")}
        </Typography>

        <Paper variant="outlined" sx={{ p: 2 }}>
          <SelfImprovementControls />
        </Paper>

        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="h6" sx={{ mb: 1 }}>
            {t("selfImprovement.selfEdits.title")}
          </Typography>
          <SelfEditsList />
        </Paper>

        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="h6" sx={{ mb: 1 }}>
            {t("selfImprovement.page.recentRuns")}
          </Typography>
          <SelfImprovementRuns />
        </Paper>

        {codeKeeps.length > 0 ? (
          <CodeKeepsTable keeps={codeKeeps} onDecide={handleCodeKeep} />
        ) : (
          <Typography variant="body2" color="text.secondary">
            {t("selfImprovement.page.noCodeKeeps")}
          </Typography>
        )}
      </Stack>
    </PageLayout>
  );
};

export default SelfImprovementPage;
