/**
 * Slash command execution handlers.
 * Each handler receives (args, context) where context has:
 *   { addMessage, updateMessage, onSendMessage, chatState, allCommands }
 */

import { useAppStore } from "../stores/useAppStore";
import { getChatImageModel, setChatImageModel as persistChatImageModel } from "../api/settingsService";

// ============================================================
// Dispatcher
// ============================================================

export async function executeBuiltinCommand(name, args, context) {
  const handlers = {
    "/help": handleHelp,
    "/clear": handleClear,
    "/voice": handleVoice,
    "/vision": handleVision,
    "/model": handleModel,
    "/imagemodel": handleImageModel,
    "/imagine": handleImagine,
    "/video": handleVideo,
    "/websearch": handleWebSearch,
    "/gpu": handleGpu,
    "/logs": handleLogs,
    "/sysmap": handleSysmap,
    "/swarm": handleSwarmSlash,
    "/outreach": handleOutreach,
    "/plan": handlePlan,
    "/training": handleTraining,
    "/agent": handleAgent,
    "/chat": handleChatMode,  // alias
    "/exit": handleChatMode,  // alias
    "/thinking": handleThinking,
  };

  const handler = handlers[name];
  if (!handler) {
    // Check if it's a DB rule command
    const cmd = context.allCommands.find((c) => c.name === name && c.handler === "rule");
    if (cmd) return handleDbRule(name, args, context, cmd);
    return { handled: false };
  }

  return handler(args, context);
}

/** Read persisted chat image model (backend), with store + legacy sessionStorage fallback. */
async function resolveCurrentImageModel(defaultModel = "auto") {
  const storeModel = useAppStore.getState().chatImageModel;
  if (storeModel && storeModel !== "auto") {
    return storeModel;
  }
  try {
    const res = await getChatImageModel();
    const model = res?.data?.model ?? res?.model;
    if (model) {
      useAppStore.getState().setChatImageModel(model);
      return model;
    }
  } catch {
    /* fall through */
  }
  const legacy = sessionStorage.getItem("slash_image_model");
  return legacy || defaultModel;
}

/** Persist model choice to backend + app store (and sessionStorage for older callers). */
async function saveImageModelChoice(modelId) {
  useAppStore.getState().setChatImageModel(modelId);
  sessionStorage.setItem("slash_image_model", modelId);
  try {
    await persistChatImageModel(modelId);
  } catch (err) {
    console.warn("Failed to persist chat image model:", err);
  }
}

// ============================================================
// /help
// ============================================================

function handleHelp(args, { addMessage, allCommands }) {
  const lines = allCommands.map(
    (cmd) => `**${cmd.name}** — ${cmd.description}\n  Usage: \`${cmd.usage}\``
  );
  addMessage({
    role: "system",
    content: `## Available Commands\n\n${lines.join("\n\n")}`,
    tempId: `help-${Date.now()}`,
    type: "command",
  });
  return { handled: true };
}

// ============================================================
// /clear
// ============================================================

function handleClear(args, { chatState }) {
  // chatState.clearMessages is expected to be passed by the parent
  if (chatState?.clearMessages) {
    chatState.clearMessages();
  }
  return { handled: true };
}

// ============================================================
// /voice
// ============================================================

function handleVoice(args, { addMessage, chatState }) {
  const voice = chatState?.voiceContext;
  if (voice?.toggleVoice) {
    voice.toggleVoice();
    addMessage({
      role: "system",
      content: `Voice chat ${voice.isVoiceActive ? "disabled" : "enabled"}.`,
      tempId: `voice-${Date.now()}`,
      type: "command",
    });
  } else {
    addMessage({
      role: "system",
      content: "Voice chat is not available in this context.",
      tempId: `voice-${Date.now()}`,
      type: "command",
    });
  }
  return { handled: true };
}

// ============================================================
// /vision
// ============================================================

