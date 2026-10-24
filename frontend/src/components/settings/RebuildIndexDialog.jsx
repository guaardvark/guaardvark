// frontend/src/components/settings/RebuildIndexDialog.jsx
// One door to "throw away index data", with a scope. Replaces the separate
// Purge, Reset and per-profile Clear buttons, which reached three endpoints
// with three different levels of confirmation.
//
//   one profile   POST /api/settings/index_profiles/<name>/rebuild  (drops that projection)
//   everything    POST /api/meta/reset-index                        (index files + every vector table)
//   by document   hands over to PurgeIndexModal (type and date filters)

import React, { useEffect, useState } from "react";
import {
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Typography,
} from "@mui/material";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { ActionButton } from "./ui";
import * as apiService from "../../api/settingsService";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const fmtBytes = (n) => {
  if (!n || n < 1024) return translate("settingsSections.units.b", { size: n || 0 });
  const units = [
    (size) => translate("settingsSections.units.kb", { size }),
    (size) => translate("settingsSections.units.mb", { size }),
    (size) => translate("settingsSections.units.gb", { size }),
  ];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return units[i](v.toFixed(1));
};

const describeProjection = (p) => {
  const proj = p?.projection || {};
  if (!proj.exists) return translate("settingsSections.indexProfiles.notBuilt");
  return translate("settingsSections.indexProfiles.projection", {
    rows: (proj.rows ?? 0).toLocaleString(uiLocale()),
    size: fmtBytes(proj.size_bytes),
  });
};

/**
 * @param {boolean}  open
 * @param {Array}    profiles   from GET /api/settings/index_profiles
 * @param {function} onClose
 * @param {function} onOpenPurge   caller opens PurgeIndexModal for the by-document scope
 * @param {function} onDone(message, severity)
 */
const RebuildIndexDialog = ({ open, profiles = [], onClose, onOpenPurge, onDone }) => {
  const { t } = useUiTranslation();
  const [scope, setScope] = useState("profile");
  const [profile, setProfile] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open && !profile && profiles.length) {
      setProfile((profiles.find((p) => p.active) || profiles[0]).name);
    }
  }, [open, profiles, profile]);

  const chosen = profiles.find((p) => p.name === profile);

  const run = async () => {
    if (scope === "documents") {
      onClose();
      onOpenPurge?.();
      return;
    }
    setBusy(true);
    try {
      if (scope === "profile") {
        const res = await fetch(`/api/settings/index_profiles/${encodeURIComponent(profile)}/rebuild`, {
          method: "POST",
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok || body?.success === false) throw new Error(body?.error || body?.message || `HTTP ${res.status}`);
        onDone?.(t("settingsSections.rebuildIndex.clearedProfile", { profile }), "success");
      } else {
        const result = await apiService.resetIndexStorage();
        if (result?.error) throw new Error(result.error.message || result.error);
        onDone?.(result?.message || t("settingsSections.rebuildIndex.clearedAll"), "success");
      }
      onClose();
    } catch (err) {
      onDone?.(t("settingsSections.rebuildIndex.failed", { error: err.message }), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1, color: "error.main", fontSize: "1rem" }}>
        <WarningAmberIcon fontSize="small" />
        {t("settingsSections.rebuildIndex.title")}
      </DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Typography variant="body2" color="text.secondary">
          {t("settingsSections.rebuildIndex.intro")}
        </Typography>
        <RadioGroup value={scope} onChange={(e) => setScope(e.target.value)}>
          <FormControlLabel
            value="profile"
            control={<Radio size="small" />}
            label={
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                <span>{t("settingsSections.rebuildIndex.oneProfile")}</span>
                <FormControl size="small" sx={{ minWidth: 200 }} disabled={scope !== "profile"}>
                  <Select value={profile} onChange={(e) => setProfile(e.target.value)} displayEmpty>
                    {profiles.map((p) => (
                      <MenuItem key={p.name} value={p.name}>
                        {p.name} · {describeProjection(p)}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Box>
            }
          />
          <FormControlLabel
            value="all"
            control={<Radio size="small" />}
            label={t("settingsSections.rebuildIndex.everything")}
          />
          <FormControlLabel
            value="documents"
            control={<Radio size="small" />}
            label={t("settingsSections.rebuildIndex.someDocuments")}
          />
        </RadioGroup>
        {scope === "profile" && chosen && !chosen.projection?.exists && (
          <Typography variant="caption" color="text.secondary">
            {t("settingsSections.rebuildIndex.nothingBuilt")}
          </Typography>
        )}
        {scope !== "documents" && (
          <Typography variant="caption" sx={{ color: "error.main" }}>
            {t("settingsSections.rebuildIndex.cannotUndo")}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose} disabled={busy}>
          {t("settingsSections.rebuildIndex.cancel")}
        </ActionButton>
        <ActionButton
          kind={scope === "documents" ? "neutral" : "destructive"}
          onClick={run}
          loading={busy}
          disabled={scope === "profile" && (!chosen || !chosen.projection?.exists)}
        >
          {scope === "documents"
            ? t("settingsSections.rebuildIndex.chooseDocuments")
            : scope === "profile"
              ? t("settingsSections.rebuildIndex.clearIndex")
              : t("settingsSections.rebuildIndex.clearEverything")}
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

export default RebuildIndexDialog;
