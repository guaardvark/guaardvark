/**
 * Slash command execution handlers.
 * Each handler receives (args, context) where context has:
 *   { addMessage, updateMessage, onSendMessage, chatState, allCommands }
 */

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
    "/websearch": handleWebSearch,
    "/plan": handlePlan,
    "/training": handleTraining,
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

async function handleImageModel(args, { addMessage }) {
  if (!args) {
    try {
      const res = await fetch("/api/batch-image/models");
      const data = await res.json();
      const models = data?.data?.models || data?.models || [];
      const defaultModel = data?.data?.default_model || "sd-1.5";
      const current = sessionStorage.getItem("slash_image_model") || defaultModel;
      const downloaded = models.filter((m) => m.is_downloaded);
      addMessage({
        role: "system",
        content: `**Current image model:** ${current}\n\n**Available (downloaded):**\n${downloaded.map((m) => `- \`${m.id}\` — ${m.name}`).join("\n")}\n\n**Not downloaded:**\n${models.filter((m) => !m.is_downloaded).map((m) => `- \`${m.id}\``).join("\n") || "_(none)_"}`,
        tempId: `imgmodel-${Date.now()}`,
        type: "command",
      });
    } catch (err) {
      addMessage({ role: "system", content: `Failed to get image models: ${err.message}`, tempId: `imgmodel-${Date.now()}`, type: "command" });
    }
    return { handled: true };
  }

  // Validate the model exists
  const modelName = args.trim();
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
        sessionStorage.setItem("slash_image_model", match.id);
        addMessage({
          role: "system",
          content: `Image model switched to **${match.id}** (${match.name}). Will be used for the next \`/imagine\` command.`,
          tempId: `imgmodel-${Date.now()}`,
          type: "command",
        });
      }
    } else {
      const available = models.filter((m) => m.is_downloaded).map((m) => m.id).join(", ");
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
// /imagine <prompt> — sends through the normal chat pipeline
// ============================================================
// The unified chat engine has an image_generation tool that the LLM calls.
// /imagine is a shortcut: it rewrites the prompt to clearly request image
// generation, then sends it through the normal onSendMessage flow.
// The LLM calls the generate_image tool → offline_image_generator →
// saves to data/outputs/generated_images/ → streams result inline.

function handleImagine(args, { addMessage, onSendMessage }) {
  if (!args) {
    addMessage({ role: "system", content: "Usage: `/imagine <prompt>`", tempId: `img-${Date.now()}`, type: "command" });
    return { handled: true };
  }

  const model = sessionStorage.getItem("slash_image_model") || "";
  const modelHint = model ? ` Use the ${model} model.` : "";

  // Send as a normal chat message — the LLM will call the generate_image tool
  const imagePrompt = `Generate an image: ${args}.${modelHint} Use the generate_image tool to create this image.`;
  onSendMessage(imagePrompt, null);

  return { handled: true };
}

// ============================================================
// /websearch — stub (migration from ChatInput handled in a follow-up)
// ============================================================

async function handleWebSearch(args, { addMessage }) {
  if (!args) {
    addMessage({ role: "system", content: "Usage: `/websearch <query>`", tempId: `ws-${Date.now()}` });
    return { handled: true };
  }
  // Return unhandled so the existing ChatInput websearch handler can pick it up
  return { handled: false };
}

// ============================================================
// /plan — stub (migration from ChatPage handled in a follow-up)
// ============================================================

async function handlePlan(args, { addMessage }) {
  if (!args) {
    addMessage({ role: "system", content: "Usage: `/plan <request>`", tempId: `plan-${Date.now()}` });
    return { handled: true };
  }
  // Return unhandled so the existing ChatPage /plan handler can pick it up
  return { handled: false };
}

// ============================================================
// /training <task> — runs the agent's 1000-iteration training loop
// ============================================================
// Posts the raw task directly to /api/agent-control/execute with
// training_mode: true, bypassing the chat LLM entirely. The chat
// LLM's habit of decomposing multi-step tasks into single clicks
// is what was making every trainer run stop after one action.
// User watches progress via VNC; backend logs show servo events.

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
    const res = await fetch("/api/generation/from_command", {
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
