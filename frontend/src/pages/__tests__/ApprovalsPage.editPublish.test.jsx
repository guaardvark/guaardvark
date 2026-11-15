import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const { hook, messages } = vi.hoisted(() => ({
  hook: { rows: [], next: null },
  messages: [],
}));

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
vi.mock("../../hooks/usePendingApprovals", async () => {
  const actual = await vi.importActual("react");
  const ReactActual = actual.default || actual;
  return {
    usePendingApprovals: () => {
      const [pending, setPending] = ReactActual.useState(hook.rows);
      const refresh = ReactActual.useCallback(async () => {
        if (hook.next) setPending(hook.next);
      }, []);
      return {
        pending,
        held: [],
        outreach: [],
        outreachWaiting: [],
        loading: false,
        heldLoading: false,
        outreachLoading: false,
        error: null,
        refresh,
      };
    },
    desktopNotificationsAvailable: () => false,
    desktopNotificationsGranted: () => false,
    requestDesktopNotifications: async () => false,
  };
});
vi.mock("../../api/connectionsService");
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/approvals/HeldChangesPanel", () => ({ default: () => null }));
vi.mock("../../components/approvals/OutreachDraftsPanel", () => ({ default: () => null }));
vi.mock("../../components/approvals/AlwaysApprovedToolsPanel", () => ({ default: () => null }));
vi.mock("../../components/common/SnackbarProvider", () => {
  const api = { showMessage: (text, level) => messages.push({ text, level }) };
  return { useSnackbar: () => api };
});

import ApprovalsPage from "../ApprovalsPage";
import * as connectionsService from "../../api/connectionsService";

const SPEC = {
  provider: "discord_webhook",
  label: "Discord (webhook)",
  capabilities: {
    max_text_chars: 2000,
    supports_link: true,
    visibilities: ["public"],
    ai_disclosure: { required_when: "never", mechanism: "text", text: "Made with AI." },
    rate_limits: [{ posts: 10, window_seconds: 3600, platform_limit: "" }],
  },
  post_fields: [],
};

const RECORD = {
  id: 7,
  connection_id: 3,
  platform: "discord_webhook",
  status: "awaiting_approval",
  requested_by: "chat",
  body: "Draft from chat",
  title: null,
  link_url: null,
  tags: [],
  visibility: "public",
  ai_disclosure: false,
  post_options: {},
  scheduled_at: null,
  media_refs: [],
  revision: 0,
  edited_at: null,
  edited_by: null,
  edited_fields: [],
  created_at: "2026-10-16T10:00:00",
};

const EDITED = {
  ...RECORD,
  body: "Reviewed text",
  revision: 1,
  edited_at: "2026-10-16T10:05:00-04:00",
  edited_by: "ui",
  edited_fields: ["body"],
};

const preflightEcho = (required = false) => (id, changes) =>
  Promise.resolve({
    ok: true,
    violations: [],
    changed: Object.keys(changes),
    per_connection: {
      3: {
        label: "Team Discord",
        body: changes.body ?? RECORD.body,
        violations: [],
        ai_disclosure: { ...SPEC.capabilities.ai_disclosure, required, applied: required },
      },
    },
  });

const openCard = async () => {
  render(
    <MemoryRouter>
      <ApprovalsPage />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText("Team Discord"));
};

describe("ApprovalsPage: edit before approving", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    messages.length = 0;
    hook.rows = [RECORD];
    hook.next = null;
    connectionsService.fetchConnections.mockResolvedValue([{ id: 3, display_name: "Team Discord" }]);
    connectionsService.fetchPublishes.mockResolvedValue([]);
    connectionsService.fetchProviders.mockResolvedValue([SPEC]);
    connectionsService.preflightPublishEdit.mockImplementation(preflightEcho());
    connectionsService.approvePublish.mockResolvedValue({ status: "queued" });
  });

  it("says the text was pre-filled, saves an edit, then approves the revision shown", async () => {
    connectionsService.editPublish.mockImplementation(async () => {
      hook.next = [EDITED];
      return { publish: EDITED, changed: ["body"] };
    });
    await openCard();

    expect(screen.getByText(/Chat drafted this post\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    expect(await screen.findByText("Edit before approving")).toBeInTheDocument();

    const text = await screen.findByLabelText("Text");
    expect(text).toHaveValue("Draft from chat");
    expect(screen.getByText(/At most 10 posts per hour/)).toBeInTheDocument();
    fireEvent.change(text, { target: { value: "Reviewed text" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(connectionsService.editPublish).toHaveBeenCalledWith(7, {
        body: "Reviewed text",
        revision: 0,
        edited_by: "ui",
      }),
    );
    expect(connectionsService.approvePublish).not.toHaveBeenCalled();
    expect(await screen.findByText("Reviewed text")).toBeInTheDocument();
    expect(screen.getByText(/Edited .*: text/)).toBeInTheDocument();
    expect(messages.at(-1).text).toMatch(/still waits for your approval/);

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    await waitFor(() => expect(connectionsService.approvePublish).toHaveBeenCalledWith(7, 1));
  });

  it("shows the current text when an approval is refused as edited since", async () => {
    connectionsService.approvePublish.mockRejectedValue(
      Object.assign(new Error("This publish was edited after it was shown to you."), {
        status: 409,
        data: { code: "edited_since" },
      }),
    );
    hook.next = [EDITED];
    await openCard();

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));

    await waitFor(() => expect(connectionsService.approvePublish).toHaveBeenCalledWith(7, 0));
    expect(await screen.findByText("Reviewed text")).toBeInTheDocument();
    expect(messages).toContainEqual({
      text: "This publish was edited after it was shown to you.",
      level: "warning",
    });
  });

  it("keeps the AI label on where the platform requires it", async () => {
    connectionsService.preflightPublishEdit.mockImplementation(preflightEcho(true));
    await openCard();

    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));

    const label = await screen.findByLabelText("Label as made with AI");
    await waitFor(() => expect(label).toBeDisabled());
    expect(label).toBeChecked();
  });

  it("shows the platform's default where no visibility is stored, and saves a picked one", async () => {
    hook.rows = [{ ...RECORD, platform: "mastodon", visibility: "" }];
    connectionsService.fetchProviders.mockResolvedValue([
      {
        ...SPEC,
        provider: "mastodon",
        label: "Mastodon",
        capabilities: {
          ...SPEC.capabilities,
          visibilities: ["public", "unlisted", "private", "direct"],
          default_visibility: "unlisted",
        },
      },
    ]);
    connectionsService.editPublish.mockResolvedValue({ publish: RECORD, changed: ["visibility"] });
    await openCard();

    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    const select = await screen.findByRole("combobox", { name: /^Visibility/ });
    expect(select).toHaveTextContent("unlisted");
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();

    fireEvent.mouseDown(select);
    fireEvent.click(within(await screen.findByRole("listbox")).getByRole("option", { name: "private" }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(connectionsService.editPublish).toHaveBeenCalledWith(7, {
        visibility: "private",
        revision: 0,
        edited_by: "ui",
      }),
    );
  });

  it("leaves Save off until something changes", async () => {
    await openCard();

    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    await screen.findByLabelText("Text");

    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
    expect(connectionsService.editPublish).not.toHaveBeenCalled();
  });
});
