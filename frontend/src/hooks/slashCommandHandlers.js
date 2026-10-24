/**
 * Slash command execution handlers.
 * Each handler receives (args, context) where context has:
 *   { addMessage, updateMessage, onSendMessage, chatState, allCommands }
 */

import { useAppStore } from "../stores/useAppStore";
import { BASE_URL, handleResponse } from "../api/apiClient";
import { stopChatTurn } from "../api/chatStopService";
import { getChatImageModel, setChatImageModel as persistChatImageModel } from "../api/settingsService";
import { getPlugin } from "../api/pluginsService";
import { translate } from "../i18n";

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
    "/removebg": handleRemoveBg,
    "/inpaint": handleInpaint,
    "/outpaint": handleOutpaint,
    "/identity": handleIdentity,
    "/video": handleVideo,
    "/music-video": handleMusicVideo,
    "/film-crew": handleFilmCrew,
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
    (cmd) => translate("messages.slash.helpLine", { name: cmd.name, description: cmd.description, usage: cmd.usage })
  );
  addMessage({
    role: "system",
    content: translate("messages.slash.help", { commands: lines.join("\n\n") }),
    tempId: `help-${Date.now()}`,
    type: "command",
  });
  return { handled: true };
}

// ============================================================
// /clear
// ============================================================

function handleClear(args, { chatState }) {
  // chatState.clearMessages is passed by the parent. Emptying the screen alone
  // is not a clear: the session's stored messages come back on reload, so the
  // Chat page passes its "new chat" action and the floating chat starts a new
  // session id. The old chat stays listed under All chats.
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
      content: voice.isVoiceActive ? translate("messages.slash.voiceDisabled") : translate("messages.slash.voiceEnabled"),
      tempId: `voice-${Date.now()}`,
      type: "command",
    });
  } else {
    addMessage({
      role: "system",
      content: translate("messages.slash.voiceUnavailable"),
      tempId: `voice-${Date.now()}`,
      type: "command",
    });
  }
  return { handled: true };
}

// ============================================================
// /vision
// ============================================================

