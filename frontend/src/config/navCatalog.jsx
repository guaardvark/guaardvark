// Canonical navigation catalog. Sidebar and the software workspace chrome
// both project this list; profiles hide by path; extensions prepend groups.
import React from "react";
import { spacing } from "../theme/tokens";

import DashboardIcon from "@mui/icons-material/Dashboard";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import ArticleIcon from "@mui/icons-material/Article";
import FolderIcon from "@mui/icons-material/Folder";
import LanguageIcon from "@mui/icons-material/Language";
import RuleFolderIcon from "@mui/icons-material/RuleFolder";
import SettingsIcon from "@mui/icons-material/Settings";
import AccountBoxIcon from "@mui/icons-material/AccountBox";
import PetsIcon from "@mui/icons-material/Pets";
import ImageIcon from "@mui/icons-material/Image";
import GraphicEqIcon from "@mui/icons-material/GraphicEq";
import CodeIcon from "@mui/icons-material/Code";
import LibraryBooksIcon from "@mui/icons-material/LibraryBooks";
import BuildIcon from "@mui/icons-material/Build";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import StickyNote2Icon from "@mui/icons-material/StickyNote2";
import ExtensionIcon from "@mui/icons-material/Extension";
import HubIcon from "@mui/icons-material/Hub";
import RuleIcon from "@mui/icons-material/Rule";
import HiveIcon from "@mui/icons-material/Hive";
import CampaignIcon from "@mui/icons-material/Campaign";
import TextFieldsIcon from "@mui/icons-material/TextFields";
import QueueIcon from "@mui/icons-material/Queue";
import MonitorHeartIcon from "@mui/icons-material/MonitorHeart";
import MovieFilterIcon from "@mui/icons-material/MovieFilter";
import LocalMoviesIcon from "@mui/icons-material/LocalMovies";
import MusicVideoIcon from "@mui/icons-material/MusicVideo";
import BubbleChartIcon from "@mui/icons-material/BubbleChart";
import ScienceIcon from "@mui/icons-material/Science";
import PhotoLibraryIcon from "@mui/icons-material/PhotoLibrary";
import VideoCameraBackIcon from "@mui/icons-material/VideoCameraBack";
import InsertChartOutlinedIcon from "@mui/icons-material/InsertChartOutlined";
import AutoFixHighIcon from "@mui/icons-material/AutoFixHigh";
import RecordVoiceOverIcon from "@mui/icons-material/RecordVoiceOver";
import SchoolIcon from "@mui/icons-material/School";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DriveFolderUploadIcon from "@mui/icons-material/DriveFolderUpload";
import WebAssetIcon from "@mui/icons-material/WebAsset";
import DescriptionIcon from "@mui/icons-material/Description";
import BugReportIcon from "@mui/icons-material/BugReport";
import BarChartIcon from "@mui/icons-material/BarChart";
import DesktopWindowsIcon from "@mui/icons-material/DesktopWindows";

export const NAV_CHROME = Object.freeze({
  SIDEBAR: "sidebar",
  SOFTWARE: "software",
});

export const SIDEBAR_GROUPS = Object.freeze([
  "Main",
  "Studio",
  "Management",
  "Configuration",
]);

export const WORKSPACES = Object.freeze([
  { id: "home", label: "Home" },
  { id: "chat", label: "Chat" },
  { id: "studio", label: "Studio" },
  { id: "library", label: "Library" },
  { id: "code", label: "Code" },
  { id: "work", label: "Work" },
  { id: "agents", label: "Agents" },
  { id: "system", label: "System" },
]);

const page = (entry) => ({
  listed: true,
  kind: "page",
  stripOrder: 100,
  ...entry,
});

const softwarePage = (entry) => page({ listed: false, ...entry });

const action = (entry) => ({
  listed: false,
  kind: "action",
  ...entry,
});

