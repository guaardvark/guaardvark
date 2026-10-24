// frontend/src/components/settings/IndexProfileEditDialog.jsx
// Edit the retrieval knobs of one index profile. top_k, the context window
// and reranking apply at query time, so a change costs nothing. The chunking
// and embedding settings are recorded at build time and are shown, not edited:
// changing them means rebuilding the profile.

import React, { useEffect, useState } from "react";
import {
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from "@mui/material";
import { ActionButton, Cluster, Hint, Line, SettingChip } from "./ui";
import { useUiTranslation } from "../../i18n";

const clampInt = (value, min, max, fallback) => {
  const n = parseInt(value, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.max(min, Math.min(max, n));
};

/**
 * @param {boolean}  open
 * @param {object}   profile   one entry from GET /api/settings/index_profiles
 * @param {function} onClose
 * @param {function} onSaved(message)
 */
const IndexProfileEditDialog = ({ open, profile, onClose, onSaved }) => {
  const { t } = useUiTranslation();
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open && profile) {
      setDraft({
        description: profile.description || "",
        top_k: profile.top_k ?? 5,
        context_window_chunks: profile.context_window_chunks ?? 3,
        rerank: profile.rerank !== false,
      });
      setError("");
    }
  }, [open, profile]);

  if (!profile || !draft) return null;

  const dirty =
    draft.description !== (profile.description || "") ||
    draft.top_k !== (profile.top_k ?? 5) ||
    draft.context_window_chunks !== (profile.context_window_chunks ?? 3) ||
    draft.rerank !== (profile.rerank !== false);

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      // Send the whole profile: the server rebuilds it from this dict and
      // fills any missing field with the shipped default.
      const { projection: _projection, ...stored } = profile;
      const body = {
        profile: {
          ...stored,
          description: draft.description,
          top_k: clampInt(draft.top_k, 1, 50, 5),
          context_window_chunks: clampInt(
            draft.context_window_chunks,
            0,
            20,
            3,
          ),
          rerank: Boolean(draft.rerank),
        },
      };
      const res = await fetch("/api/settings/index_profiles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || payload?.success === false)
        throw new Error(
          payload?.error || payload?.message || `HTTP ${res.status}`,
        );
      onSaved?.(
        t("settingsSections.indexProfileEdit.saved", { name: profile.name }),
      );
      onClose();
    } catch (e) {
      setError(e.message || t("settingsSections.indexProfileEdit.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={busy ? undefined : onClose}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle sx={{ fontSize: "1rem" }}>
        {t("settingsSections.indexProfileEdit.title", { name: profile.name })}
      </DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <TextField
          label={t("settingsSections.indexProfileEdit.description")}
          size="small"
          fullWidth
          value={draft.description}
          onChange={(e) =>
            setDraft((d) => ({ ...d, description: e.target.value }))
          }
        />
        <Cluster label={t("settingsSections.indexProfileEdit.retrieval")} note={t("settingsSections.indexProfileEdit.retrievalNote")}>
          <Line>
            <TextField
              label="top_k"
              type="number"
              size="small"
              className="grow"
              inputProps={{ min: 1, max: 50, step: 1 }}
              value={draft.top_k}
              onChange={(e) =>
                setDraft((d) => ({ ...d, top_k: e.target.value }))
              }
              helperText={t("settingsSections.indexProfileEdit.topKHelp")}
            />
            <TextField
              label={t("settingsSections.indexProfileEdit.contextWindow")}
              type="number"
              size="small"
              className="grow"
              inputProps={{ min: 0, max: 20, step: 1 }}
              value={draft.context_window_chunks}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  context_window_chunks: e.target.value,
                }))
              }
              helperText={t("settingsSections.indexProfileEdit.contextWindowHelp")}
            />
          </Line>
          <Line>
            <SettingChip
              label={t("settingsSections.indexProfileEdit.rerank")}
              on={draft.rerank}
              onToggle={(next) => setDraft((d) => ({ ...d, rerank: next }))}
              tooltip={t("settingsSections.indexProfileEdit.rerankTooltip")}
            />
          </Line>
        </Cluster>
        <Cluster
          label={t("settingsSections.indexProfileEdit.recorded")}
          note={t("settingsSections.indexProfileEdit.recordedNote")}
        >
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              columnGap: 2,
              rowGap: 0.5,
            }}
          >
            <Typography variant="caption" color="text.secondary">
              {t("settingsSections.indexProfileEdit.embeddingModel")}
            </Typography>
            <Typography variant="body2">
              {profile.embedding_model || t("settingsSections.indexProfileEdit.globalModel")}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {t("settingsSections.indexProfileEdit.vectorWidth")}
            </Typography>
            <Typography variant="body2">
              {profile.embed_dim ? `${profile.embed_dim}d` : t("settingsSections.indexProfileEdit.notBuiltYet")}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {t("settingsSections.indexProfileEdit.chunking")}
            </Typography>
            <Typography variant="body2">
              {profile.chunk_strategy || "auto"} ·{" "}
              {t("settingsSections.indexProfileEdit.chunkChars", { chars: profile.chunk_chars || 600 })}
            </Typography>
          </Box>
        </Cluster>
        {error && <Hint sx={{ color: "error.main" }}>{error}</Hint>}
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose} disabled={busy}>
          {t("settingsSections.indexProfileEdit.cancel")}
        </ActionButton>
        <ActionButton
          kind={dirty ? "primary" : "neutral"}
          onClick={save}
          loading={busy}
          disabled={!dirty}
        >
          {t("settingsSections.indexProfileEdit.save")}
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

export default IndexProfileEditDialog;
