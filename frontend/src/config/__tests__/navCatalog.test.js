import { describe, it, expect } from "vitest";
import {
  CORE_NAV_CATALOG,
  NAV_CHROME,
  SIDEBAR_GROUPS,
  catalogToNavGroups,
  filterCatalog,
  buildNavCatalog,
  pathIsActive,
  matchCatalogItem,
  hrefForItem,
  navChromeWidth,
  toolsForWorkspace,
  visibleWorkspaces,
  workspaceBadge,
} from "../navCatalog";
import { spacing } from "../../theme/tokens";

const listedPaths = (groups) =>
  groups.flatMap((group) => group.items.map((item) => [group.label, item.text, item.path, item.badge || null]));

describe("catalogToNavGroups", () => {
  const groups = catalogToNavGroups(CORE_NAV_CATALOG);

  it("projects the same sidebar groups and items as the current rail", () => {
    expect(groups.map((group) => group.label)).toEqual([...SIDEBAR_GROUPS]);
    expect(listedPaths(groups)).toEqual([
      ["Main", "Dashboard", "/", null],
      ["Main", "Chat", "/chat", null],
      ["Main", "Code Editor", "/code-editor", null],
      ["Main", "Files", "/documents", null],
      ["Main", "Media", "/images", null],
      ["Main", "Notes", "/notes", null],
      ["Studio", "Film Crew", "/film-crew", null],
      ["Studio", "Cast & LoRA", "/cast", null],
      ["Studio", "Music Video", "/music-video", null],
      ["Studio", "Video Editor", "/video-editor", null],
      ["Studio", "Video Gen", "/video", null],
      ["Studio", "Image Gen", "/batch-images", null],
      ["Studio", "Infographic", "/infographic", null],
      ["Studio", "Upscaling", "/upscaling", null],
      ["Studio", "Audio Studio", "/audio", null],
      ["Studio", "Video Text", "/video-text-overlay", null],
      ["Management", "Clients", "/clients", null],
      ["Management", "Projects", "/projects", null],
      ["Management", "Websites", "/websites", null],
      ["Management", "Jobs", "/tasks", null],
      ["Management", "Activity", "/activity", null],
      ["Management", "Outreach", "/outreach", null],
      ["Configuration", "Rules & Prompts", "/rules", null],
      ["Configuration", "Agent Tools", "/tools", null],
      ["Configuration", "Agents", "/agents", null],
      ["Configuration", "FileGen", "/file-generation", null],
      ["Configuration", "CSVGen", "/content-library", null],
      ["Configuration", "Swarm", "/swarm", null],
      ["Configuration", "Autoresearch", "/autoresearch", null],
      ["Configuration", "Plugins", "/plugins", null],
      ["Configuration", "Connections", "/connections", null],
      ["Configuration", "Approvals", "/approvals", "pendingApprovals"],
      ["Configuration", "System Map", "/system-map", null],
      ["Configuration", "Settings", "/settings", null],
    ]);
  });

  it("does not list software-only pages or actions in the sidebar", () => {
    const paths = new Set(groups.flatMap((group) => group.items.map((item) => item.path)));
    expect(paths.has("/training")).toBe(false);
    expect(paths.has("/voice-chat")).toBe(false);
    expect(paths.has("/upload")).toBe(false);
    expect(paths.has("/dev-tools")).toBe(false);
    expect(paths.has("/wordpress/sites")).toBe(false);
    expect(paths.has("/progress-test")).toBe(false);
  });

  it("keeps an icon on every listed item", () => {
    for (const group of groups) {
      for (const item of group.items) {
        expect(item.icon).toBeTruthy();
      }
    }
  });
});

describe("filterCatalog", () => {
  it("unlists hidden paths without dropping actions", () => {
    const filtered = filterCatalog(CORE_NAV_CATALOG, ["/notes", "/clients"]);
    const paths = filtered.filter((item) => item.kind === "page").map((item) => item.path);
    expect(paths).not.toContain("/notes");
    expect(paths).not.toContain("/clients");
    expect(filtered.some((item) => item.id === "system-metrics")).toBe(true);
    expect(catalogToNavGroups(filtered).some((group) => group.label === "Main")).toBe(true);
    expect(catalogToNavGroups(filtered).find((group) => group.label === "Main").items.map((i) => i.path)).not.toContain("/notes");
  });
});

