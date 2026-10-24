import React, { useEffect, useState } from "react";
import { Box, FormControlLabel, Switch, Tooltip, Typography } from "@mui/material";
import { getOllamaLifecycle, setOllamaLifecycle } from "../../api/settingsService";
import { useSnackbar } from "../common/SnackbarProvider";
import { useUiTranslation } from "../../i18n";

/**
 * Two switches that decide whether the start/stop scripts touch Ollama.
 * Both persist to .env and apply on the next stop or start; nothing restarts.
 */
// `title` lets the host name the block: "Ollama" where it stands alone, "Lifecycle"
// when it already sits inside the Ollama plugin card.
const OllamaLifecycleSection = ({ title = "Ollama" }) => {
  const { t } = useUiTranslation();
  const { showMessage } = useSnackbar();
  const [state, setState] = useState({ keep_running: false, external: false, env_writable: true });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getOllamaLifecycle()
      .then((data) => {
        const payload = data?.data ?? data;
        if (!cancelled && payload && typeof payload === "object") setState((s) => ({ ...s, ...payload }));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const update = async (patch) => {
    setSaving(true);
    try {
      const data = await setOllamaLifecycle(patch);
      const payload = data?.data ?? data;
      setState((s) => ({ ...s, ...patch, ...(payload || {}) }));
      showMessage(t("settingsSections.ollama.saved"), "success");
    } catch (err) {
      showMessage(t("settingsSections.ollama.saveFailed", { error: err.message || err }), "error");
    } finally {
      setSaving(false);
    }
  };

  const disabled = saving || state.env_writable === false;
  const external = Boolean(state.external);

  // Wrapped in a span only while "external" wins, because a disabled MUI Switch
  // swallows the pointer events a Tooltip listens for.
  const keepRunningSwitch = (
    <Switch
      size="small"
      checked={Boolean(state.keep_running)}
      disabled={disabled || external}
      onChange={(e) => update({ keep_running: e.target.checked })}
      inputProps={{ "data-testid": "ollama-keep-running" }}
    />
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5, mt: 2 }} data-testid="ollama-lifecycle">
      <Typography variant="subtitle2">{title}</Typography>
      <FormControlLabel
        control={
          external ? (
            <Tooltip title={t("settingsSections.ollama.notApplicableExternal")} arrow>
              <span>{keepRunningSwitch}</span>
            </Tooltip>
          ) : (
            keepRunningSwitch
          )
        }
        label={<Typography variant="body2">{t("settingsSections.ollama.keepRunning")}</Typography>}
      />
      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={external}
            disabled={disabled}
            onChange={(e) => update({ external: e.target.checked })}
            inputProps={{ "data-testid": "ollama-external" }}
          />
        }
        label={<Typography variant="body2">{t("settingsSections.ollama.external")}</Typography>}
      />
      <Typography variant="caption" color="text.secondary">
        {t("settingsSections.ollama.note")}
        {state.env_writable === false ? ` ${t("settingsSections.ollama.envNotWritable")}` : ""}
      </Typography>
    </Box>
  );
};

export default OllamaLifecycleSection;
