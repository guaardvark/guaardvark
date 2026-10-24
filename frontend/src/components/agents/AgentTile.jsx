// frontend/src/components/agents/AgentTile.jsx
// One agent on the Agents page: a dashboard tile that opens the editor on click
// and hands right-clicks to the page's menu.

import React from "react";
import { Box } from "@mui/material";
import { DashboardTile } from "../settings/ui";
import { translate, useUiTranslation } from "../../i18n";

/** Fields a person changed from the built-in default, not counting the on/off switch. */
export const agentEdits = (agent) => (agent?.overridden || []).filter((field) => field !== "enabled");

const isOrchestrator = (agent) => agent?.agent_type === "orchestrator";

/** ok when it can run, warn when something it needs is missing, off when switched off. */
export function agentTone(agent) {
  if (!agent.enabled) return "off";
  if (agent.unavailable_reason || (agent.tools_missing || []).length) return "warn";
  return "ok";
}

/**
 * The hover text on the tile's title: the description, plus the exact setting to
 * change when the agent cannot run. The tile's one line holds only the plain reason.
 */
export function agentHelp(agent) {
  if (!agent.unavailable_detail) return agent.description;
  return (
    <>
      {agent.description}
      <br />
      {agent.unavailable_detail}
    </>
  );
}

/** One word or phrase for the table's Status column: why it cannot run, or Ready. */
export function agentStatus(agent) {
  if (!agent.enabled) return translate("agents.tile.off");
  if (agent.unavailable_reason) return agent.unavailable_reason;
  const missing = (agent.tools_missing || []).length;
  if (missing) return translate("agents.tile.toolsMissing", { count: missing });
  return translate("agents.tile.ready");
}

/** The tile's second line: why it cannot run, or its tool count, limit, model and edits. */
export function agentSub(agent) {
  if (!agent.enabled) return translate("agents.tile.off");
  if (agent.unavailable_reason) return agent.unavailable_reason;
  if (isOrchestrator(agent)) return translate("agents.tile.handsSteps");
  const tools = (agent.tools || []).length;
  const missing = (agent.tools_missing || []).length;
  const parts = [translate("agents.tile.toolCount", { count: tools })];
  if (missing) parts.push(translate("agents.tile.missingCount", { count: missing }));
  parts.push(translate("agents.tile.maxSteps", { max: agent.max_iterations }));
  if (agent.model) parts.push(agent.model);
  if (agentEdits(agent).length) parts.push(translate("agents.tile.edited"));
  return parts.join(" · ");
}

/**
 * @param {object}   agent          an entry from GET /api/agents
 * @param {function} onOpen         called with the agent on click or Enter
 * @param {function} onContextMenu  called with (event, agent) on right-click
 */
const AgentTile = ({ agent, onOpen, onContextMenu }) => {
  // agentSub() words the tile's second line in the interface language.
  useUiTranslation();
  return (
    <Box
      data-testid={`agent-tile-${agent.id}`}
      onContextMenu={(event) => onContextMenu?.(event, agent)}
      sx={{ display: "grid", minWidth: 0 }}
    >
      <DashboardTile
        size="large"
        label={agent.name}
        help={agentHelp(agent)}
        tone={agentTone(agent)}
        value={agent.summary || agent.description}
        sub={agentSub(agent)}
        onClick={() => onOpen?.(agent)}
      />
    </Box>
  );
};

export default AgentTile;
