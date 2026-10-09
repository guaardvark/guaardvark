import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

const withdrawVoiceClipConsent = vi.fn();
const deleteVoiceClip = vi.fn();
vi.mock("../../api/audioFoundryService", () => ({
  withdrawVoiceClipConsent: (...args) => withdrawVoiceClipConsent(...args),
  deleteVoiceClip: (...args) => deleteVoiceClip(...args),
}));

import VoiceClipManager from "./VoiceClipManager";

const CLIPS = [
  { id: "me", filename: "me.wav", size_bytes: 204800, consented: true },
  { id: "old", filename: "old.mp3", size_bytes: 1024, consented: false },
];

const renderManager = (props = {}) => {
  const onChanged = vi.fn();
  const onRemoved = vi.fn();
  render(
    <MemoryRouter>
      <VoiceClipManager clips={CLIPS} onChanged={onChanged} onRemoved={onRemoved} defaultOpen {...props} />
    </MemoryRouter>,
  );
  return { onChanged, onRemoved };
};

const row = (name) => screen.getAllByTestId("voice-clip-row").find((r) => within(r).queryByText(name));

describe("VoiceClipManager", () => {
  beforeEach(() => {
    withdrawVoiceClipConsent.mockReset();
    deleteVoiceClip.mockReset();
  });

  it("lists each clip with its consent and offers withdrawal only where consent exists", () => {
    renderManager();
    expect(within(row("me.wav")).getByText("Consent recorded")).toBeInTheDocument();
    expect(within(row("me.wav")).getByRole("button", { name: "Withdraw consent" })).toBeInTheDocument();
    expect(within(row("old.mp3")).getByText("No consent")).toBeInTheDocument();
    expect(within(row("old.mp3")).queryByRole("button", { name: "Withdraw consent" })).not.toBeInTheDocument();
    expect(within(row("old.mp3")).getByRole("button", { name: "Delete clip" })).toBeInTheDocument();
  });

  it("withdraws consent only after the dialog is confirmed", async () => {
    withdrawVoiceClipConsent.mockResolvedValue({ consented: false, withdrawn: true });
    const { onChanged, onRemoved } = renderManager();
    fireEvent.click(within(row("me.wav")).getByRole("button", { name: "Withdraw consent" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/cannot be used to clone a voice until you confirm/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/cannot be undone/)).not.toBeInTheDocument();
    expect(withdrawVoiceClipConsent).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Withdraw consent" }));
    await waitFor(() => expect(withdrawVoiceClipConsent).toHaveBeenCalledWith(CLIPS[0]));
    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith(CLIPS[0], "withdrawn"));
    expect(onChanged).toHaveBeenCalled();
  });

  it("deletes a clip through the danger confirmation", async () => {
    deleteVoiceClip.mockResolvedValue({ deleted: "old.mp3" });
    const { onRemoved } = renderManager();
    fireEvent.click(within(row("old.mp3")).getByRole("button", { name: "Delete clip" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete this voice clip?")).toBeInTheDocument();
    expect(within(dialog).getByText(/cannot be undone/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete clip" }));
    await waitFor(() => expect(deleteVoiceClip).toHaveBeenCalledWith(CLIPS[1]));
    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith(CLIPS[1], "deleted"));
  });

  it("says where to enter the API key when another device may not delete", async () => {
    const refusal = Object.assign(new Error("This works only on the Guaardvark machine itself."), {
      authRefused: "local_only",
    });
    deleteVoiceClip.mockRejectedValue(refusal);
    const { onRemoved } = renderManager();
    fireEvent.click(within(row("me.wav")).getByRole("button", { name: "Delete clip" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete clip" }));

    expect(await screen.findByText(/works only on the Guaardvark machine/)).toBeInTheDocument();
    // Found once the closing dialog stops hiding the page from assistive tech.
    expect(await screen.findByRole("button", { name: /Settings → Access/ })).toBeInTheDocument();
    expect(onRemoved).not.toHaveBeenCalled();
  });

  it("shows nothing when there are no clips", () => {
    const { container } = render(
      <MemoryRouter>
        <VoiceClipManager clips={[]} />
      </MemoryRouter>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
