// frontend/src/components/settings/IndexProfilesSection.jsx
// Index profiles: one corpus, several derived projections.
//
// The Document registry is the source of truth; each profile is a projection of
// it. Activating a profile does not copy documents, and clearing a projection
// does not delete any — that distinction is the whole point of the design, so
// the UI states it rather than leaving the operator to infer it.

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, CircularProgress, Switch, Tooltip, Typography,
} from "@mui/material";
import SettingsSection from "./SettingsSection";
import SettingsRow from "./SettingsRow";

const fmtBytes = (n) => {
  if (!n || n < 1024) return `${n || 0} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(1)} ${units[i]}`;
};

const IndexProfilesSection = () => {
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
      if (!res.ok) throw new Error(body?.error || "Failed to load profiles");
      setProfiles(body?.data?.profiles || body?.profiles || []);
    } catch (e) {
      setError(e.message || "Failed to load index profiles");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

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
      if (!res.ok) throw new Error((await res.json())?.error || "Failed to update");
      await load();
    } catch (e) {
      setError(e.message || "Failed to update profiles");
    } finally {
      setBusy("");
    }
  };

  const clearProjection = async (name) => {
    setBusy(name);
    setError("");
    try {
      const res = await fetch(`/api/settings/index_profiles/${encodeURIComponent(name)}/rebuild`, {
        method: "POST",
      });
      if (!res.ok) throw new Error((await res.json())?.error || "Failed to clear projection");
      await load();
    } catch (e) {
      setError(e.message || "Failed to clear projection");
    } finally {
      setBusy("");
    }
  };

  return (
    <SettingsSection title="Index Profiles">
      <Typography variant="body2" sx={{ color: "text.secondary", mb: 1.5 }}>
        Each profile is a separate view of the same documents, indexed its own way.
        Turning one on does not copy your documents, and clearing one does not delete
        any — only that profile&apos;s index is rebuilt.
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
      {loading && <CircularProgress size={20} />}

      {!loading && profiles.map((p) => {
        const proj = p.projection || {};
        const rows = proj.rows ?? 0;
        return (
          <SettingsRow key={p.name} label={p.name} stacked>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap" }}>
              <Tooltip title={p.active ? "Active — kept up to date" : "Inactive — not built or queried"}>
                <Switch
                  checked={!!p.active}
                  disabled={busy === p.name}
                  onChange={(e) => toggle(p.name, e.target.checked)}
                />
              </Tooltip>
              <Chip size="small" label={`top_k ${p.top_k}`} />
              <Chip size="small" label={`window ${p.context_window_chunks}`} />
              <Chip size="small" label={p.rerank ? "rerank on" : "rerank off"} />
              {proj.exists
                ? <Chip size="small" color="success" variant="outlined"
                        label={`${rows} vectors · ${fmtBytes(proj.size_bytes)}`} />
                : <Chip size="small" variant="outlined" label="not built" />}
              <Button
                size="small"
                disabled={busy === p.name || !proj.exists}
                onClick={() => clearProjection(p.name)}
              >
                Clear index
              </Button>
            </Box>
            {p.description && (
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                {p.description}
              </Typography>
            )}
          </SettingsRow>
        );
      })}
    </SettingsSection>
  );
};

export default IndexProfilesSection;
