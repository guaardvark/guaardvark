import { describe, it, expect } from "vitest";
import { handleResponse } from "../apiClient";

const jsonResponse = (status, body) =>
  new Response(JSON.stringify(body), {
    status,
    statusText: status === 503 ? "Service Unavailable" : "",
    headers: { "content-type": "application/json" },
  });

describe("handleResponse error text", () => {
  it("uses the message inside the backend's nested error object", async () => {
    const text = "Cannot connect to master server: connection refused";
    const response = jsonResponse(503, {
      success: false,
      message: text,
      error: { code: "SERVICE_UNAVAILABLE", message: text },
    });
    await expect(handleResponse(response, { quiet: true })).rejects.toMatchObject({
      message: text,
      status: 503,
    });
  });

  it("still accepts a bare string error and flags the offline proxy", async () => {
    const response = jsonResponse(502, { error: "backend_offline" });
    await expect(handleResponse(response, { quiet: true })).rejects.toMatchObject({
      message: "backend_offline",
      backendOffline: true,
    });
  });

  it("falls back to the HTTP status when the body carries no text", async () => {
    const response = new Response("", { status: 500, headers: { "content-type": "text/plain" } });
    await expect(handleResponse(response, { quiet: true })).rejects.toMatchObject({
      message: "HTTP error 500 - Unknown Error",
    });
  });
});
