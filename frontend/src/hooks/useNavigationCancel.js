/**
 * useNavigationCancel — Global navigation-aware request cancellation.
 *
 * Mount this ONCE in App.jsx. It watches for route changes and aborts
 * all pending API requests from the previous page. This prevents DB
 * connection pool exhaustion when users click through sidebar items quickly.
 *
 * How it works:
 * 1. Monkey-patches window.fetch to track all in-flight requests
 * 2. On route change, aborts all tracked requests
 * 3. Exempt requests: Socket.IO, persistent flag, health checks
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

// Global tracking of in-flight AbortControllers
const activeControllers = new Set();

// Paths that should NOT be cancelled on navigation
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

    activeControllers.add(controller);

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
 * Cancel all in-flight non-exempt requests.
 * Called automatically on route change, or manually if needed.
 */
export function cancelAllPendingRequests() {
  const count = activeControllers.size;
  if (count > 0) {
    for (const controller of activeControllers) {
      try { controller.abort(); } catch (_) { /* ignore */ }
    }
    activeControllers.clear();
    if (count > 2) {
      // Only log if we cancelled more than trivial background polls
      console.debug(`[NavigationCancel] Aborted ${count} pending requests`);
    }
  }
}

/**
 * Get count of currently in-flight requests (for debugging).
 */
export function getPendingRequestCount() {
  return activeControllers.size;
}

/**
 * React hook — mount in App.jsx. Cancels requests on every route change.
 */
export default function useNavigationCancel() {
  const location = useLocation();
  const previousPath = useRef(location.pathname);

  // Patch fetch on first mount
  useEffect(() => {
    patchFetch();
  }, []);

  // Cancel pending requests when route changes
  useEffect(() => {
    if (previousPath.current !== location.pathname) {
      cancelAllPendingRequests();
      previousPath.current = location.pathname;
    }
  }, [location.pathname]);
}
