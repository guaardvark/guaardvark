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
    description: "Capture and describe the agent's virtual screen",
    usage: "/vision [prompt]",
    category: "agent",
    args: "optional",
    handler: "builtin",
    ruleId: null,
  },
  {
    name: "/agent",
    description: "Execute a task on the agent's virtual screen (browse, click, type, etc.)",
    usage: "/agent <task description>",
    category: "agent",
    args: "required",
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
 */
export function filterCommands(commands, input) {
  if (!input || !input.startsWith("/")) return [];
  const query = input.toLowerCase();
  return commands.filter(
    (cmd) =>
      cmd.name.toLowerCase().startsWith(query) ||
      cmd.description.toLowerCase().includes(query.slice(1))
  );
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
