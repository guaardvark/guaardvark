// The voice a Cast member speaks with in Film Crew renders, picked from Audio
// Foundry's voice catalog instead of typed, so a typo cannot become a voice id
// that renders silently drop.
//
// A stored id that is not in the catalog (typed before this picker, or set by
// the Casting Director) is shown as it is and marked invalid until the person
// picks a voice; the picker never changes it on its own. While the catalog
// cannot be loaded, nothing is claimed about the stored id.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Alert, Box, Button, ListSubheader, MenuItem, TextField, Typography } from "@mui/material";

import { getVoiceCatalog } from "../../api/audioFoundryService";
import { catalogGroups, indexVoices, voiceStatus } from "../../utils/voiceCatalog";
import { StatusPill } from "../settings/ui";
import { useUiTranslation, translate } from "../../i18n";

export const defaultVoiceLabel = () => translate("filmCrew.voicePicker.defaultVoice");
// Audio Studio opens Manage models on the Kokoro row from this link.
export const MANAGE_KOKORO_PATH = "/audio?models=kokoro";

const plainLabel = (label) => String(label || "").replace(/\s*\(default\)\s*$/i, "");

const Caption = ({ children, color = "text.secondary" }) => (
  <Typography component="span" variant="caption" sx={{ ml: 1, color }}>
    {children}
  </Typography>
);

Caption.propTypes = { children: PropTypes.node, color: PropTypes.string };

const CastVoicePicker = ({ value, onChange, disabled = false }) => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState(null);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getVoiceCatalog()
      .then((data) => {
        if (cancelled) return;
        if (catalogGroups(data)) setCatalog(data);
        else setLoadFailed(true);
      })
      .catch(() => {
        if (!cancelled) setLoadFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const groups = catalogGroups(catalog);
  const index = useMemo(() => indexVoices(groups), [groups]);
  const status = voiceStatus(value, index);
  // The select shows the stored id itself when the catalog does not list it.
  const offCatalog = status.state === "invalid" || status.state === "unknown";
  const selectValue = status.id;

  const autoFallback = plainLabel(index?.get(catalog?.kokoro?.default)?.label) || t("filmCrew.voicePicker.defaultVoiceLower");
  const defaultHint = t("filmCrew.voicePicker.defaultHint", { voice: autoFallback });

  const renderValue = (selected) => {
    if (!selected) return defaultVoiceLabel();
    const voice = index?.get(selected);
    if (voice) return `${voice.label} (${voice.id})${voice.installed === false ? ` · ${t("filmCrew.voicePicker.notInstalledLower")}` : ""}`;
    return status.state === "invalid" ? t("filmCrew.voicePicker.notFoundryVoice", { id: selected }) : selected;
  };

  let helperText = "";
  if (status.state === "default") helperText = defaultHint;
  else if (status.state === "installed") helperText = t("filmCrew.voicePicker.installedHint", { group: status.voice.group });

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <TextField
        select
        fullWidth
        label={t("filmCrew.voicePicker.label")}
        value={selectValue}
        disabled={disabled}
        error={status.state === "invalid"}
        onChange={(e) => onChange(e.target.value)}
        helperText={helperText}
        InputLabelProps={{ shrink: true }}
        SelectProps={{
          displayEmpty: true,
          renderValue,
          MenuProps: { PaperProps: { sx: { maxHeight: 420 } } },
        }}
      >
        <MenuItem value="">
          {defaultVoiceLabel()}
          <Caption>{t("filmCrew.voicePicker.foundryChooses")}</Caption>
        </MenuItem>
        {offCatalog && (
          <MenuItem value={status.id}>
            {status.id}
            {status.state === "invalid" ? (
              <Caption color="error.main">{t("filmCrew.voicePicker.savedInvalid")}</Caption>
            ) : (
              <Caption>{t("filmCrew.voicePicker.saved")}</Caption>
            )}
          </MenuItem>
        )}
        {(groups || []).flatMap((group) => [
          <ListSubheader key={`group-${group.label}`}>{group.label}</ListSubheader>,
          ...(group.voices || []).map((voice) => (
            <MenuItem key={voice.id} value={voice.id}>
              <Box component="span" sx={{ display: "flex", alignItems: "center", width: "100%", gap: 1 }}>
                <span>{voice.label}</span>
                <Caption>{voice.id}</Caption>
                {voice.installed === false && (
                  <StatusPill label={t("filmCrew.voicePicker.notInstalled")} tone="warn" sx={{ ml: "auto" }} />
                )}
              </Box>
            </MenuItem>
          )),
        ])}
      </TextField>

      {status.state === "invalid" && (
        <Alert severity="error">
          {t("filmCrew.voicePicker.invalidAlert", { id: status.id })}
        </Alert>
      )}
      {status.state === "not_installed" && (
        <Alert
          severity="warning"
          action={
            <Button color="inherit" size="small" sx={{ whiteSpace: "nowrap" }} onClick={() => navigate(MANAGE_KOKORO_PATH)}>
              {t("filmCrew.voicePicker.manageModels")}
            </Button>
          }
        >
          {t("filmCrew.voicePicker.notInstalledAlert", { voice: status.voice.label })}
        </Alert>
      )}
      {loadFailed && (
        <Alert severity="info">
          {t("filmCrew.voicePicker.loadFailed")}
        </Alert>
      )}
    </Box>
  );
};

CastVoicePicker.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

export default CastVoicePicker;
