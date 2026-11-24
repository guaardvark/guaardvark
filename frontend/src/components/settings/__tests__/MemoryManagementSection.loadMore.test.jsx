import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import MemoryManagementSection from "../MemoryManagementSection";

// 230 rows on the server; the API serves `limit` rows from the top and the
// real count in total. Rows the person marks wrong leave the "active" list.
const TOTAL = 230;
const wrong = new Set();
const row = (i) => ({ id: `m-${i}`, content: `fact ${i}`, type: "fact", status: "active", tags: [] });

const page = (url) => {
  const params = new URL(url).searchParams;
  const limit = Number(params.get("limit"));
  const offset = Number(params.get("offset") || 0);
  const all = [];
  for (let i = 1; i <= TOTAL; i += 1) if (params.get("status") === "all" || !wrong.has(i)) all.push(row(i));
  return { success: true, memories: all.slice(offset, offset + limit), total: all.length, limit, offset };
};

const params = (call) => new URL(call.url).searchParams;

describe("Agent Memory list past the first page", () => {
  let calls;
  beforeEach(() => {
    calls = [];
    wrong.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, opts = {}) => {
        calls.push({ url: String(url), method: opts.method || "GET", body: opts.body });
        if ((opts.method || "GET") === "PATCH") {
          const id = Number(String(url).match(/m-(\d+)$/)[1]);
          wrong.add(id);
          return { json: async () => ({ success: true, memory: { ...row(id), status: "wrong" } }) };
        }
        return { json: async () => page(String(url)) };
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the API's total, loads 100 at a time and offers Load more while rows remain", async () => {
    render(<MemoryManagementSection />);
    await screen.findByText("fact 1");
    expect(screen.getByText(/Total: 230/)).toBeInTheDocument();
    expect(screen.getByText(/\(showing 100\)/)).toBeInTheDocument();
    expect(params(calls[0]).get("limit")).toBe("100");
    expect(params(calls[0]).get("status")).toBe("active");

    fireEvent.click(screen.getByRole("button", { name: /Load more/ }));
    await screen.findByText("fact 200");
    expect(screen.getByText(/\(showing 200\)/)).toBeInTheDocument();
    expect(params(calls[1]).get("limit")).toBe("200");
    expect(params(calls[1]).get("offset")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Load more/ }));
    await screen.findByText("fact 230");
    await waitFor(() => expect(screen.queryByText(/showing/)).toBeNull());
    expect(screen.queryByRole("button", { name: /Load more/ })).toBeNull();
  }, 20000);

  it("skips no row after a row on screen was marked wrong", async () => {
    render(<MemoryManagementSection />);
    await screen.findByText("fact 1");
    const first = screen.getByText("fact 1").closest("tr");
    fireEvent.click(within(first).getByRole("button", { name: /Wrong/i }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: /Load more/ }));
    // Row 101 is the first row past the page; an offset of the on-screen
    // count (100, with the wrong row still shown) would have started at 102.
    await screen.findByText("fact 101");
    expect(screen.getByText("fact 2")).toBeInTheDocument();
    expect(screen.queryByText("fact 1")).toBeNull();
    expect(screen.getByText(/Total: 229/)).toBeInTheDocument();
  });

  it("loads more with the query in effect, not an unapplied control", async () => {
    render(<MemoryManagementSection />);
    await screen.findByText("fact 1");
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /Load more/ }));
    await screen.findByText("fact 200");
    expect(params(calls[1]).get("sort")).toBe("rank");
    expect(params(calls[1]).get("status")).toBe("active");
  });

  it("asks for every status, rows marked wrong included, when All status is chosen", async () => {
    render(<MemoryManagementSection />);
    await screen.findByText("fact 1");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "all" } });
    fireEvent.click(screen.getByRole("button", { name: /Apply Filters/ }));
    await waitFor(() => expect(calls.length).toBe(2));
    expect(params(calls[1]).get("status")).toBe("all");
  });
});
