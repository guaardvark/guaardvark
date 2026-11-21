import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
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

let saved;
let posts;

beforeEach(() => {
  posts = [];
  global.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  saved = {
    notes: {
      a: { title: "Alpha", content: "first" },
      b: { title: "Beta", content: "second" },
      c: { title: "Gamma", content: "third" },
    },
    layout: [
      { i: "a", x: 0, y: 0, w: 35, h: 35 },
      { i: "b", x: 40, y: 0, w: 35, h: 35 },
      { i: "c", x: 80, y: 40, w: 35, h: 20 },
    ],
    noteColors: {},
    minimizedCards: {},
    pinnedNotes: {},
    closedNotes: {},
    layoutMode: "normal",
  };
  global.fetch = vi.fn(async (url, opts = {}) => {
    if ((opts.method || "GET") === "POST") {
      posts.push(JSON.parse(opts.body));
      return { ok: true, json: async () => ({}) };
    }
    return { ok: true, json: async () => saved };
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

const barOf = (name) => screen.getByText(name).closest(".card-header-buttons");

// The card frame reads a double-click as two presses in quick succession,
// because each press on the bar may also be the start of a drag.
const doublePress = (el) => {
  fireEvent.mouseDown(el);
  fireEvent.mouseUp(el);
  fireEvent.mouseDown(el);
  fireEvent.mouseUp(el);
};

describe("Notes title bar double-click", () => {
  it("minimizes an open note from the bar and expands it again from anywhere on the bar", async () => {
    renderPage();
    await screen.findByText("Alpha");

    doublePress(barOf("Alpha"));
    await waitFor(() => expect(posts.at(-1)?.minimizedCards).toEqual({ a: true }));

    // On the minimized bar the name expands too; it does not open the name box.
    doublePress(screen.getByText("Alpha"));
    await waitFor(() => expect(posts.at(-1)?.minimizedCards?.a).toBeFalsy());
    expect(screen.queryByDisplayValue("Alpha")).not.toBeInTheDocument();
  });

  it("renames an open note when its name is double-clicked, without minimizing it", async () => {
    renderPage();
    doublePress(await screen.findByText("Beta"));
    const box = await screen.findByDisplayValue("Beta");
    expect(posts.some((p) => p.minimizedCards?.b)).toBe(false);

    fireEvent.change(box, { target: { value: "Beta two" } });
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.blur(box);
    await waitFor(() => expect(posts.at(-1)?.notes?.b?.title).toBe("Beta two"));
    expect(screen.getByText("Beta two")).toBeInTheDocument();
  });

  it("leaves the name alone when the name box is closed with Escape", async () => {
    renderPage();
    doublePress(await screen.findByText("Gamma"));
    const box = await screen.findByDisplayValue("Gamma");
    fireEvent.change(box, { target: { value: "Something else" } });
    fireEvent.keyDown(box, { key: "Escape" });
    expect(await screen.findByText("Gamma")).toBeInTheDocument();
    expect(posts.some((p) => p.notes?.c?.title === "Something else")).toBe(false);
  });

  it("closes a note from the button on its bar", async () => {
    renderPage();
    await screen.findByText("Alpha");
    fireEvent.click(within(barOf("Alpha")).getByRole("button", { name: "Close note" }));
    await waitFor(() => expect(posts.at(-1)?.closedNotes?.a?.title).toBe("Alpha"));
    expect(posts.at(-1).notes.a).toBeUndefined();
  });
});
