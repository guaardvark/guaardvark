// frontend/src/api/chatService.js
// Version 3.0: Enhanced chat service with comprehensive input validation and security
import { BASE_URL, handleResponse } from "./apiClient";
import { validateChatInput, checkRateLimit, ValidationError } from "../utils/inputValidation";
import { RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_MS } from "../config/constants";

export const sendQuery = async (
  inputText,
  tags = [],
  projectId = null,
  sessionId = null,
  rulesCutoff = false
) => {
  try {
    // Rate limiting check
    const rateLimitId = `chat_${sessionId || 'anonymous'}_${Date.now().toString().slice(-6)}`;
    checkRateLimit(rateLimitId, RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_MS);

    // Skip security checks for code-related content (code editor prompts legitimately
    // contain patterns that look like XSS/SQL injection - onclick=, SELECT, etc.)
    const isCodeContent = tags && (
      tags.includes('coding') ||
      tags.includes('code') ||
      tags.includes('assistance') ||
      tags.some(tag => ['javascript', 'typescript', 'python', 'java', 'cpp', 'c', 'csharp', 'php', 'ruby', 'go', 'rust', 'swift', 'kotlin', 'scala', 'html', 'css', 'sql', 'json', 'xml', 'yaml'].includes(tag))
    );

    // Validate and sanitize input
    const validatedInput = validateChatInput({
      prompt: inputText,
      tags,
      project_id: projectId,
      session_id: sessionId
    }, { skipSecurityChecks: isCodeContent });

    // Prepare payload with validated data
    const payload = {
      prompt: validatedInput.prompt,
      tags: validatedInput.tags,
      timestamp: Date.now(),
      client_version: '3.0',
      bypassRules: rulesCutoff
    };

    if (validatedInput.project_id) payload.project_id = validatedInput.project_id;
    if (validatedInput.session_id) payload.session_id = validatedInput.session_id;

    const response = await fetch(`${BASE_URL}/query`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Client-Version": "3.0"
      },
      body: JSON.stringify(payload),
    });

    const data = await handleResponse(response);

    if (typeof data === "object" && data !== null && data.error) {
      throw new Error(data.error);
    }

    return data;
  } catch (err) {
    console.error("chatService: Error sending query:", err.message);

    // Provide more specific error messages for validation errors
    if (err instanceof ValidationError) {
      return {
        error: err.message,
        validationErrors: err.errors || [err],
        code: err.code
      };
    }

    return { error: err.message || "Failed to send query." };
  }
};

