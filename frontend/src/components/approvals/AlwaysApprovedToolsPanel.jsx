// Tools chat runs without asking, chosen with "Always approve" on a tool card
// or "Always create" on the file card. "Ask again" brings the card back.
import React, { useCallback, useEffect, useState } from "react";
import { Alert, Box, CircularProgress, Stack, Typography } from "@mui/material";
import { ActionButton } from "../settings/ui";
import {
  getAlwaysApprovedTools,
  updateAlwaysApprovedTools,
} from "../../api/settingsService";
import { useUiTranslation } from "../../i18n";

const AlwaysApprovedToolsPanel = ({ showMessage }) => {
  const { t } = useUiTranslation();
  const [tools, setTools] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      setTools(await getAlwaysApprovedTools());
      setError(null);
    } catch (err) {
      setError(err.message || t("approvals.chatTools.loadFailed"));
      setTools([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const askAgain = async (name) => {
    setBusy(name);
    try {
      setTools(await updateAlwaysApprovedTools({ remove: [name] }));
      showMessage?.(t("approvals.chatTools.asksAgain", { name }));
    } catch (err) {
      setError(err.message || t("approvals.chatTools.updateFailed"));
    } finally {
      setBusy(null);
    }
  };

  if (tools === null) {
    return (
      <Box sx={{ p: 3, display: "flex", justifyContent: "center" }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {tools.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          {t("approvals.chatTools.empty")}
        </Typography>
      ) : (
        <Stack spacing={1}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
            {t("approvals.chatTools.intro")}
          </Typography>
          {tools.map((name) => (
            <Box key={name} sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
              <Typography variant="body2" sx={{ fontFamily: "monospace", minWidth: 220 }}>
                {name}
              </Typography>
              <ActionButton kind="neutral" loading={busy === name} onClick={() => askAgain(name)}>
                {t("approvals.chatTools.askAgain")}
              </ActionButton>
            </Box>
          ))}
        </Stack>
      )}
    </Box>
  );
};

export default AlwaysApprovedToolsPanel;
