// frontend/src/components/settings/LanguageSetting.jsx
import React from "react";
import { FormControl, MenuItem, Select } from "@mui/material";
import { SUPPORTED_LANGUAGES, languageName, setLanguage, useUiTranslation } from "../../i18n";
import { Cluster, Line } from "./ui";

/** Settings → General: the interface language. Every language with a folder
 * under src/locales is offered, each in its own name. Saved in this browser. */
const LanguageSetting = () => {
  const { t, language } = useUiTranslation();
  return (
    <Cluster label={t("settings.language.label")} help={t("settings.language.help")}>
      <Line>
        <FormControl size="small" sx={{ minWidth: 200, maxWidth: "100%" }}>
          <Select
            id="ui-language"
            value={language}
            inputProps={{ "aria-label": t("settings.language.label") }}
            onChange={(event) => setLanguage(event.target.value)}
            data-testid="language-setting"
          >
            {SUPPORTED_LANGUAGES.map((code) => (
              <MenuItem key={code} value={code} lang={code}>
                {languageName(code)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Line>
    </Cluster>
  );
};

export default LanguageSetting;
