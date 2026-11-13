import React, { useEffect, useState } from "react";
import { Box, FormControlLabel, Switch, Typography } from "@mui/material";
import { getComfyUILifecycle, setComfyUILifecycle } from "../../api/settingsService";
import { useSnackbar } from "../common/SnackbarProvider";
import { useUiTranslation } from "../../i18n";

/**
 * The switch that tells Guaardvark the user runs ComfyUI themselves: it then
 * only talks to the ComfyUI at its address and never starts, stops, installs or
 * updates one. Persists to .env (GUAARDVARK_COMFYUI_EXTERNAL).
 *
 * @param {object} props
 * @param {string} [props.title] heading of the block
 * @param {(external: boolean) => void} [props.onChange] called after the switch is saved
 */
const ComfyUILifecycleSection = ({ title = "ComfyUI", onChange }) => {
  const { t } = useUiTranslation();
  const { showMessage } = useSnackbar();
  const [state, setState] = useState({ external: false, env_writable: true });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getComfyUILifecycle()
      .then((data) => {
        const payload = data?.data ?? data;
        if (!cancelled && payload && typeof payload === "object") setState((s) => ({ ...s, ...payload }));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const update = async (external) => {
    setSaving(true);
    try {
      const data = await setComfyUILifecycle({ external });
      const payload = data?.data ?? data;
      const saved = typeof payload?.external === "boolean" ? payload.external : external;
      setState((s) => ({ ...s, external: saved }));
      showMessage(t("settingsSections.comfyui.saved"), "success");
      onChange?.(saved);
    } catch (err) {
      showMessage(t("settingsSections.comfyui.saveFailed", { error: err.message || err }), "error");
    } finally {
      setSaving(false);
    }
  };

  const external = Boolean(state.external);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5, mt: 2 }} data-testid="comfyui-lifecycle">
      <Typography variant="subtitle2">{title}</Typography>
      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={external}
            disabled={saving || state.env_writable === false}
            onChange={(e) => update(e.target.checked)}
            inputProps={{ "data-testid": "comfyui-external" }}
          />
        }
        label={<Typography variant="body2">{t("settingsSections.comfyui.external")}</Typography>}
      />
      <Typography variant="caption" color="text.secondary">
        {t("settingsSections.comfyui.note")}
        {state.env_writable === false ? ` ${t("settingsSections.comfyui.envNotWritable")}` : ""}
      </Typography>
      {external && (
        <Typography variant="caption" color="text.secondary" data-testid="comfyui-external-models">
          {t("settingsSections.comfyui.models")}
        </Typography>
      )}
    </Box>
  );
};

export default ComfyUILifecycleSection;
