// frontend/src/stores/useModelDownloadsStore.js
// Model downloads outlive the dialog that starts them. This store is the one
// reader of each downloader's status: it polls while a download runs, toasts
// how it ended, tells pages their model list changed, and works through an
// "install all" list one item at a time. The model dialogs only render it.

import { useEffect, useRef } from "react";
import axios from "axios";
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { translate } from "../i18n";

export const POLL_MS = 1000;

// Each server-side downloader. Infographic installs go through the image one.
// envelope: the status sits under `data` ({success, data}) rather than beside
// `success`. modelKey: the field naming the model being fetched.
export const DOWNLOAD_KINDS = {
  image: {
    statusUrl: "/api/batch-image/models/download-status",
    startUrl: "/api/batch-image/models/download",
    envelope: true,
    modelKey: "current_model",
  },
  video: {
    statusUrl: "/api/batch-video/models/download-status",
    startUrl: "/api/batch-video/models/download",
    envelope: true,
    modelKey: "current_model",
  },
  voice: {
    statusUrl: "/api/voice/models/download-status",
    startUrl: "/api/voice/models/download",
    envelope: true,
    modelKey: "current_model",
  },
  audio: {
    statusUrl: "/api/audio-foundry/models/download-status",
    startUrl: "/api/audio-foundry/models/download",
    envelope: false,
    modelKey: "current_id",
  },
};

const KINDS = Object.keys(DOWNLOAD_KINDS);
const perKind = (value) => Object.fromEntries(KINDS.map((k) => [k, typeof value === "function" ? value() : value]));

/** The model a status names, under the kind's own field. */
export const downloadModelOf = (kind, status) => status?.[DOWNLOAD_KINDS[kind].modelKey] ?? null;

// Timers and request ordering live outside the state: they are not rendered.
const timers = new Map();
const requestSeq = perKind(0);
const appliedSeq = perKind(0);
const queueBusy = new Set();

const schedule = (kind) => {
  if (timers.has(kind)) return;
  timers.set(
    kind,
    setTimeout(() => {
      timers.delete(kind);
      useModelDownloadsStore.getState().refresh(kind);
    }, POLL_MS),
  );
};

const initialState = () => ({
  // Last status read per downloader, as the server sent it; null until read.
  statuses: perKind(null),
  // Display names of the downloads started here, so the footer and the toast
  // say "Z-Image Turbo" rather than a repo path.
  names: perKind(null),
  // Bumped when a download ends; pages and dialogs reload their lists on it.
  versions: perKind(0),
  // Items an "install all" still has to start: { body, name, status }.
  queues: perKind(() => []),
  // A download another kind handed to the video downloader (image-editing
  // packs, FLUX, MiniMax Music). The kind that started it reports it, so the
  // video kind's own view of the same run stays out of the footer and toasts.
  videoMirrored: false,
  // showMessage(message, severity) from the app's snackbar.
  notify: null,
});

