
import React, { Suspense, lazy } from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import useNavigationCancel from "./hooks/useNavigationCancel";
import useGpuIntent from "./hooks/useGpuIntent";
import useKeyboardForwarding from "./hooks/useKeyboardForwarding";
import {
  ThemeProvider as MuiThemeProvider,
  CssBaseline,
  Box,
  CircularProgress,
} from "@mui/material";
import { themes } from "./theme";
import { spacing } from "./theme/tokens";
import { useAppStore } from "./stores/useAppStore";
import { GuaardvarkLogo } from "./components/branding";

import TrainingFloater from "./components/agent/TrainingFloater";

// Eagerly loaded — core navigation targets
import DashboardPage from "./pages/DashboardPage";
import ChatPage from "./pages/ChatPage";
import NotFoundPage from "./pages/NotFoundPage";

// Lazy-loaded — loaded on demand when route is visited
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const ProjectsPage = lazy(() => import("./pages/ProjectsPage"));
const TaskPage = lazy(() => import("./pages/TaskPage"));
const ActivityPage = lazy(() => import("./pages/ActivityPage"));
const DocumentsPage = lazy(() => import("./pages/DocumentsPage"));
const RulesPage = lazy(() => import("./pages/RulesPage"));
const ToolsPage = lazy(() => import("./pages/ToolsPage"));
const AgentsPage = lazy(() => import("./pages/AgentsPage"));
const WebsitesPage = lazy(() => import("./pages/WebsitesPage"));
const FileGenerationPage = lazy(() => import("./pages/FileGenerationPage"));
const ProjectDetailPage = lazy(() => import("./pages/ProjectDetailPage"));
const ClientPage = lazy(() => import("./pages/ClientPage"));
const UploadPage = lazy(() => import("./pages/UploadPage"));
const TrainingPage = lazy(() => import("./pages/TrainingPage"));
const ImagesPage = lazy(() => import("./pages/ImagesPage"));
const AudioFoundryPage = lazy(() => import("./pages/AudioFoundryPage"));
const VideoGeneratorPage = lazy(() => import("./pages/VideoGeneratorPage"));
const VideoTextOverlayPage = lazy(() => import("./pages/VideoTextOverlayPage"));
const VideoEditorPage = lazy(() => import("./pages/VideoEditorPage"));
const BulkImportDocumentsPage = lazy(() => import("./pages/BulkImportDocumentsPage"));
const CodeEditorPage = lazy(() => import("./pages/CodeEditorPage"));
const ContentLibraryPage = lazy(() => import("./pages/ContentLibraryPage"));
const ProgressTestPage = lazy(() => import("./pages/ProgressTestPage"));
const WordPressSitesPage = lazy(() => import("./pages/WordPressSitesPage"));
const WordPressPagesPage = lazy(() => import("./pages/WordPressPagesPage"));
const StickyNotesPage = lazy(() => import("./pages/StickyNotesPage"));
const DevToolsPage = lazy(() => import("./pages/DevToolsPage"));
const PluginsPage = lazy(() => import("./pages/PluginsPage"));
const SwarmPage = lazy(() => import("./pages/SwarmPage"));
const OutreachPage = lazy(() => import("./pages/OutreachPage"));
const VoiceChatPage = lazy(() => import("./pages/VoiceChatPage"));
import Sidebar from "./components/layout/Sidebar";
import ProgressFooterBar from "./components/layout/ProgressFooterBar";
import { StatusProvider } from "./contexts/StatusContext";
import { SnackbarProvider } from "./components/common/SnackbarProvider";
import { ErrorProvider } from "./components/common/ErrorProvider";
import ErrorBoundary from "./components/common/ErrorBoundary";
import { LayoutProvider } from "./contexts/LayoutContext";
import { UnifiedProgressProvider } from './contexts/UnifiedProgressContext';
import { VoiceProvider } from "./contexts/VoiceContext";
import FloatingChatProvider from "./components/chat/FloatingChatProvider";
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

  const sidebarExpanded = useAppStore((state) => state.sidebarExpanded);
  const drawerWidth = sidebarExpanded ? spacing.sidebarExpanded : spacing.sidebarCollapsed;

  return (
    <Box sx={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <Box
        component="main"
        data-main-content
        sx={(theme) => ({
          flexGrow: 1,
          display: "flex",
          flexDirection: "column",
          width: `calc(100% - ${drawerWidth}px)`,
          height: "100vh",
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
          {children}
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
  const theme = themes[themeName]?.theme || themes["guaardvark"].theme;
  const fetchSystemInfo = useAppStore((state) => state.fetchSystemInfo);
  const systemName = useAppStore((state) => state.systemName);

  // Route keystrokes to DISPLAY=:99 when the user flips the toggle on either
  // floater. No-op when disabled.
  useKeyboardForwarding();

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
      document.title = "Guaardvark";
    }
  }, [systemName]);

  return (
    <MuiThemeProvider theme={theme}>
      <CssBaseline />
      <StatusProvider>
        <Router
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <UnifiedProgressProvider>
            <LayoutProvider>
              <VoiceProvider>
                <SnackbarProvider>
                  <UncleNotificationListener />
                  <ErrorProvider>
                    <Suspense fallback={<Box sx={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", height: "100vh", gap: 2 }}><GuaardvarkLogo size={64} animate /><CircularProgress size={24} /></Box>}>
                    <Routes>
                      <Route
                        path="/"
                        element={
                          <AppLayout>
                            <ErrorBoundary>
                              <DashboardPage />
                            </ErrorBoundary>
                          </AppLayout>
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
                              <VideoGeneratorPage />
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
                  </ErrorProvider>
                </SnackbarProvider>
              </VoiceProvider>
            </LayoutProvider>
          </UnifiedProgressProvider>
        </Router>
      </StatusProvider>
    </MuiThemeProvider>
  );
};

function App() {
  return (
    <ErrorBoundary>
      <AppContainer />
    </ErrorBoundary>
  );
}

export default App;
