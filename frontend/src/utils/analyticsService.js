/**
 * Analytics Service
 *
 * Centralized service for tracking user interactions, events, and analytics
 * Supports multiple tracking backends (Google Analytics, custom endpoints, etc.)
 */

class AnalyticsService {
  constructor() {
    this.enabled = true;
    this.debug = import.meta.env.DEV || import.meta.env.MODE === 'development';
    this.userId = null;
    this.sessionId = this.generateSessionId();
    this.eventQueue = [];
    this.performanceMarks = new Map();
  }

  /**
   * Initialize analytics service
   * @param {Object} config - Configuration options
   */
  initialize(config = {}) {
    this.userId = config.userId || null;
    this.enabled = config.enabled !== false;

    if (this.debug) {
      console.log('[Analytics] Service initialized', {
        userId: this.userId,
        sessionId: this.sessionId,
        enabled: this.enabled
      });
    }

    // Track page load performance
    this.trackPageLoad();
  }

  /**
   * Generate a unique session ID
   */
  generateSessionId() {
    return `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  /**
   * Track a custom event
   * @param {string} eventName - Name of the event
   * @param {Object} properties - Event properties
   * @param {Object} options - Additional options
   */
  trackEvent(eventName, properties = {}, options = {}) {
    if (!this.enabled) return;

    const event = {
      eventName,
      properties: {
        ...properties,
        timestamp: new Date().toISOString(),
        sessionId: this.sessionId,
        userId: this.userId,
        userAgent: navigator.userAgent,
        path: window.location.pathname,
        ...options
      }
    };

    this.eventQueue.push(event);

    if (this.debug) {
      console.log('[Analytics] Event tracked:', event);
    }

    // Send to analytics backends
    this.sendEvent(event);
  }

  /**
   * Track page view
   * @param {string} pageName - Name of the page
   * @param {Object} properties - Additional properties
   */
  trackPageView(pageName, properties = {}) {
    this.trackEvent('page_view', {
      pageName,
      url: window.location.href,
      ...properties
    });
  }

  /**
   * Track user interaction
   * @param {string} componentName - Name of the component
   * @param {string} action - Action performed
   * @param {Object} properties - Additional properties
   */
  trackInteraction(componentName, action, properties = {}) {
    this.trackEvent('user_interaction', {
      componentName,
      action,
      ...properties
    });
  }

  /**
   * Track API call
   * @param {string} endpoint - API endpoint
   * @param {string} method - HTTP method
   * @param {Object} metadata - Additional metadata
   */
  trackApiCall(endpoint, method, metadata = {}) {
    this.trackEvent('api_call', {
      endpoint,
      method,
      ...metadata
    });
  }

  /**
   * Track API error
   * @param {string} endpoint - API endpoint
   * @param {Error} error - Error object
   * @param {Object} context - Additional context
   */
  trackApiError(endpoint, error, context = {}) {
    this.trackEvent('api_error', {
      endpoint,
      errorMessage: error.message,
      errorStack: error.stack,
      errorType: error.name,
      ...context
    });
  }

  /**
   * Track error
   * @param {Error} error - Error object
   * @param {Object} context - Error context
   */
  trackError(error, context = {}) {
    this.trackEvent('error', {
      errorMessage: error.message,
      errorStack: error.stack,
      errorType: error.name,
      severity: context.severity || 'error',
      ...context
    });
  }

  /**
   * Start performance measurement
   * @param {string} markName - Name of the performance mark
   */
  startPerformanceMark(markName) {
    this.performanceMarks.set(markName, performance.now());
  }

  /**
   * End performance measurement and track
   * @param {string} markName - Name of the performance mark
   * @param {Object} properties - Additional properties
   */
  endPerformanceMark(markName, properties = {}) {
    const startTime = this.performanceMarks.get(markName);
    if (!startTime) {
      // Silently skip if no start mark found - this can happen if:
      // - Function is called multiple times concurrently
      // - Component unmounts before async operation completes
      // - Error occurred before start mark was set
      if (this.debug) {
        console.debug(`[Analytics] No start mark found for: ${markName} (skipping)`);
      }
      return;
    }

    const duration = performance.now() - startTime;
    this.performanceMarks.delete(markName);

    this.trackEvent('performance', {
      markName,
      duration,
      durationFormatted: `${duration.toFixed(2)}ms`,
      ...properties
    });

    return duration;
  }

  /**
   * Track page load performance
   */
  trackPageLoad() {
    if (typeof window === 'undefined' || !window.performance) return;

    window.addEventListener('load', () => {
      setTimeout(() => {
        const perfData = window.performance.timing;
        const pageLoadTime = perfData.loadEventEnd - perfData.navigationStart;
        const connectTime = perfData.responseEnd - perfData.requestStart;
        const renderTime = perfData.domComplete - perfData.domLoading;

        this.trackEvent('page_load_performance', {
          pageLoadTime,
          connectTime,
          renderTime,
          domContentLoaded: perfData.domContentLoadedEventEnd - perfData.navigationStart,
          firstPaint: perfData.responseStart - perfData.navigationStart
        });
      }, 0);
    });
  }

  /**
   * Track task-related events
   */
  trackTaskCreated(taskData) {
    this.trackEvent('task_created', {
      taskId: taskData.id,
      projectId: taskData.project_id,
      modelId: taskData.model_id,
      hasInputFile: !!taskData.input_file
    });
  }

  trackTaskUpdated(taskId, updates) {
    this.trackEvent('task_updated', {
      taskId,
      updatedFields: Object.keys(updates),
      ...updates
    });
  }

  trackTaskDeleted(taskId) {
    this.trackEvent('task_deleted', { taskId });
  }

  trackTaskStarted(taskId) {
    this.trackEvent('task_started', { taskId });
  }

  trackTaskStopped(taskId) {
    this.trackEvent('task_stopped', { taskId });
  }

  trackTaskQueued(taskIds) {
    this.trackEvent('task_queued', {
      taskIds,
      count: taskIds.length
    });
  }

  /**
   * Track output-related events
   */
  trackOutputViewed(outputId, metadata = {}) {
    this.trackEvent('output_viewed', {
      outputId,
      ...metadata
    });
  }

  trackOutputRetried(outputId, jobId) {
    this.trackEvent('output_retried', {
      outputId,
      jobId
    });
  }

  trackOutputDeleted(outputId) {
    this.trackEvent('output_deleted', { outputId });
  }

  trackOutputDownloaded(outputId, format, fileSize = null) {
    this.trackEvent('output_downloaded', {
      outputId,
      format,
      fileSize
    });
  }

  trackOutputFiltered(filters) {
    this.trackEvent('output_filtered', {
      filterType: Object.keys(filters),
      ...filters
    });
  }

  /**
   * Track tab/navigation changes
   */
  trackTabChange(from, to, context = {}) {
    this.trackEvent('tab_changed', {
      from,
      to,
      ...context
    });
  }

  /**
   * Track search/filter actions
   */
  trackSearch(query, resultsCount, context = {}) {
    this.trackEvent('search', {
      query,
      resultsCount,
      queryLength: query.length,
      ...context
    });
  }

  trackFilterApplied(filterType, filterValue, context = {}) {
    this.trackEvent('filter_applied', {
      filterType,
      filterValue,
      ...context
    });
  }

  /**
   * Track sorting changes
   */
  trackSortChanged(sortBy, sortOrder, context = {}) {
    this.trackEvent('sort_changed', {
      sortBy,
      sortOrder,
      ...context
    });
  }

  /**
   * Send event to analytics backend(s)
   * @param {Object} event - Event to send
   */
  sendEvent(event) {
    // Google Analytics integration (if available)
    if (typeof window !== 'undefined' && window.gtag) {
      window.gtag('event', event.eventName, event.properties);
    }

    // Could add other backends here:
    // - Mixpanel
    // - Segment
    // - Custom endpoint
    // - Sentry (for errors)

    // Example: Send to custom endpoint
    if (import.meta.env.VITE_ANALYTICS_ENDPOINT) {
      this.sendToCustomEndpoint(event);
    }
  }

  /**
   * Send event to custom analytics endpoint
   * @param {Object} event - Event to send
   */
  async sendToCustomEndpoint(event) {
    try {
      await fetch(import.meta.env.VITE_ANALYTICS_ENDPOINT, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(event),
      });
    } catch (error) {
      if (this.debug) {
        console.error('[Analytics] Failed to send event:', error);
      }
    }
  }

  /**
   * Get all tracked events
   */
  getEvents() {
    return [...this.eventQueue];
  }

  /**
   * Clear event queue
   */
  clearEvents() {
    this.eventQueue = [];
  }

  /**
   * Enable/disable tracking
   */
  setEnabled(enabled) {
    this.enabled = enabled;
    if (this.debug) {
      console.log(`[Analytics] Tracking ${enabled ? 'enabled' : 'disabled'}`);
    }
  }

  /**
   * Set user ID
   */
  setUserId(userId) {
    this.userId = userId;
    if (this.debug) {
      console.log('[Analytics] User ID set:', userId);
    }
  }
}

// Create singleton instance
const analyticsService = new AnalyticsService();

// Auto-initialize
if (typeof window !== 'undefined') {
  window.addEventListener('DOMContentLoaded', () => {
    analyticsService.initialize();
  });
}

export default analyticsService;
