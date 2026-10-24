# Interface languages

The interface can be shown in any language that has a folder under
`frontend/src/locales/`. English is the source and ships with the page; every
other language is loaded when someone picks it in **Settings → General →
Language**, and any string a language lacks is shown in English. The choice is
saved per browser. Chat replies, documents and generated media are not affected.

## How strings are stored

```
frontend/src/locales/
  en/navigation.json     the English source, one file per area of the interface
  en/settings.json
  de/navigation.json     the same files, translated
  de/_sources.json       which English each translation was made from
```

A string is addressed as `area.key`, for example `t("settings.language.label")`
reads `"label"` inside `"language"` in `settings.json`. Every language uses the
same areas and keys as English.

## Writing interface text

In a component, take `t` from the hook; elsewhere (constants, services,
notifications) use `translate`:

```jsx
import { useUiTranslation, translate } from "../i18n";

function SaveButton({ count }) {
  const { t } = useUiTranslation();
  return <Button>{t("files.saveSelected", { count })}</Button>;
}
```

```json
{ "saveSelected_one": "Save {{count}} file", "saveSelected_other": "Save {{count}} files" }
```

- **Whole sentences.** One key per sentence or label, never a sentence glued
  from pieces. Word order differs between languages; `"Delete " + name + "?"`
  cannot be translated, `"Delete {{name}}?"` can.
- **Named placeholders.** `{{name}}`, `{{count}}`, `{{model}}`, not `{{value1}}`.
- **Plurals through `count`.** Give `_one` and `_other` in English; each
  language gets the forms it needs (Polish and Russian have more than two).
- **Markup inside a sentence** uses `<Trans>` from `react-i18next` with
  numbered tags (`"Open <1>Settings</1> to change it."`), so the link moves with
  the words.
- **Only displayed text is translated.** Values sent to the API, compared in
  code, used as keys or stored stay in English.
- **Names stay names.** Product, model and file names are not put through `t`.
- **Dates and numbers** go through `formatDateTime` and `formatNumber` from
  `src/i18n`. While the interface is English they follow the browser's own
  locale, as they always have.

## Adding or updating a language

Translations are made on your own machine with a local model through Ollama;
nothing is sent anywhere else.

```bash
python3 scripts/translate_locales.py de          # fill in what German is missing
python3 scripts/translate_locales.py ja ko pt-BR # start new languages
python3 scripts/translate_locales.py --check     # report on every language, change nothing
```

- Use a language code the browser knows (`de`, `fr`, `pt-BR`, `zh-CN`). The new
  folder appears in the picker under the language's own name.
- `--model` picks the Ollama model (default `gemma4:12b`); `--area settings`
  limits a run to one file.
- Each translation is checked before it is written: it must keep the English
  string's placeholders, tags and surrounding spaces. One that fails twice is
  left out and listed, and the interface shows the English for it.
- When an English string changes, the next run translates it again.
- Right-to-left languages (Arabic, Hebrew, Persian, Urdu) are not offered yet:
  the layout has to be mirrored first.

## Correcting a translation

Edit the string in `frontend/src/locales/<language>/<area>.json`. The script
never overwrites a string that was edited by hand. If its English later
changes, `--check` lists it as needing a look instead of replacing it.

## Checking

```bash
cd frontend
npx vitest run src/i18n
```

The tests check that every language file holds only keys that exist in
English, with the same placeholders and tags, and that switching languages
updates open screens without losing what was typed.
