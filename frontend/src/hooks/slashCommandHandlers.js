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
// /imagine <prompt> — full implementation with batch polling
// ============================================================

async function handleImagine(args, { addMessage, updateMessage }) {
  if (!args) {
    addMessage({ role: "system", content: "Usage: `/imagine <prompt>`", tempId: `img-${Date.now()}` });
    return { handled: true };
  }

  const tempId = `imagine-${Date.now()}`;
  const model = sessionStorage.getItem("slash_image_model") || "sd-1.5";
  const isXl = model.includes("xl") || model.includes("sdxl");

  // Show user message
  addMessage({ role: "user", content: `/imagine ${args}`, tempId: `img-user-${Date.now()}` });

  // Show progress message
  addMessage({ role: "assistant", content: "Generating image...", tempId, type: "progress" });

  try {
    // Start generation
    const genRes = await fetch("/api/batch-image/generate/prompts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompts: [{ text: args }],
        model,
        count: 1,
        steps: 20,
        width: isXl ? 1024 : 512,
        height: isXl ? 1024 : 512,
      }),
    });
    const genData = await genRes.json();
    const batchId = genData?.data?.batch_id || genData?.batch_id;

    if (!batchId) {
      updateMessage(tempId, { content: `Image generation failed: ${genData?.error || "No batch ID returned"}` });
      return { handled: true };
    }

    // Poll for completion
    const startTime = Date.now();
    const pollInterval = setInterval(async () => {
      try {
        if (Date.now() - startTime > 120000) {
          clearInterval(pollInterval);
          updateMessage(tempId, { content: "Image generation timed out after 2 minutes." });
          return;
        }

        const statusRes = await fetch(`/api/batch-image/status/${batchId}?include_results=true`);
        const statusData = await statusRes.json();
        const status = statusData?.data?.status || statusData?.status;

        if (status === "completed" || status === "done") {
          clearInterval(pollInterval);
          const results = statusData?.data?.results || statusData?.results || [];
          const firstResult = results[0];
          const imagePath = firstResult?.output_path || firstResult?.image_path;

          if (imagePath) {
            // Convert file path to API URL
            const imageUrl = imagePath.includes("/outputs/")
              ? `/api/output/file/${imagePath.split("/outputs/").pop()}`
              : imagePath;
            updateMessage(tempId, {
              content: `![Generated Image](${imageUrl})\n\n*Model: ${model} | Prompt: ${args}*`,
              type: "image",
            });
          } else {
            updateMessage(tempId, { content: "Image generated but no output path found." });
          }
        } else if (status === "failed" || status === "error") {
          clearInterval(pollInterval);
          const error = statusData?.data?.error || statusData?.error || "Unknown error";
          updateMessage(tempId, { content: `Image generation failed: ${error}` });
        } else {
          // Still processing — update progress if step info available
          const progress = statusData?.data?.progress;
          if (progress) {
            updateMessage(tempId, { content: `Generating image... ${progress}` });
          }
        }
      } catch (pollErr) {
        console.error("Image poll error:", pollErr);
      }
    }, 3000);
  } catch (err) {
    updateMessage(tempId, { content: `Image generation failed: ${err.message}` });
  }

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
// DB rule commands
// ============================================================

async function handleDbRule(name, args, { addMessage }, cmd) {
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
