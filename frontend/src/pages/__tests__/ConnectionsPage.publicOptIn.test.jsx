import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render as renderInto, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api/connectionsService");
vi.mock("../../components/modals/ConnectionModal", () => ({ default: () => null }));
vi.mock("../../contexts/LayoutContext", () => {
  const layout = {
    gridSettings: {
      CONTAINER_PADDING_PX: 4,
      CARD_MARGIN_PX: 8,
      COLS_COUNT: 175,
      ROW_HEIGHT_PX: 10,
      cardGridW: 35,
      cardGridH: 49,
      cardMinGridW: 30,
    },
  };
  return { useDashboardWidth: () => 1750, useLayout: () => layout };
});

import ConnectionsPage from "../ConnectionsPage";
import * as connectionsService from "../../api/connectionsService";

const OPT_IN = {
  visibilities: ["unlisted", "public"],
  label: "Allow public uploads",
  explanation: "Off: every upload from this account is private. Every upload still waits for your approval.",
  refusal: "Public and unlisted uploads are off for this YouTube account.",
  sources: [{ title: "Developer policies", url: "https://developers.google.com/youtube/terms/developer-policies" }],
  legacy_setting: "youtube_allow_public_publish",
};

const SPECS = [
  { provider: "bluesky", family: "social", label: "Bluesky", capabilities: { public_opt_in: null } },
  { provider: "youtube", family: "social", label: "YouTube", capabilities: { public_opt_in: OPT_IN } },
];

const account = (fields) => ({
  family: "social",
  status: "connected",
  enabled: true,
  has_credentials: true,
  credential_hint: "••••",
  credential_source: "file",
  config: {},
  ...fields,
});

const BLUESKY = account({ id: 1, provider: "bluesky", handle: "me.example", public_opt_in: null });
const YOUTUBE = account({ id: 2, provider: "youtube", handle: "My channel", public_opt_in: { on: false, source: "default" } });

const render = (page) => renderInto(<MemoryRouter>{page}</MemoryRouter>);

const toggle = () => screen.findByRole("checkbox", { name: "Allow public uploads" });

describe("ConnectionsPage: a provider's public opt-in", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.ResizeObserver = class {
      observe() {}
      disconnect() {}
    };
    // The board finds no saved arrangement and shows its default.
    global.fetch = vi.fn(async () => ({ ok: false, status: 404, statusText: "NOT FOUND", json: async () => ({}) }));
    connectionsService.fetchConnections.mockResolvedValue([BLUESKY, YOUTUBE]);
    connectionsService.fetchProviders.mockResolvedValue(SPECS);
    connectionsService.fetchEnvironment.mockResolvedValue([]);
    connectionsService.fetchStoreHealth.mockResolvedValue(null);
    connectionsService.fetchPublishSettings.mockResolvedValue({ publish_enabled: true, posting_stopped: false });
    connectionsService.updateConnection.mockResolvedValue({
      ...YOUTUBE,
      config: { allow_public: true },
      public_opt_in: { on: true, source: "connection" },
    });
  });

  it("is offered off, on that provider's card only, with what it allows and its sources", async () => {
    render(<ConnectionsPage />);

    expect(await toggle()).not.toBeChecked();
    expect(screen.getAllByRole("checkbox", { name: "Allow public uploads" })).toHaveLength(1);
    expect(screen.getByText(/Every upload still waits for your approval\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Developer policies" })).toHaveAttribute("href", OPT_IN.sources[0].url);
  });

  it("is saved on the connection", async () => {
    render(<ConnectionsPage />);

    fireEvent.click(await toggle());

    await waitFor(() => expect(connectionsService.updateConnection).toHaveBeenCalledWith(2, { allow_public: true }));
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "Allow public uploads" })).toBeChecked(),
    );
  });

  it("shows as on when the old install-wide setting turns it on, and says so", async () => {
    connectionsService.fetchConnections.mockResolvedValue([
      { ...YOUTUBE, public_opt_in: { on: true, source: "setting" } },
    ]);
    render(<ConnectionsPage />);

    expect(await toggle()).toBeChecked();
    expect(screen.getByText(/On through the install-wide setting youtube_allow_public_publish/)).toBeInTheDocument();
  });

  it("lists each family's connections in that family's card", async () => {
    connectionsService.fetchConnections.mockResolvedValue([
      BLUESKY,
      account({ id: 3, family: "mcp_server", provider: "mcp", handle: "files server", public_opt_in: null }),
    ]);
    render(<ConnectionsPage />);

    const card = (id) => document.querySelector(`[data-card-id="${id}"]`);
    await screen.findByText("me.example");
    expect(connectionsService.fetchConnections).toHaveBeenCalledWith();
    expect(card("social")).toHaveTextContent("me.example");
    expect(card("social")).not.toHaveTextContent("files server");
    expect(card("mcp_server")).toHaveTextContent("files server");
    expect(card("ai_provider")).toHaveTextContent("No connections in this category yet.");
  });
});
