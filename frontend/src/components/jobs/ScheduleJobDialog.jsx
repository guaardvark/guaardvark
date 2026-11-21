// frontend/src/components/jobs/ScheduleJobDialog.jsx
//
// The Jobs page's "Schedule a job": pick what to make, describe it in one
// prompt, and say when. The features come from /api/night-shift/kinds, so a
// feature the backend can schedule shows here without a change to this file.
// Work that needs more than a prompt (a batch with its model and size, a
// training run) is scheduled on its own page; those are linked at the bottom.
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ActionButton from "../settings/ui/ActionButton";
import ChoiceChips from "../settings/ui/ChoiceChips";
import { AtTimeDialog } from "./ScheduleButton";
import { getScheduleKinds, scheduleFeature, WHEN } from "../../api/nightShiftService";
import { uiLocale, useUiTranslation } from "../../i18n";

// Features whose prompt makes one item each, so "how many" has a meaning.
const COUNTED = new Set(["image", "video", "infographic", "music"]);
const MAX_COUNT = 20;

const ScheduleJobDialog = ({ open, onClose, onScheduled }) => {
  const { t } = useUiTranslation();
  const [kinds, setKinds] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [feature, setFeature] = useState("");
  const [prompt, setPrompt] = useState("");
  const [count, setCount] = useState(1);
  const [choice, setChoice] = useState({ when: WHEN.NIGHT_SHIFT });
  const [atOpen, setAtOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    setError(null);
    setLoadError(null);
    getScheduleKinds()
      .then((list) => {
        if (!live) return;
        setKinds(list);
        setFeature((current) => current || list.find((k) => !k.page_only)?.id || "");
      })
      .catch((e) => live && setLoadError(e?.message || String(e)));
    return () => {
      live = false;
    };
  }, [open]);

  const features = (kinds || []).filter((k) => !k.page_only);
  const pages = (kinds || []).filter((k) => k.page_only);
  const picked = features.find((k) => k.id === feature);
  const counted = COUNTED.has(feature);
  const ready = Boolean(picked) && prompt.trim().length > 0 && !busy;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const saved = await scheduleFeature(
        { feature, prompt: prompt.trim(), count: counted ? Math.max(1, Math.min(Number(count) || 1, MAX_COUNT)) : 1 },
        choice,
      );
      setPrompt("");
      setCount(1);
      onScheduled?.(saved);
      onClose();
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth data-testid="schedule-job-dialog">
      <DialogTitle>{t("jobs.schedule.title")}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t("jobs.schedule.intro")}
        </Typography>
        {loadError && <Alert severity="error" sx={{ mb: 2 }}>{t("jobs.schedule.loadFailed", { error: loadError })}</Alert>}
        <Stack spacing={2}>
          <TextField
            select
            size="small"
            label={t("jobs.schedule.feature")}
            value={picked ? feature : ""}
            onChange={(e) => setFeature(e.target.value)}
            disabled={!features.length}
            slotProps={{ htmlInput: { "data-testid": "schedule-feature" } }}
          >
            {features.map((k) => (
              <MenuItem key={k.id} value={k.id} data-testid={`schedule-feature-${k.id}`}>
                {k.label}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            multiline
            minRows={3}
            maxRows={10}
            label={t("jobs.schedule.prompt")}
            placeholder={picked?.hint || ""}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            slotProps={{ htmlInput: { "data-testid": "schedule-prompt" } }}
          />
          {counted && (
            <TextField
              size="small"
              type="number"
              label={t("jobs.schedule.count")}
              helperText={t("jobs.schedule.countHelp")}
              value={count}
              onChange={(e) => setCount(e.target.value)}
              slotProps={{ htmlInput: { min: 1, max: MAX_COUNT, "data-testid": "schedule-count" } }}
              sx={{ maxWidth: 220 }}
            />
          )}
          <Box data-testid="schedule-when">
            <ChoiceChips
              ariaLabel={t("jobs.nightShift.whenLabel")}
              options={[
                { value: WHEN.NIGHT_SHIFT, label: t("jobs.nightShift.when.night"), tooltip: t("jobs.nightShift.addHelp") },
                {
                  value: WHEN.AT,
                  label: choice.when === WHEN.AT ? new Date(choice.at).toLocaleString(uiLocale()) : t("jobs.nightShift.atMenu"),
                },
              ]}
              value={choice.when}
              onChange={(next) => (next === WHEN.AT ? setAtOpen(true) : setChoice({ when: next }))}
            />
          </Box>
          {picked?.page && (
            <Typography variant="caption" color="text.secondary">
              {t("jobs.schedule.defaults")}{" "}
              <Link component={RouterLink} to={picked.page} onClick={onClose}>
                {t("jobs.schedule.openPage", { page: picked.label })}
              </Link>
            </Typography>
          )}
          {error && <Alert severity="error">{t("jobs.nightShift.addFailed", { error })}</Alert>}
          {pages.length > 0 && (
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
                {t("jobs.schedule.onPages")}
              </Typography>
              <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1.5}>
                {pages.map((k) => (
                  <Link key={k.id} component={RouterLink} to={k.page} onClick={onClose} variant="caption">
                    {k.label}
                  </Link>
                ))}
              </Stack>
            </Box>
          )}
        </Stack>
        <AtTimeDialog
          open={atOpen}
          onClose={() => setAtOpen(false)}
          onPick={(at) => {
            setAtOpen(false);
            setChoice({ when: WHEN.AT, at });
          }}
        />
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <ActionButton kind="link" onClick={onClose} disabled={busy}>
          {t("common.cancel")}
        </ActionButton>
        <ActionButton kind="primary" onClick={submit} disabled={!ready} loading={busy} data-testid="schedule-job-confirm">
          {t("jobs.nightShift.atConfirm")}
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

ScheduleJobDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onScheduled: PropTypes.func,
};

export default ScheduleJobDialog;
