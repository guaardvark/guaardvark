import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

const TASKS = [
  { id: 11, name: "Write copy", status: "pending", type: "file_generation", client_id: 3, created_at: "2026-10-02T00:00:00" },
  { id: 12, name: "Crawl site", status: "completed", type: "file_generation", website_id: 5, created_at: "2026-10-01T00:00:00" },
  { id: 13, name: "Other work", status: "pending", type: "file_generation", created_at: "2026-09-30T00:00:00" },
];

// The page lays its cards out on the shared board, which reads the grid from the layout context.
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
vi.mock("../../api", () => ({
  cancelJob: vi.fn(),
  createTask: vi.fn(),
  deleteTask: vi.fn(),
  duplicateTask: vi.fn(),
  updateTask: vi.fn(),
  getAvailableModels: vi.fn(async () => []),
  getProjects: vi.fn(async () => [{ id: 7, name: "Launch" }]),
  getTasks: vi.fn(async () => TASKS),
  getClients: vi.fn(async () => [{ id: 3, name: "Acme" }]),
  getWebsite: vi.fn(async (id) => ({ id: Number(id), url: "https://example.com", client: { id: 3, name: "Acme" } })),
}));
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
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/cards/TaskCard", () => ({
  default: ({ task }) => <div>{task.name}</div>,
}));
vi.mock("../../components/modals/TaskActionModal", () => ({
  default: ({ open, taskData, defaults }) =>
    open ? (
      <div data-testid="task-modal">
        {taskData ? `edit:${taskData.name}` : `new:${JSON.stringify(defaults)}`}
      </div>
    ) : null,
}));

import TaskPage from "../TaskPage";

const Where = () => {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname + loc.search}</div>;
};

const renderAt = (url) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route
          path="/tasks"
          element={
            <>
              <TaskPage />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );

describe("TaskPage links", () => {
  it("opens the task named by taskId and drops the parameter", async () => {
    renderAt("/tasks?taskId=12");
    expect(await screen.findByTestId("task-modal")).toHaveTextContent("edit:Crawl site");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/tasks$/));
  });

  it("shows the client's tasks and starts a new one for that client", async () => {
    renderAt("/tasks?client_id=3&new=1");
    const modal = await screen.findByTestId("task-modal");
    expect(JSON.parse(modal.textContent.replace("new:", ""))).toEqual({ client_id: 3, client_name: "Acme" });
    expect(screen.getByText("Write copy")).toBeInTheDocument();
    expect(screen.queryByText("Crawl site")).not.toBeInTheDocument();
    expect(screen.queryByText("Other work")).not.toBeInTheDocument();
    expect(screen.getByText("Client: Acme")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/tasks?client_id=3"));
  });

  it("filters to a website and starts a task with that website picked", async () => {
    renderAt("/tasks?website_id=5&new=1");
    const modal = await screen.findByTestId("task-modal");
    expect(JSON.parse(modal.textContent.replace("new:", ""))).toEqual({ client_name: "Acme", website_id: 5 });
    expect(screen.getByText("Crawl site")).toBeInTheDocument();
    expect(screen.queryByText("Write copy")).not.toBeInTheDocument();
  });

  it("clearing the filter chip shows every task again", async () => {
    renderAt("/tasks?website_id=5");
    const chip = await screen.findByText("Website: https://example.com");
    expect(screen.queryByText("Other work")).not.toBeInTheDocument();
    fireEvent.click(chip.parentElement.querySelector("svg"));
    expect(await screen.findByText("Other work")).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/tasks$/);
  });

  it("keeps the project filter it already had", async () => {
    const { getTasks } = await import("../../api");
    renderAt("/tasks?project_id=7&new=1");
    const modal = await screen.findByTestId("task-modal");
    expect(JSON.parse(modal.textContent.replace("new:", ""))).toEqual({ project_id: 7 });
    expect(getTasks).toHaveBeenLastCalledWith("7");
    expect(await screen.findByText("Project: Launch")).toBeInTheDocument();
  });
});
