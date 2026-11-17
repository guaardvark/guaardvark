import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

const getVoiceCatalog = vi.fn();
vi.mock("../../api/audioFoundryService", () => ({
  getVoiceCatalog: (...args) => getVoiceCatalog(...args),
}));

import CastVoicePicker from "./CastVoicePicker";

const CATALOG = {
  kokoro: {
    default: "af_heart",
    groups: [
      { label: "American Female", voices: [
        { id: "af_heart", label: "Heart (default)", installed: true },
        { id: "af_bella", label: "Bella", installed: false },
      ] },
      {
        label: "Japanese Female",
        language: "Japanese",
        language_installed: false,
        install: { id: "kokoro-japanese", name: "Kokoro Japanese" },
        voices: [{ id: "jf_alpha", label: "Alpha", installed: true, language_installed: false }],
      },
    ],
  },
};

const renderPicker = (value, onChange = vi.fn()) => {
  render(
    <MemoryRouter initialEntries={["/cast/1"]}>
      <Routes>
        <Route path="/cast/1" element={<CastVoicePicker value={value} onChange={onChange} />} />
        <Route path="/audio" element={<div>Audio Studio page</div>} />
      </Routes>
    </MemoryRouter>,
  );
  return onChange;
};

describe("CastVoicePicker", () => {
  beforeEach(() => {
    getVoiceCatalog.mockReset();
  });

  it("offers the default voice for an unset voice", async () => {
    getVoiceCatalog.mockResolvedValue(CATALOG);
    renderPicker("");
    expect(await screen.findByText(/Chatterbox's stock voice, or Kokoro's Heart/)).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveTextContent("Default voice");
  });

  it("shows a saved id the catalog does not list as invalid and leaves it alone", async () => {
    getVoiceCatalog.mockResolvedValue(CATALOG);
    const onChange = renderPicker("af_bellla");
    expect(await screen.findByText(/is not an Audio Foundry voice/)).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveTextContent("af_bellla (not an Audio Foundry voice)");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("picks a voice from the list", async () => {
    getVoiceCatalog.mockResolvedValue(CATALOG);
    const onChange = renderPicker("af_bellla");
    await screen.findByText(/is not an Audio Foundry voice/);
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: /Heart/ }));
    expect(onChange).toHaveBeenCalledWith("af_heart");
  });

  it("points a voice that is not installed at Manage models", async () => {
    getVoiceCatalog.mockResolvedValue(CATALOG);
    renderPicker("af_bella");
    expect(await screen.findByText(/Bella is not installed on this machine/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Manage models/ }));
    expect(await screen.findByText("Audio Studio page")).toBeInTheDocument();
  });

  it("lists a voice whose language is not installed and says to install it first", async () => {
    getVoiceCatalog.mockResolvedValue(CATALOG);
    renderPicker("jf_alpha");
    expect(await screen.findByText(/Alpha speaks Japanese, which is not installed/)).toBeInTheDocument();
    expect(screen.getByText(/Install Kokoro Japanese in Audio Studio/)).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveTextContent("Alpha (jf_alpha) · Install Japanese first");
    fireEvent.mouseDown(screen.getByRole("combobox"));
    expect(await screen.findByRole("option", { name: /Alpha.*Install Japanese first/ })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: /Manage models/ }));
    expect(await screen.findByText("Audio Studio page")).toBeInTheDocument();
  });

  it("claims nothing about the saved voice when the list cannot be loaded", async () => {
    getVoiceCatalog.mockRejectedValue(new Error("offline"));
    const onChange = renderPicker("af_bellla");
    expect(await screen.findByText(/voice list could not be loaded/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveTextContent("af_bellla"));
    expect(screen.queryByText(/is not an Audio Foundry voice/)).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
