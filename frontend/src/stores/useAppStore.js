import { create } from "zustand";
import { persist, createJSONStorage, subscribeWithSelector, devtools } from "zustand/middleware";

// eslint-disable-next-line no-unused-vars
const createUISlice = (set, get) => ({
  themeName: "guaardvark",
  setThemeName: (name) => set({ themeName: name }),
  
  dashboardLayout: [],
  setDashboardLayout: (layout) => set({ dashboardLayout: layout }),
  
  sidebarExpanded: false,
  setSidebarExpanded: (expanded) => set({ sidebarExpanded: expanded }),
  toggleSidebar: () => set((state) => ({ sidebarExpanded: !state.sidebarExpanded })),

  listenerModeEnabled: false,
  toggleListenerMode: () => set((state) => ({ listenerModeEnabled: !state.listenerModeEnabled })),

  trainerOpen: false,
  setTrainerOpen: (open) => set({ trainerOpen: open }),

  // Keyboard → Agent Screen forwarding. When true, a global keydown listener
  // forwards keystrokes to :99 via /api/agent-control/learn/input. Mirrored
  // on both the AgentScreenViewer and TrainingFloater headers — flip from
  // either. Skips events whose target is a local input/textarea/contentEditable
  // so the rest of the Guaardvark UI stays usable.
  keyboardForwardingEnabled: false,
  setKeyboardForwardingEnabled: (v) => set({ keyboardForwardingEnabled: v }),
  toggleKeyboardForwarding: () =>
    set((state) => ({ keyboardForwardingEnabled: !state.keyboardForwardingEnabled })),

  // Lesson Pearls — transient (not persisted). While activeLessonId is set,
  // thumbs-up feedback carries lesson_id to the backend and the floater
  // renders accumulating pearls via the lesson:pearl_added socket event.
  activeLessonId: null,
  setActiveLessonId: (id) => set({ activeLessonId: id }),
  lessonPearls: [],
  addLessonPearl: (pearl) =>
    set((state) => ({ lessonPearls: [...state.lessonPearls, pearl] })),
  clearLessonPearls: () => set({ lessonPearls: [] }),

  // True when the AgentScreenViewer is mounted/visible. The chat sender reads
  // this to decide whether to flip `agent_screen_active` in the chat POST
  // options. When false, the backend routes vision models through the normal
  // ReACT path instead of the screen-action direct path.
  agentScreenOpen: false,
  setAgentScreenOpen: (open) => set({ agentScreenOpen: open }),

  isLoading: false,
  setIsLoading: (loading) => set({ isLoading: loading }),
  
  error: null,
  setError: (error) => set({ error }),
  clearError: () => set({ error: null }),
});

const createDataSlice = (set, get) => ({
  projects: [],
  clients: [],
  activeModel: null,
  activeProjectId: null,
  systemName: null,
  systemLogo: null,
  isFetchingSystemInfo: false,

  setProjects: (projects) => set({ projects }),
  setClients: (clients) => set({ clients }),
  setActiveModel: (model) => set({ activeModel: model }),
  setActiveProjectId: (id) => set({ activeProjectId: id }),
  setSystemInfo: (name, logo) => set({
    systemName: name,
    systemLogo: logo
  }),

  // eslint-disable-next-line no-unused-vars
  fetchSystemInfo: async (force = false) => {
    const { setIsLoading, setError, clearError, isFetchingSystemInfo } = get();

    if (isFetchingSystemInfo) {
      return;
    }

    set({ isFetchingSystemInfo: true });

    try {
      setIsLoading(true);
      clearError();
      
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10000);
      const res = await fetch("/api/settings/branding", { signal: controller.signal });
      clearTimeout(timer);
      if (!res.ok) {
        throw new Error(`Failed to fetch branding: ${res.status}`);
      }
      
      const response = await res.json();
      const data = response.data || response;
      set({
        systemName: data.system_name || null,
        systemLogo: data.logo_path || null,
      });
      clearError();
    } catch (err) {
      console.warn("useAppStore: failed to fetch branding", err);
      setError(err.message);
    } finally {
      setIsLoading(false);
      set({ isFetchingSystemInfo: false });
    }
  },
  
  getActiveProject: () => {
    const { projects, activeProjectId } = get();
    return projects.find(p => p.id === activeProjectId) || null;
  },
});

export const useAppStore = create(
  subscribeWithSelector(
    devtools(
      persist(
        (set, get) => ({
          ...createUISlice(set, get),
          ...createDataSlice(set, get),
        }),
        {
          name: "guaardvark-app-storage",
          storage: createJSONStorage(() => localStorage),
          partialize: (state) => ({
            themeName: state.themeName,
            dashboardLayout: state.dashboardLayout,
            sidebarExpanded: state.sidebarExpanded,
            listenerModeEnabled: state.listenerModeEnabled,
            activeModel: state.activeModel,
            activeProjectId: state.activeProjectId,
            systemName: state.systemName,
            systemLogo: state.systemLogo,
          }),
          merge: (persistedState, currentState) => ({
            ...currentState,
            ...persistedState,
          }),
        },
      ),
      {
        name: "Guaardvark App Store",
      }
    )
  )
);

export const useAppSelectors = {
  themeName: (state) => state.themeName,
  
  projects: (state) => state.projects,
  clients: (state) => state.clients,
  activeModel: (state) => state.activeModel,
  activeProject: (state) => state.getActiveProject(),
  
  isLoading: (state) => state.isLoading,
  error: (state) => state.error,
  
  systemInfo: (state) => ({
    name: state.systemName,
    logo: state.systemLogo,
  }),
};
