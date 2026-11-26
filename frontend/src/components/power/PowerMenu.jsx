// The power button: shut Guaardvark down, restart it, or restart it fast
// (start.sh --fast: no package installs, no frontend build). Each choice is
// confirmed first, and the confirmation names a render that would be lost.

import React, { useState } from "react";
import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Tooltip,
  Typography,
} from "@mui/material";
import PowerSettingsNewIcon from "@mui/icons-material/PowerSettingsNew";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import BoltIcon from "@mui/icons-material/Bolt";
import { BASE_URL } from "../../api/apiClient";
import { announceRefusal, authRefusalCode, describeAuthRefusal } from "../../api/apiAuth";
import useJobsGate from "../../hooks/useJobsGate";
import { useUiTranslation } from "../../i18n";
import ConfirmActionDialog from "../settings/ui/ConfirmActionDialog";
import RebootProgressModal from "../modals/RebootProgressModal";

const ACTIONS = [
  { id: "shutdown", icon: <PowerSettingsNewIcon fontSize="small" /> },
  { id: "restart", icon: <RestartAltIcon fontSize="small" /> },
  { id: "fast", icon: <BoltIcon fontSize="small" /> },
];

/** The confirmation, mounted only while open so the GPU poll runs only then. */
const PowerConfirm = ({ action, busy, error, onConfirm, onClose }) => {
  const { t } = useUiTranslation();
  const { gpuBusy, blockReason } = useJobsGate({ submitMode: "queue" });
  const facts = [];
  if (gpuBusy) {
    facts.push({ label: t("layout.power.runningNow"), value: blockReason || t("layout.power.gpuJob") });
  }
  if (error) facts.push({ label: t("layout.power.failed"), value: error });
  return (
    <ConfirmActionDialog
      open
      onClose={onClose}
      onConfirm={onConfirm}
      title={t(`layout.power.${action}.title`)}
      description={t(`layout.power.${action}.description`)}
      facts={facts}
      keeps={t("layout.power.keeps")}
      confirmLabel={t(`layout.power.${action}.confirm`)}
      busy={busy}
    />
  );
};

/**
 * @param {function} [renderButton]  ({ onClick, label, open }) => element, for a
 *   bar that draws its own buttons; an icon button otherwise.
 */
const PowerMenu = ({ renderButton }) => {
  const { t } = useUiTranslation();
  const [anchor, setAnchor] = useState(null);
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [restartMode, setRestartMode] = useState(null);
  const [stopped, setStopped] = useState(null);

  const label = t("layout.power.button");
  const openMenu = (event) => setAnchor(event.currentTarget);
  const choose = (id) => {
    setAnchor(null);
    setError(null);
    setPending(id);
  };

  const shutDown = async () => {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${BASE_URL}/reboot/shutdown`, { method: "POST" });
      const body = await response.json().catch(() => null);
      if (!response.ok || !body?.success) {
        const code = response.status === 401 || response.status === 403 ? authRefusalCode(body) : null;
        if (code) {
          const rejected = Boolean(body.credential_rejected);
          announceRefusal({ code, rejected, url: response.url });
          setError(describeAuthRefusal(code, rejected));
        } else {
          setError(typeof body?.error === "string" ? body.error : `HTTP ${response.status}`);
        }
        return;
      }
      setPending(null);
      setStopped({ startCommand: body.start_command || "./start.sh" });
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  const confirm = () => {
    if (pending === "shutdown") {
      shutDown();
      return;
    }
    setRestartMode(pending);
    setPending(null);
  };

  return (
    <>
      {renderButton ? (
        renderButton({ onClick: openMenu, label, open: Boolean(anchor) })
      ) : (
        <Tooltip title={label}>
          <IconButton size="small" onClick={openMenu} aria-label={label} aria-haspopup="menu">
            <PowerSettingsNewIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {ACTIONS.map((action) => (
          <MenuItem key={action.id} onClick={() => choose(action.id)}>
            <ListItemIcon>{action.icon}</ListItemIcon>
            <ListItemText
              primary={t(`layout.power.${action.id}.label`)}
              secondary={t(`layout.power.${action.id}.hint`)}
            />
          </MenuItem>
        ))}
      </Menu>
      {pending && (
        <PowerConfirm
          action={pending}
          busy={busy}
          error={error}
          onConfirm={confirm}
          onClose={() => setPending(null)}
        />
      )}
      <RebootProgressModal
        open={Boolean(restartMode)}
        mode={restartMode || "restart"}
        onClose={() => setRestartMode(null)}
      />
      {/* Nothing answers after a shutdown, so this stays up with the way back. */}
      <Dialog open={Boolean(stopped)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1, fontSize: "1rem" }}>
          <PowerSettingsNewIcon fontSize="small" />
          {t("layout.power.stopped.title")}
        </DialogTitle>
        <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            {t("layout.power.stopped.description")}
          </Typography>
          <Box
            component="code"
            sx={{
              p: 1.25,
              borderRadius: "6px",
              border: 1,
              borderColor: "divider",
              fontSize: "0.8rem",
              wordBreak: "break-all",
              userSelect: "all",
            }}
          >
            {stopped?.startCommand}
          </Box>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            {t("layout.power.stopped.reload")}
          </Typography>
        </DialogContent>
      </Dialog>
    </>
  );
};

export default PowerMenu;
