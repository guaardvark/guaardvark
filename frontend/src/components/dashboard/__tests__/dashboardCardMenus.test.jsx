import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("../../../api", async () => {
  const actual = await vi.importActual("../../../api");
  return {
    ...actual,
    getProjects: vi.fn(async () => [{ id: 7, name: "Launch", client: { name: "Acme" } }]),
    getWebsites: vi.fn(async () => [{ id: 3, url: "https://example.com", project: { name: "Launch" } }]),
  };
});

import ProjectManagerCard from "../ProjectManagerCard";
import WebsiteDataCard from "../WebsiteDataCard";

describe("dashboard card row menus", () => {
  beforeEach(() => navigate.mockClear());

  it("a project row opens its own menu, not the card's", async () => {
    render(<ProjectManagerCard id="project" contextMenuActions={[]} onHideCard={() => {}} />);
    const row = await screen.findByText("Launch");
    fireEvent.contextMenu(row, { clientX: 30, clientY: 30 });
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual([
      "Open",
      "Edit…",
      "Files",
      "Schedule Task",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Schedule Task" }));
    expect(navigate).toHaveBeenLastCalledWith("/tasks?project_id=7&new=1");
  });

  it("the card menu still opens from the card's own space", async () => {
    render(<ProjectManagerCard id="project" contextMenuActions={[]} onHideCard={() => {}} />);
    await screen.findByText("Launch");
    fireEvent.contextMenu(screen.getByText("Project Manager"), { clientX: 30, clientY: 30 });
    const labels = screen.getAllByRole("menuitem").map((el) => el.textContent);
    expect(labels).toContain("Hide card");
    expect(labels).toContain("Refresh");
  });

  it("a website row reaches the edit dialog", async () => {
    render(<WebsiteDataCard id="website" contextMenuActions={[]} />);
    const urlText = await screen.findByText("https://example.com");
    fireEvent.contextMenu(urlText.closest("li"), { clientX: 30, clientY: 30 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit…" }));
    await waitFor(() => expect(screen.getByText(/Edit Website: https:\/\/example.com/)).toBeInTheDocument());
  });
});
