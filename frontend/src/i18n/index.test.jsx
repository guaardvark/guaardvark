import React, { memo, useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as muiLocales from "@mui/material/locale";
import i18n, {
  formatNumber,
  LANGUAGE_STORAGE_KEY,
  languageName,
  setLanguage,
  SUPPORTED_LANGUAGES,
  Trans,
  uiLocale,
  useUiTranslation,
} from "./index";
import { pickMuiLocale } from "./muiLocale";
import { navigationLabel } from "./navigation";
import LanguageSetting from "../components/settings/LanguageSetting";
import germanNavigation from "../locales/de/navigation.json";

const LOCALE_FILES = import.meta.glob("../locales/*/*.json", { eager: true, import: "default" });

beforeEach(async () => {
  localStorage.clear();
  await setLanguage("en");
});

afterEach(async () => {
  vi.restoreAllMocks();
  await setLanguage("en");
});

describe("choosing a language", () => {
  it("starts in English", () => {
    expect(i18n.isInitialized).toBe(true);
    expect(i18n.resolvedLanguage).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("offers English first, then every language folder", () => {
    expect(SUPPORTED_LANGUAGES[0]).toBe("en");
    expect(SUPPORTED_LANGUAGES).toEqual(expect.arrayContaining(["de", "es", "fr"]));
    expect(languageName("de")).toBe("Deutsch");
  });

  it("remembers the choice and ignores a language it does not have", async () => {
    await setLanguage("de");
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("de");
    expect(document.documentElement.lang).toBe("de");
    await setLanguage("xx-unknown");
    expect(i18n.resolvedLanguage).toBe("en");
  });

  it("still switches when storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    await setLanguage("de");
    expect(i18n.resolvedLanguage).toBe("de");
  });

  it("follows a switch made in another tab, and storage being cleared", async () => {
    await act(async () => {
      window.dispatchEvent(new StorageEvent("storage", { key: LANGUAGE_STORAGE_KEY, newValue: "de" }));
      await vi.waitFor(() => expect(i18n.resolvedLanguage).toBe("de"));
    });
    await act(async () => {
      window.dispatchEvent(new StorageEvent("storage", { key: null }));
      await vi.waitFor(() => expect(i18n.resolvedLanguage).toBe("en"));
    });
  });
});

describe("strings", () => {
  it("shows the English for a string a language lacks", async () => {
    i18n.addResource("en", "translation", "fallbackTest.onlyInEnglish", "English text");
    await setLanguage("de");
    expect(i18n.t("fallbackTest.onlyInEnglish")).toBe("English text");
  });

  it("keeps the browser's own date and number formats while the interface is English", async () => {
    expect(uiLocale()).toBeUndefined();
    await setLanguage("de");
    expect(uiLocale()).toBe("de");
    expect(formatNumber(1234.5)).toBe(new Intl.NumberFormat("de").format(1234.5));
  });

  it("translates core navigation and leaves labels a brand or extension supplied", async () => {
    await setLanguage("de");
    expect(navigationLabel("Settings", { id: "settings", path: "/settings" })).toBe(germanNavigation.items.settings);
    expect(navigationLabel("My settings", { id: "settings", path: "/settings" })).toBe("My settings");
    expect(navigationLabel("Extension", { id: "ext:/extension" })).toBe("Extension");
  });
});

describe("locale files", () => {
  const flatten = (tree, prefix = "") =>
    Object.entries(tree).flatMap(([key, value]) =>
      value && typeof value === "object" ? flatten(value, `${prefix}${key}.`) : [[`${prefix}${key}`, value]],
    );
  const marks = (text) =>
    (String(text).match(/\{\{[^{}]+\}\}|<\/?[A-Za-z0-9]+\s*\/?>|\$t\([^)]*\)/g) || [])
      .map((mark) => mark.replace(/\s/g, ""))
      .sort();
  const pluralBase = (key) => key.replace(/_(zero|one|two|few|many|other)$/, "");

  const english = {};
  const others = [];
  for (const [path, strings] of Object.entries(LOCALE_FILES)) {
    const [language, file] = path.split("/").slice(-2);
    if (file.startsWith("_")) continue;
    const area = file.replace(/\.json$/, "");
    if (language === "en") english[area] = Object.fromEntries(flatten(strings));
    else others.push([language, area, flatten(strings)]);
  }

  it.each(others)("%s/%s.json has only English keys, with their placeholders and tags", (_language, area, entries) => {
    const source = english[area];
    expect(source, `no English ${area}.json`).toBeDefined();
    const sourceByBase = Object.fromEntries(Object.entries(source).map(([key, text]) => [pluralBase(key), text]));
    for (const [key, text] of entries) {
      const original = source[key] ?? sourceByBase[pluralBase(key)];
      expect(original, `${area}.${key} is not in English`).toBeDefined();
      expect(String(text).trim(), `${area}.${key}`).not.toBe("");
      expect(marks(text), `${area}.${key}`).toEqual(marks(original));
    }
  });
});

describe("markup inside a sentence", () => {
  it("renders named tags from the English and follows a language change", async () => {
    i18n.addResource("en", "translation", "transTest.open", "Open <settingsLink>Settings</settingsLink> to change it, {{name}}.");
    i18n.addResource("de", "translation", "transTest.open", "Öffne <settingsLink>Einstellungen</settingsLink>, {{name}}.");
    // Trans re-renders with its component; the component subscribes to the
    // language through useUiTranslation, as every translated screen does.
    function Sentence() {
      useUiTranslation();
      return (
        <p data-testid="sentence">
          <Trans i18nKey="transTest.open" components={{ settingsLink: <a href="/settings" /> }} values={{ name: "Ada" }} />
        </p>
      );
    }
    render(<Sentence />);
    expect(screen.getByTestId("sentence")).toHaveTextContent("Open Settings to change it, Ada.");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
    await act(() => setLanguage("de"));
    expect(screen.getByRole("link", { name: "Einstellungen" })).toBeInTheDocument();
  });
});

describe("values inside a Trans sentence", () => {
  it("shows a value holding tags as text, even one named like the sentence's own", () => {
    i18n.addResource("en", "translation", "transTest.file", "Open <bold>{{name}}</bold> now");
    render(
      <p data-testid="file">
        <Trans
          i18nKey="transTest.file"
          values={{ name: "a <bold>x</bold> <strong>y</strong> & z.txt" }}
          components={{ bold: <b /> }}
        />
      </p>,
    );
    const sentence = screen.getByTestId("file");
    expect(sentence).toHaveTextContent("Open a <bold>x</bold> <strong>y</strong> & z.txt now");
    expect(sentence.querySelectorAll("b")).toHaveLength(1);
    expect(sentence.querySelector("strong")).toBeNull();
  });

  it("renders an entity written in the English", () => {
    i18n.addResource("en", "translation", "transTest.space", "Used&nbsp;{{used}} of <bold>{{total}}</bold>");
    render(
      <p data-testid="space">
        <Trans i18nKey="transTest.space" values={{ used: 1, total: 2 }} components={{ bold: <b /> }} />
      </p>,
    );
    expect(screen.getByTestId("space").textContent).toBe("Used\u00a01 of 2");
  });
});

describe("the picker", () => {
  it("lists each language in its own name and switches mounted screens without losing what was typed", async () => {
    const Surface = memo(function Surface() {
      const { t } = useUiTranslation();
      const [draft, setDraft] = useState("");
      return (
        <input
          aria-label={t("navigation.items.settings")}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      );
    });
    render(
      <>
        <LanguageSetting />
        <Surface />
      </>,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Settings" }), { target: { value: "draft text" } });
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Language" }));
    expect(screen.getByRole("option", { name: "Français" })).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("option", { name: "Deutsch" })));
    await vi.waitFor(() => expect(i18n.resolvedLanguage).toBe("de"));
    expect(screen.getByRole("textbox", { name: germanNavigation.items.settings })).toHaveValue("draft text");
  });
});

describe("MUI's own strings", () => {
  it("picks the matching MUI locale pack", () => {
    expect(pickMuiLocale("de", muiLocales)).toBe(muiLocales.deDE);
    expect(pickMuiLocale("pt-BR", muiLocales)).toBe(muiLocales.ptBR);
    expect(pickMuiLocale("zh", muiLocales)).toBe(muiLocales.zhCN);
    expect(pickMuiLocale("xx", muiLocales)).toBeNull();
  });
});
