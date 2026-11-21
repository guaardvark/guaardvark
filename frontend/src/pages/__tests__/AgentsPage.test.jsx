import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import AgentsPage from "../AgentsPage";
import * as agentsService from "../../api/agentsService";

vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));

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

vi.mock("../../contexts/StatusContext", () => ({
  useStatus: () => ({ activeModel: "chat:latest", isLoadingModel: false, modelError: null }),
}));

vi.mock("../../api/modelService", () => ({
  getAvailableModels: vi.fn(async () => [{ name: "chat:latest" }, { name: "small:latest" }]),
}));

vi.mock("../../api/orchestratorService", () => ({
  createPlan: vi.fn(async () => ({ success: true, plan: { steps: [] } })),
}));

vi.mock("../../api/agentsService", () => ({
  getAgents: vi.fn(),
  getAgent: vi.fn(),
  updateAgent: vi.fn(),
  toggleAgent: vi.fn(),
  resetAgent: vi.fn(),
  executeAgent: vi.fn(),
}));

const EDITABLE = ["enabled", "max_iterations", "system_prompt", "model"];

const agent = (over) => ({
  agent_type: "general_assistant",
  enabled: true,
  max_iterations: 8,
  model: null,
  tools: ["web_search", "fetch_url"],
  tools_missing: [],
  unavailable_reason: null,
  overridden: [],
  editable: EDITABLE,
  system_prompt: "Search the web.",
  ...over,
});

const AGENTS = [
  agent({ id: "research_agent", name: "Web Research Agent", group: "web", summary: "Searches and reads the web",
          description: "Answers questions from the web." }),
  agent({ id: "code_assistant", name: "Code Assistant", group: "create", summary: "Finds, reads and edits code",
          description: "Works on code.", overridden: ["max_iterations"], max_iterations: 4 }),
  agent({ id: "orchestrator_agent", name: "Task Orchestrator", group: "routing", agent_type: "orchestrator",
          summary: "Splits a request across agents", description: "Plans a request.", system_prompt: "",
          tools: ["delegate_task"], editable: ["enabled"] }),
];

const detail = (a) => ({
  ...a,
  tools_detail: a.tools.map((name) => ({ name, description: `${name} does a thing.`, requires_approval: false,
                                         installed: true })),
});

