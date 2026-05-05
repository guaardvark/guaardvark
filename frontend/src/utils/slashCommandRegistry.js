/**
 * Slash command registry — built-in commands + DB command fetching.
 *
 * Built-in commands are always available. DB commands (COMMAND_RULE type)
 * are fetched from the backend and cached with a 60-second TTL.
 */

const BUILT_IN_COMMANDS = [
  {
    name: "/imagine",
    description: "Generate an image from a text prompt",
    usage: "/imagine <prompt>",
    category: "generation",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/imagemodel",
    description: "Switch Stable Diffusion model or show current",
    usage: "/imagemodel [model-name]",
    category: "model",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/model",
    description: "Switch LLM chat model or show current",
    usage: "/model [model-name]",
    category: "model",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/websearch",
    description: "Search the web via DuckDuckGo",
    usage: "/websearch <query>",
    category: "utility",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/plan",
    description: "Create an orchestrator plan",
    usage: "/plan <request>",
    category: "utility",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/training",
    description: "Run the agent in training mode — full 1000-iteration loop, bypasses chat LLM decomposition",
    usage: "/training <task>",
    category: "agent",
    args: "required",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/agent",
    description: "Switch this chat into agent mode — every message becomes a screen-control task. Optional <task> fires immediately.",
    usage: "/agent [task]",
    category: "agent",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/chat",
    description: "Switch this chat back to chat mode (exits agent mode)",
    usage: "/chat",
    category: "agent",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/exit",
    description: "Exit agent mode (alias for /chat)",
    usage: "/exit",
    category: "agent",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/voice",
    description: "Toggle voice chat on/off",
    usage: "/voice",
    category: "utility",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/vision",
    description: "Show Vision Pipeline plugin status (start/stop from the Plugins page)",
    usage: "/vision",
    category: "utility",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/clear",
    description: "Clear current chat history",
    usage: "/clear",
    category: "utility",
    args: "none",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/help",
    description: "Show available commands",
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
    const rules = data.data?.rules || data.rules || [];
    _dbCommandsCache = rules
      .filter((r) => r.command_label)
      .map((r) => ({
        name: r.command_label.startsWith("/") ? r.command_label : `/${r.command_label}`,
        description: r.description || r.name || "Custom command",
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

export default { getAllCommands, getBuiltInCommands, filterCommands, parseCommand };
