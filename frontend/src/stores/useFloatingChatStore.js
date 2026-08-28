import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

// Enough for a working day of floating-chat turns; older ones live in the
// session history on the server.
const MAX_PERSISTED_MESSAGES = 200;

export const useFloatingChatStore = create(
  persist(
    // eslint-disable-next-line no-unused-vars
    (set, get) => ({
      // Visibility
      isOpen: false,
      setIsOpen: (open) => set({ isOpen: open }),
      toggleOpen: () => set((state) => ({ isOpen: !state.isOpen })),

      // Window geometry (persisted)
      position: { x: -1, y: -1 }, // -1 signals "use default" on first render
      setPosition: (pos) => set({ position: pos }),
      size: { w: 380, h: 520 },
      setSize: (size) => set({ size }),
      collapsed: false,
      setCollapsed: (collapsed) => set({ collapsed }),
      toggleCollapsed: () => set((s) => ({ collapsed: !s.collapsed })),

      // Chat state. The thread and its session id persist so a page refresh
      // brings the conversation back; "+" starts a new one.
      messages: [],
      addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
      updateMessage: (id, updates) =>
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === id ? { ...m, ...updates } : m
          ),
        })),
      setMessages: (msgs) => set({ messages: msgs }),
      clearMessages: () => set({ messages: [], sessionId: `floating_${Date.now()}` }),

      isSending: false,
      setIsSending: (val) => set({ isSending: val }),

      error: null,
      setError: (error) => set({ error }),
      clearError: () => set({ error: null }),

      sessionId: `floating_${Date.now()}`,
      setSessionId: (id) => set({ sessionId: id }),

      // Page context (updated reactively by FloatingChatProvider). A page that
      // knows what its entity is called sets entityLabel after it loads; a route
      // change clears it so a stale name never sits over a new id.
      pageContext: null,
      setPageContext: (ctx) => set({ pageContext: ctx, entityLabel: null }),
      entityLabel: null,
      setEntityLabel: (label) => set({ entityLabel: label || null }),
    }),
    {
      name: "guaardvark-floating-chat",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        position: state.position,
        size: state.size,
        collapsed: state.collapsed,
        isOpen: state.isOpen,
        sessionId: state.sessionId,
        messages: state.messages.slice(-MAX_PERSISTED_MESSAGES),
      }),
      merge: (persistedState, currentState) => ({
        ...currentState,
        ...persistedState,
      }),
    }
  )
);
