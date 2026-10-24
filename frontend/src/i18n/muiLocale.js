// MUI's own strings (table pagination, "Rows per page", picker labels) come
// from its locale packs. They load only for a language other than English, so
// the English page builds its theme exactly as before.
import { useEffect, useMemo, useState } from "react";
import { createTheme } from "@mui/material/styles";
import { useUiTranslation } from "./index";

/** The MUI pack for a language code: "pt-BR" → ptBR, "de" → deDE. */
export function pickMuiLocale(language, locales) {
  const exact = locales[language.replace("-", "")];
  if (exact) return exact;
  const base = language.split("-")[0];
  const name = Object.keys(locales)
    .sort()
    .find((key) => key.startsWith(base) && /[A-Z]/.test(key.charAt(base.length)));
  return name ? locales[name] : null;
}

export function useLocalizedTheme(baseTheme) {
  const { language } = useUiTranslation();
  const [pack, setPack] = useState(null);

  useEffect(() => {
    if (language === "en") return undefined;
    let current = true;
    import("@mui/material/locale").then((locales) => {
      if (current) setPack({ language, locale: pickMuiLocale(language, locales) });
    });
    return () => {
      current = false;
    };
  }, [language]);

  return useMemo(() => {
    if (language === "en" || pack?.language !== language || !pack.locale) return baseTheme;
    return createTheme(baseTheme, pack.locale);
  }, [baseTheme, language, pack]);
}