export const CORE_NAV_CATALOG = Object.freeze([
  page({
    id: "dashboard",
    path: "/",
    label: "Dashboard",
    icon: <DashboardIcon />,
    sidebarGroup: "Main",
    menu: "View",
    workspace: "home",
    stripOrder: 10,
  }),
  page({
    id: "chat",
    path: "/chat",
    label: "Chat",
    icon: <ChatBubbleOutlineIcon />,
    sidebarGroup: "Main",
    menu: "Chat",
    workspace: "chat",
    stripOrder: 10,
  }),
  page({
    id: "code-editor",
    path: "/code-editor",
    label: "Code Editor",
    icon: <CodeIcon />,
    sidebarGroup: "Main",
    menu: "View",
    workspace: "code",
    stripOrder: 10,
  }),
  page({
    id: "files",
    path: "/documents",
    label: "Files",
    icon: <ArticleIcon />,
    sidebarGroup: "Main",
    menu: "Library",
    workspace: "library",
    stripOrder: 10,
  }),
  page({
    id: "media",
    path: "/images",
    label: "Media",
    icon: <PhotoLibraryIcon />,
    sidebarGroup: "Main",
    menu: "Library",
    workspace: "library",
    stripOrder: 20,
  }),
  page({
    id: "notes",
    path: "/notes",
    label: "Notes",
    icon: <StickyNote2Icon />,
    sidebarGroup: "Main",
    menu: "Library",
    workspace: "home",
    stripOrder: 20,
  }),

  page({
    id: "film-crew",
    path: "/film-crew",
    label: "Film Crew",
    icon: <LocalMoviesIcon />,
    sidebarGroup: "Studio",
    menu: "Studio",
    workspace: "studio",
    stripOrder: 80,
  }),
  page({
    id: "cast",
    path: "/cast",
    label: "Cast & LoRA",
    icon: <AccountBoxIcon />,
    sidebarGroup: "Studio",
    menu: "Studio",
    workspace: "studio",
    stripOrder: 90,
  }),
  page({
    id: "music-video",
    path: "/music-video",
    label: "Music Video",
    icon: <MusicVideoIcon />,
    sidebarGroup: "Studio",
    menu: "Studio",
    workspace: "studio",
    stripOrder: 100,
  }),
  page({
    id: "video-editor",
    path: "/video-editor",
    label: "Video Editor",
    icon: <MovieFilterIcon />,
    sidebarGroup: "Studio",
    menu: "Studio",
    workspace: "studio",
    stripOrder: 60,
  }),
  page({
    id: "video-gen",
    path: "/video",
    label: "Video Gen",
    icon: <VideoCameraBackIcon />,
    sidebarGroup: "Studio",
    menu: "Studio",
    workspace: "studio",
    stripOrder: 10,
  }),
  page({
    id: "image-gen",
    path: "/batch-images",
    label: "Image Gen",
    icon: <ImageIcon />,
    sidebarGroup: "Studio",
    menu: "Studio",
    workspace: "studio",
    stripOrder: 20,
  }),
  page({
    id: "infographic",
    path: "/infographic",
    label: "Infographic",
    icon: <InsertChartOutlinedIcon />,
    sidebarGroup: "Studio",
    menu: "Studio",
    workspace: "studio",
    stripOrder: 30,
  }),
  page({
    id: "upscaling",
    path: "/upscaling",
    label: "Upscaling",
    icon: <AutoFixHighIcon />,
    sidebarGroup: "Studio",
    menu: "Studio",
    workspace: "studio",
    stripOrder: 40,
  }),
  page({
    id: "audio",
    path: "/audio",
    label: "Audio Studio",
    icon: <GraphicEqIcon />,
    sidebarGroup: "Studio",
    menu: "Studio",
    workspace: "studio",
    stripOrder: 50,
  }),
  page({
    id: "video-text",
    path: "/video-text-overlay",
    label: "Video Text",
    icon: <TextFieldsIcon />,
    sidebarGroup: "Studio",
    menu: "Studio",
    workspace: "studio",
    stripOrder: 70,
  }),

  page({
    id: "clients",
    path: "/clients",
    label: "Clients",
    icon: <AccountBoxIcon />,
    sidebarGroup: "Management",
    menu: "Work",
    workspace: "work",
    stripOrder: 10,
  }),
  page({
    id: "projects",
    path: "/projects",
    label: "Projects",
    icon: <FolderIcon />,
    sidebarGroup: "Management",
    menu: "Work",
    workspace: "work",
    stripOrder: 20,
  }),
  page({
    id: "websites",
    path: "/websites",
    label: "Websites",
    icon: <LanguageIcon />,
    sidebarGroup: "Management",
    menu: "Work",
    workspace: "work",
    stripOrder: 30,
  }),
  page({
    id: "jobs",
    path: "/tasks",
    label: "Jobs",
    icon: <QueueIcon />,
    sidebarGroup: "Management",
    menu: "Work",
    workspace: "work",
    stripOrder: 60,
  }),
  page({
    id: "activity",
    path: "/activity",
    label: "Activity",
    icon: <MonitorHeartIcon />,
    sidebarGroup: "Management",
    menu: "Work",
    workspace: "work",
    stripOrder: 70,
  }),
  page({
    id: "outreach",
    path: "/outreach",
    label: "Outreach",
    icon: <CampaignIcon />,
    sidebarGroup: "Management",
    menu: "Work",
    workspace: "work",
    stripOrder: 80,
  }),

  page({
    id: "rules",
    path: "/rules",
    label: "Rules & Prompts",
    icon: <RuleFolderIcon />,
    sidebarGroup: "Configuration",
    menu: "Agents",
    workspace: "agents",
    stripOrder: 80,
  }),
  page({
    id: "agent-tools",
    path: "/tools",
    label: "Agent Tools",
    icon: <BuildIcon />,
    sidebarGroup: "Configuration",
    menu: "Agents",
    workspace: "agents",
    stripOrder: 20,
  }),
  page({
    id: "agents",
    path: "/agents",
    label: "Agents",
    icon: <SmartToyIcon />,
    sidebarGroup: "Configuration",
    menu: "Agents",
    workspace: "agents",
    stripOrder: 10,
  }),
  page({
    id: "filegen",
    path: "/file-generation",
    label: "FileGen",
    icon: <PetsIcon />,
    sidebarGroup: "Configuration",
    menu: "Agents",
    workspace: "agents",
    stripOrder: 60,
  }),
  page({
    id: "csvgen",
    path: "/content-library",
    label: "CSVGen",
    icon: <LibraryBooksIcon />,
    sidebarGroup: "Configuration",
    menu: "Library",
    workspace: "library",
    stripOrder: 30,
  }),
  page({
    id: "swarm",
    path: "/swarm",
    label: "Swarm",
    icon: <HiveIcon />,
    sidebarGroup: "Configuration",
    menu: "Agents",
    workspace: "agents",
    stripOrder: 40,
  }),
  page({
    id: "autoresearch",
    path: "/autoresearch",
    label: "Autoresearch",
    icon: <ScienceIcon />,
    sidebarGroup: "Configuration",
    menu: "Agents",
    workspace: "agents",
    stripOrder: 50,
  }),
  page({
    id: "plugins",
    path: "/plugins",
    label: "Plugins",
    icon: <ExtensionIcon />,
    sidebarGroup: "Configuration",
    menu: "View",
    workspace: "system",
    stripOrder: 20,
  }),
  page({
    id: "connections",
    path: "/connections",
    label: "Connections",
    icon: <HubIcon />,
    sidebarGroup: "Configuration",
    menu: "View",
    workspace: "system",
    stripOrder: 30,
  }),
  page({
    id: "approvals",
    path: "/approvals",
    label: "Approvals",
    icon: <RuleIcon />,
    sidebarGroup: "Configuration",
    menu: "Agents",
    workspace: "agents",
    stripOrder: 70,
    badge: "pendingApprovals",
  }),
  page({
    id: "system-map",
    path: "/system-map",
    label: "System Map",
    icon: <BubbleChartIcon />,
    sidebarGroup: "Configuration",
    menu: "View",
    workspace: "system",
    stripOrder: 40,
  }),
  page({
    id: "settings",
    path: "/settings",
    label: "Settings",
    icon: <SettingsIcon />,
    sidebarGroup: "Configuration",
    menu: "File",
    workspace: "system",
    stripOrder: 10,
    pinned: true,
  }),

  softwarePage({
    id: "voice-chat",
    path: "/voice-chat",
    label: "Voice Chat",
    icon: <RecordVoiceOverIcon />,
    sidebarGroup: "Main",
    menu: "Chat",
    workspace: "chat",
    stripOrder: 20,
  }),
  softwarePage({
    id: "training",
    path: "/training",
    label: "Training",
    icon: <SchoolIcon />,
    sidebarGroup: "Configuration",
    menu: "Agents",
    workspace: "agents",
    stripOrder: 30,
  }),
  softwarePage({
    id: "upload",
    path: "/upload",
    label: "Upload",
    icon: <UploadFileIcon />,
    sidebarGroup: "Main",
    menu: "File",
    workspace: "library",
    stripOrder: 40,
  }),
  softwarePage({
    id: "bulk-import",
    path: "/documents/bulk-import",
    label: "Bulk Import",
    icon: <DriveFolderUploadIcon />,
    sidebarGroup: "Main",
    menu: "File",
    workspace: "library",
    stripOrder: 50,
  }),
  softwarePage({
    id: "wordpress-sites",
    path: "/wordpress/sites",
    label: "WordPress Sites",
    icon: <WebAssetIcon />,
    sidebarGroup: "Management",
    menu: "Work",
    workspace: "work",
    stripOrder: 40,
  }),
  softwarePage({
    id: "wordpress-pages",
    path: "/wordpress/pages",
    label: "WordPress Pages",
    icon: <DescriptionIcon />,
    sidebarGroup: "Management",
    menu: "Work",
    workspace: "work",
    stripOrder: 50,
  }),
  softwarePage({
    id: "dev-tools",
    path: "/dev-tools",
    label: "System Dashboard",
    icon: <BugReportIcon />,
    sidebarGroup: "Configuration",
    menu: "View",
    workspace: "system",
    stripOrder: 50,
  }),

  action({
    id: "system-metrics",
    label: "System Metrics",
    icon: <BarChartIcon />,
    menu: "View",
  }),
  action({
    id: "agent-screen",
    label: "Agent Screen",
    icon: <DesktopWindowsIcon />,
    menu: "View",
  }),
]);

