import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  applyPreviewFrame,
  blobUrlFromPreviewPayload,
  clearAllPreviews,
  dropPreview,
  dropStalePreviews,
} from "../jobPreviewUrls";

describe("jobPreviewUrls", () => {
  const created = [];
  const revoked = [];

  beforeEach(() => {
    created.length = 0;
    revoked.length = 0;
    vi.stubGlobal("URL", {
      createObjectURL: (blob) => {
        const url = `blob:test-${created.length}`;
        created.push({ url, type: blob.type, size: blob.size });
        return url;
      },
      revokeObjectURL: (url) => {
        revoked.push(url);
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const payload = (jobId, b64 = btoa("jpeg-bytes")) => ({
    job_id: jobId,
    mime: "image/jpeg",
    b64,
  });

  it("builds a blob URL from a valid payload", () => {
    const url = blobUrlFromPreviewPayload(payload("item-1"));
    expect(url).toBe("blob:test-0");
    expect(created[0].type).toBe("image/jpeg");
  });

  it("ignores missing job_id or b64", () => {
    expect(blobUrlFromPreviewPayload({})).toBeNull();
    expect(blobUrlFromPreviewPayload({ job_id: "x" })).toBeNull();
    expect(blobUrlFromPreviewPayload({ b64: "YQ==" })).toBeNull();
  });

  it("replaces and revokes the previous URL for the same job", () => {
    let store = new Map();
    store = applyPreviewFrame(store, payload("item-1", btoa("a")));
    store = applyPreviewFrame(store, payload("item-1", btoa("b")));
    expect(store.get("item-1")).toBe("blob:test-1");
    expect(revoked).toEqual(["blob:test-0"]);
  });

  it("does not drop a preview that has not been live in UPS yet", () => {
    let store = applyPreviewFrame(new Map(), payload("item-1"));
    store = dropStalePreviews(store, new Set(), new Set());
    expect(store.has("item-1")).toBe(true);
    expect(revoked).toEqual([]);
  });

  it("drops a preview after the job leaves UPS", () => {
    let store = applyPreviewFrame(new Map(), payload("item-1"));
    store = dropStalePreviews(store, new Set(), new Set(["item-1"]));
    expect(store.has("item-1")).toBe(false);
    expect(revoked).toEqual(["blob:test-0"]);
  });

  it("dropPreview and clearAllPreviews revoke", () => {
    let store = applyPreviewFrame(new Map(), payload("a"));
    store = applyPreviewFrame(store, payload("b"));
    store = dropPreview(store, "a");
    expect(revoked).toContain("blob:test-0");
    store = clearAllPreviews(store);
    expect(revoked).toContain("blob:test-1");
    expect(store.size).toBe(0);
  });
});