describe("buildNavCatalog", () => {
  it("prepends extension items so they list ahead of core", () => {
    const catalog = buildNavCatalog([
      { label: "Acme", items: [{ text: "Roof", path: "/acme", icon: "x" }] },
    ]);
    expect(catalog[0]).toMatchObject({ path: "/acme", sidebarGroup: "Acme", workspace: "system" });
    const groups = catalogToNavGroups(catalog);
    expect(groups[0].label).toBe("Acme");
    expect(groups[1].label).toBe("Main");
  });

  it("maps an extension group named after a workspace onto that workspace", () => {
    const catalog = buildNavCatalog([
      { label: "Studio", items: [{ text: "LUT Lab", path: "/lut", icon: "x" }] },
    ]);
    expect(catalog[0].workspace).toBe("studio");
  });
});

describe("pathIsActive and matchCatalogItem", () => {
  it("does not let /video swallow /video-editor", () => {
    expect(pathIsActive("/video", "/video-editor")).toBe(false);
    expect(pathIsActive("/video", "/video")).toBe(true);
    expect(pathIsActive("/video", "/video/abc")).toBe(true);
    expect(matchCatalogItem(CORE_NAV_CATALOG, "/video-editor").id).toBe("video-editor");
    expect(matchCatalogItem(CORE_NAV_CATALOG, "/video").id).toBe("video-gen");
  });

  it("prefers the longer nested path", () => {
    expect(matchCatalogItem(CORE_NAV_CATALOG, "/documents/bulk-import").id).toBe("bulk-import");
    expect(matchCatalogItem(CORE_NAV_CATALOG, "/cast/42").id).toBe("cast");
  });

  it("treats /dashboard as the dashboard item", () => {
    expect(pathIsActive("/", "/dashboard")).toBe(true);
    expect(matchCatalogItem(CORE_NAV_CATALOG, "/dashboard").id).toBe("dashboard");
    expect(hrefForItem(matchCatalogItem(CORE_NAV_CATALOG, "/"))).toBe("/dashboard");
  });
});

describe("navChromeWidth", () => {
  it("is zero for software chrome and the rail width for the sidebar", () => {
    expect(navChromeWidth(NAV_CHROME.SOFTWARE, true)).toBe(0);
    expect(navChromeWidth(NAV_CHROME.SOFTWARE, false)).toBe(0);
    expect(navChromeWidth(NAV_CHROME.SIDEBAR, true)).toBe(spacing.sidebarExpanded);
    expect(navChromeWidth(NAV_CHROME.SIDEBAR, false)).toBe(spacing.sidebarCollapsed);
    expect(navChromeWidth(undefined, false)).toBe(spacing.sidebarCollapsed);
  });
});

describe("workspaces", () => {
  it("orders studio tools generate-then-produce", () => {
    expect(toolsForWorkspace(CORE_NAV_CATALOG, "studio").map((item) => item.id)).toEqual([
      "video-gen",
      "image-gen",
      "infographic",
      "upscaling",
      "audio",
      "video-editor",
      "video-text",
      "film-crew",
      "cast",
      "music-video",
    ]);
  });

  it("places previously unlisted pages in a workspace", () => {
    const byId = Object.fromEntries(CORE_NAV_CATALOG.map((item) => [item.id, item]));
    expect(byId["voice-chat"].workspace).toBe("chat");
    expect(byId.training.workspace).toBe("agents");
    expect(byId.upload.workspace).toBe("library");
    expect(byId["dev-tools"].workspace).toBe("system");
    expect(byId["wordpress-sites"].workspace).toBe("work");
  });

  it("drops empty workspaces after a profile hides their only tools", () => {
    const filtered = filterCatalog(CORE_NAV_CATALOG, ["/code-editor"]);
    expect(visibleWorkspaces(filtered).map((w) => w.id)).not.toContain("code");
  });

  it("rolls tool badges up to the workspace", () => {
    expect(workspaceBadge(CORE_NAV_CATALOG, "agents", { pendingApprovals: 3 })).toBe(3);
    expect(workspaceBadge(CORE_NAV_CATALOG, "home", { pendingApprovals: 3 })).toBe(0);
  });
});