function handleVision(args, { addMessage }) {
  addMessage({
    role: "system",
    content: "Vision pipeline coming soon. Use the Plugins page to start the Vision Pipeline service.",
    tempId: `vision-${Date.now()}`,
    type: "command",
  });
  return { handled: true };
}

// ============================================================
// /model [name]
// ============================================================

async function handleModel(args, { addMessage }) {
  if (!args) {
    // Show current model and available models
    try {
      const [activeRes, listRes] = await Promise.all([
        fetch("/api/model/active"),
        fetch("/api/model/list"),
      ]);
      const active = await activeRes.json();
      const list = await listRes.json();
      const models = list?.message?.models || list?.data || [];
      const modelNames = models.map((m) => m.name || m).slice(0, 20);
      addMessage({
        role: "system",
        content: `**Current model:** ${active?.model || active?.data?.model || "Unknown"}\n\n**Available models:**\n${modelNames.map((n) => `- ${n}`).join("\n")}`,
        tempId: `model-${Date.now()}`,
        type: "command",
      });
    } catch (err) {
      addMessage({ role: "system", content: `Failed to get models: ${err.message}`, tempId: `model-${Date.now()}`, type: "command" });
    }
    return { handled: true };
  }

  // Switch model
  try {
    const res = await fetch("/api/model/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: args.trim() }),
    });
    const data = await res.json();
    addMessage({
      role: "system",
      content: data.success !== false ? `Model switched to **${args.trim()}**.` : `Failed: ${data.error || data.message}`,
      tempId: `model-${Date.now()}`,
      type: "command",
    });
  } catch (err) {
    addMessage({ role: "system", content: `Model switch failed: ${err.message}`, tempId: `model-${Date.now()}`, type: "command" });
  }
  return { handled: true };
}

// ============================================================
// /imagemodel [name]
// ============================================================

const KONTEXT_EDIT_OPTION = {
  id: "kontext",
  name: "FLUX.1 Kontext [dev] (instruction image editing)",
  is_downloaded: true,
};

async function handleImageModel(args, { addMessage }) {
  if (!args) {
    try {
      const [modelsRes, current] = await Promise.all([
        fetch("/api/batch-image/models"),
        resolveCurrentImageModel("auto"),
      ]);
      const data = await modelsRes.json();
      const models = data?.data?.models || data?.models || [];
      const downloaded = [
        KONTEXT_EDIT_OPTION,
        ...models.filter((m) => m.is_downloaded),
      ];
      addMessage({
        role: "system",
        content: `**Current image model:** \`${current}\`\n\n`
          + `Used for \`/imagine\`, **generate_image**, and **edit_image** in chat.\n\n`
          + `**Available:**\n${downloaded.map((m) => `- \`${m.id}\` — ${m.name}`).join("\n")}\n\n`
          + `**Not downloaded:**\n${models.filter((m) => !m.is_downloaded).map((m) => `- \`${m.id}\``).join("\n") || "_(none)_"}\n\n`
          + `_Tip: \`kontext\` / \`auto\` use FLUX Kontext for edits when installed; other models use img2img._`,
        tempId: `imgmodel-${Date.now()}`,
        type: "command",
      });
    } catch (err) {
      addMessage({ role: "system", content: `Failed to get image models: ${err.message}`, tempId: `imgmodel-${Date.now()}`, type: "command" });
    }
    return { handled: true };
  }

  const modelName = args.trim().toLowerCase();
  if (modelName === "kontext") {
    await saveImageModelChoice("kontext");
    addMessage({
      role: "system",
      content: "Image model switched to **kontext** (FLUX.1 Kontext instruction editing).",
      tempId: `imgmodel-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  try {
    const res = await fetch("/api/batch-image/models");
    const data = await res.json();
    const models = data?.data?.models || data?.models || [];
    const match = models.find((m) => m.id === modelName || m.id.startsWith(modelName));
    if (match) {
      if (!match.is_downloaded) {
        addMessage({
          role: "system",
          content: `Model \`${match.id}\` is not downloaded. Download it from the Images page first.`,
          tempId: `imgmodel-${Date.now()}`,
          type: "command",
        });
      } else {
        await saveImageModelChoice(match.id);
        addMessage({
          role: "system",
          content: `Image model switched to **${match.id}** (${match.name}). Used for chat generation and img2img edits.`,
          tempId: `imgmodel-${Date.now()}`,
          type: "command",
        });
      }
    } else {
      const available = ["kontext", ...models.filter((m) => m.is_downloaded).map((m) => m.id)].join(", ");
      addMessage({
        role: "system",
        content: `Model \`${modelName}\` not found. Available: ${available}`,
        tempId: `imgmodel-${Date.now()}`,
        type: "command",
      });
    }
  } catch (err) {
    addMessage({ role: "system", content: `Failed: ${err.message}`, tempId: `imgmodel-${Date.now()}`, type: "command" });
  }
  return { handled: true };
}

