// The search reranker (Settings > Knowledge): whether it is installed, and the
// Install that is the only way its weights reach this machine. Searches run
// without reranking until it is installed; they never download it themselves.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Line, Hint, StatusPill, ActionButton } from "./ui";
import { getRerankerStatus, installReranker } from "../../api/settingsService";
import { useUiTranslation } from "../../i18n";

const RerankerControl = ({ showMessage }) => {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(null);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getRerankerStatus());
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
    timer.current = setTimeout(load, 2000);
    return () => clearTimeout(timer.current);
  }, [running, status, load]);

  const install = async () => {
    try {
      setStatus(await installReranker());
    } catch (e) {
      showMessage?.(t("settingsSections.reranker.installStartFailed", { error: e.message || e }), "error");
    }
  };

  if (!status) return null;
  const size = status.size_gb ? t("settingsSections.reranker.size", { size: status.size_gb }) : "";
  const job = status.install || {};

  if (!status.enabled) {
    return (
      <Line>
        <StatusPill label={t("settingsSections.reranker.off")} tooltip={t("settingsSections.reranker.offTooltip")} />
      </Line>
    );
  }
  if (status.installed) {
    return (
      <Line>
        <StatusPill label={t("settingsSections.reranker.installed")} tone="ok" tooltip={status.model} />
        {status.load_error && <Hint>{t("settingsSections.reranker.loadFailed", { error: status.load_error })}</Hint>}
      </Line>
    );
  }
  return (
    <Line>
      {running ? (
        <StatusPill
          label={job.progress != null ? t("settingsSections.reranker.installingPercent", { percent: job.progress }) : t("settingsSections.reranker.installingGb", { size: job.downloaded_gb })}
          tone="info"
          tooltip={status.model}
        />
      ) : (
        <StatusPill label={t("settingsSections.reranker.notInstalled")} tone="warn" tooltip={status.model} />
      )}
      {!running && (
        <ActionButton
          onClick={install}
          tooltip={
            size
              ? t("settingsSections.reranker.downloadTooltipWithSize", { model: status.model, size })
              : t("settingsSections.reranker.downloadTooltip", { model: status.model })
          }
        >
          {size ? t("settingsSections.reranker.installWithSize", { size }) : t("settingsSections.reranker.install")}
        </ActionButton>
      )}
      {job.state === "failed" && <Hint>{t("settingsSections.reranker.installFailed", { error: job.error })}</Hint>}
    </Line>
  );
};

export default RerankerControl;
