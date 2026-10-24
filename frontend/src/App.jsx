
import React, { Suspense } from "react";
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from "react-router-dom";
import useNavigationCancel from "./hooks/useNavigationCancel";
import useGpuIntent from "./hooks/useGpuIntent";
import useKeyboardForwarding from "./hooks/useKeyboardForwarding";
import useFileDropGuard from "./hooks/useFileDropGuard";
import {
  ThemeProvider as MuiThemeProvider,
  CssBaseline,
  Box,
  CircularProgress,
} from "@mui/material";
import { themes } from "./theme";
import { spacing } from "./theme/tokens";
import { themeCssVars } from "./theme/cssVars";
import { useLocalizedTheme } from "./i18n/muiLocale";
import { useAppStore } from "./stores/useAppStore";
import { BrandLogo } from "./components/branding";
import brand from "./config/brand";
import { landingRouteFor } from "./config/profile";
import { NAV_CHROME, navChromeWidth } from "./config/navCatalog";
import { extensionRoutes, extensionHeaders, extensionLandingRoute } from "./extensions";
import lazyWithReload from "./utils/lazyWithReload";

import TrainingFloater from "./components/agent/TrainingFloater";
import FirstRunProfileDialog from "./components/modals/FirstRunProfileDialog";

// Eagerly loaded — core navigation targets
import DashboardPage from "./pages/DashboardPage";
import ChatPage from "./pages/ChatPage";
import NotFoundPage from "./pages/NotFoundPage";

