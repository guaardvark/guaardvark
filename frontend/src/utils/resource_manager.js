/**
 * Frontend Resource Manager - System Coordinator Integration
 * Fixes Bug #1 (Race Conditions), Bug #4 (Message Length), Bug #5 (CSV Dialog Race), Bug #8 (Progress Memory Leaks)
 * 
 * This module provides unified resource management for the frontend to prevent:
 * - Race conditions in file upload + text message flow
 * - Message length limit enforcement issues  
 * - Multiple dialog state corruption
 * - Progress event memory leaks
 * - Orphaned API requests and promises
 */

import { nanoid } from 'nanoid';

// =============================================================================
// RESOURCE TYPES AND ENUMS
// =============================================================================

export const ResourceType = {
  API_REQUEST: 'api_request',
  EVENT_LISTENER: 'event_listener',
  CUSTOM_EVENT: 'custom_event',
  PROGRESS_TRACKER: 'progress_tracker',
  DIALOG_STATE: 'dialog_state',
  FILE_UPLOAD: 'file_upload',
  MESSAGE_QUEUE: 'message_queue',
  TIMEOUT: 'timeout',
  INTERVAL: 'interval'
};

export const ProcessType = {
  FILE_UPLOAD: 'file_upload',
  CHAT_MESSAGE: 'chat_message',
  FILE_GENERATION: 'file_generation',
  PROGRESS_TRACKING: 'progress_tracking',
  DIALOG_MANAGEMENT: 'dialog_management'
};

// =============================================================================
// FRONTEND RESOURCE MANAGER
// =============================================================================

class FrontendResourceManager {
  constructor() {
    this.resources = new Map();
    this.processes = new Map();
    this.eventCleanupQueue = new Set();
    this.progressTrackers = new Map();
    this.dialogStates = new Map();
    this.messageQueue = new Map();
    
    // Configuration
    this.maxResources = 1000;
    this.maxMessageLength = 100000; // 100k characters
    
    // ON-DEMAND ONLY: Remove constant cleanup loop
    this.isActive = false;
    this.cleanupInterval = null;
    
    // Only log initialization once per instance
    if (!this.hasLoggedInit) {
      console.log('Frontend Resource Manager initialized (on-demand mode)');
      this.hasLoggedInit = true;
    }
  }
  
  // ON-DEMAND ACTIVATION
  activate() {
    if (this.isActive) return;
    
    this.isActive = true;
    console.log('Resource Manager activated');
    
    // Start cleanup loop only when active
    this.startCleanupLoop();
  }
  
  deactivate() {
    if (!this.isActive) return;
    
    this.isActive = false;
    console.log('Resource Manager deactivated');
    
    // Stop cleanup loop
    if (this.cleanupInterval) {
      clearInterval(this.cleanupInterval);
      this.cleanupInterval = null;
    }
  }
  
  // =============================================================================
  // PROCESS MANAGEMENT
  // =============================================================================
  
  createProcess(processType, metadata = {}) {
    const processId = nanoid();
    const process = {
      id: processId,
      type: processType,
      metadata,
      resources: new Set(),
      state: {},
      created: Date.now(),
      lastActivity: Date.now()
    };
    
    this.processes.set(processId, process);
    console.debug(`Created process ${processId} of type ${processType}`);
    return processId;
  }
  
  updateProcessActivity(processId) {
    const process = this.processes.get(processId);
    if (process) {
      process.lastActivity = Date.now();
    }
  }
  
  getProcessState(processId, key = null) {
    const process = this.processes.get(processId);
    if (!process) return null;
    
    return key ? process.state[key] : { ...process.state };
  }
  
  setProcessState(processId, key, value) {
    const process = this.processes.get(processId);
    if (!process) return false;
    
    process.state[key] = value;
    this.updateProcessActivity(processId);
    return true;
  }
  
  cleanupProcess(processId) {
    const process = this.processes.get(processId);
    if (!process) return;
    
    // Clean up all resources associated with this process
    for (const resourceId of process.resources) {
      this.releaseResource(resourceId);
    }
    
    this.processes.delete(processId);
    console.debug(`Cleaned up process ${processId}`);
  }
  
  // =============================================================================
  // RESOURCE MANAGEMENT
  // =============================================================================
  
