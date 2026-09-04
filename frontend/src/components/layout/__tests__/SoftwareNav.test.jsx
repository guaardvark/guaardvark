import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import SoftwareNav from "../SoftwareNav";

// This test describes the core catalog; a distribution's brand.jsx may
// carry another one, so the brand is pinned to the core lists here.
vi.mock("../../../config/brand", async (importOriginal) => {
  const actual = await importOriginal();
  const { CORE_NAV_CATALOG, WORKSPACES } = await import("../../../config/navCatalog");
  const brand = { ...actual.default, navCatalog: CORE_NAV_CATALOG, workspaces: WORKSPACES };
  return { ...actual, brand, default: brand };
});

vi.mock("../../../hooks/usePendingApprovals", () => ({
  usePendingApprovals: () => ({ count: 0 }),
}));

vi.mock("../../modals/SystemMetricsModal", () => ({
  default: () => null,
}));

vi.mock("../../agent/AgentScreenViewer", () => ({
  default: () => null,
}));

function renderNav(path = "/batch-images") {
  return render(
    <ThemeProvider theme={createTheme()}>
      <MemoryRouter initialEntries={[path]}>
        <SoftwareNav />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("SoftwareNav", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows workspaces and the studio tool strip on a studio route", () => {
    renderNav("/batch-images");
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Studio" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("tablist", { name: "Workspace tools" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Image Gen/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /Video Gen/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Film Crew/ })).toBeInTheDocument();
  });

  it("hides the tool strip when the workspace has a single page", () => {
    renderNav("/code-editor");
    expect(screen.getByRole("button", { name: "Code" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("tablist", { name: "Workspace tools" })).not.toBeInTheDocument();
  });

  it("pins the catalog's actions and Settings on the right", () => {
    renderNav("/chat");
    expect(screen.getByRole("button", { name: "System Metrics" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Agent Screen" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });

  it("surfaces pages the sidebar does not list", () => {
    renderNav("/chat");
    expect(screen.getByRole("tab", { name: /Voice Chat/ })).toBeInTheDocument();
  });
});
