/**
 * The active profile, as the backend reports it on /api/settings/branding.
 *
 * A profile decides what the sidebar lists, where "/" lands and which pages
 * are chat surfaces of their own. It never removes a route: everything stays
 * reachable by deep link, and Settings can switch profiles. Data plus pure
 * predicates, same shape as config/floatingChat.js, so a fetched profile is a
 * value to select from the store rather than a module-level constant.
 */
import { FLOATING_CHAT_HIDDEN_ROUTES } from "./floatingChat";
import { extensionChatSurfaces } from "../extensions";

export const DEFAULT_PROFILE = Object.freeze({
  name: "workstation",
  label: "Workstation",
  description: "",
  source: "core",
  hidden_routes: [],
  landing_route: null,
  chat_surfaces: null,
  brand: {},
});

/** Drop nav items whose path the profile hides, and groups left empty. */
export const filterNavGroups = (groups, hiddenRoutes = []) => {
  if (!hiddenRoutes || hiddenRoutes.length === 0) return groups;
  const hidden = new Set(hiddenRoutes);
  return groups
    .map((group) => ({ ...group, items: group.items.filter((item) => !hidden.has(item.path)) }))
    .filter((group) => group.items.length > 0);
};

/** Where "/" (and the sidebar avatar) should go; null keeps the dashboard. */
export const landingRouteFor = (profile) => {
  const route = profile?.landing_route;
  return route && route !== "/" ? route : null;
};

/** Pages that are chat surfaces themselves — the floating chat stays away. */
export const chatSurfacesFor = (profile) => {
  const base = Array.isArray(profile?.chat_surfaces) ? profile.chat_surfaces : FLOATING_CHAT_HIDDEN_ROUTES;
  const extra = extensionChatSurfaces();
  return extra.length ? [...base, ...extra] : base;
};