  registerResource(resource, resourceType, processId = null, cleanup = null) {
    const resourceId = nanoid();
    
    const resourceInfo = {
      id: resourceId,
      type: resourceType,
      resource,
      processId,
      cleanup,
      created: Date.now(),
      lastAccessed: Date.now()
    };
    
    this.resources.set(resourceId, resourceInfo);
    
    // Associate with process if provided
    if (processId && this.processes.has(processId)) {
      this.processes.get(processId).resources.add(resourceId);
    }
    
    console.debug(`Registered resource ${resourceId} of type ${resourceType}`);
    return resourceId;
  }
  
  accessResource(resourceId) {
    const resourceInfo = this.resources.get(resourceId);
    if (!resourceInfo) return null;
    
    resourceInfo.lastAccessed = Date.now();
    return resourceInfo.resource;
  }
  
  releaseResource(resourceId) {
    const resourceInfo = this.resources.get(resourceId);
    if (!resourceInfo) return false;
    
    // Run cleanup if provided
    if (resourceInfo.cleanup) {
      try {
        resourceInfo.cleanup(resourceInfo.resource);
      } catch (error) {
        console.error(`Resource cleanup failed for ${resourceId}:`, error);
      }
    }
    
    // Cleanup based on resource type
    this.cleanupResourceByType(resourceInfo.resource, resourceInfo.type);
    
    // Remove from tracking
    this.resources.delete(resourceId);
    
    // Remove from process if associated
    if (resourceInfo.processId && this.processes.has(resourceInfo.processId)) {
      this.processes.get(resourceInfo.processId).resources.delete(resourceId);
    }
    
    console.debug(`Released resource ${resourceId}`);
    return true;
  }
  
  cleanupResourceByType(resource, resourceType) {
    try {
      switch (resourceType) {
        case ResourceType.API_REQUEST:
          if (resource.abort) {
            resource.abort();
          }
          break;
          
        case ResourceType.EVENT_LISTENER:
          if (resource.element && resource.event && resource.handler) {
            resource.element.removeEventListener(resource.event, resource.handler);
          }
          break;
          
        case ResourceType.CUSTOM_EVENT:
          // Custom events are automatically cleaned up
          break;
          
        case ResourceType.TIMEOUT:
          clearTimeout(resource);
          break;
          
        case ResourceType.INTERVAL:
          clearInterval(resource);
          break;
          
        case ResourceType.PROGRESS_TRACKER:
          this.progressTrackers.delete(resource.id);
          break;
          
        case ResourceType.DIALOG_STATE:
          this.dialogStates.delete(resource.id);
          break;
          
        case ResourceType.FILE_UPLOAD:
          // File uploads are handled by the browser
          break;
          
        case ResourceType.MESSAGE_QUEUE:
          // Message queues are cleaned up by removing from the map
          this.messageQueue.delete(resource.id);
          break;
          
        default:
          console.warn(`Unknown resource type for cleanup: ${resourceType}`);
      }
    } catch (error) {
      console.error(`Error cleaning up resource of type ${resourceType}:`, error);
    }
  }
  
  // =============================================================================
  // MANAGED API REQUESTS (FIXES BUG #1 - RACE CONDITIONS)
  // =============================================================================
  
  async managedApiRequest(processId, requestFn, options = {}) {
    const { timeout = 30000, retries = 0, onProgress = null } = options;
    
    // Create abort controller for this request
    const controller = new AbortController();
    const signal = controller.signal;
    
    // Register abort controller as a resource
    const resourceId = this.registerResource(
      controller, 
      ResourceType.API_REQUEST, 
      processId,
      (ctrl) => ctrl.abort()
    );
    
    try {
      // Add timeout
      const timeoutId = setTimeout(() => {
        controller.abort();
      }, timeout);
      
      this.registerResource(timeoutId, ResourceType.TIMEOUT, processId);
      
      // Execute request with abort signal
      const result = await requestFn(signal);
      
      // Clear timeout on success
      clearTimeout(timeoutId);
      
      return result;
      
    } catch (error) {
      if (signal.aborted) {
        throw new Error('Request was aborted');
      }
      
      // Retry logic
      if (retries > 0 && !signal.aborted) {
        console.warn(`API request failed, retrying (${retries} attempts left):`, error);
        await new Promise(resolve => setTimeout(resolve, 1000)); // 1s delay
        return this.managedApiRequest(processId, requestFn, { ...options, retries: retries - 1 });
      }
      
      throw error;
    } finally {
      // Clean up resources
      this.releaseResource(resourceId);
    }
  }
  
