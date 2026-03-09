// frontend/src/api/sessionStateService.js
// CLAUDE-STYLE ENHANCEMENT: Advanced session state management for conversation continuity
// Integrates with existing resource manager and state service

import { BASE_URL, handleResponse } from "./apiClient";
import { getResourceManager } from "../utils/resource_manager";
import { getSystemMetrics } from "./stateService";

/**
 * Enhanced Session State Service
 * Prevents memory resets and maintains conversation context across all interactions
 */
class SessionStateService {
  constructor() {
    this.resourceManager = getResourceManager();
    this.sessionStates = new Map();
    this.contextBackups = new Map();
    this.conversationHistory = new Map();

    // CLAUDE_FIX: Initialize context preservation tracking
    this.initializeContextPreservation();
  }

  /**
   * Initialize context preservation mechanisms
   */
  initializeContextPreservation() {
    // Auto-backup conversation state every 30 seconds
    this.backupInterval = setInterval(() => {
      this.backupAllSessions();
    }, 30000);

    // Listen for page unload to preserve state
    if (typeof window !== 'undefined') {
      window.addEventListener('beforeunload', () => {
        this.emergencyBackupAllSessions();
      });

      // Listen for visibility change to preserve state when tab becomes hidden
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
          this.backupAllSessions();
        }
      });
    }
  }

  /**
   * CLAUDE_FIX: Register a session for advanced state management
   */
  registerSession(sessionId, options = {}) {
    const {
      autoBackup = true,
      preserveContext = true,
      maxHistoryItems = 1000,
      fileGenerationCapable = true
    } = options;

    const sessionState = {
      id: sessionId,
      created: Date.now(),
      lastActivity: Date.now(),
      messageCount: 0,
      conversationContext: [],
      fileGenerationAttempts: [],
      options: {
        autoBackup,
        preserveContext,
        maxHistoryItems,
        fileGenerationCapable
      },
      // CLAUDE_FIX: Memory reset prevention flags
      memoryResetPrevention: {
        lastMessageBeforeFileGeneration: null,
        fileGenerationContext: null,
        conversationContinuityMarkers: []
      }
    };

    this.sessionStates.set(sessionId, sessionState);

    // Register with resource manager for cleanup
    const processId = this.resourceManager.createProcess('session_management', { sessionId });
    sessionState.processId = processId;

    console.log(`CLAUDE_FIX: Session ${sessionId} registered with advanced state management`);
    return sessionState;
  }

  /**
   * CLAUDE_FIX: Record a message with context preservation
   */
  recordMessage(sessionId, message, role = 'user', metadata = {}) {
    let sessionState = this.sessionStates.get(sessionId);
    if (!sessionState) {
      sessionState = this.registerSession(sessionId);
    }

    const messageRecord = {
      id: `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      role,
      content: message,
      timestamp: Date.now(),
      metadata: {
        ...metadata,
        conversationIndex: sessionState.messageCount,
        fileGenerationTriggered: metadata.fileGenerationTriggered || false,
        systemRouting: metadata.systemRouting || 'normal_chat'
      }
    };

    // CLAUDE_FIX: Always preserve the conversation context
    sessionState.conversationContext.push(messageRecord);
    sessionState.messageCount++;
    sessionState.lastActivity = Date.now();

    // CLAUDE_FIX: Special handling for file generation scenarios
    if (metadata.fileGenerationTriggered) {
      sessionState.memoryResetPrevention.lastMessageBeforeFileGeneration = messageRecord;
      sessionState.memoryResetPrevention.fileGenerationContext = {
        originalMessage: message,
        timestamp: Date.now(),
        continuityMarker: `file_gen_${Date.now()}`
      };

      console.log(`CLAUDE_FIX: Preserved context before file generation for session ${sessionId}`);
    }

    // Limit history size to prevent memory issues
    if (sessionState.conversationContext.length > sessionState.options.maxHistoryItems) {
      const removed = sessionState.conversationContext.shift();
      console.log(`CLAUDE_FIX: Archived old message to prevent memory overflow: ${removed.id}`);
    }

    // Auto-backup if enabled
    if (sessionState.options.autoBackup) {
      this.backupSessionState(sessionId);
    }

    return messageRecord;
  }

  /**
   * CLAUDE_FIX: Ensure conversation continuity during file generation
   */
  preserveContextDuringFileGeneration(sessionId, userMessage, fileGenerationData) {
    const sessionState = this.sessionStates.get(sessionId);
    if (!sessionState) {
      console.warn(`CLAUDE_FIX: No session state found for ${sessionId}, creating new one`);
      return this.registerSession(sessionId);
    }

    // Record the user message with file generation metadata
    const messageRecord = this.recordMessage(sessionId, userMessage, 'user', {
      fileGenerationTriggered: true,
      fileGenerationData,
      systemRouting: 'file_generation_parallel'
    });

    // CLAUDE_FIX: Create a continuity marker to prevent memory reset
    const continuityMarker = {
      id: `continuity_${Date.now()}`,
      type: 'file_generation_continuity',
      userMessage: userMessage,
      messageId: messageRecord.id,
      timestamp: Date.now(),
      fileGenerationData,
      preservedContext: sessionState.conversationContext.slice(-10) // Last 10 messages
    };

    sessionState.memoryResetPrevention.conversationContinuityMarkers.push(continuityMarker);

    // Store in sessionStorage for persistence across component remounts
    try {
      sessionStorage.setItem(
        `conversation_continuity_${sessionId}`,
        JSON.stringify(continuityMarker)
      );
    } catch (e) {
      console.warn('CLAUDE_FIX: Failed to store continuity marker in sessionStorage:', e);
    }

    console.log(`CLAUDE_FIX: Context preserved during file generation for session ${sessionId}`);
    return continuityMarker;
  }

  /**
   * CLAUDE_FIX: Restore conversation context after system routing
   */
  restoreConversationContext(sessionId) {
    const sessionState = this.sessionStates.get(sessionId);
    if (!sessionState) {
      console.warn(`CLAUDE_FIX: No session state found for restoration: ${sessionId}`);
      return null;
    }

    // Check for continuity markers
    try {
      const storedMarker = sessionStorage.getItem(`conversation_continuity_${sessionId}`);
      if (storedMarker) {
        const continuityMarker = JSON.parse(storedMarker);
        console.log(`CLAUDE_FIX: Found conversation continuity marker for session ${sessionId}`);

        // Verify the marker is recent (within last 5 minutes)
        if (Date.now() - continuityMarker.timestamp < 300000) {
          return {
            restored: true,
            context: continuityMarker.preservedContext,
            originalMessage: continuityMarker.userMessage,
            continuityMarker
          };
        }
      }
    } catch (e) {
      console.warn('CLAUDE_FIX: Failed to restore continuity marker:', e);
    }

    // Fallback to session state
    return {
      restored: true,
      context: sessionState.conversationContext.slice(-10),
      messageCount: sessionState.messageCount,
      lastActivity: sessionState.lastActivity
    };
  }

  /**
   * CLAUDE_FIX: Backup session state to prevent data loss
   */
  backupSessionState(sessionId) {
    const sessionState = this.sessionStates.get(sessionId);
    if (!sessionState) return false;

    const backup = {
      sessionId,
      timestamp: Date.now(),
      messageCount: sessionState.messageCount,
      lastActivity: sessionState.lastActivity,
      conversationContext: sessionState.conversationContext.slice(-50), // Last 50 messages
      memoryResetPrevention: sessionState.memoryResetPrevention
    };

    // Store in localStorage for persistence
    try {
      localStorage.setItem(`session_backup_${sessionId}`, JSON.stringify(backup));
      this.contextBackups.set(sessionId, backup);
      return true;
    } catch (e) {
      console.warn(`CLAUDE_FIX: Failed to backup session ${sessionId}:`, e);
      return false;
    }
  }

  /**
   * CLAUDE_FIX: Restore session state from backup
   */
  restoreSessionFromBackup(sessionId) {
    try {
      const backupData = localStorage.getItem(`session_backup_${sessionId}`);
      if (!backupData) return null;

      const backup = JSON.parse(backupData);

      // Verify backup is recent (within last 24 hours)
      if (Date.now() - backup.timestamp > 24 * 60 * 60 * 1000) {
        console.log(`CLAUDE_FIX: Backup for session ${sessionId} is too old, discarding`);
        localStorage.removeItem(`session_backup_${sessionId}`);
        return null;
      }

      // Restore session state
      const restoredState = this.registerSession(sessionId, {
        autoBackup: true,
        preserveContext: true
      });

      restoredState.messageCount = backup.messageCount;
      restoredState.conversationContext = backup.conversationContext;
      restoredState.memoryResetPrevention = backup.memoryResetPrevention || {
        lastMessageBeforeFileGeneration: null,
        fileGenerationContext: null,
        conversationContinuityMarkers: []
      };

      console.log(`CLAUDE_FIX: Restored session ${sessionId} from backup`);
      return restoredState;

    } catch (e) {
      console.error(`CLAUDE_FIX: Failed to restore session ${sessionId} from backup:`, e);
      return null;
    }
  }

  /**
   * Backup all active sessions
   */
  backupAllSessions() {
    for (const [sessionId] of this.sessionStates) {
      this.backupSessionState(sessionId);
    }
  }

  /**
   * Emergency backup during page unload
   */
  emergencyBackupAllSessions() {
    try {
      const sessionIds = Array.from(this.sessionStates.keys());
      sessionStorage.setItem('active_sessions', JSON.stringify(sessionIds));
      this.backupAllSessions();
      console.log('CLAUDE_FIX: Emergency backup completed for all sessions');
    } catch (e) {
      console.error('CLAUDE_FIX: Emergency backup failed:', e);
    }
  }

  /**
   * Get session statistics
   */
  getSessionStats(sessionId) {
    const sessionState = this.sessionStates.get(sessionId);
    if (!sessionState) return null;

    return {
      sessionId,
      messageCount: sessionState.messageCount,
      created: sessionState.created,
      lastActivity: sessionState.lastActivity,
      ageMinutes: Math.round((Date.now() - sessionState.created) / 60000),
      fileGenerationAttempts: sessionState.fileGenerationAttempts.length,
      hasBackup: this.contextBackups.has(sessionId),
      memoryResetPrevention: {
        hasContinuityMarkers: sessionState.memoryResetPrevention.conversationContinuityMarkers.length > 0,
        hasFileGenerationContext: !!sessionState.memoryResetPrevention.fileGenerationContext
      }
    };
  }

  /**
   * Cleanup old sessions and backups
   */
  cleanup() {
    const maxAge = 24 * 60 * 60 * 1000; // 24 hours
    const now = Date.now();

    // Cleanup session states
    for (const [sessionId, sessionState] of this.sessionStates) {
      if (now - sessionState.lastActivity > maxAge) {
        this.sessionStates.delete(sessionId);
        console.log(`CLAUDE_FIX: Cleaned up old session state: ${sessionId}`);
      }
    }

    // Cleanup backups
    for (const [sessionId, backup] of this.contextBackups) {
      if (now - backup.timestamp > maxAge) {
        this.contextBackups.delete(sessionId);
        localStorage.removeItem(`session_backup_${sessionId}`);
        console.log(`CLAUDE_FIX: Cleaned up old backup: ${sessionId}`);
      }
    }
  }

  /**
   * Shutdown the service
   */
  shutdown() {
    this.emergencyBackupAllSessions();

    if (this.backupInterval) {
      clearInterval(this.backupInterval);
    }

    this.sessionStates.clear();
    this.contextBackups.clear();
    this.conversationHistory.clear();

    console.log('CLAUDE_FIX: SessionStateService shutdown complete');
  }
}

// Create singleton instance
const sessionStateService = new SessionStateService();

// Export service functions
export const registerSession = (sessionId, options = {}) =>
  sessionStateService.registerSession(sessionId, options);

export const recordMessage = (sessionId, message, role = 'user', metadata = {}) =>
  sessionStateService.recordMessage(sessionId, message, role, metadata);

export const preserveContextDuringFileGeneration = (sessionId, userMessage, fileGenerationData) =>
  sessionStateService.preserveContextDuringFileGeneration(sessionId, userMessage, fileGenerationData);

export const restoreConversationContext = (sessionId) =>
  sessionStateService.restoreConversationContext(sessionId);

export const backupSessionState = (sessionId) =>
  sessionStateService.backupSessionState(sessionId);

export const restoreSessionFromBackup = (sessionId) =>
  sessionStateService.restoreSessionFromBackup(sessionId);

export const getSessionStats = (sessionId) =>
  sessionStateService.getSessionStats(sessionId);

export default sessionStateService;