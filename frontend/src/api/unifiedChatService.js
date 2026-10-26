/**
 * Unified Chat Service
 * WebSocket-based chat service that streams thinking, tool calls, and responses.
 */

import { BASE_URL } from "./apiClient";
import { useAppStore } from "../stores/useAppStore";
import { debugLog } from "../utils/debugLog";
import { translate } from "../i18n";

const API_BASE = BASE_URL.replace(/\/api$/, "");

class UnifiedChatService {
  constructor(socket) {
    this.socket = socket;
    this._listeners = [];
    debugLog('[UnifiedChatService] CONSTRUCTED for socket', !!socket, 'connected=', socket?.connected);
  }

  joinSession(sessionId) {
    if (!this.socket) {
      debugLog('[UnifiedChatService] joinSession SKIPPED: no socket', sessionId);
      return;
    }
    
    debugLog('[UnifiedChatService] joinSession: emitting chat:join for', sessionId, 'socket.connected=', this.socket.connected);
    console.debug(`[SOCKET-CHAT] EMIT chat:join session=${sessionId} connected=${this.socket.connected} id=${this.socket.id}`);
    // Emit immediately (will buffer if currently disconnected)
    this.socket.emit("chat:join", { session_id: sessionId });
    
    // Re-join automatically if the socket drops and reconnects
    this._on("connect", () => {
      debugLog('[UnifiedChatService] reconnect: re-emitting chat:join for', sessionId);
      console.debug(`[SOCKET-CHAT] RECONNECT re-EMIT chat:join session=${sessionId}`);
      this.socket.emit("chat:join", { session_id: sessionId });
    });
  }

  /**
   * Send a message via HTTP. Response arrives via Socket.IO events.
   */
  async sendMessage(sessionId, message, options = {}, imageBase64 = null, isVoiceMessage = false) {
    // imageBase64: one base64 string, or an array of them when several pictures are attached.
    debugLog('[UnifiedChatService] sendMessage START for session', sessionId, 'options=', options);
    // The backend gates Gemma4 direct path and screen tools on this flag.
    // Trigger when EITHER the screen viewer is open (user is watching live)
    // OR the session is in agent mode (sticky /agent toggle) — both cases
    // mean "the model should be able to drive the virtual screen".
    const store = useAppStore.getState();
    const screenOpen = store.agentScreenOpen === true;
    const inAgentMode = store.getSessionMode?.(sessionId) === "agent";
    const agentScreenActive = screenOpen || inAgentMode;
    // Per-chat thinking override (set via /thinking on|off). Only sent when the
    // user explicitly toggled it; otherwise omitted so the backend applies the
    // global `chat_thinking_default` Setting.
    const thinkPref = store.getSessionThinking?.(sessionId);
    const imageModel = store.chatImageModel || "auto";
    const body = {
      session_id: sessionId,
      message,
      options: {
        ...options,
        agent_screen_active: agentScreenActive,
        screen_viewer_open: screenOpen,
        // Sticky /agent mode: every message is a screen task, so the backend
        // does not reroute "draw ..." to image generation.
        agent_mode: inAgentMode,
        image_model: options.image_model || imageModel,
        ...(thinkPref !== undefined ? { think: thinkPref } : {}),
      },
      project_id: options.project_id,
      // Support project_root so backend auto-loads GUAARDVARK.md and rich project context.
      // Enables consistent project analysis (build.py review, CSS suggestions, etc.) across CLI and GUI.
      project_root: options.project_root || options.projectRoot,
      is_voice_message: isVoiceMessage,
    };
    // One picture goes as ``image``; several go as ``images`` (primary
    // first), each sent once. The server reads ``images`` when present and
    // falls back to ``image`` (backend/socketio_instance.py, chat_attachments),
    // so a request never carries the first picture twice.
    const pictures = Array.isArray(imageBase64) ? imageBase64.filter(Boolean) : (imageBase64 ? [imageBase64] : []);
    if (pictures.length > 1) {
      body.images = pictures;
    } else if (pictures.length === 1) {
      body.image = pictures[0];
    }
    debugLog('[UnifiedChatService] sendMessage POST /unified (agent_screen_active=', agentScreenActive, ', inAgentMode=', inAgentMode, ')');
    const response = await fetch(`${API_BASE}/api/chat/unified`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      debugLog('[UnifiedChatService] sendMessage HTTP ERROR', response.status);
      throw new Error(err.error || `HTTP ${response.status}`);
    }

    const json = await response.json();
    debugLog('[UnifiedChatService] sendMessage ACK received, response=', json);
    return json;
  }

