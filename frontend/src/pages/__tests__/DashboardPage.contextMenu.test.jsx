import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Every card is the real DashboardCardWrapper with a stub body, so the test
// exercises the props the page hands each card.
const { makeCard } = vi.hoisted(() => ({
  makeCard: async (title) => {
    const ReactMod = (await import("react")).default;
    const Wrapper = (await import("../../components/dashboard/DashboardCardWrapper")).default;
    const Card = ReactMod.forwardRef((props, ref) =>
      ReactMod.createElement(Wrapper, { ref, title, ...props }, ReactMod.createElement("div", null, `${title} body`)),
    );
    return { default: Card };
  },
}));

vi.mock("../../components/dashboard/ProjectManagerCard", () => makeCard("Project Manager"));
vi.mock("../../components/dashboard/WebsiteDataCard", () => makeCard("Website Data"));
vi.mock("../../components/dashboard/TaskManagerCard", () => makeCard("Tasks"));
vi.mock("../../components/dashboard/SemanticSearchCard", () => makeCard("Chat"));
vi.mock("../../components/dashboard/ClientsDashboardCard", () => makeCard("Clients"));
vi.mock("../../components/dashboard/CSVGenerationCard", () => makeCard("CSV Generation"));
vi.mock("../../components/dashboard/CodeGenerationCard", () => makeCard("Code Generation"));
vi.mock("../../components/dashboard/ImageGenerationCard", () => makeCard("Image Generation"));
vi.mock("../../components/dashboard/VideoGenerationCard", () => makeCard("Videos"));
vi.mock("../../components/dashboard/ApprovalsCard", () => makeCard("Waiting for You"));
vi.mock("../../components/dashboard/FileManagerCard", () => makeCard("File Manager"));
vi.mock("../../components/dashboard/FamilySelfImprovementCard", () => makeCard("Family & Self-improvement"));
vi.mock("../../components/dashboard/RAGAutoresearchCard", () => makeCard("RAG Autoresearch"));
vi.mock("../../components/dashboard/GpuStatusCard", () => makeCard("GPU Memory"));

vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../contexts/StatusContext", () => ({
  useStatus: () => ({ activeModel: "test-model", isLoadingModel: false, modelError: null }),
}));
vi.mock("../../stores/useAppStore", () => ({
  useAppStore: (sel) => sel({ systemName: "Dashboard" }),
}));
// One settings object: the page memoises its layouts on it, and a fresh object
// per render would refetch the saved state forever.
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
      cardMinGridH: 18,
    },
  };
  return { useDashboardWidth: () => 1600, useLayout: () => layout };
});

import DashboardPage from "../DashboardPage";

let saved;
let posts;

beforeEach(() => {
  posts = [];
  global.ResizeObserver = class {
    observe() {}
    disconnect() {}
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
      <DashboardPage />
    </MemoryRouter>,
  );

describe("DashboardPage right-click", () => {
  it("hides a card from its menu and saves it", async () => {
    saved = { layout: [], cardColors: {}, layoutMode: "normal" };
    renderPage();
    fireEvent.contextMenu(await screen.findByText("Tasks body"), { clientX: 40, clientY: 40 });
    expect(screen.getAllByRole("menu")).toHaveLength(1);
    fireEvent.click(screen.getByRole("menuitem", { name: "Hide card" }));

    await waitFor(() => expect(screen.queryByText("Tasks body")).not.toBeInTheDocument());
    expect(posts.at(-1).hiddenCards).toEqual({ tasks: true });
    expect(screen.getByText("Chat body")).toBeInTheDocument();
  });

  it("shows hidden cards again from the empty grid's menu", async () => {
    saved = { layout: [], cardColors: {}, layoutMode: "normal", hiddenCards: { gpu: true, tasks: true } };
    const { container } = renderPage();
    await screen.findByText("Chat body");
    expect(screen.queryByText("GPU Memory body")).not.toBeInTheDocument();

    fireEvent.contextMenu(container.querySelector(".layout"), { clientX: 5, clientY: 900 });
    const labels = screen.getAllByRole("menuitem").map((el) => el.textContent);
    expect(labels).toEqual([
      "Cycle layout (next: Compact)",
      "Reset layout…",
      "Show GPU Memory",
      "Show Tasks",
      "Show all hidden cards",
      "Sticky Notes",
    ]);

    fireEvent.click(screen.getByRole("menuitem", { name: "Show GPU Memory" }));
    await screen.findByText("GPU Memory body");
    expect(screen.queryByText("Tasks body")).not.toBeInTheDocument();
    expect(posts.at(-1).hiddenCards).toEqual({ tasks: true });
  });

  it("asks before resetting the layout", async () => {
    saved = { layout: [], cardColors: { chat: "#336699" }, layoutMode: "normal", hiddenCards: { tasks: true } };
    const { container } = renderPage();
    await screen.findByText("Chat body");
    fireEvent.contextMenu(container.querySelector(".layout"), { clientX: 5, clientY: 900 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Reset layout…" }));
    expect(posts).toHaveLength(0);

    fireEvent.click(await screen.findByRole("button", { name: "Reset layout" }));
    await screen.findByText("Tasks body");
    expect(posts.at(-1)).toMatchObject({ cardColors: {}, hiddenCards: {}, layoutMode: "normal" });
  });

  it("the Dashboard Cards button opens the same menu as the empty grid", async () => {
    saved = { layout: [], cardColors: {}, layoutMode: "normal", hiddenCards: { gpu: true } };
    renderPage();
    await screen.findByText("Chat body");
    fireEvent.click(screen.getByRole("button", { name: "Dashboard Cards" }));
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual([
      "Cycle layout (next: Compact)",
      "Reset layout…",
      "Show GPU Memory",
      "Sticky Notes",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Show GPU Memory" }));
    await screen.findByText("GPU Memory body");
  });
});
