import { afterEach, describe, expect, it, vi } from "vitest";
import axios from "axios";
import {
  AUTH_REFUSED_EVENT,
  crossOriginBackends,
  describeAuthRefusal,
  installBackendCredentials,
  isBackendUrl,
  needsCredentials,
  notifySessionChanged,
  onSessionChanged,
} from "../apiAuth";
import { handleResponse } from "../apiClient";

const PAGE = { origin: "http://192.168.1.5:5173", href: "http://192.168.1.5:5173/chat" };
const sameOrigin = (extra = {}) => ({ location: PAGE, apiBase: "/api", backendUrls: [], ...extra });
const crossOrigin = () => sameOrigin({ apiBase: "http://192.168.1.5:5000/api" });

const jsonResponse = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("isBackendUrl", () => {
  it("accepts the API path on the page's own origin only", () => {
    expect(isBackendUrl("/api/tools/execute", sameOrigin())).toBe(true);
    expect(isBackendUrl("http://192.168.1.5:5173/api/auth/status", sameOrigin())).toBe(true);
    expect(isBackendUrl("/assets/index.js", sameOrigin())).toBe(false);
    expect(isBackendUrl("/apiary", sameOrigin())).toBe(false);
  });

  it("refuses other hosts, other ports and non-http URLs", () => {
    expect(isBackendUrl("https://huggingface.co/api/models", sameOrigin())).toBe(false);
    expect(isBackendUrl("http://192.168.1.9:5173/api/interconnector/status", sameOrigin())).toBe(false);
    expect(isBackendUrl("http://192.168.1.5:5000/api/x", sameOrigin())).toBe(false);
    expect(isBackendUrl("data:text/plain,hi", sameOrigin())).toBe(false);
    expect(isBackendUrl("", sameOrigin())).toBe(false);
  });

  it("accepts every path on a backend the build names on another origin", () => {
    expect(isBackendUrl("http://192.168.1.5:5000/voice/stream", crossOrigin())).toBe(true);
  });
});

describe("needsCredentials", () => {
  it("is never needed when the API is on the page's origin", () => {
    expect(crossOriginBackends(sameOrigin())).toEqual([]);
    expect(needsCredentials("/api/tools/execute", sameOrigin())).toBe(false);
  });

  it("is needed for the cross-origin backend and nothing else", () => {
    expect(crossOriginBackends(crossOrigin())).toEqual(["http://192.168.1.5:5000"]);
    expect(needsCredentials("http://192.168.1.5:5000/api/tools/execute", crossOrigin())).toBe(true);
    expect(needsCredentials("https://huggingface.co/api/models", crossOrigin())).toBe(false);
    expect(needsCredentials("/assets/index.js", crossOrigin())).toBe(false);
  });
});

describe("installBackendCredentials", () => {
  it("leaves fetch alone when the API is on the page's origin", () => {
    const fetch = vi.fn();
    const target = { fetch };
    installBackendCredentials({ target, options: sameOrigin() });
    expect(target.fetch).toBe(fetch);
  });

  it("asks for credentials only on requests to a cross-origin backend", async () => {
    const fetch = vi.fn(async () => jsonResponse(200, {}));
    const target = { fetch };
    installBackendCredentials({ target, options: crossOrigin() });
    installBackendCredentials({ target, options: crossOrigin() }); // a second call changes nothing

    await target.fetch("http://192.168.1.5:5000/api/tools/execute", { method: "POST" });
    expect(fetch.mock.calls[0][1]).toEqual({ method: "POST", credentials: "include" });

    await target.fetch("https://huggingface.co/api/models", { method: "GET" });
    expect(fetch.mock.calls[1][1]).toEqual({ method: "GET" });

    await target.fetch("http://192.168.1.5:5000/api/x", { credentials: "omit" });
    expect(fetch.mock.calls[2][1]).toEqual({ credentials: "omit" });
  });

  it("never writes the key or anything else to storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    notifySessionChanged();
    installBackendCredentials({ target: { fetch: vi.fn() }, options: crossOrigin() });
    expect(setItem).not.toHaveBeenCalled();
  });
});

