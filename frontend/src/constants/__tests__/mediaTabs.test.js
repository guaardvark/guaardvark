import { describe, it, expect } from "vitest";
import { MEDIA_TABS, MEDIA_LIBRARY_TAB, mediaTabIndexForPath } from "../mediaTabs";

describe("mediaTabIndexForPath", () => {
  it("maps every tab route to its own index", () => {
    MEDIA_TABS.forEach((tab, index) => {
      expect(mediaTabIndexForPath(tab.path)).toBe(index);
    });
  });

  it("does not let /images swallow /batch-images", () => {
    expect(mediaTabIndexForPath("/batch-images")).toBe(
      MEDIA_TABS.findIndex((t) => t.path === "/batch-images"),
    );
  });

  it("keeps nested routes on their tab", () => {
    expect(mediaTabIndexForPath("/video/abc")).toBe(
      MEDIA_TABS.findIndex((t) => t.path === "/video"),
    );
  });

  it("falls back to the media library for anything else", () => {
    expect(mediaTabIndexForPath("/settings")).toBe(MEDIA_LIBRARY_TAB);
  });
});
