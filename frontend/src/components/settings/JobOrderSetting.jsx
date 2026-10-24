// frontend/src/components/settings/JobOrderSetting.jsx
import React, { useEffect, useState } from "react";
import { getJobPriority, setJobPriority } from "../../api/settingsService";
import { Cluster, Line, SettingChip } from "./ui";
import { useUiTranslation } from "../../i18n";

const readFlag = (result) => {
  const on = result?.data?.job_priority_enabled ?? result?.job_priority_enabled;
  return typeof on === "boolean" ? on : null;
};

/**
 * Settings → General: the job order switch (backend/services/job_arbiter.py).
 * Off keeps today's order: each queue runs its own jobs first come, first
 * served. On ranks waiting work by class and lets the Activity page move it.
 */
const JobOrderSetting = ({ showMessage }) => {
  const { t } = useUiTranslation();
  const [on, setOn] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    getJobPriority().then((result) => {
      if (!alive) return;
      const flag = readFlag(result);
      if (flag !== null) setOn(flag);
      setLoaded(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  const toggle = async (next) => {
    const previous = on;
    setOn(next);
    setSaving(true);
    const result = await setJobPriority(next);
    setSaving(false);
    const stored = readFlag(result);
    if (result?.error || stored === null) {
      setOn(previous);
      showMessage?.(t("settingsSections.jobOrder.notSaved"), "error");
      return;
    }
    setOn(stored);
    showMessage?.(stored ? t("settingsSections.jobOrder.on") : t("settingsSections.jobOrder.off"), "success");
  };

  return (
    <Cluster
      label={t("settingsSections.jobOrder.label")}
      help={t("settingsSections.jobOrder.help")}
    >
      <Line>
        <SettingChip
          label={t("settingsSections.jobOrder.chip")}
          on={on}
          onToggle={toggle}
          disabled={!loaded || saving}
          tooltip={t("settingsSections.jobOrder.chipTooltip")}
          testId="job-order-setting"
        />
      </Line>
    </Cluster>
  );
};

export default JobOrderSetting;