export const sendCommandToQueryApi = async (
  commandText,
  sessionId = null,
  projectId = null
) => {
  console.log(`chatService: Sending command "${commandText}" to /api/query`);
  try {
    const payload = {
      prompt: commandText,
      session_id: sessionId,
      ...(projectId && { project_id: projectId }),
    };
    const response = await fetch(`${BASE_URL}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await handleResponse(response);
    if (data && typeof data === "object" && data.error) {
      throw new Error(data.error.message || data.error);
    }
    return data;
  } catch (err) {
    console.error(
      "chatService: Error sending command to /api/query:",
      err.message,
      err.data || ""
    );
    throw new Error(err.message || "Failed to send command.");
  }
};

// Enhanced chat service with session context management
class EnhancedChatService {
  constructor() {
    this.sessionContexts = new Map(); // Local session context cache
    this.webSearchEnabled = true; // Default enabled
    this.activeRequests = new Set(); // Track active requests to prevent duplicates
    this.requestResults = new Map(); // Cache recent results
  }

  /**
   * Send enhanced message with web search and context management
   */
  async sendEnhancedMessage(sessionId, message, options = {}) {
    const {
      useRag = true,
      debugMode = false,
      autoWebSearch = true,
      projectId = null,
      rulesCutoff = false,
      requestId = `${sessionId}_${Date.now()}_${Math.random()}`,
      signal = null,
      onDeltaReceived = null,
    } = options;

    console.log('CHAT_DEBUG: sendEnhancedMessage called', { sessionId, requestId, message: message?.substring(0, 50) + '...' });

    // Check for duplicate requests
    const requestKey = `${sessionId}_${message}`;
    if (this.activeRequests.has(requestKey)) {
      console.warn('CHAT_DEBUG: Duplicate request detected, blocking:', requestKey);
      throw new Error('Duplicate request detected');
    }

    // Check for recent identical request
    if (this.requestResults.has(requestKey)) {
      const cachedResult = this.requestResults.get(requestKey);
      const timeDiff = Date.now() - cachedResult.timestamp;
      if (timeDiff < 2000) { // 2 second cache
        console.log('CHAT_DEBUG: Returning cached result for recent duplicate request');
        return { ...cachedResult.result, cached: true };
      }
    }

    // Mark request as active
    this.activeRequests.add(requestKey);
    console.log('CHAT_DEBUG: Marked request as active:', requestKey);

    try {
      // Check if we should use web search for this query
      const shouldUseWebSearch = autoWebSearch && this.webSearchEnabled && 
        this._shouldUseWebSearch(message);

      let enhancedMessage = message;
      let webSearchContext = null;

      // Perform web search if needed
      if (shouldUseWebSearch) {
        console.log('CHAT_DEBUG: Using web search for query');
        try {
          const searchResult = await this.performWebSearch(message);
          if (searchResult.success && searchResult.data.has_result) {
            webSearchContext = searchResult.data;
            enhancedMessage = `${message}\n\n[Web Search Context: ${searchResult.data.snippet}]`;
          }
        } catch (error) {
          console.warn('CHAT_DEBUG: Web search failed, continuing without:', error);
        }
      }

      let result = null;
      let enhancedSuccess = false;

      // Try enhanced chat API first
      try {
        console.log('CHAT_DEBUG: Trying enhanced chat API');
        const response = await fetch(`${BASE_URL}/enhanced-chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionId,
            message: enhancedMessage,
            use_rag: useRag,
            debug: debugMode,
            project_id: projectId,
            request_id: requestId,
            bypassRules: rulesCutoff
          }),
          ...(signal ? { signal } : {}),
        });

        const data = await handleResponse(response);
        
        if (data.success) {
          enhancedSuccess = true;
          console.log('CHAT_DEBUG: Enhanced chat API succeeded');
          
          // Store context locally for persistence
          this._updateSessionContext(sessionId, message, data.data.response);
          
          result = {
            success: true,
            content: data.data.response,
            userMessageId: data.data.user_message_id, // Pass through user message ID
            webSearchUsed: !!webSearchContext,
            webSearchContext: webSearchContext,
            context_info: data.data.context_info,
            rag_info: data.data.rag_info,
            enhanced: true,
            requestId: requestId
          };

          // Notify delta callback with full response (non-streaming API)
          if (onDeltaReceived && data.data.response) {
            onDeltaReceived(data.data.response);
          }
        } else {
          console.warn('CHAT_DEBUG: Enhanced chat API returned unsuccessful response');
        }
      } catch (enhancedError) {
        console.warn('CHAT_DEBUG: Enhanced Chat API failed, will try fallback:', enhancedError);
      }

      // Only use fallback if enhanced API completely failed
      if (!enhancedSuccess) {
        console.log('CHAT_DEBUG: Using fallback to basic chat API');
        result = await this._fallbackToBasicChat(sessionId, enhancedMessage, projectId);
        result.requestId = requestId;
      }

      // Cache the result
      this.requestResults.set(requestKey, {
        result: result,
        timestamp: Date.now()
      });

      // Clean up old cached results (keep only last 10)
      if (this.requestResults.size > 10) {
        const oldestKey = this.requestResults.keys().next().value;
        this.requestResults.delete(oldestKey);
      }

      console.log('CHAT_DEBUG: sendEnhancedMessage completed successfully', { 
        enhanced: result.enhanced, 
        fallback: result.fallback,
        requestId: requestId
      });

      return result;

    } catch (error) {
      console.error('CHAT_DEBUG: Error in sendEnhancedMessage:', error);
      throw error;
    } finally {
      // Always remove from active requests
      this.activeRequests.delete(requestKey);
      console.log('CHAT_DEBUG: Removed request from active set:', requestKey);
    }
  }

  /**
   * Perform web search using backend API
   */
  async performWebSearch(query) {
    try {
      const response = await fetch(`${BASE_URL}/web-search/quick-search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query.substring(0, 200) }) // Limit query length
      });

      return await handleResponse(response);
    } catch (error) {
      console.error('Enhanced Chat: Web search error:', error);
      return { success: false, error: error.message };
    }
  }

  /**
   * Get enhanced chat history with context
   */
  async getEnhancedChatHistory(sessionId, limit = 50) {
    try {
      // Try to get enhanced context first
      const contextResponse = await fetch(`${BASE_URL}/enhanced-chat/${sessionId}/context`);
      let contextInfo = null;
      
      if (contextResponse.ok) {
        const contextData = await handleResponse(contextResponse);
        if (contextData.success) {
          contextInfo = contextData.data;
        }
      }

      // Get regular chat history
      const historyResponse = await fetch(`${BASE_URL}/chat/${sessionId}?limit=${limit}`);
      const historyData = await handleResponse(historyResponse);

      // Combine with local context if available
      const localContext = this.sessionContexts.get(sessionId);

      return {
        messages: historyData.messages || [],
        context_info: contextInfo,
        local_context: localContext,
        enhanced: !!contextInfo
      };

    } catch (error) {
      console.error('Enhanced Chat: Error getting history:', error);
      // Fallback to basic history
      const response = await fetch(`${BASE_URL}/chat/${sessionId}?limit=${limit}`);
      const data = await handleResponse(response);
      return {
        messages: data.messages || [],
        enhanced: false
      };
    }
  }

  /**
   * Clear enhanced session context
   */
  async clearEnhancedSession(sessionId) {
    try {
      // Clear backend context
      await fetch(`${BASE_URL}/enhanced-chat/${sessionId}/clear`, {
        method: "POST"
      });

      // Clear local context
      this.sessionContexts.delete(sessionId);

      return { success: true };
    } catch (error) {
      console.error('Enhanced Chat: Error clearing session:', error);
      return { success: false, error: error.message };
    }
  }

  /**
   * Update local session context
   */
  _updateSessionContext(sessionId, userMessage, assistantResponse) {
    if (!this.sessionContexts.has(sessionId)) {
      this.sessionContexts.set(sessionId, {
        messages: [],
        lastUpdate: Date.now()
      });
    }

    const context = this.sessionContexts.get(sessionId);
    context.messages.push(
      { role: 'user', content: userMessage, timestamp: Date.now() },
      { role: 'assistant', content: assistantResponse, timestamp: Date.now() }
    );

    // Keep only last 20 messages for memory efficiency
    if (context.messages.length > 20) {
      context.messages = context.messages.slice(-20);
    }

    context.lastUpdate = Date.now();
  }

  /**
   * Check if a message should trigger web search
   */
  _shouldUseWebSearch(message) {
    const webSearchTriggers = [
      /what.*happening/i,
      /current.*news/i,
      /latest.*information/i,
      /today.*weather/i,
      /stock.*price/i,
      /recent.*events/i,
      /tell me about.*[0-9]{4}/i, // Years
      /what.*year/i,
      /when.*did/i,
      /who.*is.*currently/i,
      /what.*company/i,
      /current.*status/i,
      /real.*time/i
    ];

    return webSearchTriggers.some(pattern => pattern.test(message));
  }

  /**
   * Fallback to basic chat API
   */
  async _fallbackToBasicChat(sessionId, message, projectId) {
    console.log('CHAT_DEBUG: Starting fallback to basic chat API', { sessionId, projectId });
    
    const debugId = `fallback-${Date.now()}`;
    const response = await fetch(`${BASE_URL}/simple-chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({
        message: message,
        session_id: sessionId,
        project_id: projectId,
        debug_id: debugId
      }),
    });

    if (!response.ok) {
      console.error('CHAT_DEBUG: Basic chat API error:', response.status, response.statusText);
      throw new Error(`Chat API error: ${response.status}`);
    }

    console.log('CHAT_DEBUG: Basic chat API response received, processing stream');

    // Handle streaming response
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let content = '';
    let chunkCount = 0;

    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        console.log('CHAT_DEBUG: Stream completed, total chunks:', chunkCount);
        break;
      }

      chunkCount++;
      const chunk = decoder.decode(value);
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.substring(6));
            if (data.delta) {
              content += data.delta;
            }
          } catch (e) {
            console.warn('CHAT_DEBUG: Failed to parse streaming data:', line);
          }
        }
      }
    }

    console.log('CHAT_DEBUG: Fallback chat completed', { 
      contentLength: content.length,
      debugId: debugId
    });

    // Update local context
    this._updateSessionContext(sessionId, message, content);

    return {
      success: true,
      content: content,
      enhanced: false,
      fallback: true
    };
  }
}

