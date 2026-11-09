import React from "react";
import ReactDOM from "react-dom";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  getClients: vi.fn(async () => [{ id: 3, name: "Acme", project_count: 0 }]),
  getProjects: vi.fn(async () => [{ id: 7, name: "Launch", description: "" }]),
  getWebsites: vi.fn(async () => [{ id: 5, url: "https://example.com" }]),
  getTasks: vi.fn(async () => [
    { id: 11, name: "Write copy", status: "pending", type: "file_generation", created_at: "2026-10-01T00:00:00" },
  ]),
  getAvailableModels: vi.fn(async () => []),
  getWebsite: vi.fn(),
  getCurrentlyLinkedItems: vi.fn(async () => []),
  getProjectsForClient: vi.fn(async () => []),
  createClient: vi.fn(),
  updateClient: vi.fn(),
  deleteClient: vi.fn(),
  uploadClientLogo: vi.fn(),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
  createWebsite: vi.fn(),
  updateWebsite: vi.fn(),
  deleteWebsite: vi.fn(),
  cancelJob: vi.fn(),
  createTask: vi.fn(),
  deleteTask: vi.fn(),
  duplicateTask: vi.fn(),
  updateTask: vi.fn(),
}));
vi.mock("../../api/wordpressService", () => ({ getWordPressSites: vi.fn(async () => ({ success: true, data: [] })) }));
vi.mock("../../api/websiteService", () => ({ scrapeWebsite: vi.fn() }));
vi.mock("../../api/jobsService", () => ({
  listJobs: vi.fn(async () => ({ jobs: [] })),
  cancelJob: vi.fn(),
  JOB_KINDS: { VIDEO_GEN: "video_gen" },
}));
vi.mock("../../api/taskService", () => ({ processTaskQueue: vi.fn() }));
vi.mock("../../contexts/StatusContext", () => ({
  useStatus: () => ({ activeModel: "m", isLoadingModel: false, modelError: null }),
}));
vi.mock("../../contexts/UnifiedProgressContext", () => ({ useUnifiedProgress: () => ({}) }));
vi.mock("../../stores/useAppStore", () => ({
  useAppStore: (select) => select({ projects: [], setProjects: () => {} }),
}));
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/common/ProjectStateErrorBoundary", () => ({ default: ({ children }) => children }));
vi.mock("../../components/modals/IndexingDialog", () => ({ default: () => null }));
vi.mock("../../components/modals/LinkingModal", () => ({ default: () => null }));
vi.mock("../../components/cards/TaskCard", () => ({ default: ({ task }) => <div>{task.name}</div> }));

// Edit dialogs render in a portal, as the real MUI dialogs do.
const { portalDialog } = vi.hoisted(() => ({
  portalDialog: (label) => ({ open }) =>
    open ? ReactDOM.createPortal(<div role="dialog"><input aria-label={label} /></div>, document.body) : null,
}));
vi.mock("../../components/modals/ClientActionModal", () => ({ default: portalDialog("Client name") }));
vi.mock("../../components/modals/WebsiteActionModal", () => ({ default: portalDialog("Website URL") }));
vi.mock("../../components/modals/TaskActionModal", () => ({ default: portalDialog("Task name") }));

import ClientPage from "../ClientPage";
import ProjectsPage from "../ProjectsPage";
import WebsitesPage from "../WebsitesPage";
import TaskPage from "../TaskPage";

const renderPage = (page) => render(<MemoryRouter>{page}</MemoryRouter>);
const rightClick = (el) => fireEvent.contextMenu(el, { clientX: 30, clientY: 30 });
const menuOpen = () => screen.queryByRole("menu") !== null;

afterEach(() => {
  window.getSelection()?.removeAllRanges();
});

describe("entity pages keep the browser menu where it is needed", () => {
  it("Clients: right-click in the edit dialog's field is the browser's", async () => {
    renderPage(<ClientPage />);
    rightClick(await screen.findByText("Acme"));
    expect(menuOpen()).toBe(true);
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const field = await screen.findByLabelText("Client name");
    expect(rightClick(field)).toBe(true);
    expect(menuOpen()).toBe(false);
  });

  it("Projects: right-click in the edit dialog's field and on selected text is the browser's", async () => {
    renderPage(<ProjectsPage />);
    const name = await screen.findByText("Launch");

    const range = document.createRange();
    range.selectNodeContents(name);
    window.getSelection().addRange(range);
    expect(rightClick(name)).toBe(true);
    expect(menuOpen()).toBe(false);
    window.getSelection().removeAllRanges();

    rightClick(name);
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const field = (await screen.findAllByRole("textbox"))[0];
    expect(rightClick(field)).toBe(true);
    expect(screen.queryByRole("menuitem", { name: "Schedule Task" })).not.toBeInTheDocument();
  });

  it("Websites: right-click in the edit dialog's field is the browser's", async () => {
    renderPage(<WebsitesPage />);
    rightClick(await screen.findByText("https://example.com"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const field = await screen.findByLabelText("Website URL");
    expect(rightClick(field)).toBe(true);
    expect(menuOpen()).toBe(false);
  });

  it("Tasks: right-click in the task dialog's field and the type filter is the browser's", async () => {
    renderPage(<TaskPage />);
    rightClick(await screen.findByText("Write copy"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const field = await screen.findByLabelText("Task name");
    expect(rightClick(field)).toBe(true);
    expect(menuOpen()).toBe(false);

    const typeFilter = screen.getByTestId("task-type-filter");
    expect(rightClick(typeFilter)).toBe(true);
    expect(menuOpen()).toBe(false);
  });

  it("Tasks: right-click on the page itself still opens the page menu", async () => {
    renderPage(<TaskPage />);
    await screen.findByText("Write copy");
    expect(rightClick(screen.getByText("Video Batch Jobs"))).toBe(false);
    expect(screen.getByRole("menuitem", { name: "New Custom Job" })).toBeInTheDocument();
  });
});
