import { create } from "zustand";
import { persist, createJSONStorage, subscribeWithSelector, devtools } from "zustand/middleware";
import brand from "../config/brand";
import { DEFAULT_PROFILE } from "../config/profile";
import { extensionStoreSlices } from "../extensions";
import { DEFAULT_ACTIVATION_MODE, isActivationMode } from "../config/voiceDefaults";
import { translate } from "../i18n";

// Extensions may add state (extensions/<id>/frontend/index.jsx storeSlice);
// their persisted keys join partialize below.
const extensionSlices = extensionStoreSlices();
const createExtensionSlices = (set, get) =>
  Object.assign({}, ...extensionSlices.map((slice) => (slice.state ? slice.state(set, get) : {})));
const extensionPersistedKeys = extensionSlices.flatMap((slice) => slice.partialize || []);

const createUISlice = (set, get) => ({
  themeName: brand.defaultThemeKey,
  setThemeName: (name) => set({ themeName: name }),
  
  dashboardLayout: [],
  setDashboardLayout: (layout) => set({ dashboardLayout: layout }),
  
  sidebarExpanded: false,
  setSidebarExpanded: (expanded) => set({ sidebarExpanded: expanded }),
  toggleSidebar: () => set((state) => ({ sidebarExpanded: !state.sidebarExpanded })),

  navChrome: "sidebar",
  setNavChrome: (chrome) => {
    if (chrome !== "sidebar" && chrome !== "software") return;
    set({ navChrome: chrome });
  },

  // What a click on a mic button does: "push" | "toggle" | "handsfree"
  // (config/voiceDefaults.js). Wake word and VAD live in the voice settings.
  voiceActivationMode: DEFAULT_ACTIVATION_MODE,
  setVoiceActivationMode: (mode) => {
    if (!isActivationMode(mode)) return;
    set({ voiceActivationMode: mode });
  },

  // "Did you know" tips (components/common/DidYouKnowTip.jsx); Settings → General turns them off.
  tipsEnabled: true,
  setTipsEnabled: (on) => set({ tipsEnabled: Boolean(on) }),

  // Set while the update notice floats above the footer, so a tip stays out of its way. Not persisted.
  updateNoticeVisible: false,
  setUpdateNoticeVisible: (visible) => set({ updateNoticeVisible: Boolean(visible) }),

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

  // Modal session mode — per-session "chat" | "agent". Backend at
  // /api/chat-sessions/<id>/mode is the source of truth; this is just a
  // cache so ChatInput can render visual chrome and route messages without
  // re-fetching every keystroke. ChatInput hydrates the cache on
  // session-change; slash handlers update both backend (PATCH) and cache.
  // Sessions whose mode is unknown to the cache default to "chat".
  sessionModes: {},
  setSessionMode: (sessionId, mode) =>
    set((state) => ({
      sessionModes: { ...state.sessionModes, [sessionId]: mode },
    })),
  getSessionMode: (sessionId) => (get().sessionModes[sessionId] || "chat"),

  // Per-session plan mode (research first, act on an approved plan), cached
  // the same way: the session row at /api/chat-sessions/<id>/mode is the
  // source of truth, ChatInput hydrates it, /plan and the toggle update both.
  sessionPlanModes: {},
  setSessionPlanMode: (sessionId, on) =>
    set((state) => ({
      sessionPlanModes: { ...state.sessionPlanModes, [sessionId]: Boolean(on) },
    })),
  getSessionPlanMode: (sessionId) => Boolean(get().sessionPlanModes[sessionId]),

  // Per-session "thinking" override for thinking-capable models (gemma4:12b,
  // qwen3, ...), toggled via the /thinking command. Value is true/false when the
  // user has explicitly set it, or undefined when unset (→ backend falls back to
  // the global `chat_thinking_default` Setting). unifiedChatService only sends
  // `think` in the chat options when this is defined.
  sessionThinking: {},
  setSessionThinking: (sessionId, on) =>
    set((state) => ({
      sessionThinking: { ...state.sessionThinking, [sessionId]: on },
    })),
  getSessionThinking: (sessionId) => get().sessionThinking[sessionId],

  // Persisted chat image model (/imagemodel). Backend SystemSetting is source of
  // truth; hydrated on ChatPage mount. Used for /imagine and LLM image tools.
  chatImageModel: "auto",
  setChatImageModel: (model) => set({ chatImageModel: model || "auto" }),

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
  // The active profile from /api/settings/branding. Deliberately not
  // persisted: a stale copy must not outlive a change to .env.
  profile: DEFAULT_PROFILE,
  // First run: no profile chosen yet (no .env key, no marker, no extension). Not persisted.
  profileFirstRun: false,
  // The first branding fetch has finished (either way), so profileFirstRun can be trusted. Not persisted.
  systemInfoLoaded: false,
  isFetchingSystemInfo: false,

  setProjects: (projects) => set({ projects }),
  setClients: (clients) => set({ clients }),
  setActiveModel: (model) => set({ activeModel: model }),
  setActiveProjectId: (id) => set({ activeProjectId: id }),
  setSystemInfo: (name, logo) => set({
    systemName: name,
    systemLogo: logo
  }),
  setProfile: (profile) => set({ profile: profile ? { ...DEFAULT_PROFILE, ...profile } : DEFAULT_PROFILE }),
  setProfileFirstRun: (pending) => set({ profileFirstRun: Boolean(pending) }),

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
        throw new Error(translate("messages.appStore.brandingFailed", { status: res.status }));
      }
      
      const response = await res.json();
      const data = response.data || response;
      set({
        systemName: data.system_name || null,
        systemLogo: data.logo_path || null,
        profile: data.profile ? { ...DEFAULT_PROFILE, ...data.profile } : DEFAULT_PROFILE,
        profileFirstRun: Boolean(data.profile_first_run),
      });
      clearError();
    } catch (err) {
      console.warn("useAppStore: failed to fetch branding", err);
      setError(err.message);
    } finally {
      setIsLoading(false);
      set({ isFetchingSystemInfo: false, systemInfoLoaded: true });
    }
  },
  
  getActiveProject: () => {
    const { projects, activeProjectId } = get();
    return projects.find(p => p.id === activeProjectId) || null;
  },
});

