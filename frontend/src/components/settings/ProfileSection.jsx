import React, { useEffect, useState } from "react";
import { Alert, FormControl, InputLabel, MenuItem, Select } from "@mui/material";
import { getProfile, setProfile as saveProfile } from "../../api/settingsService";
import { useAppStore } from "../../stores/useAppStore";
import { useSnackbar } from "../common/SnackbarProvider";
import { ActionButton, Cluster, Line } from "./ui";
import { useUiTranslation, Trans } from "../../i18n";

/**
 * Product Profile — one switch that sets the product shape.
 *
 * Switching writes GUAARDVARK_PROFILE to .env; the new shape applies after a
 * restart, because env flags are read once at boot. Nothing is removed by a
 * profile: hidden pages stay reachable by URL, and this card can always put
 * them back.
 */
const ProfileSection = () => {
  const { t } = useUiTranslation();
  const { showMessage } = useSnackbar();
  const activeProfile = useAppStore((s) => s.profile);
  const [info, setInfo] = useState(null);
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getProfile();
        if (cancelled) return;
        setInfo(data);
        setSelected(data?.configured || data?.active?.name || "workstation");
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const available = info?.available || [];
  const chosen = available.find((p) => p.name === selected);
  const activeName = info?.active?.name || activeProfile?.name;
  const changed = Boolean(info) && selected !== (info.configured || activeName);
  const locked = Boolean(info) && !info.env_writable;

  const handleApply = async () => {
    setSaving(true);
    try {
      await saveProfile(selected);
      setRestartNeeded(true);
      setInfo((prev) => (prev ? { ...prev, configured: selected } : prev));
      showMessage(t("settingsSections.profile.setTo", { profile: chosen?.label || selected }), "success");
    } catch (err) {
      showMessage(t("settingsSections.profile.setFailed", { error: err.message }), "error");
    } finally {
      setSaving(false);
    }
  };

  if (error) {
    return <Alert severity="warning">{t("settingsSections.profile.unavailable", { error })}</Alert>;
  }

  return (
    <Cluster
      label={t("settingsSections.profile.label")}
      help={
        <>
          {chosen?.description ? `${chosen.description} ` : ""}
          <Trans i18nKey="settingsSections.profile.help" components={{ code: <code /> }} />
        </>
      }
      note={locked ? t("settingsSections.profile.noteLocked") : t("settingsSections.profile.noteRestart")}
    >
      <Line nowrap>
        <FormControl size="small" className="grow" disabled={!info || saving || locked}>
          <InputLabel id="product-profile-label">{t("settingsSections.profile.profile")}</InputLabel>
          <Select
            labelId="product-profile-label"
            label={t("settingsSections.profile.profile")}
            value={available.some((p) => p.name === selected) ? selected : ""}
            onChange={(e) => setSelected(e.target.value)}
          >
            {available.map((p) => (
              <MenuItem key={p.name} value={p.name}>
                {p.label}
                {p.source === "extension" ? ` ${t("settingsSections.profile.extension")}` : ""}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        {/* Primary only while a change is pending; otherwise nothing to commit. */}
        <ActionButton
          kind={changed ? "primary" : "neutral"}
          onClick={handleApply}
          loading={saving}
          disabled={!changed || locked}
          tooltip={locked ? t("settingsSections.profile.lockedTooltip") : ""}
        >
          {t("settingsSections.profile.apply")}
        </ActionButton>
      </Line>
      {locked && (
        <Alert severity="info" sx={{ py: 0.25 }}>
          <Trans i18nKey="settingsSections.profile.locked" components={{ code: <code /> }} />
        </Alert>
      )}
      {info?.active?.fallback_reason && (
        <Alert severity="warning" sx={{ py: 0.25 }}>
          {info.active.fallback_reason}
        </Alert>
      )}
      {restartNeeded && (
        <Alert severity="warning" sx={{ py: 0.25 }}>
          {t("settingsSections.profile.restartNeeded", { name: activeName })}
        </Alert>
      )}
    </Cluster>
  );
};

export default ProfileSection;
