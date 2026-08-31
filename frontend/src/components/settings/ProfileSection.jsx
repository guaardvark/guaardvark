import React, { useEffect, useState } from "react";
import {
  Box,
  Button,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Typography,
  Alert,
} from "@mui/material";
import { getProfile, setProfile as saveProfile } from "../../api/settingsService";
import { useAppStore } from "../../stores/useAppStore";
import { useSnackbar } from "../common/SnackbarProvider";
import SettingsRow from "./SettingsRow";

/**
 * Product Profile — one switch that sets the product shape.
 *
 * Switching writes GUAARDVARK_PROFILE to .env; the new shape applies after a
 * restart, because env flags are read once at boot. Nothing is removed by a
 * profile: hidden pages stay reachable by URL, and this card can always put
 * them back.
 */
const ProfileSection = () => {
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

  const handleApply = async () => {
    setSaving(true);
    try {
      await saveProfile(selected);
      setRestartNeeded(true);
      setInfo((prev) => (prev ? { ...prev, configured: selected } : prev));
      showMessage(`Profile set to ${chosen?.label || selected}. Restart to apply.`, "success");
    } catch (err) {
      showMessage(`Failed to set profile: ${err.message}`, "error");
    } finally {
      setSaving(false);
    }
  };

  if (error) {
    return <Alert severity="warning">Profiles unavailable: {error}</Alert>;
  }

  return (
    <>
      <SettingsRow label="Profile">
        <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
          <FormControl size="small" sx={{ minWidth: 220 }}>
            <InputLabel id="product-profile-label">Profile</InputLabel>
            <Select
              labelId="product-profile-label"
              label="Profile"
              value={available.some((p) => p.name === selected) ? selected : ""}
              onChange={(e) => setSelected(e.target.value)}
              disabled={!info || saving || !info.env_writable}
            >
              {available.map((p) => (
                <MenuItem key={p.name} value={p.name}>
                  {p.label}
                  {p.source === "extension" ? " (extension)" : ""}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <Button
            variant="contained"
            size="small"
            onClick={handleApply}
            disabled={!changed || saving || !info?.env_writable}
          >
            {saving ? "Saving..." : "Apply"}
          </Button>
        </Box>
      </SettingsRow>
      {chosen?.description && (
        <Typography variant="body2" color="text.secondary" sx={{ px: 1.5, pb: 1 }}>
          {chosen.description}
        </Typography>
      )}
      {info && !info.env_writable && (
        <Alert severity="info" sx={{ mx: 1.5, mb: 1 }}>
          The profile is set by <code>GUAARDVARK_PROFILE</code> in <code>.env</code>, which this
          server cannot write. Edit the file or start with <code>./start.sh --profile NAME</code>.
        </Alert>
      )}
      {info?.active?.fallback_reason && (
        <Alert severity="warning" sx={{ mx: 1.5, mb: 1 }}>
          {info.active.fallback_reason}
        </Alert>
      )}
      {restartNeeded && (
        <Alert severity="warning" sx={{ mx: 1.5, mb: 1 }}>
          Restart Guaardvark to apply the new profile. Running now: {activeName}.
        </Alert>
      )}
      <Typography variant="caption" color="text.secondary" sx={{ px: 1.5, pb: 1, display: "block" }}>
        A profile decides what is listed and what is on by default. Nothing is removed — every page
        stays reachable by its address, and explicit settings in <code>.env</code> always win.
      </Typography>
    </>
  );
};

export default ProfileSection;
