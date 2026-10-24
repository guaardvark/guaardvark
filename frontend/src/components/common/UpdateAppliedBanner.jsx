import React, { useEffect, useState } from "react";
import { Box, Button, IconButton, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { useHealth } from "../../contexts/HealthContext";
import RebootProgressModal from "../modals/RebootProgressModal";
import { ConfirmActionDialog } from "../settings/ui";
import { useAppStore } from "../../stores/useAppStore";

const DISMISSED_KEY = "guaardvark:update-banner-dismissed";

const readDismissed = () => {
  try {
    return window.localStorage.getItem(DISMISSED_KEY);
  } catch {
    return null;
  }
};

const writeDismissed = (key) => {
  try {
    window.localStorage.setItem(DISMISSED_KEY, key);
  } catch {
    /* storage blocked: the dismissal lasts until the page reloads */
  }
};

/**
 * Which update notice the health payload calls for, if any.
 *
 * @param {object|null} health   GET /api/health body
 * @param {{bootId: string, updateTs: number|null}|null} baseline  what the first poll after page load saw
 * @returns {{kind: "restart"|"reload"|"restarted", key: string, message: string}|null}
 */
export function describeUpdate(health, baseline) {
  if (!health?.boot_id) return null;
  const update = health.update || null;

  if (health.gpu_fault) {
    const at = String(health.gpu_fault.at || "");
    const time = at.includes("T") ? at.split("T")[1].slice(0, 5) : "";
    return {
      kind: "restart",
      reason: "gpu_fault",
      key: `gpufault:${health.boot_id}:${at}`,
      message: `The GPU stopped responding${time ? ` at ${time}` : ""}. Image generation fails until Guaardvark restarts.`,
    };
  }

  if (health.restart_required) {
    return {
      kind: "restart",
      key: `restart:${health.boot_id}:${update?.ts ?? health.disk_version ?? ""}`,
      message: `Guaardvark was updated. Restart it to finish${
        health.restart_reason ? ` (${health.restart_reason})` : ""
      }.`,
    };
  }
  if (!baseline) return null;
  if (health.boot_id !== baseline.bootId) {
    return {
      kind: "restarted",
      key: `restarted:${health.boot_id}`,
      message: "Guaardvark restarted. Reload this page to use the new version.",
    };
  }
  // An update this page loaded after is already in it.
  if (update?.frontend_changed && update.ts !== baseline.updateTs) {
    const files = update.files ? ` (${update.files} file${update.files === 1 ? "" : "s"})` : "";
    return {
      kind: "reload",
      key: `reload:${health.boot_id}:${update.ts}`,
      message: `Guaardvark was updated${files}. Reload this page to use the new version.`,
    };
  }
  return null;
}

/**
 * Top-of-page notice after an Interconnector update: Reload when only the
 * page's own files changed or the backend restarted, Restart when the backend
 * is still running the old code. Dismissing hides that notice for this boot.
 */
const UpdateAppliedBanner = () => {
  const { healthData } = useHealth();
  const health = healthData?.backend || null;
  const [baseline, setBaseline] = useState(null);
  const [dismissed, setDismissed] = useState(readDismissed);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [rebootOpen, setRebootOpen] = useState(false);

  useEffect(() => {
    if (!baseline && health?.boot_id) {
      setBaseline({ bootId: health.boot_id, updateTs: health.update?.ts ?? null });
    }
  }, [baseline, health]);

  const notice = describeUpdate(health, baseline);
  const visible = Boolean(notice && notice.key !== dismissed);

  const setUpdateNoticeVisible = useAppStore((s) => s.setUpdateNoticeVisible);
  useEffect(() => {
    setUpdateNoticeVisible(visible);
  }, [visible, setUpdateNoticeVisible]);
  useEffect(() => () => setUpdateNoticeVisible(false), [setUpdateNoticeVisible]);

  return (
    <>
      {visible && (
        <Box
          role="status"
          sx={{
            // A floating notice above the footer bar: across the top it would
            // cover the navigation and its buttons for as long as it shows.
            position: "fixed",
            bottom: 36,
            left: "50%",
            transform: "translateX(-50%)",
            maxWidth: "calc(100% - 32px)",
            borderRadius: "8px",
            zIndex: (theme) => theme.zIndex.snackbar,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 1.5,
            py: 0.5,
            pl: 2,
            pr: 1,
            bgcolor: notice.kind === "restart" ? "warning.dark" : "info.dark",
            color: notice.kind === "restart" ? "warning.contrastText" : "info.contrastText",
            boxShadow: 3,
          }}
        >
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {notice.message}
          </Typography>
          {notice.kind === "restart" ? (
            <Button size="small" variant="outlined" color="inherit" onClick={() => setConfirmOpen(true)}>
              Restart
            </Button>
          ) : (
            <Button size="small" variant="outlined" color="inherit" onClick={() => window.location.reload()}>
              Reload
            </Button>
          )}
          <IconButton
            size="small"
            color="inherit"
            aria-label="Dismiss"
            onClick={() => {
              writeDismissed(notice.key);
              setDismissed(notice.key);
            }}
          >
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>
      )}
      <ConfirmActionDialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={() => {
          setConfirmOpen(false);
          setRebootOpen(true);
        }}
        title="Restart Guaardvark"
        description={`Restarts the backend, the workers and ComfyUI ${
          notice?.reason === "gpu_fault" ? "so the GPU can be used again" : "so the update takes effect"
        }. Every generation, index job and chat reply in flight is lost, and this page will disconnect until the services are back.`}
        keeps="documents, chats, media, rules and settings."
        confirmLabel="Restart now"
      />
      <RebootProgressModal open={rebootOpen} onClose={() => setRebootOpen(false)} />
    </>
  );
};

export default UpdateAppliedBanner;
