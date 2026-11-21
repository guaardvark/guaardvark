import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

const getTasks = vi.fn();
vi.mock("../../../api/taskService", () => ({ getTasks: (...args) => getTasks(...args) }));

import ImageGenerationCard from "../ImageGenerationCard";
import { SnackbarProvider } from "../../common/SnackbarProvider";
import CSVGenerationCard from "../CSVGenerationCard";
import CodeGenerationCard from "../CodeGenerationCard";
import { imageBatchRows, isCsvJob, jobRows, runStatus } from "../recentRuns";

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

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const batches = [
  { batch_id: "ImageBatch_1", display_name: "Lighthouses", status: "completed", total_images: 4, completed_images: 4, start_time: "2026-10-01T10:00:00" },
  { batch_id: "ImageBatch_2", display_name: "Harbour at dusk", status: "running", total_images: 8, completed_images: 3, start_time: "2026-10-07T09:00:00" },
];

beforeEach(() => {
  getTasks.mockReset();
  vi.unstubAllGlobals();
});

describe("recent-runs rows", () => {
  it("orders image batches newest first and links each to its batch on the Image Gen page", () => {
    const rows = imageBatchRows(batches);
    expect(rows.map((r) => r.name)).toEqual(["Harbour at dusk", "Lighthouses"]);
    expect(rows[0]).toMatchObject({ path: "/batch-images?batch=ImageBatch_2", detail: "3/8 images" });
  });

  it("links jobs to the Jobs page and keeps only CSV runs for the CSV card", () => {
    const tasks = [
      { id: 1, name: "Old", output_filename: "a.csv", created_at: "2026-09-01T00:00:00", status: "completed" },
      { id: 2, name: "Notes", output_filename: "notes.md", created_at: "2026-10-01T00:00:00", status: "completed" },
      { id: 3, name: "New", output_filename: "b.CSV", created_at: "2026-10-02T00:00:00", status: "in-progress" },
    ];
    expect(jobRows(tasks, isCsvJob).map((r) => [r.name, r.path])).toEqual([
      ["New", "/tasks?taskId=3"],
      ["Old", "/tasks?taskId=1"],
    ]);
    expect(runStatus("in-progress").label).toBe("Running");
    expect(runStatus("error")).toEqual({ label: "Failed", color: "error" });
  });
});

describe("dashboard generation cards", () => {
  it("Image Generation lists real batches and opens one on the Image Gen page", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ success: true, data: { batches } })));
    renderCard(<ImageGenerationCard id="imggen" />);
    fireEvent.click(await screen.findByText("Harbour at dusk"));
    expect(screen.getByTestId("where")).toHaveTextContent("/batch-images?batch=ImageBatch_2");
  });

  it("Image Generation says there are none only when the feed has none, and shows a failure as one", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ success: true, data: { batches: [] } })));
    const first = renderCard(<ImageGenerationCard id="imggen" />);
    expect(await screen.findByText("No image batches yet.")).toBeInTheDocument();
    first.unmount();

    vi.stubGlobal("fetch", vi.fn(async () => json({ error: "Batch image generation service not available" }, 503)));
    renderCard(<ImageGenerationCard id="imggen" />);
    expect(await screen.findByText(/Could not load recent runs/)).toBeInTheDocument();
    expect(screen.queryByText("No image batches yet.")).not.toBeInTheDocument();
  });

  it("CSV Generation lists CSV jobs and opens one on the Jobs page", async () => {
    getTasks.mockResolvedValue([
      { id: 7, name: "CSV Generation: products", output_filename: "products.csv", status: "completed", created_at: "2026-10-05T00:00:00" },
      { id: 8, name: "Readme", output_filename: "README.md", status: "completed", created_at: "2026-10-06T00:00:00" },
    ]);
    renderCard(<CSVGenerationCard id="csv" />);
    fireEvent.click(await screen.findByText("CSV Generation: products"));
    expect(getTasks).toHaveBeenCalledWith(null, null, "file_generation");
    expect(screen.getByTestId("where")).toHaveTextContent("/tasks?taskId=7");
  });

  it("Code Generation lists code generation jobs", async () => {
    getTasks.mockResolvedValue([]);
    renderCard(<CodeGenerationCard id="code" />);
    expect(await screen.findByText("No code generation jobs yet.")).toBeInTheDocument();
    expect(getTasks).toHaveBeenCalledWith(null, null, "code_generation");
  });
});
