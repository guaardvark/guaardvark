import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

import CSVGenerationCard from "../CSVGenerationCard";
import CodeGenerationCard from "../CodeGenerationCard";

// Every route a dashboard card navigates to must exist in App.jsx; these two
// used to point at pages that were never built (404 from the dashboard).
describe("dashboard card routes", () => {
  it("New CSV opens the File Generation page", () => {
    render(<CSVGenerationCard />);
    fireEvent.click(screen.getByRole("button", { name: /new csv/i }));
    expect(navigate).toHaveBeenLastCalledWith("/file-generation");
  });

  it("Import opens the File Generation page", () => {
    render(<CSVGenerationCard />);
    fireEvent.click(screen.getByRole("button", { name: /import/i }));
    expect(navigate).toHaveBeenLastCalledWith("/file-generation");
  });

  it("Code generation actions open the code editor", () => {
    render(<CodeGenerationCard />);
    const buttons = screen.getAllByRole("button");
    buttons.forEach((b) => fireEvent.click(b));
    const targets = navigate.mock.calls.map((c) => c[0]).filter((t) => typeof t === "string");
    expect(targets.some((t) => t === "/code-editor")).toBe(true);
    expect(targets.every((t) => !t.startsWith("/code-generation"))).toBe(true);
  });
});
