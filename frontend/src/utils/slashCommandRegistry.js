/**
 * Slash command registry — built-in commands + DB command fetching.
 *
 * Built-in commands are always available. DB commands (COMMAND_RULE type)
 * are fetched from the backend and cached with a 60-second TTL.
 */
import { translate } from "../i18n";

const BUILT_IN_COMMANDS = [
    {
    name: "/imagine",
    get description() {
      return translate("options.slashCommands.imagine");
    },
    usage: "/imagine <prompt>",
    category: "generation",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/removebg",
    get description() {
      return translate("options.slashCommands.removebg");
    },
    usage: "/removebg",
    category: "generation",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/inpaint",
    get description() {
      return translate("options.slashCommands.inpaint");
    },
    usage: "/inpaint <instruction>",
    category: "generation",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/outpaint",
    get description() {
      return translate("options.slashCommands.outpaint");
    },
    usage: "/outpaint [left|right|top|bottom|all] [instruction]",
    category: "generation",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/video",
    get description() {
      return translate("options.slashCommands.video");
    },
    usage: "/video <prompt>",
    category: "generation",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/music-video",
    get description() {
      return translate("options.slashCommands.musicVideo");
    },
    usage: "/music-video <song-path-or-id> <style>",
    category: "generation",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/film-crew",
    get description() {
      return translate("options.slashCommands.filmCrew");
    },
    usage: "/film-crew <screenplay>",
    category: "generation",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/imagemodel",
    get description() {
      return translate("options.slashCommands.imagemodel");
    },
    usage: "/imagemodel [model-name]",
    category: "model",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/model",
    get description() {
      return translate("options.slashCommands.model");
    },
    usage: "/model [model-name]",
    category: "model",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/websearch",
    get description() {
      return translate("options.slashCommands.websearch");
    },
    usage: "/websearch <query> | site:<address> | sitemap:<url>",
    category: "utility",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/gpu",
    get description() {
      return translate("options.slashCommands.gpu");
    },
    usage: "/gpu",
    category: "utility",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/logs",
    get description() {
      return translate("options.slashCommands.logs");
    },
    usage: "/logs [backend.log] [query]",
    category: "utility",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/sysmap",
    get description() {
      return translate("options.slashCommands.sysmap");
    },
    usage: "/sysmap [refresh]",
    category: "utility",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/swarm",
    get description() {
      return translate("options.slashCommands.swarm");
    },
    usage: "/swarm [swarm-id]",
    category: "utility",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/outreach",
    get description() {
      return translate("options.slashCommands.outreach");
    },
    usage: "/outreach [status|reddit|self_share|recon|draft|youtube| <natural language>]",
    category: "outreach",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/plan",
    get description() {
      return translate("options.slashCommands.plan");
    },
    usage: "/plan <request>",
    category: "utility",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/training",
    get description() {
      return translate("options.slashCommands.training");
    },
    usage: "/training <task>",
    category: "agent",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/agent",
    get description() {
      return translate("options.slashCommands.agent");
    },
    usage: "/agent [task]",
    category: "agent",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/chat",
    get description() {
      return translate("options.slashCommands.chat");
    },
    usage: "/chat",
    category: "agent",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/thinking",
    get description() {
      return translate("options.slashCommands.thinking");
    },
    usage: "/thinking [on|off]",
    category: "model",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/exit",
    get description() {
      return translate("options.slashCommands.exit");
    },
    usage: "/exit",
    category: "agent",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/voice",
    get description() {
      return translate("options.slashCommands.voice");
    },
    usage: "/voice",
    category: "utility",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/vision",
    get description() {
      return translate("options.slashCommands.vision");
    },
    usage: "/vision",
    category: "utility",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/clear",
    get description() {
      return translate("options.slashCommands.clear");
    },
    usage: "/clear",
    category: "utility",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/help",
    get description() {
      return translate("options.slashCommands.help");
    },
    usage: "/help",
    category: "utility",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
];

let _dbCommandsCache = null;
let _dbCommandsCacheTime = 0;
const DB_COMMANDS_TTL = 60000; // 60 seconds

/**
 * COMMAND_RULE list from GET /api/rules. The live handler returns a bare array
 * (`jsonify(items)`). Older envelopes used `{rules}` or `{data: {rules}}`.
 */
export function extractCommandRules(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.data?.rules)) return payload.data.rules;
  if (Array.isArray(payload?.rules)) return payload.rules;
  return [];
}