// ============================================================
// /imagine <prompt> — direct generate_image tool (bypass LLM for explicit command to avoid eviction issues during the turn; uses selected /imagemodel)
// ============================================================
// Restored direct path per investigation (git f5ad8c8 introduced guaranteed eviction for chat image to fix OOM; direct avoids LLM call for /imagine itself).
// The model from /imagemodel is passed explicitly so chat uses the selected one.

async function handleImagine(args, { addMessage, onSendMessage }) {
  if (!args) {
    addMessage({ role: "system", content: "Usage: `/imagine <prompt>`", tempId: `img-${Date.now()}`, type: "command" });
    return { handled: true };
  }

  const model = await resolveCurrentImageModel("auto");
  const displayText = `/imagine ${args}`;

  onSendMessage(displayText, null, {
    direct_tool: "generate_image",
    direct_tool_params: { prompt: args, model },
    slash_command: "imagine",
    slash_args: args,
    image_model: model,
  });

  return { handled: true };
}

async function handleVideo(args, { addMessage, onSendMessage }) {
  if (!args) {
    addMessage({ role: "system", content: "Usage: `/video <prompt>`", tempId: `vid-${Date.now()}`, type: "command" });
    return { handled: true };
  }

  onSendMessage(`/video ${args}`, null, {
    direct_tool: "generate_video",
    direct_tool_params: { prompt: args },
    slash_command: "video",
    slash_args: args,
  });

  return { handled: true };
}

// ============================================================
// /websearch <query> — direct web_search tool
// ============================================================

async function handleWebSearch(args, { addMessage, onSendMessage }) {
  if (!args) {
    addMessage({ role: "system", content: "Usage: `/websearch <query>`", tempId: `ws-${Date.now()}` });
    return { handled: true };
  }

  onSendMessage(`/websearch ${args}`, null, {
    direct_tool: "web_search",
    direct_tool_params: { query: args },
    slash_command: "websearch",
    slash_args: args,
  });

  return { handled: true };
}

function handleGpu(_args, { onSendMessage }) {
  onSendMessage("/gpu", null, {
    direct_tool: "inspect_gpu",
    direct_tool_params: {},
    slash_command: "gpu",
  });
  return { handled: true };
}

function handleLogs(args, { onSendMessage }) {
  onSendMessage(args ? `/logs ${args}` : "/logs", null, {
    direct_tool: "read_logs",
    slash_command: "logs",
    slash_args: args || "",
  });
  return { handled: true };
}

function handleSysmap(args, { onSendMessage }) {
  onSendMessage(args ? `/sysmap ${args}` : "/sysmap", null, {
    direct_tool: "map_codebase",
    slash_command: "sysmap",
    slash_args: args || "",
  });
  return { handled: true };
}

function handleSwarmSlash(args, { onSendMessage }) {
  onSendMessage(args ? `/swarm ${args}` : "/swarm", null, {
    direct_tool: "swarm_status",
    slash_command: "swarm",
    slash_args: args || "",
  });
  return { handled: true };
}

// ============================================================
// /outreach [status|reddit|…| freeform NL]
// ============================================================

