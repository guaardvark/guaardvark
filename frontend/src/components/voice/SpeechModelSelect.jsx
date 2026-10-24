// The Whisper model voice transcribes with, chosen on this machine. Used by
// Settings → Voice and the Voice page. Lists installed models only: choosing
// never downloads, and installs happen in Voice models.
import React, { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import { FormControl, InputLabel, MenuItem, Select } from "@mui/material";
import voiceService from "../../api/voiceService";
import { ActionButton, Cluster, Hint, Line } from "../settings/ui";
import { useUiTranslation } from "../../i18n";

/**
 * @param {{reloadKey?: any, onManageModels?: () => void, onChange?: (id: string) => void,
 *   showMessage?: (text: string, severity: string) => void}} props
 *   `reloadKey` re-reads the list when it changes (after an install, say);
 *   `onManageModels` adds a button that opens Voice models.
 */
const SpeechModelSelect = ({ reloadKey, onManageModels, onChange, showMessage }) => {
  const { t } = useUiTranslation();
  const [state, setState] = useState(null);
  // { detail, change }: worded while rendering, so it follows a language switch.
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await voiceService.getSpeechModel();
      setState(res?.data || null);
      setError(null);
    } catch (e) {
      setError({ detail: e?.message });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  const installed = state?.installed || [];
  const nameOf = (id) => installed.find((m) => m.id === id)?.name || id;

  const choose = async (modelId) => {
    if (!modelId) return;
    setSaving(true);
    try {
      const res = await voiceService.setSpeechModel(modelId);
      setState(res?.data || null);
      setError(null);
      showMessage?.(t("voice.speechModel.changed", { model: nameOf(modelId) }), "success");
      onChange?.(modelId);
    } catch (e) {
      const detail = String(e?.message || e);
      if (showMessage) showMessage(t("voice.speechModel.changeFailed", { error: detail }), "error");
      else setError({ detail, change: true });
    } finally {
      setSaving(false);
    }
  };

  // The select shows what transcription uses: the choice, or tiny.en while
  // a chosen model is missing.
  const value = installed.some((m) => m.id === state?.in_use) ? state.in_use : "";
  const fallingBack = Boolean(state && state.model !== state.in_use);
  const help = t("voice.speechModel.help");
  const errorText = !error
    ? null
    : error.change
      ? t("voice.speechModel.changeFailed", { error: error.detail })
      : error.detail || t("voice.speechModel.readFailed");

  return (
    <Cluster label={t("voice.speechModel.label")} help={help}>
      <Line>
        {installed.length > 0 ? (
          <FormControl size="small" className="grow">
            <InputLabel id="speech-model-label">{t("voice.speechModel.model")}</InputLabel>
            <Select
              labelId="speech-model-label"
              label={t("voice.speechModel.model")}
              value={value}
              onChange={(e) => choose(e.target.value)}
              disabled={saving}
            >
              {installed.map((m) => (
                <MenuItem key={m.id} value={m.id}>
                  {m.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        ) : (
          state && <Hint>{t("voice.speechModel.noneInstalled")}</Hint>
        )}
        {onManageModels && (
          <ActionButton onClick={onManageModels} tooltip={t("voice.speechModel.manageTooltip")}>
            {t("voice.speechModel.manage")}
          </ActionButton>
        )}
      </Line>
      {fallingBack && (
        <Hint>
          {t("voice.speechModel.fallback", { chosen: state.model_name || state.model, inUse: nameOf(state.in_use) })}
        </Hint>
      )}
      {error && <Hint sx={{ color: "error.main" }}>{errorText}</Hint>}
    </Cluster>
  );
};

SpeechModelSelect.propTypes = {
  reloadKey: PropTypes.any,
  onManageModels: PropTypes.func,
  onChange: PropTypes.func,
  showMessage: PropTypes.func,
};

export default SpeechModelSelect;
