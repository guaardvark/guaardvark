import { describe, it, expect } from "vitest";
import { contextChipLabel } from "../contextChipLabel";

describe("contextChipLabel", () => {
  it("hides on the chat page and unknown pages", () => {
    expect(contextChipLabel({ page: "Chat" }, null)).toBeNull();
    expect(contextChipLabel({ page: "Unknown" }, null)).toBeNull();
    expect(contextChipLabel(null, "x")).toBeNull();
  });
  it("shows the page when there is no entity", () => {
    expect(contextChipLabel({ page: "Clients", entityId: null }, null)).toBe("Clients");
  });
  it("keeps the id next to a supplied name", () => {
    expect(contextChipLabel({ page: "Client Folder", entityId: 18 }, "Alan Fonk")).toBe("Alan Fonk · #18");
  });
  it("falls back to page + id without a name", () => {
    expect(contextChipLabel({ page: "Client Folder", entityId: 18 }, null)).toBe("Client Folder #18");
  });
});