async function handleOutreach(args, { addMessage }) {
  const {
    fetchStatus,
    runPass,
    executeIntent,
  } = await import("../api/outreachService");

  const raw = (args || "").trim();
  const [verbRaw = "status", ...rest] = raw.split(/\s+/).filter(Boolean);
  const verb = verbRaw.toLowerCase().replace("-", "_");

  addMessage({
    role: "user",
    content: raw ? `/outreach ${raw}` : "/outreach status",
    tempId: `outreach-user-${Date.now()}`,
    type: "command",
  });

  if (!raw || verb === "status") {
    try {
      const data = await fetchStatus();
      const enabled = data.enabled ? "Enabled" : "Disabled";
      const supervised = data.supervised ? "supervised" : "unsupervised";
      const cadence = data.cadence || {};
      const cadenceLines = Object.entries(cadence).map(([platform, value]) => {
        if (value?.redis === "unavailable") {
          return `- ${platform}: Redis offline`;
        }
        const posts = value.posts_in_24h ?? 0;
        const cap = value.daily_cap ?? 0;
        const last = value.last_post_seconds_ago != null
          ? `, last ${Math.floor(value.last_post_seconds_ago / 60)}m ago`
          : "";
        return `- ${platform}: ${posts}/${cap} today${last}`;
      });
      addMessage({
        role: "system",
        content: `**Outreach:** ${enabled} (${supervised})\n\n${cadenceLines.join("\n") || "No cadence data."}`,
        tempId: `outreach-status-${Date.now()}`,
        type: "command",
      });
    } catch (err) {
      addMessage({
        role: "system",
        content: `Outreach status failed: ${err.message}`,
        tempId: `outreach-status-err-${Date.now()}`,
        type: "command",
      });
    }
    return { handled: true };
  }

  const platformAliases = {
    reddit: "reddit",
    self_share: "self_share",
    selfshare: "self_share",
    share: "self_share",
    recon: "recon",
    draft: "draft",
    youtube: "youtube",
  };
  const platform = platformAliases[verb];

  // Known short verbs → run-pass; anything else is classify-then-dispatch.
  if (!platform) {
    try {
      const data = await executeIntent({ text: raw, created_by: "slash" });
      const intent = data.intent || data.classification?.intent;
      let content = data.message || data.error || JSON.stringify(data.plan || data);
      if (data.refused) {
        content = data.message || data.error || "Outreach: request refused.";
      } else if (data.ok && Array.isArray(data.task_ids) && data.task_ids.length) {
        // Keep server message (already says Queued …)
        content = data.message || content;
      } else if (data.ok && (intent === "status" || intent === "list_queue")) {
        content = data.message || content;
      } else if (!data.ok) {
        content = data.message || data.error || content;
      }
      addMessage({
        role: "system",
        content,
        tempId: `outreach-intent-${Date.now()}`,
        type: "command",
      });
    } catch (err) {
      addMessage({
        role: "system",
        content: `Outreach intent failed: ${err.message}`,
        tempId: `outreach-intent-err-${Date.now()}`,
        type: "command",
      });
    }
    return { handled: true };
  }

  const subreddit = rest[0] ? rest[0].replace(/^r\//i, "") : undefined;
  const linkUrl = rest.find((token) => /^https?:\/\//i.test(token));

  try {
    const data = await runPass({
      platform,
      ...(subreddit && platform !== "draft" ? { subreddit } : {}),
      ...(linkUrl ? { link_url: linkUrl } : {}),
      ...(platform === "youtube" ? { chain_draft: true, topics: rest.join(" ") || undefined } : {}),
    });
    addMessage({
      role: "system",
      content: data.message || `Outreach job queued as task #${data.task_id}.`,
      tempId: `outreach-ok-${Date.now()}`,
      type: "command",
    });
  } catch (err) {
    addMessage({
      role: "system",
      content: `Outreach command failed: ${err.message}`,
      tempId: `outreach-err-${Date.now()}`,
      type: "command",
    });
  }
  return { handled: true };
}

// ============================================================
// /plan — stub (migration from ChatPage handled in a follow-up)
// ============================================================

async function handlePlan(args, { addMessage }) {
  if (!args) {
    addMessage({ role: "system", content: "Usage: `/plan <request>`", tempId: `plan-${Date.now()}` });
    return { handled: true };
  }
  return { handled: false };
}

// ============================================================
// /training <task> — runs the agent's 1000-iteration training loop
// ============================================================

async function handleTraining(args, { addMessage }) {
  if (!args) {
    addMessage({
      role: "system",
      content: "Usage: `/training <task>` — e.g. `/training Work the Comments Trainer — follow the banner, click Start Over when done, don't stop.`",
      tempId: `train-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  addMessage({
    role: "user",
    content: `/training ${args}`,
    tempId: `train-user-${Date.now()}`,
  });

  try {
    const res = await fetch("/api/agent-control/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task: args, training_mode: true }),
    });
    const data = await res.json();
    if (data.success) {
      addMessage({
        role: "system",
        content: `Training run started (up to 1000 iterations / 1 hour). Watch VNC for progress; tail \`logs/backend.log\` for servo events. Task: _${args}_`,
        tempId: `train-ok-${Date.now()}`,
        type: "command",
      });
    } else {
      addMessage({
        role: "system",
        content: `Training run rejected: ${data.error || "unknown error"}${data.error === "Agent already active" ? " — use kill switch or wait for current run." : ""}`,
        tempId: `train-fail-${Date.now()}`,
        type: "command",
      });
    }
  } catch (err) {
    addMessage({
      role: "system",
      content: `Training run failed: ${err.message}`,
      tempId: `train-err-${Date.now()}`,
      type: "command",
    });
  }
  return { handled: true };
}

// ============================================================
// DB rule commands
// ============================================================

async function handleDbRule(name, args, { addMessage }) {
  addMessage({ role: "user", content: `${name} ${args}`, tempId: `rule-user-${Date.now()}` });
  try {
    const res = await fetch("/api/generate/from_command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command_label: name,
        generation_parameters: { args },
      }),
    });
    const data = await res.json();
    addMessage({
      role: "assistant",
      content: data?.data?.content || data?.content || data?.message || "Command executed.",
      tempId: `rule-asst-${Date.now()}`,
    });
  } catch (err) {
    addMessage({ role: "system", content: `Command failed: ${err.message}`, tempId: `rule-err-${Date.now()}` });
  }
  return { handled: true };
}

