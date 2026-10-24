// frontend/src/components/settings/TipsSetting.jsx
import React from "react";
import { useAppStore } from "../../stores/useAppStore";
import { Cluster, Line, SettingChip } from "./ui";
import { useUiTranslation } from "../../i18n";

/** Settings → General: the on/off switch for the "Did you know" tips. Stored per browser. */
const TipsSetting = () => {
  const { t } = useUiTranslation();
  const tipsEnabled = useAppStore((s) => s.tipsEnabled);
  const setTipsEnabled = useAppStore((s) => s.setTipsEnabled);
  return (
    <Cluster
      label={t("settingsSections.tips.label")}
      help={t("settingsSections.tips.help")}
    >
      <Line>
        <SettingChip label={t("settingsSections.tips.chip")} on={tipsEnabled} onToggle={setTipsEnabled} testId="tips-setting" />
      </Line>
    </Cluster>
  );
};

export default TipsSetting;
