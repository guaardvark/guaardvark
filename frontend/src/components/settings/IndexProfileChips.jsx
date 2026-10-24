// frontend/src/components/settings/IndexProfileChips.jsx
// Index profiles as state chips: one corpus, several derived projections.
// Activating a profile marks it for future indexing and querying; nothing is
// built on the spot and no document is copied. Clearing a projection lives in
// the danger zone (RebuildIndexDialog), not here.

import React, { useCallback, useEffect, useState } from "react";
import { CircularProgress } from "@mui/material";
import { Hint, Line, SettingChip } from "./ui";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

export const fmtBytes = (n) => {
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

export const describeProjection = (p) => {
  const proj = p?.projection || {};
  if (!proj.exists) return translate("settingsSections.indexProfiles.notBuilt");
  return translate("settingsSections.indexProfiles.projection", {
    rows: (proj.rows ?? 0).toLocaleString(uiLocale()),
    size: fmtBytes(proj.size_bytes),
  });
};

/**
 * @param {function} [onLoaded]   receives the profile list on every load
 * @param {any}      [reloadKey]  change it to force a reload (after a rebuild)
 * @param {function} [showMessage]
 * @param {function} [onEdit]      receives the profile; the chip shows a gear
 */
const IndexProfileChips = ({ onLoaded, reloadKey, showMessage, onEdit }) => {
  const { t } = useUiTranslation();
  const [profiles, setProfiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/settings/index_profiles");
      const body = await res.json();
      if (!res.ok) throw new Error(body?.error || t("settingsSections.indexProfiles.loadFailed"));
      const list = body?.data?.profiles || body?.profiles || [];
      setProfiles(list);
      onLoaded?.(list);
    } catch (e) {
      setError(e.message || t("settingsSections.indexProfiles.loadIndexFailed"));
    } finally {
      setLoading(false);
    }
  }, [onLoaded]);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  const toggle = async (name, next) => {
    const active = profiles.filter((p) => (p.name === name ? next : p.active)).map((p) => p.name);
    setBusy(name);
    setError("");
    try {
      const res = await fetch("/api/settings/index_profiles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active }),
      });
      if (!res.ok) throw new Error((await res.json())?.error || t("settingsSections.indexProfiles.updateFailed"));
      await load();
      showMessage?.(
        next
          ? t("settingsSections.indexProfiles.activated", { name })
          : t("settingsSections.indexProfiles.deactivated", { name }),
        "info",
      );
    } catch (e) {
      setError(e.message || t("settingsSections.indexProfiles.updateProfilesFailed"));
    } finally {
      setBusy("");
    }
  };

  if (loading && profiles.length === 0) return <CircularProgress size={16} />;

  return (
    <Line>
      {profiles.map((p) => (
        <SettingChip
          key={p.name}
          label={p.name}
          on={!!p.active}
          disabled={busy === p.name}
          note={describeProjection(p)}
          tooltip={
            (p.description ? `${p.description} ` : "") +
            t("settingsSections.indexProfiles.settingsSummary", {
              topK: p.top_k,
              window: p.context_window_chunks,
              rerank: p.rerank
                ? t("settingsSections.indexProfiles.rerankOn")
                : t("settingsSections.indexProfiles.rerankOff"),
            }) +
            " " +
            (p.active ? t("settingsSections.indexProfiles.activeNote") : t("settingsSections.indexProfiles.inactiveNote"))
          }
          onToggle={(next) => toggle(p.name, next)}
          onSettings={onEdit ? () => onEdit(p) : undefined}
        />
      ))}
      {error && <Hint sx={{ color: "error.main" }}>{error}</Hint>}
    </Line>
  );
};

export default IndexProfileChips;