/**
 * Persisted state over the defaults. A browser that saved the old Listener
 * toggle on (listenerModeEnabled) opens in Hands-free; an explicit
 * voiceActivationMode wins, and the old key is not carried forward.
 */
export function mergePersistedAppState(persistedState, currentState) {
  const { listenerModeEnabled, ...persisted } = persistedState || {};
  const merged = { ...currentState, ...persisted };
  if (!isActivationMode(persisted.voiceActivationMode)) {
    merged.voiceActivationMode =
      listenerModeEnabled === true ? "handsfree" : currentState.voiceActivationMode;
  }
  return merged;
}

export const useAppStore = create(
  subscribeWithSelector(
    devtools(
      persist(
        (set, get) => ({
          ...createUISlice(set, get),
          ...createDataSlice(set, get),
          ...createExtensionSlices(set, get),
        }),
        {
          name: "guaardvark-app-storage",
          storage: createJSONStorage(() => localStorage),
          partialize: (state) => ({
            themeName: state.themeName,
            dashboardLayout: state.dashboardLayout,
            sidebarExpanded: state.sidebarExpanded,
            navChrome: state.navChrome,
            voiceActivationMode: state.voiceActivationMode,
            tipsEnabled: state.tipsEnabled,
            activeModel: state.activeModel,
            activeProjectId: state.activeProjectId,
            systemName: state.systemName,
            systemLogo: state.systemLogo,
            ...Object.fromEntries(extensionPersistedKeys.map((k) => [k, state[k]])),
          }),
          merge: mergePersistedAppState,
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
  profile: (state) => state.profile,
  
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
