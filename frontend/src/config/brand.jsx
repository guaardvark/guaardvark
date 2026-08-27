// frontend/src/config/brand.jsx
// Single white-label seam for the frontend. Downstream distributions
// (private brands built on Guaardvark) override this file — or the scalar
// fields via VITE_BRAND_* env vars — and touch nothing else. Keep every
// brand-identifying value (name, tagline, theme, logo, nav layout, support
// links) flowing through here so upstream merges never collide with a brand.
import React from "react";
import GuaardvarkLogo from "../components/branding/GuaardvarkLogo";

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
import RouterIcon from "@mui/icons-material/Router";
import InsertChartOutlinedIcon from "@mui/icons-material/InsertChartOutlined";
import AutoFixHighIcon from "@mui/icons-material/AutoFixHigh";

const env = import.meta.env;

// Moved verbatim from components/layout/Sidebar.jsx so a brand can reshape
// navigation without touching the Sidebar component.
const navGroups = [
  {
    label: "Main",
    items: [
      { text: "Dashboard", icon: <DashboardIcon />, path: "/" },
      { text: "Chat", icon: <ChatBubbleOutlineIcon />, path: "/chat" },
      { text: "Code Editor", icon: <CodeIcon />, path: "/code-editor" },
      { text: "Files", icon: <ArticleIcon />, path: "/documents" },
      { text: "Media", icon: <PhotoLibraryIcon />, path: "/images" },
      { text: "Notes", icon: <StickyNote2Icon />, path: "/notes" },
    ],
  },
  {
    // Per master-plan §7 (Option A) — surface VideoGen / ImageGen / AudioGen
    // as first-class apps under their own group. Media (the library viewer)
    // stays in Main per the user's note "accessible from Files and Studio";
    // it shows up in Main and any consumer can deep-link from anywhere.
    // "Video Text" is temporary — it gets absorbed into Video Editor in
    // Phase 9 of the editor plan, then this entry goes away.
    label: "Studio",
    items: [
      { text: "Film Crew", icon: <LocalMoviesIcon />, path: "/film-crew" },
      { text: "Cast & LoRA", icon: <AccountBoxIcon />, path: "/cast" },
      { text: "Music Video", icon: <MusicVideoIcon />, path: "/music-video" },
      { text: "Video Editor", icon: <MovieFilterIcon />, path: "/video-editor" },
      { text: "Video Gen", icon: <VideoCameraBackIcon />, path: "/video" },
      { text: "Image Gen", icon: <ImageIcon />, path: "/batch-images" },
      { text: "Infographic", icon: <InsertChartOutlinedIcon />, path: "/infographic" },
      { text: "Upscaling", icon: <AutoFixHighIcon />, path: "/upscaling" },
      { text: "Audio Studio", icon: <GraphicEqIcon />, path: "/audio" },
      { text: "Video Text", icon: <TextFieldsIcon />, path: "/video-text-overlay" },
    ],
  },
  {
    label: "Management",
    items: [
      { text: "Clients", icon: <AccountBoxIcon />, path: "/clients" },
      { text: "Projects", icon: <FolderIcon />, path: "/projects" },
      { text: "Websites", icon: <LanguageIcon />, path: "/websites" },
      // Job scheduler — the legacy TaskPage at /tasks owns creation and
      // queueing of user-initiated jobs (VideoGen, FileGen, scraping,
      // research, code analysis, anything the system can do).
      // Activity is the read-only view of system-driven background work
      // (training, indexing, self-improvement) backed by the new
      // /api/jobs adapter layer.
      { text: "Jobs", icon: <QueueIcon />, path: "/tasks" },
      { text: "Activity", icon: <MonitorHeartIcon />, path: "/activity" },
      { text: "Network Monitor", icon: <RouterIcon />, path: "/network-monitor" },
      { text: "Outreach", icon: <CampaignIcon />, path: "/outreach" },
    ],
  },
  {
    label: "Configuration",
    items: [
      { text: "Rules & Prompts", icon: <RuleFolderIcon />, path: "/rules" },
      { text: "Agent Tools", icon: <BuildIcon />, path: "/tools" },
      { text: "Agents", icon: <SmartToyIcon />, path: "/agents" },
      { text: "FileGen", icon: <PetsIcon />, path: "/file-generation" },
      { text: "CSVGen", icon: <LibraryBooksIcon />, path: "/content-library" },
      { text: "Swarm", icon: <HiveIcon />, path: "/swarm" },
      { text: "Autoresearch", icon: <ScienceIcon />, path: "/autoresearch" },
      { text: "Plugins", icon: <ExtensionIcon />, path: "/plugins" },
      { text: "Connections", icon: <HubIcon />, path: "/connections" },
      // badge: the Sidebar resolves this key to a live count.
      {
        text: "Approvals",
        icon: <RuleIcon />,
        path: "/approvals",
        badge: "pendingApprovals",
      },
      { text: "System Map", icon: <BubbleChartIcon />, path: "/system-map" },
      { text: "Settings", icon: <SettingsIcon />, path: "/settings" },
    ],
  },
];

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
};

export default brand;
