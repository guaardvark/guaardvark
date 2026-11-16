import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PageLayout from "../PageLayout";
import PageStatus, { StatusChip } from "../PageStatus";

describe("PageStatus", () => {
  it("shows a page's status chips in the header, from a page embedded without one too", () => {
    const openModels = vi.fn();
    render(
      <MemoryRouter>
        <PageLayout title="Media">
          <PageLayout variant="fullscreen">
            <PageStatus>
              <StatusChip label="Model" value="Wan 2.2" onClick={openModels} />
              <StatusChip label="Queue" value="Idle" tone="ok" />
            </PageStatus>
            <div>page body</div>
          </PageLayout>
        </PageLayout>
      </MemoryRouter>,
    );
    const title = screen.getByText("Media");
    const chip = screen.getByRole("button", { name: /Wan 2\.2/ });
    // In the header beside the title, not in the page body.
    expect(title.parentElement.contains(chip)).toBe(true);
    expect(screen.getByText("page body").parentElement.contains(chip)).toBe(false);
    expect(screen.getByText("Idle")).toBeInTheDocument();
    fireEvent.click(chip);
    expect(openModels).toHaveBeenCalled();
  });

  it("renders nothing on a page without a header", () => {
    render(
      <PageStatus>
        <StatusChip label="Queue" value="Idle" />
      </PageStatus>,
    );
    expect(screen.queryByText("Idle")).not.toBeInTheDocument();
  });
});
