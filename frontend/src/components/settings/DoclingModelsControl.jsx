// The PDF layout models (Settings > Knowledge): whether they are installed, and
// the Install that is the only way they reach this machine. Until then PDFs are
// indexed as plain text; indexing never downloads them itself.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Line, Hint, StatusPill, ActionButton } from "./ui";
import { getDoclingModelsStatus, installDoclingModels } from "../../api/settingsService";
import { useUiTranslation } from "../../i18n";

const DoclingModelsControl = ({ showMessage }) => {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(null);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getDoclingModelsStatus());
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
      setStatus(await installDoclingModels());
    } catch (e) {
      showMessage?.(t("settingsSections.doclingModels.installStartFailed", { error: e.message || e }), "error");
    }
  };

  if (!status) return null;
  const size = status.size_gb ? t("settingsSections.doclingModels.size", { size: status.size_gb }) : "";
  const job = status.install || {};

  if (!status.enabled) {
    return (
      <Line>
        <StatusPill label={t("settingsSections.doclingModels.off")} tooltip={t("settingsSections.doclingModels.offTooltip")} />
      </Line>
    );
  }
  if (status.installed) {
    return (
      <Line>
        <StatusPill label={t("settingsSections.doclingModels.installed")} tone="ok" tooltip={status.model} />
      </Line>
    );
  }
  return (
    <Line>
      {running ? (
        <StatusPill
          label={job.progress != null ? t("settingsSections.doclingModels.installingPercent", { percent: job.progress }) : t("settingsSections.doclingModels.installingGb", { size: job.downloaded_gb })}
          tone="info"
          tooltip={status.model}
        />
      ) : (
        <StatusPill label={t("settingsSections.doclingModels.notInstalled")} tone="warn" tooltip={status.model} />
      )}
      {!running && (
        <ActionButton
          onClick={install}
          tooltip={
            size
              ? t("settingsSections.doclingModels.downloadTooltipWithSize", { model: status.model, size })
              : t("settingsSections.doclingModels.downloadTooltip", { model: status.model })
          }
        >
          {size ? t("settingsSections.doclingModels.installWithSize", { size }) : t("settingsSections.doclingModels.install")}
        </ActionButton>
      )}
      {job.state === "failed" && <Hint>{t("settingsSections.doclingModels.installFailed", { error: job.error })}</Hint>}
    </Line>
  );
};

export default DoclingModelsControl;