  // =============================================================================
  // MESSAGE QUEUE MANAGEMENT (FIXES BUG #1 - RACE CONDITIONS)
  // =============================================================================
  
  createMessageQueue(processId, options = {}) {
    const { maxLength = this.maxMessageLength, maxQueue = 10 } = options;
    
    const queueId = nanoid();
    const queue = {
      id: queueId,
      processId,
      maxLength,
      maxQueue,
      items: [],
      processing: false,
      created: Date.now()
    };
    
    this.messageQueue.set(queueId, queue);
    this.registerResource(queue, ResourceType.MESSAGE_QUEUE, processId);
    
    return queueId;
  }
  
  async enqueueMessage(queueId, message, options = {}) {
    // Recursion guard
    if (options._recursionDepth > 1) {
      console.error(`Recursion limit reached for queue ${queueId}`);
      return false;
    }
    
    let queue = this.messageQueue.get(queueId);
    if (!queue) {
      console.warn(`Message queue ${queueId} not found, creating new queue`);
      // Check if there's an old queue that needs cleanup first
      const existingQueue = this.messageQueue.get(queueId);
      if (existingQueue) {
        console.log(`Cleaning up existing queue ${queueId} before recreation`);
        this.releaseResource(queueId);
      }

      // Create a queue with the specific queueId instead of generating a new one
      const maxLength = options.maxLength || 10000;
      const maxQueue = options.maxQueue || 100;

      queue = {
        id: queueId,
        processId: queueId, // Use queueId as processId for simplicity
        maxLength,
        maxQueue,
        items: [],
        processing: false,
        created: Date.now()
      };

      this.messageQueue.set(queueId, queue);
      this.registerResource(queue, ResourceType.MESSAGE_QUEUE, queueId);
    }
    
    // SECURITY FIX: Message length validation with better error handling
    if (typeof message === 'string') {
      if (message.length > queue.maxLength) {
        const errorMessage = `Message too long: ${message.length} > ${queue.maxLength} characters`;
        console.error("Message length validation failed:", errorMessage);
        
        // Return error instead of throwing to prevent infinite loops
        return {
          success: false,
          error: errorMessage,
          message: message.substring(0, 100) + "..." // Truncate for logging
        };
      }
    } else if (typeof message === 'object' && message !== null) {
      // For object messages, check if they contain large text fields
      const messageStr = JSON.stringify(message);
      if (messageStr.length > queue.maxLength) {
        const errorMessage = `Message object too large: ${messageStr.length} > ${queue.maxLength} characters`;
        console.error("Message size validation failed:", errorMessage);
        
        return {
          success: false,
          error: errorMessage,
          message: "Large message object detected"
        };
      }
    }
    
    // Queue size management
    if (queue.items.length >= queue.maxQueue) {
      console.warn(`Message queue ${queueId} is full, dropping oldest message`);
      queue.items.shift();
    }
    
    queue.items.push({ message, options, timestamp: Date.now() });
    
    // Process queue if not already processing
    if (!queue.processing) {
      return this.processMessageQueue(queueId);
    }
    
    return { success: true };
  }
  
  async processMessageQueue(queueId) {
    const queue = this.messageQueue.get(queueId);
    if (!queue || queue.processing) return;
    
    queue.processing = true;
    
    try {
      while (queue.items.length > 0) {
        const item = queue.items.shift();
        
        // Update process activity
        this.updateProcessActivity(queue.processId);
        
        // Process the message (this would be implemented by the caller)
        if (item.options.processor) {
          await item.options.processor(item.message);
        }
        
        // Small delay to prevent overwhelming
        await new Promise(resolve => setTimeout(resolve, 10));
      }
    } catch (error) {
      console.error(`Error processing message queue ${queueId}:`, error);
    } finally {
      queue.processing = false;
    }
  }
  