// Create singleton instance
const enhancedChatService = new EnhancedChatService();

export const sendChatMessage = async (
  sessionId,
  userMessage,
  projectId = null,
  onDeltaReceived,
  onRagDebugReceived,
  signal = null,
  _chatMode = null, // eslint-disable-line no-unused-vars
  rulesCutoff = false
) => {
  console.log('CHAT_DEBUG: sendChatMessage called', {
    sessionId,
    userMessage: typeof userMessage === 'string' ? userMessage.substring(0, 50) + '...' : userMessage,
    projectId,
    rulesCutoff
  });
  
  // Track this request to prevent duplicate processing
  const requestId = `${sessionId}_${Date.now()}_${Math.random()}`;
  console.log('CHAT_DEBUG: Request ID:', requestId);
  
  // Use enhanced chat service for better features
  const result = await enhancedChatService.sendEnhancedMessage(sessionId, userMessage, {
    useRag: true,
    debugMode: false,
    autoWebSearch: true,
    projectId: projectId,
    requestId: requestId,
    rulesCutoff: rulesCutoff,
    signal: signal,
    onDeltaReceived: onDeltaReceived,
  });

  console.log('CHAT_DEBUG: sendChatMessage result:', {
    success: result.success,
    enhanced: result.enhanced,
    fallback: result.fallback,
    requestId: requestId
  });

  return result;
};

