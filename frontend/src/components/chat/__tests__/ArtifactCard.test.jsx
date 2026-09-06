import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import ArtifactCard, { formatBytes } from "../ArtifactCard";

const base = {
  filename: "report.txt",
  file_type: "txt",
  size_bytes: 1234,
  url: "/api/outputs/files/report.txt",
  content: "hello",
  content_truncated: false,
};

describe("ArtifactCard", () => {
  it("renders CSV content as a table", () => {
    render(
      <ArtifactCard
        artifact={{ ...base, filename: "data.csv", file_type: "csv", content: "name,qty\nbolt,4\nnut,9" }}
      />
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("qty")).toBeInTheDocument();
    expect(screen.getByText("bolt")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();
  });

  it("renders markdown content as markdown", () => {
    render(
      <ArtifactCard
        artifact={{ ...base, filename: "notes.md", file_type: "md", content: "# Heading\n\nSome **bold** text" }}
      />
    );
    expect(screen.getByRole("heading", { level: 1, name: "Heading" })).toBeInTheDocument();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
  });

  it("renders code content in a pre/code block", () => {
    const { container } = render(
      <ArtifactCard artifact={{ ...base, filename: "script.py", file_type: "py", content: "print('hi')" }} />
    );
    const body = screen.getByTestId("artifact-body");
    expect(body.querySelector("pre")).not.toBeNull();
    expect(body.querySelector("code")).not.toBeNull();
    expect(container.textContent).toContain("print");
  });

  it("shows filename, type chip and human size in the header", () => {
    render(<ArtifactCard artifact={base} />);
    expect(screen.getByText("report.txt")).toBeInTheDocument();
    expect(screen.getByText("txt")).toBeInTheDocument();
    expect(screen.getByText("1.2 KB")).toBeInTheDocument();
  });

  it("links Download and Open to the artifact url", () => {
    render(<ArtifactCard artifact={base} />);
    const download = screen.getByRole("link", { name: "Download" });
    expect(download).toHaveAttribute("href", "/api/outputs/files/report.txt");
    expect(download).toHaveAttribute("download", "report.txt");
    const open = screen.getByRole("link", { name: "Open in new tab" });
    expect(open).toHaveAttribute("href", "/api/outputs/files/report.txt?inline=1");
    expect(open).toHaveAttribute("target", "_blank");
  });

  it("hides Download and Open when url is null", () => {
    render(<ArtifactCard artifact={{ ...base, url: null }} />);
    expect(screen.queryByRole("link", { name: "Download" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Open in new tab" })).toBeNull();
    expect(screen.getByRole("button", { name: "Copy content" })).toBeInTheDocument();
  });

  it("shows a note and keeps actions when content is missing", () => {
    render(<ArtifactCard artifact={{ ...base, content: undefined }} />);
    expect(screen.getByText(/Preview not available/)).toBeInTheDocument();
    expect(screen.queryByTestId("artifact-body")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy content" })).toBeNull();
    expect(screen.getByRole("link", { name: "Download" })).toBeInTheDocument();
  });

  it("shows a truncation note alongside the partial content", () => {
    render(<ArtifactCard artifact={{ ...base, content_truncated: true }} />);
    expect(screen.getByText(/Preview truncated/)).toBeInTheDocument();
    expect(screen.getByTestId("artifact-body")).toHaveTextContent("hello");
  });
});

describe("formatBytes", () => {
  it("formats sizes with one decimal under 10 units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(20 * 1024)).toBe("20 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});