/**
 * Horizontal space the current chrome occupies on the left of the canvas.
 * Software chrome is a top bar, so this is 0.
 */
export function navChromeWidth(chrome, sidebarExpanded) {
  if (chrome === NAV_CHROME.SOFTWARE) return 0;
  return sidebarExpanded ? spacing.sidebarExpanded : spacing.sidebarCollapsed;
}

/**
 * Where a catalog item should navigate. `/` is the profile landing redirect,
 * so Dashboard always opens the unlisted `/dashboard` alias.
 */
export function hrefForItem(item) {
  if (!item?.path) return "/dashboard";
  return item.path === "/" ? "/dashboard" : item.path;
}

/**
 * Active-state for a catalog path. Requires a `/` boundary after the prefix
 * so `/video` does not light up on `/video-editor`.
 */
export function pathIsActive(itemPath, pathname) {
  if (itemPath === "/") {
    return pathname === "/" || pathname === "/dashboard";
  }
  return pathname === itemPath || pathname.startsWith(`${itemPath}/`);
}

/** Longest matching page in the catalog for `pathname`. */
export function matchCatalogItem(catalog, pathname) {
  let best = null;
  let bestLength = -1;
  for (const item of catalog) {
    if (item.kind !== "page" || !item.path) continue;
    if (pathIsActive(item.path, pathname) && item.path.length > bestLength) {
      best = item;
      bestLength = item.path.length;
    }
  }
  return best;
}