async function handleVision(args, { addMessage }) {
  // Same plugin record the Plugins page shows (GET /api/plugins/vision_pipeline).
  let content;
  try {
    const res = await getPlugin("vision_pipeline");
    const info = res?.data || res || {};
    const name = info.name || "Vision Pipeline";
    const lines = [translate("messages.slash.visionStatus", { name, status: info.status || translate("messages.slash.unknownStatus") })];
    if (info.status_message) lines.push(info.status_message);
    lines.push(translate("messages.slash.visionHint"));
    content = lines.join("\n\n");
  } catch (err) {
    content = err?.status === 404
      ? translate("messages.slash.visionNotInstalled")
      : translate("messages.slash.visionReadFailed", { reason: err.message });
  }
  addMessage({
    role: "system",
    content,
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
      // handleResponse: a 4xx/5xx or an offline backend becomes an error with
      // the server's message, instead of a JSON parse failure or "Unknown".
      const active = await handleResponse(activeRes);
      const list = await handleResponse(listRes);
      const models = list?.data?.models || list?.message?.models || list?.data || [];
      const modelNames = models.map((m) => m.name || m).slice(0, 20);
      addMessage({
        role: "system",
        content: translate("messages.slash.modelStatus", { model: active?.model || active?.data?.model || translate("messages.slash.unknownModel"), models: modelNames.map((n) => `- ${n}`).join("\n") }),
        tempId: `model-${Date.now()}`,
        type: "command",
      });
    } catch (err) {
      addMessage({ role: "system", content: translate("messages.slash.modelsFailed", { reason: err.message }), tempId: `model-${Date.now()}`, type: "command" });
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
    const data = await handleResponse(res);
    addMessage({
      role: "system",
      content: data.success !== false ? translate("messages.slash.modelSwitched", { model: args.trim() }) : translate("messages.slash.failed", { reason: data.error || data.message }),
      tempId: `model-${Date.now()}`,
      type: "command",
    });
  } catch (err) {
    addMessage({ role: "system", content: translate("messages.slash.modelSwitchFailed", { reason: err.message }), tempId: `model-${Date.now()}`, type: "command" });
  }
  return { handled: true };
}

// ============================================================
// /imagemodel [name]
// ============================================================

// Image-editing packs (Qwen-Image-Edit, Kontext, PuLID, background removal)
// come back with the image models list; each row names the chat tools it enables.
function formatEditBackendLine(m) {
  const flag = m.installed ? translate("messages.slash.installed") : translate("messages.slash.notInstalled");
  const tools = (m.tools || []).join(", ");
  return `- \`${m.id}\` — ${m.name} (${flag})${tools ? ` · ${tools}` : ""}`;
}

// Generic ComfyUI backend — always selectable in chat even when no ComfyUI
// assets are installed yet, since it routes to whatever engine ComfyUI has.
const COMFYUI_MODEL_OPTION = {
  id: "comfyui",
  get name() { return translate("messages.slash.comfyuiAutoBackend"); },
  is_downloaded: true,
};

async function handleImageModel(args, { addMessage }) {
  if (!args) {
    try {
      const [modelsRes, current] = await Promise.all([
        fetch("/api/batch-image/models"),
        resolveCurrentImageModel("auto"),
      ]);
      const data = await handleResponse(modelsRes);
      const models = data?.data?.models || data?.models || [];
      const editBackends = data?.data?.editing || data?.editing || [];
      const editLines = editBackends.length
        ? editBackends.map(formatEditBackendLine).join("\n")
        : translate("messages.slash.editBackendsFailed");
      const downloaded = [
        COMFYUI_MODEL_OPTION,
        ...models.filter((m) => m.is_downloaded),
      ];
      addMessage({
        role: "system",
        content: translate("messages.slash.imageModelStatus", {
          current,
          editLines,
          downloaded: downloaded.map((m) => `- \`${m.id}\` — ${m.name}`).join("\n") || translate("messages.slash.none"),
          notDownloaded: models.filter((m) => !m.is_downloaded).map((m) => `- \`${m.id}\``).join("\n") || translate("messages.slash.none"),
        }),
        tempId: `imgmodel-${Date.now()}`,
        type: "command",
      });
    } catch (err) {
      addMessage({ role: "system", content: translate("messages.slash.imageModelsFailed", { reason: err.message }), tempId: `imgmodel-${Date.now()}`, type: "command" });
    }
    return { handled: true };
  }

  const modelName = args.trim().toLowerCase();
  if (modelName === "auto") {
    await saveImageModelChoice("auto");
    addMessage({
      role: "system",
      content: translate("messages.slash.imageModelAuto"),
      tempId: `imgmodel-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }
  if (modelName === "kontext" || modelName === "flux-kontext-dev" || modelName === "qwen-image-edit" || modelName === "qwen") {
    const id = (modelName === "qwen" || modelName === "qwen-image-edit") ? "qwen-image-edit" : "kontext";
    await saveImageModelChoice(id);
    addMessage({
      role: "system",
      content: id === "qwen-image-edit"
        ? translate("messages.slash.imageEditsQwen")
        : translate("messages.slash.imageEditsKontext"),
      tempId: `imgmodel-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  // "comfyui" is a backend selector, not a downloadable model — accept it
  // regardless of asset status (the backend picks whatever engine is installed).
  if (modelName === "comfyui") {
    await saveImageModelChoice("comfyui");
    addMessage({
      role: "system",
      content: translate("messages.slash.imageModelComfyui"),
      tempId: `imgmodel-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  try {
    const res = await fetch("/api/batch-image/models");
    const data = await handleResponse(res);
    const models = data?.data?.models || data?.models || [];
    const match = models.find((m) => m.id === modelName || m.id.startsWith(modelName));
    if (match) {
      if (!match.is_downloaded) {
        addMessage({
          role: "system",
          content: translate("messages.slash.imageModelNotDownloaded", { model: match.id }),
          tempId: `imgmodel-${Date.now()}`,
          type: "command",
        });
      } else {
        await saveImageModelChoice(match.id);
        addMessage({
          role: "system",
          content: translate("messages.slash.imageModelSwitched", { model: match.id, name: match.name }),
          tempId: `imgmodel-${Date.now()}`,
          type: "command",
        });
      }
    } else {
      const available = ["auto", "qwen-image-edit", "kontext", "comfyui", ...models.filter((m) => m.is_downloaded).map((m) => m.id)].join(", ");
      addMessage({
        role: "system",
        content: translate("messages.slash.imageModelNotFound", { model: modelName, available }),
        tempId: `imgmodel-${Date.now()}`,
        type: "command",
      });
    }
  } catch (err) {
    addMessage({ role: "system", content: translate("messages.slash.failed", { reason: err.message }), tempId: `imgmodel-${Date.now()}`, type: "command" });
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
    addMessage({ role: "system", content: translate("messages.slash.usage.imagine"), tempId: `img-${Date.now()}`, type: "command" });
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

async function handleRemoveBg(args, { onSendMessage }) {
  onSendMessage("/removebg", null, {
    direct_tool: "remove_background",
    direct_tool_params: {},
    slash_command: "removebg",
  });
  return { handled: true };
}

async function handleInpaint(args, { addMessage, onSendMessage }) {
  if (!args) {
    addMessage({ role: "system", content: translate("messages.slash.usage.inpaint"), tempId: `img-${Date.now()}`, type: "command" });
    return { handled: true };
  }
  onSendMessage(`/inpaint ${args}`, null, {
    direct_tool: "inpaint_image",
    direct_tool_params: { instruction: args },
    slash_command: "inpaint",
    slash_args: args,
  });
  return { handled: true };
}

const OUTPAINT_SIDES = new Set(["left", "right", "top", "bottom", "all"]);

function parseOutpaintArgs(args) {
  const tokens = (args || "").trim().split(/\s+/).filter(Boolean);
  const pad = { left: 0, right: 0, top: 0, bottom: 0 };
  let i = 0;
  while (i < tokens.length && OUTPAINT_SIDES.has(tokens[i].toLowerCase())) {
    const side = tokens[i].toLowerCase();
    if (side === "all") {
      pad.left = pad.right = pad.top = pad.bottom = 256;
    } else {
      pad[side] = 256;
    }
    i += 1;
  }
  const instruction = tokens.slice(i).join(" ").trim();
  const named = pad.left || pad.right || pad.top || pad.bottom;
  return named ? { ...pad, instruction } : { instruction };
}

async function handleOutpaint(args, { onSendMessage }) {
  const parsed = parseOutpaintArgs(args);
  onSendMessage(`/outpaint ${args || ""}`.trim(), null, {
    direct_tool: "outpaint_image",
    direct_tool_params: parsed,
    slash_command: "outpaint",
    slash_args: args || "",
  });
  return { handled: true };
}

async function handleIdentity(args, { addMessage, onSendMessage }) {
  if (!args) {
    addMessage({
      role: "system",
      content: translate("messages.slash.usage.identity"),
      tempId: `img-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }
  onSendMessage(`/identity ${args}`, null, {
    direct_tool: "generate_identity",
    direct_tool_params: { prompt: args, consented: true },
    slash_command: "identity",
    slash_args: args,
  });
  return { handled: true };
}

async function handleVideo(args, { addMessage, onSendMessage }) {
  if (!args) {
    addMessage({ role: "system", content: translate("messages.slash.usage.video"), tempId: `vid-${Date.now()}`, type: "command" });
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

const AUDIO_EXT_RE = /\.(mp3|wav|flac|ogg|m4a|aac)$/i;

function parseMusicVideoArgs(args) {
  const tokens = (args || "").trim().split(/\s+/).filter(Boolean);
  let song = null;
  let songIdx = -1;
  for (let i = 0; i < tokens.length; i += 1) {
    const cleaned = tokens[i].replace(/^[.,;:"']+|[.,;:"']+$/g, "");
    if (/^\d+$/.test(cleaned) || AUDIO_EXT_RE.test(cleaned) || cleaned.includes("/")) {
      song = cleaned;
      songIdx = i;
      break;
    }
  }
  const style = tokens.filter((_, i) => i !== songIdx).join(" ").trim();
  return { song, style_prompt: style || null };
}

async function handleMusicVideo(args, { addMessage, onSendMessage }) {
  if (!args) {
    addMessage({
      role: "system",
      content: translate("messages.slash.usage.musicVideo"),
      tempId: `mv-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }
  const parsed = parseMusicVideoArgs(args);
  if (!parsed.song || !parsed.style_prompt) {
    addMessage({
      role: "system",
      content: translate("messages.slash.musicVideoNeedsSong"),
      tempId: `mv-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }
  onSendMessage(`/music-video ${args}`, null, {
    direct_tool: "generate_music_video",
    direct_tool_params: { song: parsed.song, style_prompt: parsed.style_prompt },
    slash_command: "music-video",
    slash_args: args,
  });
  return { handled: true };
}

async function handleFilmCrew(args, { addMessage, onSendMessage }) {
  if (!args) {
    addMessage({
      role: "system",
      content: translate("messages.slash.usage.filmCrew"),
      tempId: `fc-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }
  onSendMessage(`/film-crew ${args}`, null, {
    direct_tool: "start_film_crew",
    direct_tool_params: { script_text: args.trim() },
    slash_command: "film-crew",
    slash_args: args,
  });
  return { handled: true };
}

// ============================================================
// /websearch <query> | site:<address> | sitemap:<url>
// ============================================================
// Every form runs in the backend, under its web-access setting, and the reply
// names the service that answered. Nothing is fetched from the browser.
//   <query>                the web_search tool (the search engine)
//   site:<address>         the analyze_website tool: that page's title,
//                          description and SEO report (public addresses only)
//   site:<domain> <words>  a search, with the engine's own site: filter
//   sitemap:<url>          POST /api/web-search/sitemap: what the sitemap lists
//                          (public addresses only)

const websearchUsage = () => translate("messages.slash.usage.websearch");

async function handleWebSearch(args, { addMessage, onSendMessage }) {
  const query = (args || "").trim();
  const bareForm = /^(site|sitemap):\s*$/i.test(query);
  if (!query || bareForm) {
    addMessage({ role: "system", content: websearchUsage(), tempId: `ws-${Date.now()}` });
    return { handled: true };
  }

  const sitemap = query.match(/^sitemap:\s*(.+)$/i);
  if (sitemap) {
    return reportSitemap(sitemap[1].trim(), query, addMessage);
  }

  const site = query.match(/^site:\s*(\S+)$/i);
  if (site) {
    onSendMessage(`/websearch ${query}`, null, {
      direct_tool: "analyze_website",
      direct_tool_params: { url: site[1] },
      slash_command: "websearch",
      slash_args: query,
    });
    return { handled: true };
  }

  onSendMessage(`/websearch ${query}`, null, {
    direct_tool: "web_search",
    direct_tool_params: { query },
    slash_command: "websearch",
    slash_args: query,
  });

  return { handled: true };
}

async function reportSitemap(url, query, addMessage) {
  const stamp = Date.now();
  addMessage({ role: "user", content: `/websearch ${query}`, tempId: `sitemap-user-${stamp}`, type: "command" });
  let content;
  try {
    const res = await fetch(`${BASE_URL}/web-search/sitemap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const body = await res.json().catch(() => ({}));
    content = res.ok && body.success
      ? formatSitemapReport(body.data || {})
      : translate("messages.slash.sitemapNotRead", { reason: body.message || translate("messages.slash.backendAnswered", { status: res.status }) });
  } catch (err) {
    content = translate("messages.slash.sitemapNotRead", { reason: err.message });
  }
  addMessage({ role: "system", content, tempId: `sitemap-${stamp}`, type: "command" });
  return { handled: true };
}

/** The chat message for a /api/web-search/sitemap report. */
export function formatSitemapReport(report) {
  const entries = report.entries || [];
  const where = report.final_url && report.final_url !== report.url
    ? translate("messages.slash.sitemap.redirected", { url: report.url, finalUrl: report.final_url })
    : report.url;
  const lines = [translate("messages.slash.sitemap.title", { url: where })];
  if (report.type === "sitemapindex") {
    lines.push(translate("messages.slash.sitemap.index", { count: report.total }));
    const items = entries.map((e, i) => (e.lastmod
      ? translate("messages.slash.sitemap.numberedEntryModified", { number: i + 1, url: e.loc, lastmod: e.lastmod })
      : `${i + 1}. ${e.loc}`));
    // A heading and the list under it are one string, so it can be worded as a whole.
    if (entries.length < report.total) {
      const heading = translate("messages.slash.sitemap.firstOf", { shown: entries.length, items: items.join("\n") });
      // With nothing listed the heading stands alone, without the line break.
      lines.push(items.length ? heading : heading.trimEnd());
    } else {
      lines.push(...items);
    }
  } else {
    lines.push(translate("messages.slash.sitemap.pageUrls", { count: report.total }));
    const depths = Object.entries(report.by_depth || {})
      .map(([depth, count]) => translate("messages.slash.sitemap.depth", { depth, pages: count }));
    if (depths.length) lines.push(translate("messages.slash.sitemap.byDepth", { depths: depths.join(", ") }));
    const landing = report.landing_pages || [];
    if (landing.length) {
      lines.push(translate("messages.slash.sitemap.landingPages"));
      landing.forEach((e, i) => lines.push(translate("messages.slash.sitemap.landingEntry", { number: i + 1, url: e.loc, priority: e.priority })));
    }
    if (entries.length) {
      const items = entries
        .map((e) => (e.lastmod ? translate("messages.slash.sitemap.entryModified", { url: e.loc, lastmod: e.lastmod }) : `- ${e.loc}`))
        .join("\n");
      lines.push(entries.length < report.total
        ? translate("messages.slash.sitemap.firstOf", { shown: entries.length, items })
        : translate("messages.slash.sitemap.pagesList", { items }));
    }
  }
  if (report.page_cut) lines.push(`_${report.page_cut}_`);
  if (report.incomplete) lines.push(`_${report.incomplete}_`);
  return lines.join("\n");
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
      const enabled = data.enabled ? translate("messages.slash.outreach.enabled") : translate("messages.slash.outreach.disabled");
      const supervised = data.supervised ? translate("messages.slash.outreach.supervised") : translate("messages.slash.outreach.unsupervised");
      const cadence = data.cadence || {};
      const cadenceLines = Object.entries(cadence).map(([platform, value]) => {
        if (value?.redis === "unavailable") {
          return translate("messages.slash.outreach.redisOffline", { platform });
        }
        const posts = value.posts_in_24h ?? 0;
        const cap = value.daily_cap ?? 0;
        return value.last_post_seconds_ago != null
          ? translate("messages.slash.outreach.cadenceLast", { platform, posts, cap, minutes: Math.floor(value.last_post_seconds_ago / 60) })
          : translate("messages.slash.outreach.cadence", { platform, posts, cap });
      });
      addMessage({
        role: "system",
        content: translate("messages.slash.outreach.status", { state: enabled, mode: supervised, cadence: cadenceLines.join("\n") || translate("messages.slash.outreach.noCadence") }),
        tempId: `outreach-status-${Date.now()}`,
        type: "command",
      });
    } catch (err) {
      addMessage({
        role: "system",
        content: translate("messages.slash.outreach.statusFailed", { reason: err.message }),
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
        content = data.message || data.error || translate("messages.slash.outreach.refused");
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
        content: translate("messages.slash.outreach.intentFailed", { reason: err.message }),
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
      content: data.message || translate("messages.slash.outreach.queued", { taskId: data.task_id }),
      tempId: `outreach-ok-${Date.now()}`,
      type: "command",
    });
  } catch (err) {
    addMessage({
      role: "system",
      content: translate("messages.slash.outreach.commandFailed", { reason: err.message }),
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
    addMessage({ role: "system", content: translate("messages.slash.usage.plan"), tempId: `plan-${Date.now()}` });
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
      content: translate("messages.slash.usage.training"),
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
    // A refusal (409 "Agent already active") carries JSON the branch below
    // explains; a body that is not JSON (backend offline) still says why.
    const data = await res.json().catch(() => ({
      success: false,
      error: res.status === 502 || res.status === 504
        ? translate("messages.slash.backendUnreachable")
        : translate("messages.slash.backendAnswered", { status: res.status }),
    }));
    if (data.success) {
      addMessage({
        role: "system",
        content: translate("messages.slash.training.started", { task: args }),
        tempId: `train-ok-${Date.now()}`,
        type: "command",
      });
    } else {
      addMessage({
        role: "system",
        content: data.error === "Agent already active"
          ? translate("messages.slash.training.rejectedBusy", { reason: data.error })
          : translate("messages.slash.training.rejected", { reason: data.error || translate("messages.slash.training.unknownError") }),
        tempId: `train-fail-${Date.now()}`,
        type: "command",
      });
    }
  } catch (err) {
    addMessage({
      role: "system",
      content: translate("messages.slash.training.failed", { reason: err.message }),
      tempId: `train-err-${Date.now()}`,
      type: "command",
    });
  }
  return { handled: true };
}

// ============================================================
// DB rule commands
// ============================================================

/**
 * The file a Rules-page command writes. /api/generate/from_command requires
 * output_filename; take output_file="name.ext" from the arguments the way
 * /createfile does, else <command>_<timestamp>.csv|txt (csv when the command
 * is a CSV one, which is also how the backend picks its CSV prompt).
 */
export function commandOutputFilename(name, args, now = new Date()) {
  const given = /\boutput_?file\s*=\s*(?:"([^"]+)"|(\S+))/i.exec(args || "");
  const fromArgs = given && (given[1] || given[2]);
  if (fromArgs) return fromArgs.trim();
  const stem =
    String(name || "")
      .replace(/^\//, "")
      .replace(/[^a-z0-9_-]+/gi, "_")
      .replace(/^_+|_+$/g, "") || "command";
  const ext = /csv/i.test(name) ? "csv" : "txt";
  const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "");
  return `${stem}_${stamp}.${ext}`;
}

async function handleDbRule(name, args, { addMessage }) {
  addMessage({ role: "user", content: `${name} ${args}`.trim(), tempId: `rule-user-${Date.now()}` });
  const outputFilename = commandOutputFilename(name, args);
  try {
    const res = await fetch("/api/generate/from_command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command_label: name,
        output_filename: outputFilename,
        // The rule text's {{args}}, {{USER_INPUT}} and {{filename}} are filled.
        generation_parameters: { args, USER_INPUT: args, filename: outputFilename },
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.error) {
      const reason = data?.error || `HTTP ${res.status}`;
      addMessage({
        role: "system",
        content: data?.details
          ? translate("messages.slash.rule.failedDetails", { name, reason, details: data.details })
          : translate("messages.slash.rule.failed", { name, reason }),
        tempId: `rule-err-${Date.now()}`,
        type: "command",
      });
      return { handled: true };
    }
    const file = data.output_file || outputFilename;
    addMessage({
      role: "assistant",
      content: Number.isFinite(data.file_size)
        ? translate("messages.slash.rule.wroteSize", { name, file, bytes: data.file_size })
        : translate("messages.slash.rule.wrote", { name, file }),
      tempId: `rule-asst-${Date.now()}`,
    });
  } catch (err) {
    addMessage({
      role: "system",
      content: translate("messages.slash.rule.failed", { name, reason: err.message }),
      tempId: `rule-err-${Date.now()}`,
      type: "command",
    });
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
      content: translate("messages.slash.agent.needsSession"),
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
      content: translate("messages.slash.agent.switchFailed", { reason: err.message }),
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
        ? translate("messages.slash.agent.already")
        : translate("messages.slash.agent.switched"),
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
      content: translate("messages.slash.thinking.needsSession"),
      tempId: `thinking-no-session-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  const arg = (args || "").trim().toLowerCase();
  const store = useAppStore.getState();

  if (!arg) {
    const cur = store.getSessionThinking(sessionId);
    addMessage({
      role: "system",
      content:
        cur === undefined
          ? translate("messages.slash.thinking.statusDefault")
          : cur
          ? translate("messages.slash.thinking.statusOn")
          : translate("messages.slash.thinking.statusOff"),
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
      content: translate("messages.slash.usage.thinking"),
      tempId: `thinking-usage-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  store.setSessionThinking(sessionId, on);
  addMessage({
    role: "system",
    content: on
      ? translate("messages.slash.thinking.enabled")
      : translate("messages.slash.thinking.disabled"),
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
      content: translate("messages.slash.chatMode.needsSession"),
      tempId: `chat-no-session-${Date.now()}`,
      type: "command",
    });
    return { handled: true };
  }

  const previousMode = useAppStore.getState().getSessionMode(sessionId);

  try {
    const stopped = await stopChatTurn(sessionId);
    if (!stopped.ok) {
      addMessage({
        role: "system",
        content: stopped.error,
        tempId: `chat-stop-${Date.now()}`,
        type: "command",
      });
    }

    const data = await _patchSessionMode(sessionId, "chat");
    useAppStore.getState().setSessionMode(sessionId, data?.mode || "chat");
    addMessage({
      role: "system",
      content: previousMode === "chat"
        ? translate("messages.slash.chatMode.already")
        : translate("messages.slash.chatMode.switched"),
      tempId: `chat-ok-${Date.now()}`,
      type: "command",
    });
  } catch (err) {
    addMessage({
      role: "system",
      content: translate("messages.slash.chatMode.exitFailed", { reason: err.message }),
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
