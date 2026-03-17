import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

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

      // Chat state (NOT persisted)
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

      // Page context (updated reactively by FloatingChatProvider)
      pageContext: null,
      setPageContext: (ctx) => set({ pageContext: ctx }),
    }),
    {
      name: "guaardvark-floating-chat",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        position: state.position,
        size: state.size,
        collapsed: state.collapsed,
      }),
      merge: (persistedState, currentState) => ({
        ...currentState,
        ...persistedState,
      }),
    }
  )
);