// Lazy-loaded — loaded on demand when route is visited
const SettingsPage = lazyWithReload(() => import("./pages/SettingsPage"));
const ProjectsPage = lazyWithReload(() => import("./pages/ProjectsPage"));
const TaskPage = lazyWithReload(() => import("./pages/TaskPage"));
const ActivityPage = lazyWithReload(() => import("./pages/ActivityPage"));
const DocumentsPage = lazyWithReload(() => import("./pages/DocumentsPage"));
const RulesPage = lazyWithReload(() => import("./pages/RulesPage"));
const ToolsPage = lazyWithReload(() => import("./pages/ToolsPage"));
const AgentsPage = lazyWithReload(() => import("./pages/AgentsPage"));
const AgentMemoryPage = lazyWithReload(() => import("./pages/AgentMemoryPage"));
const MCPServersPage = lazyWithReload(() => import("./pages/MCPServersPage"));
const WebsitesPage = lazyWithReload(() => import("./pages/WebsitesPage"));
const WebsiteDetailPage = lazyWithReload(() => import("./pages/WebsiteDetailPage"));
const FileGenerationPage = lazyWithReload(() => import("./pages/FileGenerationPage"));
const ProjectDetailPage = lazyWithReload(() => import("./pages/ProjectDetailPage"));
const ClientPage = lazyWithReload(() => import("./pages/ClientPage"));
const UploadPage = lazyWithReload(() => import("./pages/UploadPage"));
const TrainingPage = lazyWithReload(() => import("./pages/TrainingPage"));
const ImagesPage = lazyWithReload(() => import("./pages/ImagesPage"));
const AudioFoundryPage = lazyWithReload(() => import("./pages/AudioFoundryPage"));
const VideoTextOverlayPage = lazyWithReload(() => import("./pages/VideoTextOverlayPage"));
const VideoEditorPage = lazyWithReload(() => import("./pages/VideoEditorPage"));
const BulkImportDocumentsPage = lazyWithReload(() => import("./pages/BulkImportDocumentsPage"));
const CodeEditorPage = lazyWithReload(() => import("./pages/CodeEditorPage"));
const ContentLibraryPage = lazyWithReload(() => import("./pages/ContentLibraryPage"));
const ProgressTestPage = lazyWithReload(() => import("./pages/ProgressTestPage"));
const WordPressSitesPage = lazyWithReload(() => import("./pages/WordPressSitesPage"));
const WordPressPagesPage = lazyWithReload(() => import("./pages/WordPressPagesPage"));
const StickyNotesPage = lazyWithReload(() => import("./pages/StickyNotesPage"));
const DevToolsPage = lazyWithReload(() => import("./pages/DevToolsPage"));
const PluginsPage = lazyWithReload(() => import("./pages/PluginsPage"));
const ConnectionsPage = lazyWithReload(() => import("./pages/ConnectionsPage"));
const SocialAccountsPage = lazyWithReload(() => import("./pages/SocialAccountsPage"));
const SocialPostsPage = lazyWithReload(() => import("./pages/SocialPostsPage"));
const ApprovalsPage = lazyWithReload(() => import("./pages/ApprovalsPage"));
const SwarmPage = lazyWithReload(() => import("./pages/SwarmPage"));
const AutoresearchPage = lazyWithReload(() => import("./pages/AutoresearchPage"));
const OutreachPage = lazyWithReload(() => import("./pages/OutreachPage"));
const VoiceChatPage = lazyWithReload(() => import("./pages/VoiceChatPage"));
const SystemMapPage = lazyWithReload(() => import("./pages/SystemMapPage"));
const SelfImprovementPage = lazyWithReload(() => import("./pages/SelfImprovementPage"));
const FilmCrewPage = lazyWithReload(() => import("./pages/FilmCrewPage"));
const MusicVideoPage = lazyWithReload(() => import("./pages/MusicVideoPage"));
const CastStudioPage = lazyWithReload(() => import("./pages/CastStudioPage"));
const CastMemberPage = lazyWithReload(() => import("./pages/CastMemberPage"));
import Sidebar from "./components/layout/Sidebar";
import SoftwareNav from "./components/layout/SoftwareNav";
import ProgressFooterBar from "./components/layout/ProgressFooterBar";
import { StatusProvider } from "./contexts/StatusContext";
import { HealthProvider } from "./contexts/HealthContext";
import BackendOfflineBanner from "./components/common/BackendOfflineBanner";
import UpdateAppliedBanner from "./components/common/UpdateAppliedBanner";
import ApiKeyRefusalNotice from "./components/common/ApiKeyRefusalNotice";
import { SnackbarProvider } from "./components/common/SnackbarProvider";
import { ErrorProvider } from "./components/common/ErrorProvider";
import ErrorBoundary, { ErrorResetContext } from "./components/common/ErrorBoundary";
import { LayoutProvider } from "./contexts/LayoutContext";
import { UnifiedProgressProvider } from './contexts/UnifiedProgressContext';
import { VoiceProvider } from "./contexts/VoiceContext";
import { VoiceSessionProvider } from "./contexts/VoiceSessionContext";
import FloatingChatProvider from "./components/chat/FloatingChatProvider";
import KeyboardShortcutsOverlay from "./components/common/KeyboardShortcutsOverlay";
import DidYouKnowTip from "./components/common/DidYouKnowTip";
import useUncleNotifications from "./hooks/useUncleNotifications";

function UncleNotificationListener() {
  useUncleNotifications();
  return null;
}

const AppLayout = ({ children }) => {
  // Cancel pending API requests on navigation — prevents DB pool exhaustion
  useNavigationCancel();
  // Signal GPU orchestrator on page navigation for predictive model loading
  useGpuIntent();
  const { pathname } = useLocation();

  const sidebarExpanded = useAppStore((state) => state.sidebarExpanded);
  const navChrome = useAppStore((state) => state.navChrome) || NAV_CHROME.SIDEBAR;
  const isSoftware = navChrome === NAV_CHROME.SOFTWARE;
  const drawerWidth = navChromeWidth(navChrome, sidebarExpanded);
  // Layout slot: an extension may add a header bar above every page.
  const headers = extensionHeaders();

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: isSoftware ? "column" : "row",
        height: "100vh",
        overflow: "hidden",
      }}
    >
      {isSoftware ? <SoftwareNav /> : <Sidebar />}
      <Box
        component="main"
        data-main-content
        sx={(theme) => ({
          flexGrow: 1,
          display: "flex",
          flexDirection: "column",
          width: isSoftware ? "100%" : `calc(100% - ${drawerWidth}px)`,
          height: isSoftware ? undefined : "100vh",
          minHeight: 0,
          minWidth: 0,
          overflow: "hidden",
          position: "relative",
          transition: theme.transitions.create("width", {
            duration: 200,
            easing: theme.transitions.easing.easeInOut,
          }),
        })}
      >
        <Box
          sx={{
            flexGrow: 1,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
            pb: `${spacing.footerHeight}px`,
          }}
        >
          {headers.map((Header, i) => (
            <Header key={i} />
          ))}
          {/* A page that crashes keeps the shell around it. Every boundary
              below clears on a route change without remounting the page. */}
          <ErrorResetContext.Provider value={pathname}>
            <ErrorBoundary>{children}</ErrorBoundary>
          </ErrorResetContext.Provider>
        </Box>
        <ProgressFooterBar />
      </Box>
    </Box>
  );
};

