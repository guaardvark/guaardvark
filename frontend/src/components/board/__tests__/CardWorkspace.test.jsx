import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../../contexts/LayoutContext", () => {
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

import CardWorkspace, { standardPresets, workspaceLayout } from "../CardWorkspace";
import PageLayout from "../../layout/PageLayout";

const defaults = [
  { i: "prompt", x: 0, y: 0, w: 60, h: 86, minW: 20, minH: 10 },
  { i: "queue", x: 115, y: 0, w: 60, h: 46, minW: 20, minH: 10 },
];

describe("workspaceLayout", () => {
  it("keeps saved places and sizes, drops cards the page no longer has, and adds new ones at their default", () => {
    const saved = {
      version: 2,
      layout: [
        { i: "prompt", x: 5, y: 6, w: 70, h: 40 },
        { i: "gone", x: 0, y: 0, w: 10, h: 10 },
      ],
    };
    expect(workspaceLayout(defaults, saved, 2)).toEqual([
      { i: "prompt", x: 5, y: 6, w: 70, h: 40, minW: 20, minH: 10 },
      defaults[1],
    ]);
  });

  it("uses the default layout for a save from another version, an empty one, or none", () => {
    expect(workspaceLayout(defaults, { version: 1, layout: [{ i: "prompt", x: 9, y: 9, w: 9, h: 9 }] }, 2)).toBe(defaults);
    expect(workspaceLayout(defaults, { version: 2, layout: [] }, 2)).toBe(defaults);
    expect(workspaceLayout(defaults, null, 2)).toBe(defaults);
  });
});

describe("standardPresets", () => {
  it("offers equal tiles and a full-width stack in reading order, and nothing for a single card", () => {
    const [tiles, stacked] = standardPresets(defaults, 175, (key) => key);
    expect(tiles.layout).toEqual({ prompt: { x: 0, y: 0, w: 87, h: 45 }, queue: { x: 87, y: 0, w: 87, h: 45 } });
    expect(stacked.layout).toEqual({ prompt: { x: 0, y: 0, w: 175, h: 86 }, queue: { x: 0, y: 86, w: 175, h: 46 } });
    expect(standardPresets(defaults.slice(0, 1), 175, (key) => key)).toEqual([]);
  });
});

describe("CardWorkspace", () => {
  let posts;
  beforeEach(() => {
    posts = [];
    global.ResizeObserver = class {
      observe() {}
      disconnect() {}
    };
    global.fetch = vi.fn(async (url, opts = {}) => {
      if ((opts.method || "GET") === "POST") {
        posts.push({ url, body: JSON.parse(opts.body) });
        return { ok: true, json: async () => ({}) };
      }
      return { ok: false, status: 404, statusText: "NOT FOUND", json: async () => ({}) };
    });
  });
  afterEach(() => {
    delete global.ResizeObserver;
  });

  const cards = [
    { id: "prompt", title: "Prompt", layout: { x: 0, y: 0, w: 60, h: 86 }, body: <div>prompt body</div> },
    { id: "queue", title: "Queue", layout: { x: 115, y: 0, w: 60, h: 46 }, body: <div>queue body</div> },
  ];

  it("shows each part of the page as a titled card and saves a minimize under the board's name", async () => {
    render(
      <MemoryRouter>
        <CardWorkspace boardKey="video-gen" version={3} cards={cards} />
      </MemoryRouter>,
    );
    expect(screen.getByText("prompt body")).toBeInTheDocument();
    expect(screen.getByText("queue body")).toBeInTheDocument();
    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith("/api/state/board/video-gen"));

    // Two presses in quick succession on a bar: the frame's double-click.
    const bar = screen.getByText("Queue").closest(".card-header-buttons");
    await waitFor(() => {
      fireEvent.mouseDown(bar);
      fireEvent.mouseDown(bar);
      expect(posts.length).toBeGreaterThan(0);
    });
    const saved = posts.at(-1);
    expect(saved.url).toBe("/api/state/board/video-gen");
    expect(saved.body.version).toBe(3);
    expect(saved.body.layout.map((item) => item.i)).toEqual(["prompt", "queue"]);
    // A minimized card keeps its body in the page: a form in it must not lose what was typed.
    expect(screen.getByText("queue body")).toBeInTheDocument();
  });

  it("puts a Layout button in the page header that brings a hidden card back", async () => {
    render(
      <MemoryRouter>
        <PageLayout title="Studio">
          <CardWorkspace boardKey="video-gen" version={3} cards={cards} />
        </PageLayout>
      </MemoryRouter>,
    );
    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith("/api/state/board/video-gen"));
    const button = await screen.findByRole("button", { name: /Layout/ });
    await waitFor(() => {
      fireEvent.click(within(screen.getByText("Queue").closest(".card-header-buttons")).getByRole("button", { name: "Hide card" }));
      expect(screen.queryByText("queue body")).not.toBeInTheDocument();
    });
    fireEvent.click(button);
    expect(screen.getByRole("menuitem", { name: "Layout: Tiles" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Show Queue" }));
    expect(screen.getByText("queue body")).toBeInTheDocument();
    expect(posts.at(-1).body.hiddenCards).toEqual({});
  });
});
