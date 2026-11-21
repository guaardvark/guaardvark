import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

const rule = (id) => ({
  id,
  name: `rule_${id}`,
  level: "USER_GLOBAL",
  type: "PROMPT_TEMPLATE",
  rule_text: "x",
  is_active: true,
  target_models: ["__ALL__"],
});

// 230 rules on the server: page 1 and 2 hold 100 each, page 3 the last 30.
const TOTAL = 230;
const pageOf = (page, perPage) =>
  Array.from({ length: Math.max(0, Math.min(perPage, TOTAL - (page - 1) * perPage)) }, (_, i) =>
    rule((page - 1) * perPage + i + 1),
  );

const getRulesPage = vi.fn(async ({ page = 1, per_page: perPage = 50 }) => ({
  items: pageOf(page, perPage),
  total: TOTAL,
  page,
  perPage,
}));

vi.mock("../../api", () => ({
  getRulesPage: (filters) => getRulesPage(filters),
  getRules: vi.fn(async () => []),
  updateRule: vi.fn(),
  createRule: vi.fn(),
  deleteRule: vi.fn(),
  getProjects: vi.fn(async () => []),
  getCurrentlyLinkedItems: vi.fn(async () => []),
  updateEntityLinks: vi.fn(),
}));
vi.mock("../../contexts/StatusContext", () => ({
  useStatus: () => ({ activeModel: "m" }),
}));
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/modals/RuleActionModal", () => ({ default: () => null }));
vi.mock("../../components/modals/LinkingModal", () => ({ default: () => null }));

import RulesPage from "../RulesPage";

beforeEach(() => {
  getRulesPage.mockClear();
  localStorage.clear();
});

describe("RulesPage with more rules than one page", () => {
  it("asks for pages of 100, shows the API's real total and loads more on request", async () => {
    render(<RulesPage />);
    await screen.findByText(/Total Rules: 230/);
    expect(getRulesPage).toHaveBeenCalledWith({ page: 1, per_page: 100 });
    expect(screen.getByText(/\(showing 100\)/)).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(101);

    fireEvent.click(screen.getByRole("button", { name: /Load more/ }));
    await screen.findByText(/\(showing 200\)/);
    expect(getRulesPage).toHaveBeenLastCalledWith({ page: 2, per_page: 100 });
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(201);

    fireEvent.click(screen.getByRole("button", { name: /Load more/ }));
    await waitFor(() => expect(screen.queryByText(/showing/)).toBeNull());
    expect(screen.getByText(/Total Rules: 230/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Load more/ })).toBeNull();
  }, 20000);

  it("filters Learned rules with the same paging", async () => {
    render(<RulesPage />);
    await screen.findByText(/Total Rules: 230/);
    fireEvent.click(screen.getByRole("button", { name: /Learned/ }));
    await waitFor(() =>
      expect(getRulesPage).toHaveBeenLastCalledWith({ level: "LEARNED", page: 1, per_page: 100 }),
    );
  });
});
