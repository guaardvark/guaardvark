import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  VOICE_DEFAULTS,
  VOICE_SETTINGS_KEY,
  normalizeVoiceSettings,
  readVoiceSettings,
  updateVoiceSettings,
  vadConfigFrom,
} from "../voiceDefaults";
import VoiceSessionEngine from "../../contexts/voiceSessionEngine";
import { useVoiceSessionStore } from "../../stores/useVoiceSessionStore";
import { mergePersistedAppState } from "../../stores/useAppStore";

vi.mock("../../contexts/VoiceContext", () => ({ useVoice: () => ({ availableVoices: [] }) }));

import VoiceSettingsContent from "../../components/settings/VoiceSettingsContent";

beforeEach(() => {
  localStorage.clear();
});

const sliderValue = (name) =>
  Number(screen.getByRole("slider", { name }).getAttribute("aria-valuenow"));

describe("voice defaults", () => {
  it("Settings → Voice shows the values the voice session listens with", () => {
    // SettingsPage seeds its state from readVoiceSettings().
    const settings = readVoiceSettings();
    render(
      <VoiceSettingsContent
        voiceSettings={settings}
        availableVoices={[]}
        voiceStatus={null}
        voiceError={null}
        isVoiceLoading={false}
        voiceModelsStatus={null}
        handleVoiceSettingChange={() => {}}
        testVoice={() => {}}
        systemName="Guaardvark"
      />
    );
    const engine = new VoiceSessionEngine({ store: useVoiceSessionStore });
    engine.configure({ settings: readVoiceSettings() });

    expect(sliderValue("Speech threshold")).toBe(engine.settings.silenceThreshold);
    expect(sliderValue("Pause that ends a message")).toBe(engine.settings.silenceTimeout);
    expect(sliderValue("Longest single message")).toBe(engine.settings.maxSegmentDuration);
    expect(engine.settings.silenceThreshold).toBe(VOICE_DEFAULTS.silenceThreshold);
    expect(vadConfigFrom(settings).threshold).toBe(VOICE_DEFAULTS.silenceThreshold);
    expect(screen.getByLabelText(/Hey Guaardvark/)).not.toBeChecked();
    expect(engine.settings.wakeWordEnabled).toBe(false);
  });

  it("fills missing and malformed stored values from the defaults", () => {
    localStorage.setItem(
      VOICE_SETTINGS_KEY,
      JSON.stringify({ silenceThreshold: "loud", silenceTimeout: 99999, wakeWordEnabled: "yes", voice: "ryan" })
    );
    const s = readVoiceSettings();
    expect(s.silenceThreshold).toBe(VOICE_DEFAULTS.silenceThreshold);
    expect(s.silenceTimeout).toBe(5000);
    expect(s.wakeWordEnabled).toBe(false);
    expect(s.voice).toBe("ryan");
    expect(normalizeVoiceSettings(null)).toEqual({ ...VOICE_DEFAULTS });
  });

  it("updateVoiceSettings merges, stores and announces", () => {
    const heard = vi.fn();
    window.addEventListener("voiceSettingsChanged", heard);
    updateVoiceSettings({ wakeWordEnabled: true });
    updateVoiceSettings({ silenceTimeout: 2000 });
    window.removeEventListener("voiceSettingsChanged", heard);
    expect(heard).toHaveBeenCalledTimes(2);
    expect(readVoiceSettings()).toMatchObject({ wakeWordEnabled: true, silenceTimeout: 2000 });
  });
});

describe("persisted activation mode", () => {
  const current = { voiceActivationMode: "handsfree", themeName: "x" };

  it("a browser with the old Listener toggle on opens in Hands-free and drops the old key", () => {
    const merged = mergePersistedAppState({ listenerModeEnabled: true, themeName: "y" }, current);
    expect(merged.voiceActivationMode).toBe("handsfree");
    expect(merged).not.toHaveProperty("listenerModeEnabled");
    expect(merged.themeName).toBe("y");
  });

  it("an explicit mode wins over the old toggle", () => {
    expect(mergePersistedAppState({ listenerModeEnabled: true, voiceActivationMode: "push" }, current).voiceActivationMode).toBe("push");
  });

  it("an unknown stored mode falls back to the default", () => {
    expect(mergePersistedAppState({ voiceActivationMode: "shout" }, { ...current, voiceActivationMode: "toggle" }).voiceActivationMode).toBe("toggle");
  });
});