  // =============================================================================
  // DIALOG STATE MANAGEMENT (FIXES BUG #5 - CSV DIALOG RACE CONDITIONS)
  // =============================================================================
  
  createDialogState(processId, dialogType, initialState = {}) {
    const dialogId = nanoid();
    const state = {
      id: dialogId,
      type: dialogType,
      processId,
      state: { ...initialState },
      locked: false,
      created: Date.now(),
      lastUpdate: Date.now()
    };
    
    this.dialogStates.set(dialogId, state);
    this.registerResource(state, ResourceType.DIALOG_STATE, processId);
    
    return dialogId;
  }
  
  lockDialog(dialogId) {
    const dialog = this.dialogStates.get(dialogId);
    if (!dialog) return false;
    
    if (dialog.locked) {
      console.warn(`Dialog ${dialogId} is already locked`);
      return false;
    }
    
    dialog.locked = true;
    dialog.lastUpdate = Date.now();
    return true;
  }
  
  unlockDialog(dialogId) {
    const dialog = this.dialogStates.get(dialogId);
    if (!dialog) return false;
    
    dialog.locked = false;
    dialog.lastUpdate = Date.now();
    return true;
  }
  
  updateDialogState(dialogId, updates) {
    const dialog = this.dialogStates.get(dialogId);
    if (!dialog) return false;
    
    if (dialog.locked) {
      console.warn(`Cannot update locked dialog ${dialogId}`);
      return false;
    }
    
    Object.assign(dialog.state, updates);
    dialog.lastUpdate = Date.now();
    
    // Update process activity
    this.updateProcessActivity(dialog.processId);
    
    return true;
  }
  
  getDialogState(dialogId) {
    const dialog = this.dialogStates.get(dialogId);
    return dialog ? { ...dialog.state } : null;
  }
  
  // =============================================================================
  // PROGRESS TRACKING (FIXES BUG #8 - PROGRESS EVENT MEMORY LEAKS)
  // =============================================================================
  
  createProgressTracker(processId, options = {}) {
    const trackerId = nanoid();
    const tracker = {
      id: trackerId,
      processId,
      listeners: new Set(),
      events: [],
      maxEvents: options.maxEvents || 100,
      created: Date.now()
    };
    
    this.progressTrackers.set(trackerId, tracker);
    this.registerResource(tracker, ResourceType.PROGRESS_TRACKER, processId);
    
    return trackerId;
  }
  
  addProgressListener(trackerId, listener) {
    const tracker = this.progressTrackers.get(trackerId);
    if (!tracker) return false;
    
    tracker.listeners.add(listener);
    
    // Register event listener as a resource for cleanup
    this.registerResource(
      { trackerId, listener },
      ResourceType.EVENT_LISTENER,
      tracker.processId,
      ({ trackerId, listener }) => {
        const t = this.progressTrackers.get(trackerId);
        if (t) t.listeners.delete(listener);
      }
    );
    
    return true;
  }
  
  emitProgress(trackerId, progressData) {
    const tracker = this.progressTrackers.get(trackerId);
    if (!tracker) return false;
    
    // Store event (with limit)
    tracker.events.push({ ...progressData, timestamp: Date.now() });
    if (tracker.events.length > tracker.maxEvents) {
      tracker.events.shift();
    }
    
    // Notify listeners
    for (const listener of tracker.listeners) {
      try {
        listener(progressData);
      } catch (error) {
        console.error('Progress listener error:', error);
      }
    }
    
    // Update process activity
    this.updateProcessActivity(tracker.processId);
    
    return true;
  }
  
  // =============================================================================
  // ON-DEMAND CLEANUP AND MAINTENANCE
  // =============================================================================
  
  startCleanupLoop() {
    // ON-DEMAND: Only run cleanup when active
    if (!this.isActive) return;
    
    const cleanup = () => {
      if (!this.isActive) return; // Stop if deactivated
      this.performCleanup();
      this.cleanupInterval = setTimeout(cleanup, 30000); // 30 seconds
    };
    
    this.cleanupInterval = setTimeout(cleanup, 30000);
  }
  