/** Drop the 60s COMMAND_RULE cache (tests). */
export function resetDbCommandsCache() {
  _dbCommandsCache = null;
  _dbCommandsCacheTime = 0;
}

/**
 * Fetch COMMAND_RULE entries from the backend.
 * Cached for 60 seconds to avoid redundant fetches on re-mount.
 */
async function fetchDbCommands() {
  const now = Date.now();
  if (_dbCommandsCache && now - _dbCommandsCacheTime < DB_COMMANDS_TTL) {
    return _dbCommandsCache;
  }

  try {
    const res = await fetch("/api/rules?type=COMMAND_RULE&is_active=true");
    if (!res.ok) return _dbCommandsCache || [];
    const data = await res.json();
    const rules = extractCommandRules(data);
    _dbCommandsCache = rules
      .filter((r) => r.command_label)
      .map((r) => ({
        name: r.command_label.startsWith("/") ? r.command_label : `/${r.command_label}`,
        get description() {
          return r.description || r.name || translate("options.slashCommands.custom");
        },
        usage: r.command_label,
        category: "custom",
        args: "optional",
        handler: "rule",
        ruleId: r.id,
      }));
    _dbCommandsCacheTime = now;
    return _dbCommandsCache;
  } catch (err) {
    console.warn("Failed to fetch DB commands:", err);
    return _dbCommandsCache || [];
  }
}

/**
 * Get all commands — built-in + DB.
 */
export async function getAllCommands() {
  const dbCommands = await fetchDbCommands();
  return [...BUILT_IN_COMMANDS, ...dbCommands];
}

/**
 * Get built-in commands only (synchronous, no fetch).
 */
export function getBuiltInCommands() {
  return BUILT_IN_COMMANDS;
}

/**
 * Filter commands by partial input (e.g., "/im" matches "/imagine").
 * Matches against name and description.
 *
 * Sort order: name-prefix matches first (most relevant), then
 * description-only matches. Within each tier, alphabetical by name.
 * This way typing "/agent" + Enter selects /agent itself, not /training
 * (whose description happens to contain the word "agent").
 */
export function filterCommands(commands, input) {
  if (!input || !input.startsWith("/")) return [];
  const query = input.toLowerCase();
  const matched = commands.filter(
    (cmd) =>
      cmd.name.toLowerCase().startsWith(query) ||
      cmd.description.toLowerCase().includes(query.slice(1))
  );
  return matched.sort((a, b) => {
    const aPrefix = a.name.toLowerCase().startsWith(query);
    const bPrefix = b.name.toLowerCase().startsWith(query);
    if (aPrefix && !bPrefix) return -1;
    if (!aPrefix && bPrefix) return 1;
    return a.name.localeCompare(b.name);
  });
}

/**
 * Parse a command string into { name, args }.
 * e.g., "/imagine a sunset" → { name: "/imagine", args: "a sunset" }
 */
export function parseCommand(input) {
  const trimmed = input.trim();
  const spaceIdx = trimmed.indexOf(" ");
  if (spaceIdx === -1) return { name: trimmed.toLowerCase(), args: "" };
  return {
    name: trimmed.slice(0, spaceIdx).toLowerCase(),
    args: trimmed.slice(spaceIdx + 1).trim(),
  };
}

/**
 * The request options a slash command's send carries to the chat engine.
 * Commands such as /imagine call the chat's send with
 * `{direct_tool, direct_tool_params, slash_command, slash_args, image_model}`;
 * the engine runs `direct_tool` without asking the model to choose a tool.
 * Returns {} for an ordinary message.
 */
export function directToolOptions(sendOptions) {
  if (!sendOptions?.direct_tool) return {};
  const options = {
    direct_tool: sendOptions.direct_tool,
    direct_tool_params: sendOptions.direct_tool_params || {},
  };
  if (sendOptions.slash_command) options.slash_command = sendOptions.slash_command;
  if (sendOptions.slash_args) options.slash_args = sendOptions.slash_args;
  if (sendOptions.image_model) options.image_model = sendOptions.image_model;
  return options;
}

export default {
  getAllCommands,
  getBuiltInCommands,
  filterCommands,
  parseCommand,
  extractCommandRules,
  resetDbCommandsCache,
  directToolOptions,
};
