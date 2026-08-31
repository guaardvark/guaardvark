import { describe, it, expect } from "vitest";
import { DEFAULT_PROFILE, filterNavGroups, landingRouteFor, chatSurfacesFor } from "../profile";
import { FLOATING_CHAT_HIDDEN_ROUTES } from "../floatingChat";

const groups = [
  { label: "Main", items: [{ text: "Dashboard", path: "/" }, { text: "Chat", path: "/chat" }] },
  { label: "Management", items: [{ text: "Outreach", path: "/outreach" }] },
];

describe("profile", () => {
  it("workstation lists everything and lands on the dashboard", () => {
    expect(filterNavGroups(groups, DEFAULT_PROFILE.hidden_routes)).toBe(groups);
    expect(landingRouteFor(DEFAULT_PROFILE)).toBeNull();
    expect(chatSurfacesFor(DEFAULT_PROFILE)).toBe(FLOATING_CHAT_HIDDEN_ROUTES);
  });

  it("hides listed routes and drops groups left empty", () => {
    const out = filterNavGroups(groups, ["/outreach", "/chat"]);
    expect(out).toEqual([{ label: "Main", items: [{ text: "Dashboard", path: "/" }] }]);
  });

  it("does not mutate the brand's groups", () => {
    filterNavGroups(groups, ["/chat"]);
    expect(groups[0].items).toHaveLength(2);
  });

  it("a landing route of / means no redirect", () => {
    expect(landingRouteFor({ landing_route: "/" })).toBeNull();
    expect(landingRouteFor({ landing_route: "/images" })).toBe("/images");
    expect(landingRouteFor(undefined)).toBeNull();
  });

  it("a profile may name its own chat surfaces", () => {
    expect(chatSurfacesFor({ chat_surfaces: ["/chat", "/brain*"] })).toEqual(["/chat", "/brain*"]);
    expect(chatSurfacesFor({ chat_surfaces: null })).toBe(FLOATING_CHAT_HIDDEN_ROUTES);
  });
});
