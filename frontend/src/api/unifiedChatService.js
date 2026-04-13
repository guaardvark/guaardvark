/**
 * Unified Chat Service
 * WebSocket-based chat service that streams thinking, tool calls, and responses.
 */

import { BASE_URL } from "./apiClient";
import { useAppStore } from "../stores/useAppStore";

const API_BASE = BASE_URL.replace(/\/api$/, "");

class UnifiedChatService {
  constructor(socket) {
    this.socket = socket;
    this._listeners = [];
  }

  /**
   * Join a session room for streaming events.
   */
  joinSession(sessionId) {
    if (this.socket?.connected) {
      this.socket.emit("chat:join", { session_id: sessionId });
    }
  }

  /**
   * Send a message via HTTP. Response arrives via Socket.IO events.
   */
  async sendMessage(sessionId, message, options = {}, imageBase64 = null, isVoiceMessage = false) {
    // Read the live AgentScreenViewer state from Zustand without subscribing
    // — getState() is the official escape hatch for non-component code. The
    // backend gates Gemma4 direct path and screen tools on this flag.
    const agentScreenActive = useAppStore.getState().agentScreenOpen === true;
    const body = {
      session_id: sessionId,
      message,
      options: { ...options, agent_screen_active: agentScreenActive },
      project_id: options.project_id,
      is_voice_message: isVoiceMessage,
    };
    if (imageBase64) {
      body.image = imageBase64;
    }
    const response = await fetch(`${API_BASE}/api/chat/unified`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${response.status}`);
    }

    return response.json();
  }

  /**
   * Register a callback for a Socket.IO event.
   * Tracks listeners for cleanup.
   */
  _on(event, callback) {
    if (!this.socket) return;
    // Remove any existing listener for this event from THIS service instance
    // to prevent accumulation when service is reused or recreated on the same socket.
    const existing = this._listeners.filter((l) => l.event === event);
    for (const l of existing) {
      this.socket.off(l.event, l.callback);
    }
    this._listeners = this._listeners.filter((l) => l.event !== event);
    this.socket.on(event, callback);
    this._listeners.push({ event, callback });
  }

  onThinking(callback) {
    this._on("chat:thinking", callback);
  }
  onToolCall(callback) {
    this._on("chat:tool_call", callback);
  }
  onToolResult(callback) {
    this._on("chat:tool_result", callback);
  }
  onToken(callback) {
    this._on("chat:token", callback);
  }
  onComplete(callback) {
    this._on("chat:complete", callback);
  }
  onError(callback) {
    this._on("chat:error", callback);
  }
  onJoined(callback) {
    this._on("chat:joined", callback);
  }
  onImage(callback) {
    this._on("chat:image", callback);
  }
  onVideo(callback) {
    this._on("chat:video", callback);
  }
  onToolOutputChunk(callback) {
    this._on("chat:tool_output_chunk", callback);
  }
  onToolApprovalRequest(callback) {
    this._on("chat:tool_approval_request", callback);
  }

  /**
   * Send tool approval response.
   */
  sendToolApproval(sessionId, approved) {
    if (this.socket?.connected) {
      this.socket.emit("chat:tool_approval_response", { session_id: sessionId, approved });
    }
  }

  /**
   * Request abort of current generation.
   */
  abort(sessionId) {
    if (this.socket?.connected) {
      this.socket.emit("chat:abort", { session_id: sessionId });
    }
  }

  /**
   * Fetch conversation history via REST.
   */
  async getHistory(sessionId, limit = 50) {
    const response = await fetch(
      `${API_BASE}/api/chat/unified/${sessionId}/history?limit=${limit}`
    );
    if (!response.ok) {
      throw new Error(`Failed to fetch history: ${response.status}`);
    }
    return response.json();
  }

  /**
   * Remove all registered listeners.
   */
  cleanup() {
    if (!this.socket) return;
    for (const { event, callback } of this._listeners) {
      this.socket.off(event, callback);
    }
    this._listeners = [];
  }
}

export default UnifiedChatService;