// ============================================================
// /agent and /chat — modal session toggle
// ============================================================

async function _patchSessionMode(sessionId, mode) {
  const res = await fetch(`/api/chat-sessions/${encodeURIComponent(sessionId)}/mode`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `PATCH /mode failed (${res.status})`);
  }
  return res.json();
}

async function handleAgent(args, { addMessage, onSendMessage, chatState }) {
  const sessionId = chatState?.sessionId;
  if (!sessionId) {
    addMessage({
      role: "system",
      content: "/agent needs a session — open a chat first.",
      tempId: `agent-no-session-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  const previousMode = useAppStore.getState().getSessionMode(sessionId);
  const trimmedArgs = (args || "").trim();

  try {
    const data = await _patchSessionMode(sessionId, "agent");
    useAppStore.getState().setSessionMode(sessionId, data?.mode || "agent");
  } catch (err) {
    addMessage({
      role: "system",
      content: `Failed to switch into agent mode: ${err.message}`,
      tempId: `agent-fail-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  if (!trimmedArgs) {
    addMessage({
      role: "user",
      content: "/agent",
      tempId: `agent-user-${Date.now()}`,
    });
    addMessage({
      role: "system",
      content: previousMode === "agent"
        ? "Already in **agent mode**. Type a screen-control task, or use `/chat` to exit."
        : "Switched to **agent mode** — messages route through the agent (it'll speak and act). Type `/chat` to exit.",
      tempId: `agent-ok-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  if (typeof onSendMessage === "function") {
    onSendMessage(trimmedArgs, null);
  } else {
    addMessage({
      role: "system",
      content: "Internal error: onSendMessage unavailable in this context.",
      tempId: `agent-no-send-${Date.now()}`,
      type: "command",
    });
  }
  return { handled: true };
}

// ============================================================
// /thinking [on|off]
// ============================================================

async function handleThinking(args, { addMessage, chatState }) {
  const sessionId = chatState?.sessionId;
  if (!sessionId) {
    addMessage({
      role: "system",
      content: "/thinking needs a session — open a chat first.",
      tempId: `thinking-no-session-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  const arg = (args || "").trim().toLowerCase();
  const store = useAppStore.getState();

  if (!arg) {
    const cur = store.getSessionThinking(sessionId);
    const state =
      cur === undefined
        ? "using the global default (Settings → Chat)"
        : cur
        ? "ON"
        : "OFF";
    addMessage({
      role: "system",
      content: `🧠 Thinking for this chat is **${state}**.\nUse \`/thinking on\` to enable step-by-step reasoning (slower) or \`/thinking off\` for faster replies.`,
      tempId: `thinking-status-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  const on = ["on", "true", "1", "yes", "enable"].includes(arg);
  const off = ["off", "false", "0", "no", "disable"].includes(arg);
  if (!on && !off) {
    addMessage({
      role: "system",
      content: "Usage: `/thinking on` or `/thinking off` (or `/thinking` to show the current state).",
      tempId: `thinking-usage-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  store.setSessionThinking(sessionId, on);
  addMessage({
    role: "system",
    content: on
      ? "🧠 Thinking **enabled** for this chat — the model will reason step-by-step before answering (slower, better for complex prompts)."
      : "⚡ Thinking **disabled** for this chat — faster replies.",
    tempId: `thinking-set-${Date.now()}`,
    type: "command",
  });
  return { handled: true };
}

async function handleChatMode(_args, { addMessage, chatState }) {
  const sessionId = chatState?.sessionId;
  if (!sessionId) {
    addMessage({
      role: "system",
      content: "/chat needs a session — open a chat first.",
      tempId: `chat-no-session-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  const previousMode = useAppStore.getState().getSessionMode(sessionId);

  try {
    await fetch(`/api/chat/unified/${encodeURIComponent(sessionId)}/abort`, {
      method: "POST"
    }).catch((err) => {
      console.warn("Failed to send abort signal:", err);
    });
    await fetch("/api/agent-control/kill", {
      method: "POST"
    }).catch((err) => {
      console.warn("Failed to kill agent task:", err);
    });

    const data = await _patchSessionMode(sessionId, "chat");
    useAppStore.getState().setSessionMode(sessionId, data?.mode || "chat");
    addMessage({
      role: "system",
      content: previousMode === "chat"
        ? "Already in chat mode."
        : "Switched to **chat mode**. Messages route through the LLM again. Type `/agent` to switch back.",
      tempId: `chat-ok-${Date.now()}`,
      type: "command",
    });
  } catch (err) {
    addMessage({
      role: "system",
      content: `Failed to exit agent mode: ${err.message}`,
      tempId: `chat-fail-${Date.now()}`,
      type: "command",
    });
  }
  return { handled: true };
}

/** Hydrate persisted chat image model into the app store (call once on ChatPage mount). */
export async function hydrateChatImageModel() {
  const legacy = sessionStorage.getItem("slash_image_model");
  try {
    const res = await getChatImageModel();
    const model = res?.data?.model ?? res?.model;
    if (model) {
      useAppStore.getState().setChatImageModel(model);
      sessionStorage.setItem("slash_image_model", model);
      return model;
    }
  } catch {
    /* fall through */
  }
  if (legacy) {
    useAppStore.getState().setChatImageModel(legacy);
    try {
      await persistChatImageModel(legacy);
    } catch {
      /* non-fatal */
    }
    return legacy;
  }
  return "auto";
}
