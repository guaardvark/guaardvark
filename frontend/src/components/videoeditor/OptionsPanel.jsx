// frontend/src/components/videoeditor/OptionsPanel.jsx
//
// The single, context-sensitive Options/Config panel (Shotcut-style). What it
// shows depends on the selection:
//   - nothing selected   → Project / Plan settings (scan mode + style recipe)
//   - video bin clip      → Director's Notes
//   - audio bin clip       → master-soundtrack toggle + volume
//   - image bin clip       → basic info (placeholder for now)
//   - text overlay         → text properties
// Replaces the old separate "Properties" + "Song & Controls" panels.
import React from "react";
import {
  Box, Stack, Typography, Chip, Slider, TextField, Button,
  FormControlLabel, Switch, Divider, Alert,
} from "@mui/material";
import { Delete as DeleteIcon, Star as StarIcon } from "@mui/icons-material";
import ScanModeSelector from "./ScanModeSelector";
import DirectorsNotesPanel from "./DirectorsNotesPanel";
import { useUiTranslation } from "../../i18n";

const OptionsPanel = ({
  selectedItem,
  selectedClip,
  selectedClipAnalysis,
  selectedText,
  // project / plan settings
  scanMode, setScanMode, styleRecipeName, setStyleRecipeName, recipes, planning,
  // video clip (director's notes)
  onClipOverride, onReanalyze, rescanning, clipHash,
  // audio clip
  onSetMasterSong, onSetVolume,
  // text overlay
  onUpdateText, onDeleteText,
  // errors
  error, planError,
}) => {
  const { t } = useUiTranslation();
  const isBin = selectedItem?.type === "bin";
  const kind = selectedClip?.kind || "video";

  // ── Audio clip: master-soundtrack toggle + volume ──────────────────
  if (isBin && kind === "audio") {
    return (
      <Stack spacing={2} sx={{ p: 1, height: "100%", overflow: "auto" }} className="non-draggable">
        <Typography variant="subtitle2" fontWeight="bold" noWrap title={selectedClip.filename}>
          {selectedClip.filename}
        </Typography>
        <FormControlLabel
          control={
            <Switch
              checked={!!selectedClip.isMasterSong}
              onChange={(e) => onSetMasterSong(selectedClip.clipId, e.target.checked)}
            />
          }
          label={
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <StarIcon sx={{ fontSize: 16, color: "warning.main" }} />
              <Typography variant="body2">{t("videoEditor.options.useAsMaster")}</Typography>
            </Stack>
          }
        />
        <Box>
          <Typography variant="caption" color="text.secondary">
            {t("videoEditor.options.volume", { percent: Math.round((selectedClip.volume ?? 1.0) * 100) })}
          </Typography>
          <Slider
            value={selectedClip.volume ?? 1.0}
            min={0}
            max={1}
            step={0.05}
            onChange={(_e, v) => onSetVolume(selectedClip.clipId, v)}
          />
        </Box>
        <Typography variant="caption" color="text.secondary">
          {t("videoEditor.options.masterHint")}
        </Typography>
      </Stack>
    );
  }

  // ── Video clip: Director's Notes ───────────────────────────────────
  if (isBin && kind === "video") {
    return (
      <Box sx={{ height: "100%", overflow: "auto" }}>
        <DirectorsNotesPanel
          clipAnalysis={selectedClipAnalysis}
          onOverride={onClipOverride}
          onReanalyze={onReanalyze}
          rescanning={rescanning}
          clipHash={clipHash}
        />
      </Box>
    );
  }

  // ── Image clip: minimal info (placeholder) ─────────────────────────
  if (isBin && kind === "image") {
    return (
      <Stack spacing={1} sx={{ p: 1 }} className="non-draggable">
        <Typography variant="subtitle2" fontWeight="bold" noWrap title={selectedClip.filename}>
          {selectedClip.filename}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {t("videoEditor.options.imageSoon")}
        </Typography>
      </Stack>
    );
  }

  // ── Text overlay: text properties ──────────────────────────────────
  if (selectedText) {
    return (
      <Stack spacing={2} sx={{ p: 1, height: "100%", overflow: "auto" }} className="non-draggable">
        <Typography variant="subtitle2" fontWeight="bold">{t("videoEditor.options.textProperties")}</Typography>
        <TextField
          size="small" fullWidth label={t("videoEditor.options.text")}
          value={selectedText.text}
          onChange={(e) => onUpdateText(selectedText.id, { text: e.target.value })}
        />
        <Box>
          <Typography variant="caption">{t("videoEditor.options.fontSize", { size: selectedText.fontSize })}</Typography>
          <Slider value={selectedText.fontSize} min={12} max={144}
            onChange={(_e, v) => onUpdateText(selectedText.id, { fontSize: v })} />
        </Box>
        <TextField
          size="small" fullWidth label={t("videoEditor.options.color")}
          value={selectedText.fontColor}
          onChange={(e) => onUpdateText(selectedText.id, { fontColor: e.target.value })}
        />
        <Box>
          <Typography variant="caption">{t("videoEditor.options.rotation", { rotation: selectedText.rotation })}</Typography>
          <Slider value={selectedText.rotation} min={-180} max={180}
            onChange={(_e, v) => onUpdateText(selectedText.id, { rotation: v })} />
        </Box>
        <Stack direction="row" spacing={1}>
          <TextField size="small" label={t("videoEditor.options.x")} type="number" value={selectedText.x}
            onChange={(e) => onUpdateText(selectedText.id, { x: Number(e.target.value) || 0 })} />
          <TextField size="small" label={t("videoEditor.options.y")} type="number" value={selectedText.y}
            onChange={(e) => onUpdateText(selectedText.id, { y: Number(e.target.value) || 0 })} />
        </Stack>
        <Button variant="outlined" color="error" size="small" startIcon={<DeleteIcon />}
          onClick={() => onDeleteText(selectedText.id)}>
          {t("videoEditor.options.deleteElement")}
        </Button>
      </Stack>
    );
  }

  // ── Nothing selected: Project / Plan settings ──────────────────────
  return (
    <Stack spacing={1.5} sx={{ p: 1, height: "100%", overflow: "auto" }} className="non-draggable">
      <Typography variant="subtitle2" fontWeight="bold">{t("videoEditor.options.planSettings")}</Typography>
      <ScanModeSelector value={scanMode} onChange={setScanMode} disabled={planning} />
      <Box>
        <Typography variant="caption" color="text.secondary">{t("videoEditor.options.styleRecipe")}</Typography>
        <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 0.5 }}>
          {(recipes.length === 0 ? [{ name: "Default" }] : recipes).map((r) => (
            <Chip
              key={r.name} label={r.name} size="small"
              color={styleRecipeName === r.name ? "primary" : "default"}
              onClick={() => setStyleRecipeName(r.name)}
              variant={styleRecipeName === r.name ? "filled" : "outlined"}
              sx={{ mb: 0.5 }}
            />
          ))}
        </Stack>
      </Box>
      <Divider />
      <Typography variant="caption" color="text.secondary">
        {t("videoEditor.options.selectHint")}
      </Typography>
      {error && <Alert severity="error" sx={{ py: 0 }}>{error}</Alert>}
      {planError && <Alert severity="error" sx={{ py: 0 }}>{planError}</Alert>}
    </Stack>
  );
};

export default OptionsPanel;
