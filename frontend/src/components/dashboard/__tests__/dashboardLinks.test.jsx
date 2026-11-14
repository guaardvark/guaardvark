import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

vi.mock("../../../api", () => ({
  getProjects: vi.fn(async () => [{ id: 7, name: "Launch" }]),
  getClients: vi.fn(async () => [{ id: 3, name: "Acme", project_count: 1 }]),
  getWebsites: vi.fn(async () => [{ id: 5, url: "https://example.com", document_count: 2 }]),
  getTasks: vi.fn(async () => [{ id: 11, name: "Write copy", status: "pending", created_at: "2026-10-01T00:00:00" }]),
  createTask: vi.fn(),
  createWebsite: vi.fn(),
  updateWebsite: vi.fn(),
  deleteWebsite: vi.fn(),
}));
vi.mock("../../modals/WebsiteActionModal", () => ({ default: () => null }));

import ProjectManagerCard from "../ProjectManagerCard";
import ClientsDashboardCard from "../ClientsDashboardCard";
import WebsiteDataCard from "../WebsiteDataCard";
import TaskManagerCard from "../TaskManagerCard";
import ImageGenerationCard from "../ImageGenerationCard";
import { SnackbarProvider } from "../../common/SnackbarProvider";
import {
  entityFilesFilter,
  entityFilesPath,
  entityLinkActions,
  scheduleTaskPath,
  taskPath,
} from "../../../utils/entityLinks";

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

const rightClick = (el) => fireEvent.contextMenu(el, { clientX: 20, clientY: 20 });
const menuLabels = () => screen.getAllByRole("menuitem").map((el) => el.textContent);
const pick = (label) => fireEvent.click(screen.getByRole("menuitem", { name: label }));

describe("entity links", () => {
  it("send Files to the page that lists that entity's documents", () => {
    expect(entityFilesPath("project", 7)).toBe("/projects/7?tab=documents");
    expect(entityFilesPath("client", 3)).toBe("/documents?client_id=3");
    expect(entityFilesPath("website", 5)).toBe("/documents?website_id=5");
    expect(entityFilesPath("task", 5)).toBeNull();
    expect(entityLinkActions("client", 3, () => {}).map((a) => a.label)).toEqual(["Files", "Schedule Task"]);
  });

  it("let the Files page read back which client or website it was asked for", () => {
    expect(entityFilesFilter(new URLSearchParams("client_id=3"))).toEqual({ kind: "client", id: 3 });
    expect(entityFilesFilter(new URLSearchParams("website_id=5"))).toEqual({ kind: "website", id: 5 });
    expect(entityFilesFilter(new URLSearchParams(""))).toBeNull();
    expect(entityFilesFilter(new URLSearchParams("client_id=abc"))).toBeNull();
  });

  it("send Schedule Task and Open to parameters the Tasks page reads", () => {
    expect(scheduleTaskPath("project", 7)).toBe("/tasks?project_id=7&new=1");
    expect(scheduleTaskPath("client", 3)).toBe("/tasks?client_id=3&new=1");
    expect(scheduleTaskPath("website", 5)).toBe("/tasks?website_id=5&new=1");
    expect(taskPath(11)).toBe("/tasks?taskId=11");
  });
});

describe("dashboard card links", () => {
  it("Project row: Files opens the project's Documents tab", async () => {
    renderCard(<ProjectManagerCard id="project" />);
    rightClick(await screen.findByText("Launch"));
    expect(menuLabels()).toEqual(["Open", "Edit…", "Files", "Schedule Task"]);
    pick("Files");
    expect(screen.getByTestId("where")).toHaveTextContent("/projects/7?tab=documents");
  });

  it("Project row: Schedule Task starts a task for the project", async () => {
    renderCard(<ProjectManagerCard id="project" />);
    rightClick(await screen.findByText("Launch"));
    pick("Schedule Task");
    expect(screen.getByTestId("where")).toHaveTextContent("/tasks?project_id=7&new=1");
  });

  it("Client row: Files lists the client's documents; Schedule Task carries the client", async () => {
    const first = renderCard(<ClientsDashboardCard id="clients" />);
    rightClick(await screen.findByText("Acme"));
    expect(menuLabels()).toEqual(["Open", "Files", "Schedule Task"]);
    pick("Files");
    expect(screen.getByTestId("where")).toHaveTextContent("/documents?client_id=3");
    first.unmount();

    renderCard(<ClientsDashboardCard id="clients" />);
    rightClick(await screen.findByText("Acme"));
    pick("Schedule Task");
    expect(screen.getByTestId("where")).toHaveTextContent("/tasks?client_id=3&new=1");
  });

  it("Website row: Files lists the website's documents; Schedule Task carries the website", async () => {
    const first = renderCard(<WebsiteDataCard id="website" />);
    rightClick(await screen.findByText(/Docs: 2/));
    pick("Files");
    expect(screen.getByTestId("where")).toHaveTextContent("/documents?website_id=5");
    first.unmount();

    renderCard(<WebsiteDataCard id="website" />);
    rightClick(await screen.findByText(/Docs: 2/));
    pick("Schedule Task");
    expect(screen.getByTestId("where")).toHaveTextContent("/tasks?website_id=5&new=1");
  });

  it("Task row opens that task on the Tasks page", async () => {
    renderCard(<TaskManagerCard id="tasks" />);
    fireEvent.click(await screen.findByText("Write copy"));
    expect(screen.getByTestId("where")).toHaveTextContent("/tasks?taskId=11");
  });

  it("Image Generation: New Images and Batch Mode open the Image Gen page", () => {
    const first = renderCard(<ImageGenerationCard id="imggen" />);
    fireEvent.click(screen.getByRole("button", { name: "New Images" }));
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/batch-images$/);
    first.unmount();

    renderCard(<ImageGenerationCard id="imggen" />);
    fireEvent.click(screen.getByRole("button", { name: "Batch Mode" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/batch-images?mode=bulk");
  });

  it("Image Generation card menu: Batch Mode goes to bulk input", () => {
    renderCard(<ImageGenerationCard id="imggen" />);
    rightClick(screen.getByText("Image Generation"));
    expect(menuLabels()).toEqual(expect.arrayContaining(["Open page", "New Images", "Batch Mode"]));
    pick("Batch Mode");
    expect(screen.getByTestId("where")).toHaveTextContent("/batch-images?mode=bulk");
  });

  it("Image Generation card menu: Open page is the Image Gen page", () => {
    renderCard(<ImageGenerationCard id="imggen" />);
    rightClick(screen.getByText("Image Generation"));
    pick("Open page");
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/batch-images$/);
  });
});
