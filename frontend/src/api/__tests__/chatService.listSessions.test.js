import { describe, it, expect, beforeEach, vi } from "vitest";
import { listChatSessions } from "../chatService";

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("listChatSessions", () => {
  it("rejects when the server refuses the list, so the chat list can show the failure", async () => {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ error: "boom" }), {
        status: 500,
        statusText: "Internal Server Error",
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(listChatSessions(null)).rejects.toBeTruthy();
  });

  it("returns the sessions the server sent", async () => {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ sessions: [{ id: "s1" }], total: 1 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const data = await listChatSessions(null);
    expect(data.sessions).toEqual([{ id: "s1" }]);
  });
});
