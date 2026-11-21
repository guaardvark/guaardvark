// frontend/src/components/jobs/ScheduleButton.jsx
//
// "Add to night shift" with "At a time…" behind its arrow: the choice offered
// next to a page's own start button. The page builds the job exactly as it
// would to start it now and hands it to onSchedule with { when, at }.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Menu,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import ArrowDropDownIcon from "@mui/icons-material/ArrowDropDown";
import NightlightOutlinedIcon from "@mui/icons-material/NightlightOutlined";
import ActionButton from "../settings/ui/ActionButton";
import { WHEN } from "../../api/nightShiftService";
import { useUiTranslation } from "../../i18n";

const pad = (n) => String(n).padStart(2, "0");

// A Date as the value a datetime-local field takes, in this machine's time.
export const toLocalInput = (date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;

// The next full hour, so the field opens on a time that is still ahead.
const nextHour = () => {
  const d = new Date();
  d.setHours(d.getHours() + 1, 0, 0, 0);
  return toLocalInput(d);
};

export const AtTimeDialog = ({ open, onClose, onPick, initial }) => {
  const { t } = useUiTranslation();
  const [value, setValue] = useState(initial || nextHour());
  React.useEffect(() => {
    if (open) setValue(initial || nextHour());
  }, [open, initial]);
  const picked = value ? new Date(value) : null;
  const inPast = !picked || Number.isNaN(picked.getTime()) || picked.getTime() <= Date.now();
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{t("jobs.nightShift.atTitle")}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ color: "text.secondary", mb: 2 }}>
          {t("jobs.nightShift.atIntro")}
        </Typography>
        <TextField
          type="datetime-local"
          size="small"
          fullWidth
          value={value}
          onChange={(e) => setValue(e.target.value)}
          slotProps={{ htmlInput: { "aria-label": t("jobs.nightShift.atField"), "data-testid": "schedule-at-input" } }}
          error={Boolean(value) && inPast}
          helperText={value && inPast ? t("jobs.nightShift.atPast") : " "}
        />
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <ActionButton kind="link" onClick={onClose}>
          {t("common.cancel")}
        </ActionButton>
        <ActionButton
          kind="primary"
          disabled={inPast}
          onClick={() => onPick(picked.toISOString())}
          data-testid="schedule-at-confirm"
        >
          {t("jobs.nightShift.atConfirm")}
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

AtTimeDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onPick: PropTypes.func.isRequired,
  initial: PropTypes.string,
};

const ScheduleButton = ({ onSchedule, disabled = false, tooltip = "" }) => {
  const { t } = useUiTranslation();
  const [anchor, setAnchor] = useState(null);
  const [atOpen, setAtOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const run = async (choice) => {
    setBusy(true);
    try {
      await onSchedule(choice);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box sx={{ display: "inline-flex", alignItems: "center", gap: "1px" }}>
      <ActionButton
        startIcon={<NightlightOutlinedIcon sx={{ fontSize: 15 }} />}
        onClick={() => run({ when: WHEN.NIGHT_SHIFT })}
        disabled={disabled}
        loading={busy}
        tooltip={tooltip || t("jobs.nightShift.addHelp")}
        sx={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
        data-testid="schedule-night-shift"
      >
        {t("jobs.nightShift.add")}
      </ActionButton>
      <ActionButton
        onClick={(e) => setAnchor(e.currentTarget)}
        disabled={disabled || busy}
        aria-label={t("jobs.nightShift.moreWays")}
        sx={{ px: 0, minWidth: 26, borderTopLeftRadius: 0, borderBottomLeftRadius: 0 }}
        data-testid="schedule-more"
      >
        <ArrowDropDownIcon sx={{ fontSize: 18 }} />
      </ActionButton>
      <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)}>
        <MenuItem
          onClick={() => {
            setAnchor(null);
            setAtOpen(true);
          }}
          data-testid="schedule-at"
        >
          {t("jobs.nightShift.atMenu")}
        </MenuItem>
      </Menu>
      <AtTimeDialog
        open={atOpen}
        onClose={() => setAtOpen(false)}
        onPick={(at) => {
          setAtOpen(false);
          run({ when: WHEN.AT, at });
        }}
      />
    </Box>
  );
};

ScheduleButton.propTypes = {
  onSchedule: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  tooltip: PropTypes.string,
};

export default ScheduleButton;
