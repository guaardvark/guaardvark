// The image encoder behind the Cast identity check (Settings > Generation):
// whether it is installed, which check each place gets, and the Install that is
// the only way its weights reach this machine. Without it the post-train smoke
// still reads "Identity: not measured" and clips keep the colour match.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActionButton, ConfirmActionDialog, Hint, Line, StatusPill } from "./ui";
import {
  getIdentityCheck,
  installIdentityEncoder,
  removeIdentityEncoder,
} from "../../api/productionService";
import { translate, useUiTranslation } from "../../i18n";

const describeChecks = (status) => {
  const smoke =
    status.smoke_check === "embed"
      ? translate("settingsAccess.identityCheck.smokeEmbed", { threshold: status.smoke_threshold })
      : status.smoke_check === "vlm"
        ? translate("settingsAccess.identityCheck.smokeVlm")
        : translate("settingsAccess.identityCheck.smokeNone");
  const clips =
    status.clip_check === "embed"
      ? translate("settingsAccess.identityCheck.clipsEmbed", { floor: status.clip_floor })
      : translate("settingsAccess.identityCheck.clipsColour");
  return translate("settingsAccess.identityCheck.checks", { smoke, clips });
};

const IdentityCheckControl = ({ showMessage }) => {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getIdentityCheck());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    load();
    return () => clearTimeout(timer.current);
  }, [load]);

  const running = status?.install?.state === "running";
  useEffect(() => {
    if (!running) return undefined;
    timer.current = setTimeout(load, 1500);
    return () => clearTimeout(timer.current);
  }, [running, status, load]);

  const act = async (call, failMessage) => {
    setBusy(true);
    try {
      const next = await call();
      setStatus(next);
      if (next?.error) showMessage?.(next.error, "warning");
    } catch (e) {
      showMessage?.(failMessage(e.message || e), "error");
    } finally {
      setBusy(false);
    }
  };

  if (!status) return null;
  const job = status.install || {};
  const size = t("settingsAccess.identityCheck.sizeMb", { size: status.size_mb });
  const what = t("settingsAccess.identityCheck.what", {
    repo: status.repo,
    license: status.license,
    size,
    revision: String(status.revision).slice(0, 12),
    location: status.location,
  });

  return (
    <>
      <Line>
        {status.installed ? (
          <StatusPill label={t("settingsAccess.identityCheck.installed")} tone="ok" tooltip={what} />
        ) : running ? (
          <StatusPill
            label={job.downloaded_mb > 0 ? t("settingsAccess.identityCheck.installingPercent", { percent: job.progress }) : t("settingsAccess.identityCheck.installing")}
            tone="info"
            tooltip={what}
          />
        ) : (
          <StatusPill label={t("settingsAccess.identityCheck.notInstalled")} tone="neutral" tooltip={what} />
        )}
        {!status.installed && !running && (
          <ActionButton
            onClick={() => act(installIdentityEncoder, (error) => t("settingsAccess.identityCheck.actionFailedInstall", { error }))}
            loading={busy}
            tooltip={t("settingsAccess.identityCheck.installTooltip", { what })}
          >
            {t("settingsAccess.identityCheck.install", { name: status.name, size })}
          </ActionButton>
        )}
        {status.installed && (
          <ActionButton
            onClick={() => setConfirmRemove(true)}
            disabled={busy}
            tooltip={t("settingsAccess.identityCheck.removeTooltip", { location: status.location })}
          >
            {t("settingsAccess.identityCheck.remove")}
          </ActionButton>
        )}
      </Line>
      {!status.installed && (
        <Hint>{t("settingsAccess.identityCheck.installHint", { what })}</Hint>
      )}
      <Hint>{t("settingsAccess.identityCheck.now", { checks: describeChecks(status) })}</Hint>
      {job.state === "failed" && job.error && <Hint>{t("settingsAccess.identityCheck.installFailed", { error: job.error })}</Hint>}
      <ConfirmActionDialog
        open={confirmRemove}
        reversible
        title={t("settingsAccess.identityCheck.removeTitle", { name: status.name })}
        description={t("settingsAccess.identityCheck.removeDescription")}
        facts={[
          { label: t("settingsAccess.identityCheck.deletes"), value: status.location },
          { label: t("settingsAccess.identityCheck.size"), value: size },
        ]}
        confirmLabel={t("settingsAccess.identityCheck.remove")}
        busy={busy}
        onConfirm={async () => {
          await act(removeIdentityEncoder, (error) => t("settingsAccess.identityCheck.actionFailedRemove", { error }));
          setConfirmRemove(false);
        }}
        onClose={() => setConfirmRemove(false)}
      />
    </>
  );
};

export default IdentityCheckControl;
