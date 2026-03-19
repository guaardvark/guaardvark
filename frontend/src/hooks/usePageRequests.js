/**
 * usePageRequests — Auto-cancel API requests when a page unmounts.
 *
 * Solves the DB connection pool exhaustion caused by rapid navigation:
 * when users click through sidebar items quickly, pending requests from
 * the previous page hold DB connections until they complete. This hook
 * cancels them on unmount, freeing connections immediately.
 *
 * Usage in any page component:
 *
 *   import usePageRequests from '../hooks/usePageRequests';
 *
 *   function MyPage() {
 *     const { get, post, fetchWithCancel, isMounted } = usePageRequests();
 *
 *     useEffect(() => {
 *       const loadData = async () => {
 *         const data = await get('/some-endpoint');
 *         if (data && isMounted()) {
 *           setMyState(data);
 *         }
 *       };
 *       loadData();
 *     }, []);
 *
 *     // All pending requests auto-cancel when the component unmounts.
 *     // No cleanup code needed — just use get/post instead of fetch.
 *   }
 *
 * For long-running operations (image gen, video gen, indexing):
 *   const data = await get('/long-operation', { persistent: true });
 *   // persistent=true means this request survives unmount
 */

import { useRef, useEffect, useCallback } from 'react';
import { BASE_URL, handleResponse } from '../api/apiClient';

export default function usePageRequests() {
  const controllersRef = useRef(new Set());
  const unmountedRef = useRef(false);

  // On unmount: abort all non-persistent requests
  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      for (const controller of controllersRef.current) {
        try { controller.abort(); } catch (_) { /* ignore */ }
      }
      controllersRef.current.clear();
    };
  }, []);

  /**
   * Wrap any fetch call with auto-cancel on unmount.
   * Drop-in replacement: just pass the same args as fetch().
   */
  const fetchWithCancel = useCallback(async (url, options = {}) => {
    const { persistent = false, ...fetchOptions } = options;

    const controller = new AbortController();
    if (!persistent) {
      controllersRef.current.add(controller);
    }

    // Merge signals — if caller already has a signal, listen to both
    if (fetchOptions.signal) {
      const callerSignal = fetchOptions.signal;
      callerSignal.addEventListener('abort', () => controller.abort());
    }
    fetchOptions.signal = controller.signal;

    try {
      const response = await fetch(url, fetchOptions);
      return response;
    } catch (error) {
      if (error.name === 'AbortError') {
        return null; // Cancelled due to navigation — not an error
      }
      throw error;
    } finally {
      controllersRef.current.delete(controller);
    }
  }, []);

  /**
   * GET convenience — returns parsed JSON data or null if cancelled.
   */
  const get = useCallback(async (path, options = {}) => {
    const url = path.startsWith('http') ? path : `${BASE_URL}${path.startsWith('/') ? '' : '/'}${path}`;
    const response = await fetchWithCancel(url, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    if (!response) return null; // Cancelled
    return handleResponse(response);
  }, [fetchWithCancel]);

  /**
   * POST convenience — returns parsed JSON data or null if cancelled.
   */
  const post = useCallback(async (path, body = {}, options = {}) => {
    const url = path.startsWith('http') ? path : `${BASE_URL}${path.startsWith('/') ? '' : '/'}${path}`;
    const response = await fetchWithCancel(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      ...options,
    });
    if (!response) return null;
    return handleResponse(response);
  }, [fetchWithCancel]);

  /**
   * PUT convenience.
   */
  const put = useCallback(async (path, body = {}, options = {}) => {
    const url = path.startsWith('http') ? path : `${BASE_URL}${path.startsWith('/') ? '' : '/'}${path}`;
    const response = await fetchWithCancel(url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      ...options,
    });
    if (!response) return null;
    return handleResponse(response);
  }, [fetchWithCancel]);

  /**
   * Cancel all pending requests for this page (useful before heavy ops).
   */
  const cancelAll = useCallback(() => {
    for (const controller of controllersRef.current) {
      try { controller.abort(); } catch (_) { /* ignore */ }
    }
    controllersRef.current.clear();
  }, []);

  /**
   * Check if the component is still mounted (use after await).
   */
  const isMounted = useCallback(() => !unmountedRef.current, []);

  return { fetchWithCancel, get, post, put, cancelAll, isMounted };
}
