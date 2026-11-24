import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/notes/ClosedNotesDrawer", () => ({ default: () => null }));
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
  return { useDashboardWidth: () => 1600, useLayout: () => layout };
});

import StickyNotesPage from "../StickyNotesPage";

let posts;
let readFails;

beforeEach(() => {
  posts = [];
  readFails = true;
  global.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  global.fetch = vi.fn(async (url, opts = {}) => {
    if ((opts.method || "GET") === "POST") {
      posts.push(JSON.parse(opts.body));
      return { ok: true, json: async () => ({}) };
    }
    if (readFails) {
      return { ok: false, status: 500, statusText: "Internal Server Error", json: async () => ({}) };
    }
    return {
      ok: true,
      json: async () => ({
        notes: { a: { title: "Alpha", content: "kept" } },
        layout: [{ i: "a", x: 0, y: 0, w: 35, h: 35 }],
        layoutMode: "normal",
      }),
    };
  });
});

afterEach(() => {
  delete global.ResizeObserver;
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <StickyNotesPage />
    </MemoryRouter>,
  );

describe("Notes board after a failed load", () => {
  it("does not save the blank stand-in board over the stored notes", async () => {
    renderPage();
    const add = await screen.findByRole("button", { name: "Add note" });
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    fireEvent.click(add);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(posts).toEqual([]);
  });

  it("saves normally when the stored board was read", async () => {
    readFails = false;
    renderPage();
    await screen.findByText("Alpha");
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    await waitFor(() => expect(posts.length).toBeGreaterThan(0));
    expect(Object.keys(posts.at(-1).notes)).toContain("a");
  });
});
