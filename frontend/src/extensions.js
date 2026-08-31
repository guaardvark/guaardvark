/**
 * Client extensions, frontend half.
 *
 * Each `extensions/<id>/frontend/index.jsx` default-exports what the vertical
 * adds to the UI; this module finds them at build time and hands core a
 * normalized list, so App.jsx, Sidebar, the theme registry, page context,
 * the floating chat, the store and BrandLogo each merge one array and never
 * name a client. The glob is empty in a clone without extensions.
 *
 * Contract (every key optional):
 *   id            string — must match the folder
 *   routes        [{ path, element, errorBoundary = true }]   injected before "*"
 *   navGroups     [{ label, items: [{ text, icon, path, badge? }] }]  listed ahead of the brand's groups
 *   themes        { key: { label, description, previewGradient, theme } }
 *   pageContext   { routeMap: { path: { page, entityType } }, paramRoutes: [{ pattern, page, entityType, paramName }] }
 *   chatSurfaces  [route | "prefix*"]   pages that are chat surfaces themselves
 *   storeSlice    { state: (set, get) => ({...}), partialize: [keys persisted] }
 *   layout        { header: Component }   rendered inside AppLayout above the page
 *   logo          Component   replaces the brand logo
 *   landingRoute  string      where "/" goes when no profile says otherwise
 *
 * Import core from an extension through the `@` alias (`@/api/apiClient`,
 * `@/theme/createTheme`); lazy-import pages so nothing heavy runs at load.
 * Underscore folders are templates and are skipped. An extension folder may
 * be a symlink to a private checkout elsewhere on disk.
 */
const modules = import.meta.glob("../../extensions/[!_]*/frontend/index.jsx", { eager: true });

const idFromPath = (path) => {
  const m = path.match(/extensions\/([^/]+)\/frontend\/index\.jsx$/);
  return m ? m[1] : null;
};

export const normalizeExtension = (id, mod) => {
  const spec = (mod && (mod.default || mod)) || {};
  return {
    id: spec.id || id,
    routes: (spec.routes || []).map((r) => ({ errorBoundary: true, ...r })),
    navGroups: spec.navGroups || [],
    themes: spec.themes || {},
    pageContext: {
      routeMap: spec.pageContext?.routeMap || {},
      paramRoutes: spec.pageContext?.paramRoutes || [],
    },
    chatSurfaces: spec.chatSurfaces || [],
    storeSlice: spec.storeSlice || null,
    layout: spec.layout || {},
    logo: spec.logo || null,
    landingRoute: spec.landingRoute || null,
  };
};

export const loadExtensions = (mods = modules) =>
  Object.entries(mods)
    .map(([path, mod]) => [idFromPath(path), mod])
    .filter(([id]) => id && !id.startsWith("_"))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([id, mod]) => normalizeExtension(id, mod));

export const extensions = loadExtensions();

export const extensionRoutes = () => extensions.flatMap((e) => e.routes);
export const extensionNavGroups = () => extensions.flatMap((e) => e.navGroups);
export const extensionThemes = () => Object.assign({}, ...extensions.map((e) => e.themes));
export const extensionPageContext = () => ({
  routeMap: Object.assign({}, ...extensions.map((e) => e.pageContext.routeMap)),
  paramRoutes: extensions.flatMap((e) => e.pageContext.paramRoutes),
});
export const extensionChatSurfaces = () => extensions.flatMap((e) => e.chatSurfaces);
export const extensionStoreSlices = () => extensions.map((e) => e.storeSlice).filter(Boolean);
export const extensionHeaders = () => extensions.map((e) => e.layout?.header).filter(Boolean);
export const extensionLogo = () => extensions.map((e) => e.logo).find(Boolean) || null;
export const extensionLandingRoute = () => extensions.map((e) => e.landingRoute).find(Boolean) || null;
