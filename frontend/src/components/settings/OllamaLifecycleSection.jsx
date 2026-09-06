import React, { useEffect, useState } from "react";
import { Box, FormControlLabel, Switch, Typography } from "@mui/material";
import { getOllamaLifecycle, setOllamaLifecycle } from "../../api/settingsService";
import { useSnackbar } from "../common/SnackbarProvider";

/**
 * Two switches that decide whether the start/stop scripts touch Ollama.
 * Both persist to .env and apply on the next stop or start; nothing restarts.
 */
const OllamaLifecycleSection = () => {
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
      showMessage("Saved to .env; applies on the next stop or start", "success");
    } catch (err) {
      showMessage("Could not save: " + (err.message || err), "error");
    } finally {
      setSaving(false);
    }
  };

  const disabled = saving || state.env_writable === false;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5, mt: 2 }} data-testid="ollama-lifecycle">
      <Typography variant="subtitle2">Ollama</Typography>
      <FormControlLabel
        control={
          <Switch
            checked={Boolean(state.keep_running)}
            disabled={disabled || Boolean(state.external)}
            onChange={(e) => update({ keep_running: e.target.checked })}
            inputProps={{ "data-testid": "ollama-keep-running" }}
          />
        }
        label="Leave Ollama running when Guaardvark stops"
      />
      <FormControlLabel
        control={
          <Switch
            checked={Boolean(state.external)}
            disabled={disabled}
            onChange={(e) => update({ external: e.target.checked })}
            inputProps={{ "data-testid": "ollama-external" }}
          />
        }
        label="I run Ollama myself: never start or stop it"
      />
      <Typography variant="caption" color="text.secondary">
        By default stop.sh stops only the Ollama that start.sh launched. These write
        GUAARDVARK_OLLAMA_KEEP_RUNNING / GUAARDVARK_OLLAMA_EXTERNAL to .env.
        {state.env_writable === false ? " (.env is not writable by the server; set them by hand.)" : ""}
      </Typography>
    </Box>
  );
};

export default OllamaLifecycleSection;