describe("axios refusals", () => {
  it("become the Settings advice and are announced", async () => {
    const instance = axios.create();
    instance.defaults.adapter = async (config) => {
      const error = new Error("Request failed with status code 401");
      error.config = config;
      error.response = {
        status: 401,
        data: { error: "server words", code: "api_key_required", credential_rejected: true },
        headers: {},
        config,
      };
      throw error;
    };
    installBackendCredentials({ axios: instance, target: null, options: sameOrigin() });
    const seen = [];
    const listener = (e) => seen.push(e.detail);
    window.addEventListener(AUTH_REFUSED_EVENT, listener);
    await expect(instance.post("http://192.168.1.5:5173/api/tools/execute")).rejects.toMatchObject({
      authRefused: "api_key_required",
      message: describeAuthRefusal("api_key_required", true),
    });
    window.removeEventListener(AUTH_REFUSED_EVENT, listener);
    expect(seen).toEqual([
      { code: "api_key_required", rejected: true, url: "http://192.168.1.5:5173/api/tools/execute" },
    ]);
  });
});

describe("handleResponse on a refusal", () => {
  it("says what to do in the web UI, flags the error and announces it", async () => {
    const seen = [];
    const listener = (e) => seen.push(e.detail.code);
    window.addEventListener(AUTH_REFUSED_EVENT, listener);
    await expect(
      handleResponse(jsonResponse(403, { error: "server words", code: "local_only" }), { quiet: true }),
    ).rejects.toMatchObject({
      status: 403,
      authRefused: "local_only",
      message: describeAuthRefusal("local_only", false),
    });
    window.removeEventListener(AUTH_REFUSED_EVENT, listener);
    expect(seen).toEqual(["local_only"]);
  });

  it("words a code route refused under network access", async () => {
    await expect(
      handleResponse(jsonResponse(403, { code: "runs_code_key_required", machine: "studio" }), { quiet: true }),
    ).rejects.toMatchObject({
      authRefused: "runs_code_key_required",
      message: describeAuthRefusal("runs_code_key_required", false, "studio"),
    });
    expect(describeAuthRefusal("runs_code_key_required", false, "studio")).toContain("runs a program on studio");
  });

  it("words an Interconnector route refused under network access", async () => {
    await expect(
      handleResponse(jsonResponse(403, { code: "fleet_key_required", machine: "studio" }), { quiet: true }),
    ).rejects.toMatchObject({
      authRefused: "fleet_key_required",
      message: describeAuthRefusal("fleet_key_required", false, "studio"),
    });
    expect(describeAuthRefusal("fleet_key_required", false, "studio")).toContain("Interconnector action works only on studio");
  });

  it("words a backup route refused under network access", async () => {
    await expect(
      handleResponse(jsonResponse(403, { code: "backups_key_required", machine: "studio" }), { quiet: true }),
    ).rejects.toMatchObject({
      authRefused: "backups_key_required",
      message: describeAuthRefusal("backups_key_required", false, "studio"),
    });
    expect(describeAuthRefusal("backups_key_required", false, "studio")).toContain("Backups hold everything on studio");
  });

  it("words an out-of-date sign-in differently", async () => {
    await expect(
      handleResponse(jsonResponse(401, { code: "api_key_required", credential_rejected: true }), { quiet: true }),
    ).rejects.toMatchObject({ message: describeAuthRefusal("api_key_required", true) });
  });

  it("leaves other 401s alone", async () => {
    await expect(
      handleResponse(jsonResponse(401, { error: "That is not this install's API key.", code: "wrong_key" }), { quiet: true }),
    ).rejects.toMatchObject({ message: "That is not this install's API key." });
  });
});

describe("session change notices", () => {
  it("reach subscribers until they unsubscribe", async () => {
    const callback = vi.fn();
    const stop = onSessionChanged(callback);
    notifySessionChanged();
    await settle();
    expect(callback).toHaveBeenCalled();
    stop();
    callback.mockClear();
    notifySessionChanged();
    await settle();
    expect(callback).not.toHaveBeenCalled();
  });
});