export const useModelDownloadsStore = create(
  persist(
    (set, get) => ({
      ...initialState(),

      setNotifier: (notify) => set({ notify }),

      /** Read one downloader's status now. Keeps polling while it runs. */
      refresh: async (kind) => {
        const cfg = DOWNLOAD_KINDS[kind];
        if (!cfg) return null;
        const seq = ++requestSeq[kind];
        let next;
        try {
          const res = await axios.get(cfg.statusUrl);
          const body = res?.data;
          if (!body?.success) throw new Error("status read failed");
          next = cfg.envelope ? body.data || {} : body;
        } catch {
          // A missed read during a download: try again on the next tick.
          if (get().statuses[kind]?.is_downloading) schedule(kind);
          return get().statuses[kind];
        }
        // A read sent before a newer read or a start answers for an older moment.
        if (seq < appliedSeq[kind]) return get().statuses[kind];
        appliedSeq[kind] = seq;
        get()._apply(kind, next);
        return next;
      },

      refreshAll: () => Promise.all(KINDS.map((k) => get().refresh(k))),

      /**
       * A dialog started a download: show it as running at once and poll it.
       * status: the optimistic status in the kind's own shape.
       */
      markStarted: (kind, status, name = null) => {
        // Reads already in flight predate this start.
        appliedSeq[kind] = ++requestSeq[kind];
        set((s) => ({
          statuses: { ...s.statuses, [kind]: { ...status, is_downloading: true } },
          names: { ...s.names, [kind]: name },
        }));
        schedule(kind);
      },

      /** Install each item in turn; items: [{ body, name, status }]. */
      installAll: (kind, items) => {
        if (!items?.length) return;
        set((s) => ({ queues: { ...s.queues, [kind]: [...s.queues[kind], ...items] } }));
        if (get().statuses[kind]?.is_downloading) schedule(kind);
        else get()._runQueue(kind);
      },

      _apply: (kind, next) => {
        const state = get();
        const prev = state.statuses[kind];
        const was = Boolean(prev?.is_downloading);
        const now = Boolean(next?.is_downloading);
        const mirrorsVideo = kind !== "video" && next?.delegated_to === "video";
        set((s) => ({
          statuses: { ...s.statuses, [kind]: next },
          videoMirrored: s.videoMirrored || (now && mirrorsVideo),
        }));
        if (was && !now) get()._finish(kind, next);
        if (now) schedule(kind);
        else if (get().queues[kind]?.length) get()._runQueue(kind);
      },

      _finish: (kind, status) => {
        const state = get();
        const name = state.names[kind] || downloadModelOf(kind, status) || "";
        const quiet = kind === "video" && state.videoMirrored;
        if (!quiet) {
          if (status.status === "completed") {
            state.notify?.(translate("models.shared.downloadCompletedNamed", { name }), "success");
          } else if (status.status === "failed") {
            state.notify?.(
              translate("models.shared.downloadFailedNamed", {
                name,
                error: status.error || translate("models.shared.unknownError"),
              }),
              "error",
            );
          }
        }
        set((s) => ({
          names: { ...s.names, [kind]: null },
          versions: {
            ...s.versions,
            [kind]: s.versions[kind] + 1,
            // The video downloader fetched it, so video lists change too.
            ...(status.delegated_to === "video" && kind !== "video" ? { video: s.versions.video + 1 } : {}),
          },
          videoMirrored: kind === "video" ? false : s.videoMirrored,
        }));
      },

      _runQueue: async (kind) => {
        if (queueBusy.has(kind)) return;
        const [item, ...rest] = get().queues[kind] || [];
        if (!item) return;
        queueBusy.add(kind);
        set((s) => ({ queues: { ...s.queues, [kind]: rest } }));
        let startNext = false;
        try {
          const res = await axios.post(DOWNLOAD_KINDS[kind].startUrl, item.body);
          const body = res?.data || {};
          if (body.success && (body.already_installed || body.data?.already_installed)) {
            set((s) => ({ versions: { ...s.versions, [kind]: s.versions[kind] + 1 } }));
            startNext = true;
          } else if (body.success) {
            get().markStarted(kind, item.status || {}, item.name);
          } else {
            get().notify?.(body.error || translate("models.shared.failedToStartDownload"), "error");
            startNext = true;
          }
        } catch (err) {
          if (err?.response?.status === 409) {
            // Something else holds the downloader; the rest would be refused too.
            set((s) => ({ queues: { ...s.queues, [kind]: [] } }));
            get().notify?.(translate("models.shared.downloadInProgress"), "warning");
          } else {
            const reason = err?.response?.data?.error;
            const message = typeof reason === "string" ? reason : reason?.message || err?.message;
            get().notify?.(message || translate("models.shared.errorStartingDownload"), "error");
            startNext = true;
          }
        } finally {
          queueBusy.delete(kind);
        }
        if (startNext) get()._runQueue(kind);
      },
    }),
    {
      name: "guaardvark-model-downloads",
      // Per tab: a reload keeps an "install all" going, another tab does not
      // start the same list again.
      storage: createJSONStorage(() => sessionStorage),
      partialize: (s) => ({ queues: s.queues, names: s.names }),
      merge: (persisted, current) => ({
        ...current,
        queues: { ...current.queues, ...(persisted?.queues || {}) },
        names: { ...current.names, ...(persisted?.names || {}) },
      }),
    },
  ),
);

/** For tests: forget every status, queue and timer. */
export const resetModelDownloads = () => {
  timers.forEach((t) => clearTimeout(t));
  timers.clear();
  queueBusy.clear();
  KINDS.forEach((k) => {
    requestSeq[k] = 0;
    appliedSeq[k] = 0;
  });
  useModelDownloadsStore.setState(initialState());
};

/** The kind's last status, or `fallback` before the first read. */
export const useDownloadStatus = (kind, fallback) =>
  useModelDownloadsStore((s) => s.statuses[kind]) || fallback;

/** True while an "install all" still has items to start for this kind. */
export const useInstallAllPending = (kind) => useModelDownloadsStore((s) => (s.queues[kind]?.length || 0) > 0);

/**
 * Calls onChange each time a download of this kind ends (not on mount), so a
 * page reloads its model list even when the dialog was closed long ago.
 */
export function useModelsChanged(kind, onChange) {
  const version = useModelDownloadsStore((s) => s.versions[kind]);
  const callbackRef = useRef(onChange);
  useEffect(() => {
    callbackRef.current = onChange;
  });
  const seenRef = useRef(version);
  useEffect(() => {
    if (version === seenRef.current) return;
    seenRef.current = version;
    callbackRef.current?.();
  }, [version]);
}