// Export additional enhanced chat functions
export const getEnhancedChatHistory = async (sessionId, limit = 50) => {
  return await enhancedChatService.getEnhancedChatHistory(sessionId, limit);
};

export const clearEnhancedSession = async (sessionId) => {
  return await enhancedChatService.clearEnhancedSession(sessionId);
};

export const performWebSearch = async (query) => {
  return await enhancedChatService.performWebSearch(query);
};

// Context Management Functions
export const getContextStats = async (sessionId) => {
  try {
    const response = await fetch(`${BASE_URL}/enhanced-chat/${sessionId}/context`);
    const data = await handleResponse(response);
    return data.success ? data.data : null;
  } catch (error) {
    console.error('Error getting context stats:', error);
    return null;
  }
};

export const clearSessionContext = async (sessionId) => {
  try {
    const response = await fetch(`${BASE_URL}/enhanced-chat/${sessionId}/clear`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    return await handleResponse(response);
  } catch (error) {
    console.error('Error clearing session context:', error);
    throw error;
  }
};

export const getSystemHealth = async () => {
  try {
    const response = await fetch(`${BASE_URL}/enhanced-chat/system-health`);
    const data = await handleResponse(response);
    return data.success ? data.data : null;
  } catch (error) {
    console.error('Error getting system health:', error);
    return null;
  }
};

export const getChatHistory = async (
  sessionId,
  beforeId = null,
  limit = 50
) => {
  try {
    const params = new URLSearchParams();
    if (beforeId) params.append("before_id", beforeId);
    params.append("limit", limit.toString());
    const url = `${BASE_URL}/enhanced-chat/${sessionId}/history?${params.toString()}`;
    console.log("DEBUG: getChatHistory URL:", url);
    const response = await fetch(url);
    const raw = await handleResponse(response);
    console.log("DEBUG: getChatHistory raw response:", raw);
    let data = raw;
    if (raw && typeof raw === "object" && raw.data) {
      data = raw.data;
    }
    console.log("DEBUG: getChatHistory processed data:", data);
    if (
      typeof data === "object" &&
      data !== null &&
      Array.isArray(data.messages)
    ) {
      console.log(
        "DEBUG: getChatHistory returning messages, count:",
        data.messages.length
      );
      return data;
    }
    console.warn(
      `chatService: getChatHistory unexpected data for ${sessionId}:`,
      raw
    );
    return { messages: [], has_more: false };
  } catch (err) {
    console.error(
      `chatService: Error fetching chat history for ${sessionId}:`,
      err.message
    );
    throw err;
  }
};

export const listChatSessions = async (projectId, limit = 50, offset = 0) => {
  try {
    const params = new URLSearchParams();
    if (projectId != null) params.append("project_id", projectId);
    params.append("limit", limit.toString());
    params.append("offset", offset.toString());
    const response = await fetch(`${BASE_URL}/enhanced-chat/sessions?${params.toString()}`);
    const data = await handleResponse(response);
    return data;
  } catch (err) {
    console.error("chatService: Error listing chat sessions:", err.message);
    return { sessions: [], total: 0 };
  }
};

export const deleteChatSession = async (sessionId) => {
  const response = await fetch(`${BASE_URL}/enhanced-chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
  return handleResponse(response);
};