  /**
   * Hand the running screen-agent task a note without stopping it. The agent
   * reads it at its next step. Resolves to the server's answer:
   * {queued: true} when the task took it, {queued: false, reason} when no
   * task is running for this chat (the caller then treats it as an ordinary
   * message).
   */
  async steerAgent(sessionId, message) {
    return steerAgent(sessionId, message);
  }

  /**
   * Register a callback for a Socket.IO event.
   * Tracks listeners for cleanup.
   */
  _on(event, callback) {
    if (!this.socket) {
      debugLog('[UnifiedChatService] _on SKIPPED (no socket):', event);
      return;
    }
    // Remove any existing listener for this event from THIS service instance
    // to prevent accumulation when service is reused or recreated on the same socket.
    const existing = this._listeners.filter((l) => l.event === event);
    for (const l of existing) {
      this.socket.off(l.event, l.callback);
    }
    this._listeners = this._listeners.filter((l) => l.event !== event);
    debugLog('[UnifiedChatService] _on: attaching', event, ' (had', existing.length, 'prior for event; total listeners now', this._listeners.length + 1, ')');
    this.socket.on(event, callback);
    this._listeners.push({ event, callback });
  }

  onThinking(callback) {
    debugLog('[UnifiedChatService] onThinking registration requested');
    this._on("chat:thinking", callback);
  }
  /**
   * Model reasoning stream: {delta} batches while the model thinks, then
   * {done: true, text} once per LLM call with the full reasoning text.
   */
  onReasoning(callback) {
    this._on("chat:reasoning", callback);
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
  /** Plan mode: one step of an approved plan finished (the step's own chat:complete). */
  onPlanStepDone(callback) {
    this._on("chat:plan_step_done", callback);
  }

  /**
   * Send tool approval response.
   * approvalId / requestId come from the chat:tool_approval_request being
   * answered; the server ignores an answer that names another card.
   * @param {{scope?: "once"|"session"|"task"|"always", tools?: string[], approvalId?: string, requestId?: string}} [opts]
   */
  sendToolApproval(sessionId, approved, opts) {
    if (this.socket?.connected) {
      const payload = { session_id: sessionId, approved };
      if (opts?.scope) payload.approval_scope = opts.scope;
      if (opts?.tools?.length) payload.tools = opts.tools;
      if (opts?.approvalId) payload.approval_id = opts.approvalId;
      if (opts?.requestId) payload.request_id = opts.requestId;
      this.socket.emit("chat:tool_approval_response", payload);
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
      throw new Error(translate("messages.chat.historyFailed", { status: response.status }));
    }
    return response.json();
  }

  /**
   * Remove all registered listeners.
   */
  cleanup() {
    if (!this.socket) {
      debugLog('[UnifiedChatService] cleanup: no socket');
      return;
    }
    const count = this._listeners.length;
    debugLog('[UnifiedChatService] cleanup: removing', count, 'listeners for events:', this._listeners.map(l => l.event));
    for (const { event, callback } of this._listeners) {
      this.socket.off(event, callback);
    }
    this._listeners = [];
    debugLog('[UnifiedChatService] cleanup COMPLETE');
  }
}

/**
 * POST a note to the running agent task (see UnifiedChatService.steerAgent).
 */
export async function steerAgent(sessionId, message) {
  const response = await fetch(`${API_BASE}/api/agent-control/steer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  const json = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(json.error || `HTTP ${response.status}`);
  }
  return json;
}

export default UnifiedChatService;
