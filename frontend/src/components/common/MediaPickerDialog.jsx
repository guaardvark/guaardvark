// frontend/src/components/common/MediaPickerDialog.jsx
//
// Pick one or more images, videos or audio files from the library (Files),
// or upload new ones from disk into it. Uploads land in the Files folder for
// their kind (Images, Videos, Audio) as Documents, so whatever is picked here
// is a Document either way and is handed back as one.
//
//   <MediaPickerDialog open kind="video" max={2} onPick={(docs) => ...} onClose={...} />

import React, { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import {
  Box,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Grid,
  TextField,
  Typography,
} from "@mui/material";
import { ActionButton, Hint } from "../settings/ui";
import MediaTile from "../videoeditor/MediaTile";
import { uploadDroppedMedia } from "../videoeditor/useExternalDrop";
import { listAudioDocuments, listImageDocuments, listVideoDocuments } from "../../api/videoOverlayService";
import { translate, useUiTranslation } from "../../i18n";

const LISTERS = { image: listImageDocuments, video: listVideoDocuments, audio: listAudioDocuments };
const ACCEPT = { image: "image/*", video: "video/*", audio: "audio/*" };

/** The dialog's sentences for one kind of media, each written whole. */
const kindText = (kind) => {
  if (kind === "video") {
    return {
      choose: translate("layoutCommon.mediaPicker.kinds.video.choose"),
      onlyHere: translate("layoutCommon.mediaPicker.kinds.video.onlyHere"),
      empty: translate("layoutCommon.mediaPicker.kinds.video.empty"),
      emptyFiltered: translate("layoutCommon.mediaPicker.kinds.video.emptyFiltered"),
    };
  }
  if (kind === "audio") {
    return {
      choose: translate("layoutCommon.mediaPicker.kinds.audio.choose"),
      onlyHere: translate("layoutCommon.mediaPicker.kinds.audio.onlyHere"),
      empty: translate("layoutCommon.mediaPicker.kinds.audio.empty"),
      emptyFiltered: translate("layoutCommon.mediaPicker.kinds.audio.emptyFiltered"),
    };
  }
  return {
    choose: translate("layoutCommon.mediaPicker.kinds.image.choose"),
    onlyHere: translate("layoutCommon.mediaPicker.kinds.image.onlyHere"),
    empty: translate("layoutCommon.mediaPicker.kinds.image.empty"),
    emptyFiltered: translate("layoutCommon.mediaPicker.kinds.image.emptyFiltered"),
  };
};

const MediaPickerDialog = ({ open, kind = "image", max = 1, title, onPick, onClose }) => {
  const { t } = useUiTranslation();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState([]);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    setPicked([]);
    setFilter("");
    setError("");
    setLoading(true);
    (LISTERS[kind] || listImageDocuments)()
      .then((docs) => { if (alive) setItems(docs || []); })
      .catch((e) => { if (alive) setError(e.message || t("layoutCommon.mediaPicker.listFailed")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [open, kind]);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? items.filter((d) => (d.filename || "").toLowerCase().includes(q)) : items;
  }, [items, filter]);

  const toggle = (doc) => {
    setPicked((prev) => {
      if (prev.some((d) => d.id === doc.id)) return prev.filter((d) => d.id !== doc.id);
      if (max === 1) return [doc];
      return prev.length >= max ? prev : [...prev, doc];
    });
  };

  const handleUpload = async (files) => {
    const list = Array.from(files || []).slice(0, Math.max(1, max));
    if (!list.length) return;
    setUploading(true);
    setError("");
    try {
      const docs = await uploadDroppedMedia(list, { http: axios });
      const ofKind = docs.filter((d) => d.kind === kind);
      if (ofKind.length < docs.length) setError(kindText(kind).onlyHere);
      if (ofKind.length) {
        onPick?.(ofKind);
        onClose?.();
      }
    } catch (e) {
      setError(e.response?.data?.error?.message || e.message || t("layoutCommon.mediaPicker.uploadFailed"));
    } finally {
      setUploading(false);
    }
  };

  const pickedIds = new Set(picked.map((d) => d.id));
  const text = kindText(kind);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{title || text.choose}</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center", mb: 1.5, flexWrap: "wrap" }}>
          <TextField
            size="small"
            placeholder={t("layoutCommon.mediaPicker.filterPlaceholder")}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            sx={{ flex: 1, minWidth: 180 }}
          />
          <ActionButton loading={uploading} onClick={() => inputRef.current?.click()}>
            {t("layoutCommon.mediaPicker.uploadFromDisk")}
          </ActionButton>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT[kind]}
            multiple={max > 1}
            hidden
            onChange={(e) => {
              handleUpload(e.target.files);
              e.target.value = "";
            }}
          />
        </Box>
        {error && (
          <Typography variant="caption" color="error" sx={{ display: "block", mb: 1 }}>
            {error}
          </Typography>
        )}
        {loading ? (
          <CircularProgress size={24} />
        ) : shown.length === 0 ? (
          <Hint>
            {filter ? text.emptyFiltered : text.empty}
          </Hint>
        ) : (
          <Grid container spacing={1}>
            {shown.map((doc) => (
              <Grid item xs={6} sm={4} md={3} key={doc.id}>
                <MediaTile item={doc} kind={kind} variant="grid" selected={pickedIds.has(doc.id)} onClick={() => toggle(doc)} />
              </Grid>
            ))}
          </Grid>
        )}
      </DialogContent>
      <DialogActions>
        <Hint sx={{ mr: "auto", pl: 1 }}>
          {max > 1 ? t("layoutCommon.mediaPicker.upToMore", { count: max }) : t("layoutCommon.mediaPicker.pickOne")}
        </Hint>
        <ActionButton onClick={onClose}>{t("common.cancel")}</ActionButton>
        <ActionButton
          kind={picked.length ? "primary" : "neutral"}
          disabled={!picked.length}
          onClick={() => {
            onPick?.(picked);
            onClose?.();
          }}
        >
          {picked.length > 1 ? t("layoutCommon.mediaPicker.useCount", { count: picked.length }) : t("layoutCommon.mediaPicker.use")}
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

export default MediaPickerDialog;