/**
 * Turn extension `navGroups` into catalog entries, prepended so they list
 * ahead of core the same way Sidebar concatenates groups today.
 */
export function buildNavCatalog(extensionGroups = []) {
  const extras = [];
  for (const group of extensionGroups) {
    const workspace =
      WORKSPACES.find((w) => w.label === group.label)?.id || "system";
    for (const item of group.items || []) {
      extras.push({
        id: `ext:${item.path}`,
        path: item.path,
        label: item.text,
        icon: item.icon,
        sidebarGroup: group.label,
        menu: group.label,
        workspace,
        listed: true,
        kind: "page",
        badge: item.badge,
        stripOrder: 100,
      });
    }
  }
  return extras.length ? [...extras, ...CORE_NAV_CATALOG] : CORE_NAV_CATALOG;
}

/** Drop catalog entries whose path the profile hides. Actions have no path. */
export function filterCatalog(catalog, hiddenRoutes = []) {
  if (!hiddenRoutes || hiddenRoutes.length === 0) return catalog;
  const hidden = new Set(hiddenRoutes);
  return catalog.filter((item) => !item.path || !hidden.has(item.path));
}

/**
 * Sidebar projection: listed pages, grouped in first-seen `sidebarGroup` order.
 * Pass CORE_NAV_CATALOG for the brand groups; callers still prepend extension
 * groups the same way Sidebar always has.
 */
export function catalogToNavGroups(catalog) {
  const groups = [];
  const indexByLabel = new Map();

  for (const item of catalog) {
    if (item.kind !== "page" || item.listed === false) continue;
    let group = indexByLabel.get(item.sidebarGroup);
    if (!group) {
      group = { label: item.sidebarGroup, items: [] };
      indexByLabel.set(item.sidebarGroup, group);
      groups.push(group);
    }
    const navItem = { text: item.label, icon: item.icon, path: item.path };
    if (item.badge) navItem.badge = item.badge;
    group.items.push(navItem);
  }

  return groups;
}

export function toolsForWorkspace(catalog, workspaceId) {
  return catalog
    .filter((item) => item.kind === "page" && item.workspace === workspaceId)
    .slice()
    .sort((a, b) => (a.stripOrder ?? 100) - (b.stripOrder ?? 100));
}

export function visibleWorkspaces(catalog) {
  return WORKSPACES.filter((workspace) => toolsForWorkspace(catalog, workspace.id).length > 0);
}

export function workspaceBadge(catalog, workspaceId, badgeCounts = {}) {
  return toolsForWorkspace(catalog, workspaceId).reduce((sum, item) => {
    if (!item.badge) return sum;
    return sum + (badgeCounts[item.badge] || 0);
  }, 0);
}
