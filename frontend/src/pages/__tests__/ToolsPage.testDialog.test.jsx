import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const executeTool = vi.fn(async () => ({ success: true, result: { success: true, output: "ran" } }));

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
vi.mock("../../api/toolsService", () => ({
  getTools: vi.fn(async () => ({
    success: true,
    count: 2,
    tools: [
      {
        name: "system_command",
        description: "Run a shell command",
        requires_approval: true,
        parameters: {
          command: { type: "string", required: true, description: "shell line", default: null },
          timeout: { type: "int", required: false, description: "seconds", default: 30 },
        },
      },
      {
        name: "media_status",
        description: "What is playing",
        requires_approval: false,
        parameters: {},
      },
    ],
  })),
  executeTool: (...args) => executeTool(...args),
  getToolSchemas: vi.fn(async () => ({ success: true, schemas: "" })),
  getToolCategories: vi.fn(async () => ({
    success: true,
    categories: { system: ["system_command"], media: ["media_status"] },
  })),
}));
vi.mock("../../contexts/StatusContext", () => ({
  useStatus: () => ({ activeModel: "m", isLoadingModel: false, modelError: null }),
}));
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/common/LoadingStates", () => ({
  ContextualLoader: () => <div>loading</div>,
}));
vi.mock("../../components/common/ApiKeyRefusalNotice", () => ({
  ApiKeyRefusalAlert: ({ message }) => <div>{message}</div>,
}));

import ToolsPage from "../ToolsPage";

const WARNING = /runs this tool for real, right away, without the approval step/;

beforeEach(() => {
  executeTool.mockClear();
  localStorage.clear();
});

// A tool is named in the tool list and again under its category; the tests mean the list.
const toolTitle = async (toolName) =>
  (await screen.findAllByText(toolName)).find((el) => el.closest('[data-card-id="tools"]'));

const openTestFor = async (toolName) => {
  render(<MemoryRouter><ToolsPage /></MemoryRouter>);
  const title = await toolTitle(toolName);
  const card = title.closest(".MuiCard-root");
  fireEvent.click(within(card).getByRole("button", { name: /Test/ }));
  return screen.findByRole("dialog");
};

describe("ToolsPage test dialog", () => {
  it("says an approval-gated tool runs without chat's approval step and waits for required fields", async () => {
    const dialog = await openTestFor("system_command");
    expect(within(dialog).getByText(WARNING)).toBeInTheDocument();

    const execute = within(dialog).getByRole("button", { name: /Execute/ });
    expect(execute).toBeDisabled();
    expect(within(dialog).getByText(/Required\. shell line/)).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText(/command \*/), { target: { value: "ls" } });
    expect(within(dialog).queryByText(/Required\. shell line/)).toBeNull();
    expect(execute).not.toBeDisabled();

    fireEvent.click(execute);
    await waitFor(() => expect(executeTool).toHaveBeenCalledTimes(1));
    expect(executeTool).toHaveBeenCalledWith("system_command", { command: "ls", timeout: 30 });
  });

  it("shows no warning for a tool chat runs without asking, and lets it run at once", async () => {
    const dialog = await openTestFor("media_status");
    expect(within(dialog).queryByText(WARNING)).toBeNull();
    expect(within(dialog).getByRole("button", { name: /Execute/ })).not.toBeDisabled();
  });

  it("labels a tool with the category the registry gave it", async () => {
    render(<MemoryRouter><ToolsPage /></MemoryRouter>);
    const title = await toolTitle("media_status");
    expect(within(title.closest(".MuiCard-root")).getByText("media")).toBeInTheDocument();
  });
});