const GlobalTrainer = () => {
  const trainerOpen = useAppStore((state) => state.trainerOpen);
  const setTrainerOpen = useAppStore((state) => state.setTrainerOpen);
  return <TrainingFloater open={trainerOpen} onClose={() => setTrainerOpen(false)} />;
};

const AppContainer = () => {
  const themeName = useAppStore((state) => state.themeName);
  const theme = useLocalizedTheme(
    themes[themeName]?.theme ||
      themes[brand.defaultThemeKey]?.theme ||
      themes["guaardvark"].theme,
  );
  // A profile may land somewhere other than the dashboard (creator: /images).
  // The dashboard stays reachable at /dashboard — unlisted, not removed.
  const profile = useAppStore((state) => state.profile);
  const landingRoute = landingRouteFor(profile) || extensionLandingRoute();
  const fetchSystemInfo = useAppStore((state) => state.fetchSystemInfo);
  const systemName = useAppStore((state) => state.systemName);

  // Route keystrokes to DISPLAY=:99 when the user flips the toggle on either
  // floater. No-op when disabled.
  useKeyboardForwarding();
  useFileDropGuard();

  React.useEffect(() => {
    fetchSystemInfo();

    const initModelCache = async () => {
      try {
        const { initializeModelCache } = await import('./utils/modelUtils');
        await initializeModelCache();
      } catch (error) {
        console.warn('Failed to initialize model cache:', error);
      }
    };
    initModelCache();
  }, [fetchSystemInfo]);

  React.useEffect(() => {
    if (systemName) {
      document.title = systemName;
    } else {
      document.title = brand.appName;
    }
  }, [systemName]);

  // index.css declares these as static values, so body and scrollbars kept the
  // old dark defaults no matter which theme was chosen. Publish the active
  // palette to them, and to the browser chrome colour.
  React.useEffect(() => {
    const { palette } = theme;
    const root = document.documentElement;
    Object.entries(themeCssVars(palette)).forEach(([name, value]) => {
      if (value) root.style.setProperty(name, value);
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", palette.background.default);
  }, [theme]);

  return (
    <MuiThemeProvider theme={theme}>
      <CssBaseline />
      <HealthProvider>
      <BackendOfflineBanner />
      <StatusProvider>
        <Router
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          {/* Inside the Router: the restart modal's API-key advice links to Settings. */}
          <UpdateAppliedBanner />
          <UnifiedProgressProvider>
            <LayoutProvider>
              <VoiceProvider>
              <VoiceSessionProvider>
                <SnackbarProvider>
                  <UncleNotificationListener />
                  <ApiKeyRefusalNotice />
                  <ErrorProvider>
                    <FirstRunProfileDialog />
                    <Suspense fallback={<Box sx={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", height: "100vh", gap: 2 }}><BrandLogo size={64} animate /><CircularProgress size={24} /></Box>}>
                    <Routes>
                      <Route
                        path="/"
                        element={
                          landingRoute ? (
                            <Navigate to={landingRoute} replace />
                          ) : (
                            <AppLayout>
                              <ErrorBoundary>
                                <DashboardPage />
                              </ErrorBoundary>
                            </AppLayout>
                          )
                        }
                      />
                      <Route
                        path="/dashboard"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <DashboardPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/notes"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <StickyNotesPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/chat"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ChatPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/chat/:projectId"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ChatPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/documents"
                        element={
                          <AppLayout>
                            <DocumentsPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/documents/bulk-import"
                        element={
                          <AppLayout>
                            <BulkImportDocumentsPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/tasks"
                        element={
                          <AppLayout>
                            <TaskPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/activity"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ActivityPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/projects"
                        element={
                          <AppLayout>
                            <ProjectsPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/projects/:projectId"
                        element={
                          <AppLayout>
                            <ProjectDetailPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/clients"
                        element={
                          <AppLayout>
                            <ClientPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/websites"
                        element={
                          <AppLayout>
                            <WebsitesPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/websites/:websiteId"
                        element={
                          <AppLayout>
                            <WebsiteDetailPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/images"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ImagesPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/audio"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <AudioFoundryPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/video"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ImagesPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/infographic"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ImagesPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/upscaling"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ImagesPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/video-text-overlay"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <VideoTextOverlayPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/video-editor"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <VideoEditorPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/batch-images"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ImagesPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/rules"
                        element={
                          <AppLayout>
                            <RulesPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/tools"
                        element={
                          <AppLayout>
                            <ToolsPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/agents"
                        element={
                          <AppLayout>
                            <AgentsPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/agents/memory"
                        element={
                          <AppLayout>
                            <AgentMemoryPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/agents/mcp"
                        element={
                          <AppLayout>
                            <MCPServersPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/training"
                        element={
                          <AppLayout>
                            <TrainingPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/file-generation"
                        element={
                          <AppLayout>
                            <FileGenerationPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/settings"
                        element={
                          <AppLayout>
                            <SettingsPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/voice-chat"
                        element={
                          <AppLayout>
                            <VoiceChatPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/progress-test"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ProgressTestPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/dev-tools"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <DevToolsPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/plugins"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <PluginsPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/connections"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ConnectionsPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/social/accounts"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <SocialAccountsPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/social/posts"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <SocialPostsPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/approvals"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <ApprovalsPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/swarm"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <SwarmPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/autoresearch"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <AutoresearchPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/film-crew"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <FilmCrewPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/music-video"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <MusicVideoPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/cast"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <CastStudioPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/cast/:subjectId"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <CastMemberPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/code-editor"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <CodeEditorPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/code-editor/:projectId"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <CodeEditorPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/upload"
                        element={
                          <AppLayout>
                            <UploadPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/content-library"
                        element={
                          <AppLayout>
                            <ContentLibraryPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/wordpress/sites"
                        element={
                          <AppLayout>
                            <WordPressSitesPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/wordpress/pages"
                        element={
                          <AppLayout>
                            <WordPressPagesPage />
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/outreach"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <OutreachPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/system-map"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <SystemMapPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      <Route
                        path="/self-improvement"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <SelfImprovementPage />
                            </ErrorBoundary>
                          </AppLayout>
                        }
                      />
                      {/* Extension routes (extensions/<id>/frontend/index.jsx), ahead of the catch-all. */}
                      {extensionRoutes().map((r) => (
                        <Route
                          key={r.path}
                          path={r.path}
                          element={
                            <AppLayout>
                              {r.errorBoundary ? <ErrorBoundary>{r.element}</ErrorBoundary> : r.element}
                            </AppLayout>
                          }
                        />
                      ))}
                      <Route
                        path="*"
                        element={
                          <AppLayout>
                            <NotFoundPage />
                          </AppLayout>
                        }
                      />
                    </Routes>
                    </Suspense>
                    <FloatingChatProvider />
                    <GlobalTrainer />
                    <KeyboardShortcutsOverlay />
                    <DidYouKnowTip />
                  </ErrorProvider>
                </SnackbarProvider>
              </VoiceSessionProvider>
              </VoiceProvider>
            </LayoutProvider>
          </UnifiedProgressProvider>
        </Router>
      </StatusProvider>
      </HealthProvider>
    </MuiThemeProvider>
  );
};

function App() {
  return (
    <ErrorBoundary fullPage>
      <AppContainer />
    </ErrorBoundary>
  );
}

export default App;
