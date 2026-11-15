import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const { bulkApi, messages } = vi.hoisted(() => ({
  bulkApi: { startBulkImport: vi.fn(), getBulkImportStatus: vi.fn() },
  messages: [],
}));

vi.mock("../../api/apiClient", () => ({ BASE_URL: "/api" }));
vi.mock("../../api/bulkImportService", () => bulkApi);
vi.mock("../../api/clientService", () => ({ getClients: vi.fn(async () => []) }));
vi.mock("../../api/projectService", () => ({ getProjects: vi.fn(async () => []) }));
vi.mock("../../api/websiteService", () => ({ getWebsites: vi.fn(async () => []) }));
vi.mock("axios", () => ({
  default: {
    get: vi.fn(async () => ({ data: { data: { folders: [], documents: [] } } })),
    post: vi.fn(),
  },
}));
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/common/SnackbarProvider", () => {
  const api = { showMessage: (text, level) => messages.push({ text, level }) };
  return { useSnackbar: () => api };
});
// The shared folder picker, reduced to one button that picks /data/in.
vi.mock("../../components/common/DirectoryPicker", () => ({
  default: ({ open, onSelect, onClose }) =>
    open ? (
      <button
        type="button"
        onClick={() => {
          onSelect("/data/in");
          onClose();
        }}
      >
        pick /data/in
      </button>
    ) : null,
}));

import BulkImportDocumentsPage from "../BulkImportDocumentsPage";

const renderPage = () =>
  render(
    <MemoryRouter>
      <BulkImportDocumentsPage />
    </MemoryRouter>,
  );

const pickServerFolder = () => {
  fireEvent.click(screen.getByRole("button", { name: "Browse the Guaardvark machine…" }));
  fireEvent.click(screen.getByRole("button", { name: "pick /data/in" }));
};

const runFromMenu = async (name) => {
  fireEvent.click(screen.getByRole("button", { name: "More ways to import" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: new RegExp(`^${name}`) }));
};

beforeEach(() => {
  messages.length = 0;
  localStorage.clear();
  bulkApi.startBulkImport.mockReset();
  bulkApi.getBulkImportStatus.mockReset();
  global.fetch = vi.fn(async () => ({ json: async () => ({ roots: [], home: "/home/u" }) }));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("BulkImportDocumentsPage first screen", () => {
  it("offers a drop zone, a server folder, Into and Import, with Import off until a source is chosen", () => {
    renderPage();
    expect(screen.getByTestId("import-drop-zone")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Browse the Guaardvark machine…" })).toBeInTheDocument();
    expect(screen.getByLabelText("Into")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});

describe("BulkImportDocumentsPage server folder", () => {
  it("imports a copy into Imports straight away, with no confirmation", async () => {
    bulkApi.startBulkImport.mockResolvedValue({ job_id: "job-1", status: "processing" });
    renderPage();
    pickServerFolder();
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    await screen.findByTestId("import-status");
    expect(bulkApi.startBulkImport).toHaveBeenCalledWith(expect.objectContaining({
      source_path: "/data/in",
      target_folder: "Imports",
      target_folder_id: null,
      force_copy: true,
      dry_run: false,
      reindex_missing: true,
    }));
    expect(screen.queryByText("Move the files into Files?")).not.toBeInTheDocument();
  });

  it("Preview only sends a dry run that copies nothing", async () => {
    bulkApi.startBulkImport.mockResolvedValue({ job_id: "job-1", status: "processing" });
    renderPage();
    pickServerFolder();
    await runFromMenu("Preview only");
    await screen.findByTestId("import-status");
    expect(bulkApi.startBulkImport).toHaveBeenCalledWith(expect.objectContaining({ dry_run: true, force_copy: true }));
  });

  it("Import and move counts the files first, then asks with the real count", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    bulkApi.startBulkImport.mockResolvedValue({ job_id: "job-1", status: "processing" });
    bulkApi.getBulkImportStatus.mockResolvedValue({
      job_id: "job-1",
      status: "complete",
      message: "Dry run: Would import 2 files",
      stats: { total_files: 2, files: ["a.txt", "b.txt"], skipped_private: { hidden_files: 1 } },
    });
    renderPage();
    pickServerFolder();
    await runFromMenu("Import and move");
    expect(bulkApi.startBulkImport).toHaveBeenLastCalledWith(expect.objectContaining({ dry_run: true }));
    // The poll timer starts once the job is on screen.
    await screen.findByTestId("import-status");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Files found")).toBeInTheDocument();
    expect(within(dialog).getByText("2")).toBeInTheDocument();
    bulkApi.startBulkImport.mockResolvedValue({ job_id: "job-2", status: "processing" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Move" }));
    expect(bulkApi.startBulkImport).toHaveBeenLastCalledWith(expect.objectContaining({ dry_run: false, force_copy: false }));
  });
});

describe("BulkImportDocumentsPage job status", () => {
  const startImport = async () => {
    bulkApi.startBulkImport.mockResolvedValue({ job_id: "job-1", status: "processing" });
    renderPage();
    pickServerFolder();
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    await screen.findByTestId("import-status");
  };

  it("stops polling once the worker reports \"complete\"", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await startImport();
    bulkApi.getBulkImportStatus.mockResolvedValue({
      job_id: "job-1",
      status: "complete",
      message: "Import complete: 2 imported, 0 skipped, 0 errors",
      stats: { total_files: 2, imported: 2, skipped: 0, errors: 0, queued_for_indexing: 2 },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(await screen.findByText("Imported")).toBeInTheDocument();
    expect(screen.getByText(/2 imported · 0 failed · 2 queued for indexing/)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(bulkApi.getBulkImportStatus).toHaveBeenCalledTimes(1);
  });

  it("shows a complete job with failed files as finished with errors, not imported", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await startImport();
    bulkApi.getBulkImportStatus.mockResolvedValue({
      job_id: "job-1",
      status: "complete",
      stats: { total_files: 3, imported: 1, skipped: 0, errors: 2, queued_for_indexing: 1 },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(await screen.findByText("Finished with errors (2 failed)")).toBeInTheDocument();
    expect(screen.queryByText("Imported")).not.toBeInTheDocument();
  });

  it("Start over stops the poll and clears the job", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await startImport();
    bulkApi.getBulkImportStatus.mockResolvedValue({ job_id: "job-1", status: "processing" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(bulkApi.getBulkImportStatus).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Start over" }));
    expect(screen.queryByTestId("import-status")).not.toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(bulkApi.getBulkImportStatus).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("guaardvark_bulkImportJob")).toBeNull();
  });

  it("picks up a job left running and says it finished while away", async () => {
    localStorage.setItem("guaardvark_bulkImportJob", JSON.stringify({ jobId: "job-9", mode: "import", sourceLabel: "From /data/in" }));
    bulkApi.getBulkImportStatus.mockResolvedValue({
      job_id: "job-9",
      status: "complete",
      stats: { total_files: 1, imported: 1, skipped: 0, errors: 0 },
    });
    renderPage();
    expect(await screen.findByText("Finished while you were away.")).toBeInTheDocument();
    expect(localStorage.getItem("guaardvark_bulkImportJob")).toBeNull();
  });

  it("says so when the server no longer keeps the job's status", async () => {
    localStorage.setItem("guaardvark_bulkImportJob", JSON.stringify({ jobId: "job-old", mode: "import" }));
    bulkApi.getBulkImportStatus.mockRejectedValue(Object.assign(new Error("Job not found"), { status: 404 }));
    renderPage();
    expect(await screen.findByText(/no longer kept/)).toBeInTheDocument();
  });
});