  performCleanup() {
    if (!this.isActive) return; // Don't cleanup when inactive
    
    const now = Date.now();
    const maxAge = 300000; // 5 minutes
    
    // Clean up old processes
    for (const [processId, process] of this.processes) {
      if (now - process.lastActivity > maxAge) {
        console.debug(`Cleaning up stale process ${processId}`);
        this.cleanupProcess(processId);
      }
    }
    
    // Clean up orphaned resources
    for (const [resourceId, resourceInfo] of this.resources) {
      if (now - resourceInfo.lastAccessed > maxAge) {
        console.debug(`Cleaning up stale resource ${resourceId}`);
        this.releaseResource(resourceId);
      }
    }
    
    // Force garbage collection hint
    if (this.resources.size > this.maxResources) {
      console.warn('Resource limit exceeded, forcing cleanup');
      this.forceCleanup();
    }
  }
  
  forceCleanup() {
    // Clean up oldest resources first
    const resources = Array.from(this.resources.entries())
      .sort(([,a], [,b]) => a.lastAccessed - b.lastAccessed);
    
    const toCleanup = resources.slice(0, Math.floor(resources.length * 0.3));
    
    for (const [resourceId] of toCleanup) {
      this.releaseResource(resourceId);
    }
  }
  
  getStats() {
    return {
      active: this.isActive,
      resources: this.resources.size,
      processes: this.processes.size,
      progressTrackers: this.progressTrackers.size,
      dialogStates: this.dialogStates.size,
      messageQueues: this.messageQueue.size
    };
  }
  
  shutdown() {
    console.log('Frontend Resource Manager shutting down...');
    
    // Deactivate first
    this.deactivate();
    
    // Clean up all resources
    for (const resourceId of this.resources.keys()) {
      this.releaseResource(resourceId);
    }
    
    // Clean up all processes
    for (const processId of this.processes.keys()) {
      this.cleanupProcess(processId);
    }
    
    // Clear all collections
    this.resources.clear();
    this.processes.clear();
    this.progressTrackers.clear();
    this.dialogStates.clear();
    this.messageQueue.clear();
    
    console.log('Frontend Resource Manager shutdown complete');
  }
}

// =============================================================================
// GLOBAL INSTANCE AND HELPERS
// =============================================================================

let globalResourceManager = null;
let isInitializing = false; // Prevent race conditions during initialization

export function getResourceManager() {
  if (!globalResourceManager && !isInitializing) {
    isInitializing = true;
    globalResourceManager = new FrontendResourceManager();
    isInitializing = false;
  }
  return globalResourceManager;
}

// ON-DEMAND ACTIVATION HELPERS
export function activateResourceManager() {
  const manager = getResourceManager();
  manager.activate();
  return manager;
}

export function deactivateResourceManager() {
  const manager = getResourceManager();
  manager.deactivate();
}

// Convenience functions for easy integration
export function createManagedProcess(processType, metadata = {}) {
  const manager = getResourceManager();
  // Auto-activate when used
  if (!manager.isActive) manager.activate();
  return manager.createProcess(processType, metadata);
}

export function managedApiCall(processId, requestFn, options = {}) {
  const manager = getResourceManager();
  // Auto-activate when used
  if (!manager.isActive) manager.activate();
  return manager.managedApiRequest(processId, requestFn, options);
}

export function createMessageQueue(processId, options = {}) {
  const manager = getResourceManager();
  // Auto-activate when used
  if (!manager.isActive) manager.activate();
  return manager.createMessageQueue(processId, options);
}

export function enqueueMessage(queueId, message, options = {}) {
  const manager = getResourceManager();
  // Auto-activate when used to ensure message queue functionality works
  if (!manager.isActive) manager.activate();
  return manager.enqueueMessage(queueId, message, options);
}

export function createProgressTracker(processId, options = {}) {
  const manager = getResourceManager();
  // Auto-activate when used
  if (!manager.isActive) manager.activate();
  return manager.createProgressTracker(processId, options);
}

export function createDialogState(processId, dialogType, initialState = {}) {
  const manager = getResourceManager();
  // Auto-activate when used
  if (!manager.isActive) manager.activate();
  return manager.createDialogState(processId, dialogType, initialState);
}

// Clean up on page unload
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => {
    if (globalResourceManager) {
      globalResourceManager.shutdown();
    }
  });
}

export default FrontendResourceManager;