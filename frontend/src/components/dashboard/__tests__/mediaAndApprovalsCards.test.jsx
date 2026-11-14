import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

const approvals = vi.hoisted(() => ({ value: null }));
vi.mock("../../../hooks/usePendingApprovals", () => ({ usePendingApprovals: () => approvals.value }));
const approveDraft = vi.fn();
const rejectDraft = vi.fn();
vi.mock("../../../api/outreachService", () => ({
  approveDraft: (...args) => approveDraft(...args),
  rejectDraft: (...args) => rejectDraft(...args),
}));
vi.mock("../../media/useMediaActions", () => ({
  default: () => ({ onFileClick: vi.fn(), fileMenuActions: () => [], overlays: null }),
}));

import VideoGenerationCard from "../VideoGenerationCard";
import ImageGenerationCard from "../ImageGenerationCard";
import ApprovalsCard from "../ApprovalsCard";
import { mediaOfKind, thumbnailUrl } from "../recentMedia";
import { SnackbarProvider } from "../../common/SnackbarProvider";

const Where = () => {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname + loc.search}</div>;
};

const renderCard = (card) =>
  render(
    <SnackbarProvider>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={card} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </SnackbarProvider>,
  );

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const documents = [
  { id: 9, filename: "harbour.png", updated_at: "2026-11-01T10:00:00", metadata: { prompt: "a harbour at dusk" } },
  { id: 8, filename: "take_one.mp4", updated_at: "2026-11-01T09:00:00" },
  { id: 7, filename: "notes.pdf", updated_at: "2026-11-01T08:00:00" },
];

const nothingWaiting = {
  count: 0, pending: [], held: [], outreach: [], outreachWaiting: [],
  loading: false, heldLoading: false, outreachLoading: false, error: null, refresh: vi.fn(async () => []),
};

beforeEach(() => {
  vi.unstubAllGlobals();
  approveDraft.mockReset();
  rejectDraft.mockReset();
  approvals.value = { ...nothingWaiting, refresh: vi.fn(async () => []) };
});

describe("recent media", () => {
  it("keeps only files of the kind asked for and addresses thumbnails by document", () => {
    expect(mediaOfKind(documents, "image").map((d) => d.id)).toEqual([9]);
    expect(mediaOfKind(documents, "video").map((d) => d.id)).toEqual([8]);
    expect(thumbnailUrl(documents[0])).toContain("/files/thumbnail?document_id=9&size=320&v=2026-11-01T10%3A00%3A00");
  });

  it("Videos card asks the library for clips and shows one thumbnail per clip", async () => {
    const fetchMock = vi.fn(async () => json({ success: true, data: { documents } }));
    vi.stubGlobal("fetch", fetchMock);
    renderCard(<VideoGenerationCard id="videogen" />);
    expect(await screen.findByRole("button", { name: "Open take_one.mp4" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open harbour.png" })).not.toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain("/files/recent?kind=video");
  });

  it("Videos card says there are none only when the library has none, and shows a failure as one", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ success: true, data: { documents: [] } })));
    const first = renderCard(<VideoGenerationCard id="videogen" />);
    expect(await screen.findByText("No videos yet.")).toBeInTheDocument();
    first.unmount();

    vi.stubGlobal("fetch", vi.fn(async () => json({ error: "database is down" }, 500)));
    renderCard(<VideoGenerationCard id="videogen" />);
    expect(await screen.findByText(/Could not load the latest videos/)).toBeInTheDocument();
    expect(screen.queryByText("No videos yet.")).not.toBeInTheDocument();
  });

  it("Videos card: New Video opens Video Gen", () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ success: true, data: { documents: [] } })));
    renderCard(<VideoGenerationCard id="videogen" />);
    fireEvent.click(screen.getByRole("button", { name: "New Video" }));
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/video$/);
  });

  it("Image Generation shows the latest pictures above its batches", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) =>
        String(url).includes("/files/recent")
          ? json({ success: true, data: { documents } })
          : json({ success: true, data: { batches: [] } }),
      ),
    );
    renderCard(<ImageGenerationCard id="imggen" />);
    const thumb = await screen.findByRole("button", { name: "Open harbour.png" });
    expect(thumb).toHaveAttribute("title", "a harbour at dusk");
  });
});

describe("Waiting for You card", () => {
  const draft = { id: 41, platform: "reddit", action: "comment", draft_text: "Try the local build.", target_url: "https://example.com/t/1", created_at: "2026-11-01T10:00:00" };

  it("says nothing is waiting when every queue is empty", () => {
    renderCard(<ApprovalsCard id="approvals" />);
    expect(screen.getByText("Nothing is waiting for you.")).toBeInTheDocument();
  });

  it("approves an outreach draft as written and reloads the queues", async () => {
    approvals.value = { ...approvals.value, count: 1, outreach: [draft] };
    approveDraft.mockResolvedValue({ next: "Start the post from Approvals." });
    renderCard(<ApprovalsCard id="approvals" />);
    expect(screen.getByText("Try the local build.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(approvals.value.refresh).toHaveBeenCalled());
    expect(approveDraft).toHaveBeenCalledWith(41, null);
    expect(rejectDraft).not.toHaveBeenCalled();
    expect(await screen.findByText("Approved. Start the post from Approvals.")).toBeInTheDocument();
  });

  it("rejects an outreach draft, and shows a refusal instead of hiding it", async () => {
    approvals.value = { ...approvals.value, count: 1, outreach: [draft] };
    rejectDraft.mockRejectedValue(new Error("This action needs an API key."));
    renderCard(<ApprovalsCard id="approvals" />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("This action needs an API key.")).toBeInTheDocument();
    expect(rejectDraft).toHaveBeenCalledWith(41);
    expect(approvals.value.refresh).not.toHaveBeenCalled();
  });

  it("sends a publish and a held code change to their tabs on the Approvals page", () => {
    approvals.value = {
      ...approvals.value,
      count: 2,
      pending: [{ id: 5, platform: "discord", title: "Release note", created_at: "2026-11-01T10:00:00" }],
      held: [{ id: 6, verdict: "hold", subject: "Add a download step", created_at: "2026-11-01T10:00:00" }],
    };
    const first = renderCard(<ApprovalsCard id="approvals" />);
    fireEvent.click(screen.getByText("Release note"));
    expect(screen.getByTestId("where")).toHaveTextContent("/approvals?tab=pending");
    first.unmount();

    renderCard(<ApprovalsCard id="approvals" />);
    fireEvent.click(screen.getByText("Add a download step"));
    expect(screen.getByTestId("where")).toHaveTextContent("/approvals?tab=code");
  });
});
