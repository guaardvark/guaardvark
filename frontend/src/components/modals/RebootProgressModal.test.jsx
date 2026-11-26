import React from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RebootProgressModal from "./RebootProgressModal";

vi.mock("../../api/devtoolsService", () => ({
  getBackendHealth: vi.fn(async () => ({ status: "starting" })),
}));

// One SSE payload, then the socket dies the way a killed Flask dies: an error
// whose text matches none of the old "network"/"stream" checks.
const SSE = [
  'data: {"type":"log_server","url":"http://127.0.0.1:6000"}',
  "",
  'data: {"type":"status","message":"Log server ready — live output will continue during restart"}',
  "",
  'data: {"type":"output","line":"  stopping backend"}',
  "",
].join("\n");

function streamResponse() {
  const encoded = new TextEncoder().encode(SSE);
  let sent = false;
  return {
    ok: true,
    body: {
      getReader() {
        return {
          read: async () => {
            if (!sent) {
              sent = true;
              return { done: false, value: encoded };
            }
            throw new TypeError("Load failed");
          },
        };
      },
    },
  };
}

function logResponse(lines, offset) {
  return {
    ok: true,
    status: 200,
    json: async () => ({ success: true, content_lines: lines, offset, size: offset }),
  };
}

describe("RebootProgressModal", () => {
  let reload;

  beforeEach(() => {
    reload = vi.fn();
    vi.stubGlobal("location", { reload });
    Element.prototype.scrollIntoView = vi.fn();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reconnects to the log after the socket drops and keeps a centered spinner", async () => {
    let polls = 0;
    global.fetch.mockImplementation(async (url) => {
      const target = String(url);
      if (target.includes("/reboot/stream")) return streamResponse();
      if (target.startsWith("http://127.0.0.1:6000/log")) {
        polls += 1;
        if (polls === 1) throw new TypeError("Failed to fetch");
        if (polls === 2) return logResponse(["  redis is up"], 48);
        return logResponse([], 48);
      }
      throw new Error(`unexpected fetch ${target}`);
    });

    render(<RebootProgressModal open onClose={() => {}} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getAllByText(/Reconnecting to the boot log/)).toHaveLength(1);
    expect(screen.getByText(/redis is up/)).toBeInTheDocument();
    expect(screen.getByTestId("reboot-spinner")).toBeInTheDocument();
    expect(screen.queryByTestId("reboot-mark")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rebooting..." })).toBeDisabled();
    expect(reload).not.toHaveBeenCalled();
    expect(polls).toBeGreaterThanOrEqual(2);
  });
});