function renderPage() {
  return render(
    <ThemeProvider theme={createTheme({ palette: { mode: "dark" } })}>
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const tile = (id) => screen.getByTestId(`agent-tile-${id}`);
const rightClick = (id) => fireEvent.contextMenu(tile(id), { clientX: 20, clientY: 20 });

describe("AgentsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.ResizeObserver = class {
      observe() {}
      disconnect() {}
    };
    // The board finds no saved arrangement and shows its default.
    global.fetch = vi.fn(async () => ({ ok: false, status: 404, statusText: "NOT FOUND", json: async () => ({}) }));
    agentsService.getAgents.mockResolvedValue({ success: true, agents: AGENTS });
    agentsService.getAgent.mockImplementation(async (id) => ({
      success: true,
      agent: detail(AGENTS.find((a) => a.id === id)),
    }));
  });

  it("shows grouped tiles with no chips or switches", async () => {
    const { container } = renderPage();
    expect(await screen.findByText("Web Research Agent")).toBeInTheDocument();
    for (const label of ["Create", "Web", "Routing"]) expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText("Searches and reads the web")).toBeInTheDocument();
    expect(within(tile("code_assistant")).getByText("2 tools · max 4 · edited")).toBeInTheDocument();
    expect(container.querySelector(".MuiSwitch-root")).toBeNull();
    expect(container.querySelector(".MuiChip-root")).toBeNull();
  });

  it("keeps the exact setting out of the tile and shows it in the editor", async () => {
    const desktop = agent({
      id: "desktop_automation", name: "Desktop Automation", group: "computer", summary: "Drives the desktop",
      description: "Mouse and keyboard on this computer.", unavailable_reason: "Off by default for safety",
      unavailable_detail: "Set GUAARDVARK_DESKTOP_AUTOMATION=true in Guaardvark's .env file and restart to turn it on.",
    });
    agentsService.getAgents.mockResolvedValue({ success: true, agents: [...AGENTS, desktop] });
    agentsService.getAgent.mockResolvedValue({ success: true, agent: detail(desktop) });
    renderPage();
    const tileEl = await screen.findByTestId("agent-tile-desktop_automation");
    expect(within(tileEl).getByText("Off by default for safety")).toBeInTheDocument();
    expect(within(tileEl).queryByText(/GUAARDVARK_DESKTOP_AUTOMATION/)).toBeNull();
    fireEvent.click(within(tileEl).getByRole("button"));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/GUAARDVARK_DESKTOP_AUTOMATION=true/)).toBeInTheDocument();
  });

  it("opens the editor on click", async () => {
    renderPage();
    fireEvent.click(within(await screen.findByTestId("agent-tile-research_agent")).getByRole("button"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(agentsService.getAgent).toHaveBeenCalledWith("research_agent");
    expect(await screen.findByLabelText("Instructions")).toHaveValue("Search the web.");
    expect(screen.getByText("research_agent")).toBeInTheDocument();
  });

  it("offers the agent's actions on right-click", async () => {
    renderPage();
    await screen.findByText("Web Research Agent");
    rightClick("research_agent");
    const names = screen.getAllByRole("menuitem").map((item) => item.textContent);
    expect(names).toEqual(["Edit", "Test", "Disable", "Copy id", "Reset to default"]);
  });

  it("the Test dialog says a run is real for an agent and plan-only for the orchestrator", async () => {
    renderPage();
    await screen.findByText("Web Research Agent");
    rightClick("research_agent");
    fireEvent.click(screen.getByRole("menuitem", { name: "Test" }));
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/really runs the agent/)).toBeInTheDocument();
    expect(within(dialog).getByText(/may create files/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    rightClick("orchestrator_agent");
    fireEvent.click(screen.getByRole("menuitem", { name: "Test" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/the steps are not run/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/really runs the agent/)).toBeNull();
  });

  it("Disable calls toggleAgent", async () => {
    agentsService.toggleAgent.mockResolvedValue({ success: true, enabled: false,
                                                  agent: { ...AGENTS[0], enabled: false } });
    renderPage();
    await screen.findByText("Web Research Agent");
    rightClick("research_agent");
    fireEvent.click(screen.getByRole("menuitem", { name: "Disable" }));
    await waitFor(() => expect(agentsService.toggleAgent).toHaveBeenCalledWith("research_agent"));
    expect(await within(tile("research_agent")).findByText("Off")).toBeInTheDocument();
  });

  it("Copy id writes the id to the clipboard", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderPage();
    await screen.findByText("Web Research Agent");
    rightClick("research_agent");
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy id" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("research_agent"));
  });

  it("Copy id still copies when there is no navigator.clipboard (plain http on the LAN)", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    const execCommand = vi.fn(() => true);
    document.execCommand = execCommand;
    try {
      renderPage();
      await screen.findByText("Web Research Agent");
      rightClick("research_agent");
      fireEvent.click(screen.getByRole("menuitem", { name: "Copy id" }));
      await waitFor(() => expect(execCommand).toHaveBeenCalledWith("copy"));
      expect(await screen.findByText("Copied research_agent")).toBeInTheDocument();
      expect(screen.queryByText("Could not copy to the clipboard")).toBeNull();
    } finally {
      delete document.execCommand;
    }
  });

  it("Reset is disabled for an unedited agent", async () => {
    renderPage();
    await screen.findByText("Web Research Agent");
    rightClick("research_agent");
    expect(screen.getByRole("menuitem", { name: "Reset to default" })).toHaveAttribute("aria-disabled", "true");
  });

  it("Reset asks first, then calls resetAgent", async () => {
    agentsService.resetAgent.mockResolvedValue({ success: true, agent: { ...AGENTS[1], overridden: [],
                                                                           max_iterations: 15 } });
    renderPage();
    await screen.findByText("Code Assistant");
    rightClick("code_assistant");
    fireEvent.click(screen.getByRole("menuitem", { name: "Reset to default" }));
    expect(agentsService.resetAgent).not.toHaveBeenCalled();
    const confirm = await screen.findByRole("dialog");
    expect(within(confirm).getByText("Max iterations")).toBeInTheDocument();
    fireEvent.click(within(confirm).getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(agentsService.resetAgent).toHaveBeenCalledWith("code_assistant"));
    expect(await within(tile("code_assistant")).findByText("2 tools · max 15")).toBeInTheDocument();
  });

  it("shows the orchestrator's prompt as not editable", async () => {
    renderPage();
    fireEvent.click(within(await screen.findByTestId("agent-tile-orchestrator_agent")).getByRole("button"));
    expect(await screen.findByText(/nothing to edit here/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Instructions")).toBeNull();
    expect(screen.queryByLabelText("Max iterations")).toBeNull();
    expect(screen.getByRole("button", { name: "Reset to default" })).toBeDisabled();
  });

  it("Reset from inside the editor says it reset, not that it updated", async () => {
    agentsService.resetAgent.mockResolvedValue({ success: true, agent: detail({ ...AGENTS[1], overridden: [],
                                                                                  max_iterations: 15 }) });
    renderPage();
    fireEvent.click(within(await screen.findByTestId("agent-tile-code_assistant")).getByRole("button"));
    await screen.findByLabelText("Max iterations");
    fireEvent.click(screen.getByRole("button", { name: "Reset to default" }));
    const confirm = (await screen.findAllByRole("dialog")).at(-1);
    fireEvent.click(within(confirm).getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(agentsService.resetAgent).toHaveBeenCalledWith("code_assistant"));
    expect(await screen.findByText("Code Assistant reset to default")).toBeInTheDocument();
    expect(screen.queryByText("Agent updated")).not.toBeInTheDocument();
  });

  it("Save from the editor says the agent was updated", async () => {
    agentsService.updateAgent.mockResolvedValue({ success: true, agent: detail({ ...AGENTS[0], max_iterations: 12 }) });
    renderPage();
    fireEvent.click(within(await screen.findByTestId("agent-tile-research_agent")).getByRole("button"));
    fireEvent.change(await screen.findByLabelText("Max iterations"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Agent updated")).toBeInTheDocument();
  });

  it("saves only what changed", async () => {
    agentsService.updateAgent.mockResolvedValue({ success: true, agent: detail({ ...AGENTS[0], max_iterations: 12 }) });
    renderPage();
    fireEvent.click(within(await screen.findByTestId("agent-tile-research_agent")).getByRole("button"));
    fireEvent.change(await screen.findByLabelText("Max iterations"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(agentsService.updateAgent).toHaveBeenCalledWith("research_agent", { max_iterations: 12 }));
  });
});
