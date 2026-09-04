// frontend/src/config/brand.jsx
// Single white-label seam for the frontend. Downstream distributions
// (private brands built on Guaardvark) override this file — or the scalar
// fields via VITE_BRAND_* env vars — and touch nothing else. Keep every
// brand-identifying value (name, tagline, theme, logo, nav layout, support
// links) flowing through here so upstream merges never collide with a brand.
import GuaardvarkLogo from "../components/branding/GuaardvarkLogo";
import { catalogToNavGroups, CORE_NAV_CATALOG, WORKSPACES } from "./navCatalog";

const env = import.meta.env;

// Sidebar projection of config/navCatalog.jsx. Reshape navigation there;
// this file still exposes the grouped list the Sidebar already consumes.
const navGroups = catalogToNavGroups(CORE_NAV_CATALOG);

export const brand = {
  // Static fallback name. The DB-backed branding setting (Settings →
  // Branding, /api/settings/branding) still wins at runtime once fetched;
  // this covers pre-fetch render and API-failure states.
  appName: env.VITE_BRAND_NAME || "Guaardvark",
  tagline: env.VITE_BRAND_TAGLINE || "",
  // Key into theme/themes.js registry; used as the default and fallback theme.
  defaultThemeKey: env.VITE_BRAND_THEME || "guaardvark",
  // React component rendered wherever the product logo appears.
  logo: GuaardvarkLogo,
  supportLinks: {
    githubRepo: "https://github.com/guaardvark/guaardvark",
    githubSponsors: "https://github.com/sponsors/guaardvark",
    buyMeACoffee: "https://www.buymeacoffee.com/guaardvark",
    koFi: "https://ko-fi.com/albenze",
    paypal: "https://paypal.me/albenze",
    venmo: "https://venmo.com/albenze",
    cashApp: "https://cash.app/$DeanAlbenze",
  },
  navGroups,
  // Full catalog and workspace list behind the Workspaces (top-bar) chrome.
  // Live counts for badge keys come from config/navBadges.js.
  navCatalog: CORE_NAV_CATALOG,
  workspaces: WORKSPACES,
};

export default brand;
