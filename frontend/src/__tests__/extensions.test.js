import { describe, it, expect } from "vitest";
import { loadExtensions, normalizeExtension } from "../extensions";

describe("extensions", () => {
  it("normalizes a sparse spec with safe defaults", () => {
    const ext = normalizeExtension("acme", { default: { routes: [{ path: "/acme", element: "x" }] } });
    expect(ext.id).toBe("acme");
    expect(ext.routes).toEqual([{ path: "/acme", element: "x", errorBoundary: true }]);
    expect(ext.navGroups).toEqual([]);
    expect(ext.themes).toEqual({});
    expect(ext.pageContext).toEqual({ routeMap: {}, paramRoutes: [] });
    expect(ext.storeSlice).toBeNull();
    expect(ext.logo).toBeNull();
  });

  it("finds ids from the glob path, skips templates, sorts by id", () => {
    const exts = loadExtensions({
      "../../extensions/zeta/frontend/index.jsx": { default: {} },
      "../../extensions/_template/frontend/index.jsx": { default: {} },
      "../../extensions/acme/frontend/index.jsx": { default: { id: "acme" } },
      "../../extensions/bad/other.jsx": { default: {} },
    });
    expect(exts.map((e) => e.id)).toEqual(["acme", "zeta"]);
  });

  it("prefixes a dashboard card's id with its extension and drops a card with no component", () => {
    const Card = () => null;
    const ext = normalizeExtension("acme", {
      default: { cards: [{ id: "orders", title: "Orders", component: Card, w: 2 }, { id: "broken", title: "Broken" }] },
    });
    expect(ext.cards).toEqual([{ id: "acme-orders", title: "Orders", component: Card, w: 2, h: 1 }]);
    expect(normalizeExtension("acme", { default: {} }).cards).toEqual([]);
  });

  it("is empty when there are no extensions", () => {
    expect(loadExtensions({})).toEqual([]);
  });
});
