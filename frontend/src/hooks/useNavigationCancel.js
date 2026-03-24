/**
 * useNavigationCancel — Global navigation-aware request cancellation.
 *
 * Mount this ONCE in App.jsx. It watches for route changes and aborts
 * stale API requests from the previous page. This prevents DB connection
 * pool exhaustion when users click through sidebar items quickly.
 *
 * How it works:
 * 1. Monkey-patches window.fetch to track all in-flight requests with timestamps
 * 2. On route change, aborts requests older than GRACE_MS (new page's fetches survive)
 * 3. Exempt requests: Socket.IO, persistent flag, long-running operations
 *
 * Usage in App.jsx:
 *   import useNavigationCancel from './hooks/useNavigationCancel';
 *
 *   function App() {
 *     useNavigationCancel();
 *     return <RouterProvider ... />;
 *   }
 */

import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';

// Global tracking of in-flight AbortControllers → creation timestamp
const activeControllers = new Map();

// Requests younger than this won't be aborted — protects new page fetches
// that fire in the same React render cycle as the route change.
const GRACE_MS = 150;

// Paths that should NEVER be cancelled on navigation
const EXEMPT_PATTERNS = [
  '/socket.io',
  '/api/batch-image/',    // Image generation in progress
  '/api/batch-video/',    // Video generation in progress
  '/api/indexing/',       // Document indexing
  '/api/self-improvement/trigger',  // SI run
  '/api/voice/',          // Voice processing
  '/health',              // Health checks
  '/api/meta/',           // System status polling (UnifiedProgressContext)
  '/api/model/',          // Model switching (long-running)
  '/api/plugins/',        // Plugin management
  '/api/gpu/',            // GPU orchestrator + coordinator signals
  '/api/rules',           // Slash command registry (persistent config)
  '/api/chat/',           // Chat messages + history (must not abort mid-conversation)
  '/api/settings/',       // Settings reads (lightweight, needed everywhere)
];

function isExempt(url) {
  return EXEMPT_PATTERNS.some(pattern => url.includes(pattern));
}

let isPatched = false;

function patchFetch() {
  if (isPatched) return;
  isPatched = true;

  const originalFetch = window.fetch;

  window.fetch = function patchedFetch(input, init = {}) {
    const url = typeof input === 'string' ? input : input?.url || '';

    // Don't track exempt requests
    if (isExempt(url)) {
      return originalFetch.call(this, input, init);
    }

    // Don't track if caller already has persistent flag
    if (init._persistent) {
      const { _persistent: _, ...cleanInit } = init;
      return originalFetch.call(this, input, cleanInit);
    }

    // Create an AbortController if none exists
    let controller;
    if (init.signal) {
      // Caller has their own signal — wrap it so we can also abort
      controller = new AbortController();
      const callerSignal = init.signal;

      // If caller aborts, we abort too
      if (!callerSignal.aborted) {
        callerSignal.addEventListener('abort', () => controller.abort(), { once: true });
      } else {
        controller.abort();
      }
      init = { ...init, signal: controller.signal };
    } else {
      controller = new AbortController();
      init = { ...init, signal: controller.signal };
    }

    activeControllers.set(controller, Date.now());

    const promise = originalFetch.call(this, input, init);

    // Clean up controller when request completes (success or fail)
    promise.then(
      () => activeControllers.delete(controller),
      () => activeControllers.delete(controller),
    );

    return promise;
  };
}

/**
 * Cancel stale in-flight requests (older than GRACE_MS).
 * Requests from the new page are younger than GRACE_MS and survive.
 * Called automatically on route change, or manually if needed.
 */
export function cancelAllPendingRequests() {
  const now = Date.now();
  let aborted = 0;
  for (const [controller, timestamp] of activeControllers) {
    if (now - timestamp > GRACE_MS) {
      try { controller.abort(); } catch (_) { /* ignore */ }
      activeControllers.delete(controller);
      aborted++;
    }
  }
  if (aborted > 2) {
    console.debug(`[NavigationCancel] Aborted ${aborted} stale requests (kept ${activeControllers.size} recent)`);
  }
}

/**
 * Get count of currently in-flight requests (for debugging).
 */
export function getPendingRequestCount() {
  return activeControllers.size;
}

/**
 * React hook — mount in App.jsx. Cancels stale requests on every route change.
 */
export default function useNavigationCancel() {
  const location = useLocation();
  const previousPath = useRef(location.pathname);

  // Patch fetch on first mount
  useEffect(() => {
    patchFetch();
  }, []);

  // Cancel stale requests when route changes
  useEffect(() => {
    if (previousPath.current !== location.pathname) {
      cancelAllPendingRequests();
      previousPath.current = location.pathname;
    }
  }, [location.pathname]);
}
