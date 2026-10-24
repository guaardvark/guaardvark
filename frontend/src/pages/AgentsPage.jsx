// frontend/src/pages/AgentsPage.jsx
// The built-in agents as tiles in four groups, or as one table. A click opens
// the editor; a right-click offers Edit, Test, Enable/Disable, Copy id and Reset.
/* eslint-env browser */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Refresh from "@mui/icons-material/Refresh";
import PlayArrow from "@mui/icons-material/PlayArrow";
import SmartToyOutlined from "@mui/icons-material/SmartToyOutlined";
import ViewList from "@mui/icons-material/ViewList";
import ViewModule from "@mui/icons-material/ViewModule";
import PageLayout from "../components/layout/PageLayout";
import AlertSnackbar from "../components/common/AlertSnackbar";
import EmptyState from "../components/common/EmptyState";
import EntityContextMenu from "../components/common/EntityContextMenu";
import { ContextualLoader } from "../components/common/LoadingStates";
import { ActionButton, Cluster } from "../components/settings/ui";
import AgentTile, { agentEdits, agentStatus, agentTone } from "../components/agents/AgentTile";
import AgentEditDialog, { ResetAgentDialog } from "../components/agents/AgentEditDialog";
import useContextMenu from "../hooks/useContextMenu";
import copyText from "../utils/copyText";
import { executeAgent, getAgents, resetAgent, toggleAgent } from "../api/agentsService";
import { createPlan } from "../api/orchestratorService";
import { useStatus } from "../contexts/StatusContext";
import { translate, useUiTranslation } from "../i18n";

// A function, so the labels are worded in the interface language when the groups are built.
const agentGroups = () => [
  {
    key: "create",
    label: translate("agents.groups.create"),
    help: translate("agents.groups.createHelp"),
    order: ["content_creator", "code_assistant", "data_analyst"],
  },
  {
    key: "web",
    label: translate("agents.groups.web"),
    help: translate("agents.groups.webHelp"),
    order: ["research_agent", "browser_automation"],
  },
  {
    key: "computer",
    label: translate("agents.groups.computer"),
    help: translate("agents.groups.computerHelp"),
    order: ["agent_vision_control", "desktop_automation", "media_control"],
  },
  {
    key: "routing",
    label: translate("agents.groups.routing"),
    help: translate("agents.groups.routingHelp"),
    order: ["general_assistant", "orchestrator_agent"],
  },
];

const isOrchestrator = (agent) => agent?.agent_type === "orchestrator";

function groupAgents(agents) {
  const definitions = agentGroups();
  const known = new Set(definitions.map((g) => g.key));
  const groups = definitions.map((group) => ({
    ...group,
    agents: agents
      .filter((a) => a.group === group.key)
      .sort((a, b) => {
        const ia = group.order.indexOf(a.id);
        const ib = group.order.indexOf(b.id);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      }),
  }));
  const other = agents.filter((a) => !known.has(a.group));
  if (other.length) groups.push({ key: "other", label: translate("agents.groups.other"), help: "", agents: other });
  return groups.filter((g) => g.agents.length > 0);
}


// Fixed-width tiles: they wrap instead of stretching across the row when a
// group has only one or two agents.
const TILE_GRID = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(300px, 360px))",
  gap: 1.5,
};

// Tiles or table, remembered in this browser.
const VIEW_KEY = "agentsPageViewMode";
const readView = () => {
  try {
    return localStorage.getItem(VIEW_KEY) === "table" ? "table" : "tiles";
  } catch {
    return "tiles";
  }
};
const writeView = (view) => {
  try {
    localStorage.setItem(VIEW_KEY, view);
  } catch {
    /* private window or storage blocked: the choice lasts this visit */
  }
};

const TONE_COLOR = { ok: "success.main", warn: "warning.main", off: "text.disabled" };

const HEAD_CELL = { fontWeight: 700, fontSize: "0.72rem", whiteSpace: "nowrap" };

/** Every agent in one table, in the same group order as the tiles. */
const AgentsTable = ({ groups, onOpen, onContextMenu }) => {
  const { t } = useUiTranslation();
  return (
  <TableContainer component={Paper} variant="outlined">
    <Table size="small" aria-label={t("agents.table.label")}>
      <TableHead>
        <TableRow>
          <TableCell sx={HEAD_CELL}>{t("agents.table.agent")}</TableCell>
          <TableCell sx={HEAD_CELL}>{t("agents.table.group")}</TableCell>
          <TableCell sx={HEAD_CELL}>{t("agents.table.whatItDoes")}</TableCell>
          <TableCell sx={HEAD_CELL}>{t("agents.table.status")}</TableCell>
          <TableCell sx={HEAD_CELL} align="right">{t("agents.table.tools")}</TableCell>
          <TableCell sx={HEAD_CELL} align="right">{t("agents.table.maxSteps")}</TableCell>
          <TableCell sx={HEAD_CELL}>{t("agents.table.model")}</TableCell>
          <TableCell sx={HEAD_CELL}>{t("agents.table.edited")}</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {groups.flatMap((group) =>
          group.agents.map((agent) => {
            const tone = agentTone(agent);
            const tools = (agent.tools || []).length;
            const edits = agentEdits(agent);
            return (
              <TableRow
                key={agent.id}
                hover
                data-testid={`agent-row-${agent.id}`}
                tabIndex={0}
                onClick={() => onOpen(agent)}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onOpen(agent)}
                onContextMenu={(e) => onContextMenu(e, agent)}
                sx={{ cursor: "pointer", "& td": { py: 1 } }}
              >
                <TableCell sx={{ fontWeight: 600, whiteSpace: "nowrap" }}>
                  <Box
                    component="span"
                    sx={{
                      display: "inline-block",
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      bgcolor: TONE_COLOR[tone],
                      mr: 1,
                    }}
                  />
                  {agent.name}
                </TableCell>
                <TableCell sx={{ color: "text.secondary", whiteSpace: "nowrap" }}>{group.label}</TableCell>
                <TableCell sx={{ maxWidth: 360 }}>
                  <Tooltip title={agent.description || ""} placement="top-start">
                    <span>{agent.summary || agent.description}</span>
                  </Tooltip>
                </TableCell>
                <TableCell sx={{ color: tone === "ok" ? "text.primary" : TONE_COLOR[tone] }}>
                  {agent.unavailable_detail ? (
                    <Tooltip title={agent.unavailable_detail}>
                      <span>{agentStatus(agent)}</span>
                    </Tooltip>
                  ) : (
                    agentStatus(agent)
                  )}
                </TableCell>
                <TableCell align="right">{isOrchestrator(agent) ? "—" : tools}</TableCell>
                <TableCell align="right">{isOrchestrator(agent) ? "—" : agent.max_iterations}</TableCell>
                <TableCell sx={{ color: agent.model ? "text.primary" : "text.secondary", whiteSpace: "nowrap" }}>
                  {agent.model || t("agents.table.chatModel")}
                </TableCell>
                <TableCell sx={{ color: "text.secondary" }}>{edits.length ? edits.join(", ") : "—"}</TableCell>
              </TableRow>
            );
          }),
        )}
      </TableBody>
    </Table>
  </TableContainer>
  );
};

const AgentsPage = () => {
  const { t, language } = useUiTranslation();
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: "", severity: "info" });

  const [editingId, setEditingId] = useState(null);
  const [resetTarget, setResetTarget] = useState(null);
  const [resetting, setResetting] = useState(false);

  const [testAgent, setTestAgent] = useState(null);
  const [testMessage, setTestMessage] = useState("");
  const [testContextJson, setTestContextJson] = useState("{}");
  const [testRunning, setTestRunning] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const menu = useContextMenu();
  const [view, setView] = useState(readView);
  const changeView = useCallback((_event, next) => {
    if (!next) return;
    setView(next);
    writeView(next);
  }, []);

  const notify = useCallback((message, severity = "success") => {
    setSnackbar({ open: true, message, severity });
  }, []);
  const notifyError = useCallback((message) => notify(message, "error"), [notify]);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getAgents();
      if (response?.success) {
        setAgents(response.agents || []);
      } else {
        setError(response?.error || t("agents.page.loadFailed"));
      }
    } catch (err) {
      setError(err?.message || t("agents.page.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAgents();
  }, [loadAgents]);

  const groups = useMemo(() => groupAgents(agents), [agents, language]);
  const editing = agents.find((a) => a.id === editingId) || null;

  const replaceAgent = useCallback((updated) => {
    if (!updated?.id) return;
    // The list entry has no tools_detail; keep the shape GET /api/agents returns.
    const { tools_detail: _detail, ...entry } = updated;
    setAgents((prev) => prev.map((a) => (a.id === entry.id ? { ...a, ...entry } : a)));
  }, []);

  const handleToggle = useCallback(
    async (agent) => {
      try {
        const res = await toggleAgent(agent.id);
        if (res?.success) {
          if (res.agent) replaceAgent(res.agent);
          else setAgents((prev) => prev.map((a) => (a.id === agent.id ? { ...a, enabled: res.enabled } : a)));
          notify(
            res.enabled
              ? t("agents.page.enabled", { name: agent.name })
              : t("agents.page.disabled", { name: agent.name }),
          );
        } else {
          notifyError(res?.error || t("agents.page.toggleFailed"));
        }
      } catch (err) {
        notifyError(err?.message || t("agents.page.toggleFailed"));
      }
    },
    [notify, notifyError, replaceAgent],
  );

  const copyId = useCallback(
    async (agent) => {
      // copyText falls back to execCommand: navigator.clipboard is absent over plain http on the LAN.
      if (await copyText(agent.id)) notify(t("agents.page.copied", { id: agent.id }));
      else notifyError(t("agents.page.copyFailed"));
    },
    [notify, notifyError],
  );

  const confirmReset = async () => {
    if (!resetTarget) return;
    setResetting(true);
    try {
      const res = await resetAgent(resetTarget.id);
      if (res?.success) {
        replaceAgent(res.agent);
        notify(t("agents.reset.done", { name: resetTarget.name }));
        setResetTarget(null);
      } else {
        notifyError(res?.error || t("agents.reset.failed"));
      }
    } catch (err) {
      notifyError(err?.message || t("agents.reset.failed"));
    } finally {
      setResetting(false);
    }
  };

  const openTest = useCallback((agent) => {
    setTestAgent(agent);
    setTestMessage("");
    setTestContextJson("{}");
    setTestResult(null);
  }, []);

  const runTest = async () => {
    if (!testAgent) return;
    let context = {};
    try {
      context = testContextJson?.trim() ? JSON.parse(testContextJson) : {};
    } catch (_e) {
      setTestResult({ success: false, error: t("agents.test.invalidContext") });
      return;
    }
    setTestRunning(true);
    setTestResult(null);
    try {
      // The orchestrator does not run through /agents/execute; a test shows the plan it would make.
      const res = isOrchestrator(testAgent)
        ? await createPlan(testMessage, context)
        : await executeAgent({ agent_id: testAgent.id, message: testMessage, context });
      setTestResult(res);
    } catch (err) {
      setTestResult({ success: false, error: err?.message || t("agents.test.executionFailedError") });
    } finally {
      setTestRunning(false);
    }
  };

  const testOk = testResult?.success && (isOrchestrator(testAgent) || testResult?.result?.success);

  const menuActions = (agent) => {
    if (!agent) return [];
    const edits = agentEdits(agent);
    return [
      { label: t("agents.actions.edit"), onClick: () => setEditingId(agent.id) },
      { label: t("agents.actions.test"), onClick: () => openTest(agent) },
      { label: agent.enabled ? t("agents.actions.disable") : t("agents.actions.enable"), onClick: () => handleToggle(agent) },
      { label: t("agents.actions.copyId"), onClick: () => copyId(agent) },
      {
        label: t("agents.actions.resetToDefault"),
        dividerBefore: true,
        disabled: edits.length === 0,
        onClick: () => setResetTarget(agent),
      },
    ];
  };

  return (
    <PageLayout
      title={t("agents.page.title")}
      variant="standard"
      actions={
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <ToggleButtonGroup value={view} exclusive onChange={changeView} size="small" aria-label={t("agents.page.viewLabel")}>
            <ToggleButton value="tiles" aria-label={t("agents.page.tileView")}>
              <Tooltip title={t("agents.page.tileView")}>
                <ViewModule fontSize="small" />
              </Tooltip>
            </ToggleButton>
            <ToggleButton value="table" aria-label={t("agents.page.tableView")}>
              <Tooltip title={t("agents.page.tableView")}>
                <ViewList fontSize="small" />
              </Tooltip>
            </ToggleButton>
          </ToggleButtonGroup>
          <ActionButton startIcon={<Refresh />} onClick={loadAgents} disabled={loading}>
            {t("common.refresh")}
          </ActionButton>
        </Box>
      }
      modelStatus
      activeModel={isLoadingModel ? t("common.loading") : modelError ? t("common.error") : activeModel}
    >
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {loading && agents.length === 0 ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <ContextualLoader loading message={t("agents.page.loading")} showProgress={false} inline />
        </Box>
      ) : agents.length === 0 ? (
        <EmptyState
          icon={<SmartToyOutlined />}
          title={t("agents.page.emptyTitle")}
          description={t("agents.page.emptyDescription")}
        />
      ) : view === "table" ? (
        <AgentsTable groups={groups} onOpen={(a) => setEditingId(a.id)} onContextMenu={(event, a) => menu.open(event, a)} />
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5 }}>
          {groups.map((group) => (
            <Cluster key={group.key} label={group.label} help={group.help || undefined}>
              <Box sx={TILE_GRID}>
                {group.agents.map((agent) => (
                  <AgentTile
                    key={agent.id}
                    agent={agent}
                    onOpen={(a) => setEditingId(a.id)}
                    onContextMenu={(event, a) => menu.open(event, a)}
                  />
                ))}
              </Box>
            </Cluster>
          ))}
        </Box>
      )}

      <EntityContextMenu
        anchorPosition={menu.anchorPosition}
        onClose={menu.close}
        actions={menuActions(menu.payload)}
      />

      <AgentEditDialog
        agent={editing}
        onClose={() => setEditingId(null)}
        onSaved={(updated, { reset } = {}) => {
          replaceAgent(updated);
          notify(
            reset
              ? t("agents.reset.done", { name: updated?.name || t("agents.page.unnamedAgent") })
              : t("agents.page.updated"),
          );
        }}
        onToggle={handleToggle}
        onTest={openTest}
        onError={notifyError}
      />

      <ResetAgentDialog
        agent={resetTarget}
        open={Boolean(resetTarget)}
        busy={resetting}
        onConfirm={confirmReset}
        onClose={() => setResetTarget(null)}
      />

      <Dialog open={Boolean(testAgent)} onClose={() => setTestAgent(null)} maxWidth="md" fullWidth>
        <DialogTitle>{t("agents.test.title", { name: testAgent?.name })}</DialogTitle>
        <DialogContent>
          {isOrchestrator(testAgent) ? (
            <Alert severity="info" sx={{ mb: 2 }}>
              {t("agents.test.planOnly")}
            </Alert>
          ) : (
            <Alert severity="info" sx={{ mb: 2 }}>
              {t("agents.test.realRun")}
            </Alert>
          )}
          <TextField
            fullWidth
            label={t("agents.test.message")}
            value={testMessage}
            onChange={(e) => setTestMessage(e.target.value)}
            sx={{ mt: 1, mb: 2 }}
            multiline
            minRows={3}
          />
          <TextField
            fullWidth
            label={t("agents.test.context")}
            value={testContextJson}
            onChange={(e) => setTestContextJson(e.target.value)}
            sx={{ mb: 2 }}
            multiline
            minRows={3}
          />
          {testResult && (
            <Box sx={{ mt: 1 }}>
              <Alert severity={testOk ? "success" : "error"} sx={{ mb: 2 }}>
                {testOk
                  ? isOrchestrator(testAgent)
                    ? t("agents.test.planMade")
                    : t("agents.test.complete", { iterations: testResult?.result?.iterations || 0 })
                  : testResult?.error || testResult?.result?.error || t("agents.test.failed")}
              </Alert>
              <Box
                component="pre"
                sx={(theme) => ({
                  m: 0,
                  p: 2,
                  borderRadius: 1,
                  bgcolor: theme.palette.action.hover,
                  color: "text.primary",
                  border: `1px solid ${alpha(theme.palette.text.primary, 0.08)}`,
                  fontFamily: "monospace",
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                  maxHeight: 300,
                  overflow: "auto",
                })}
              >
                {JSON.stringify(testResult, null, 2)}
              </Box>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <ActionButton onClick={() => setTestAgent(null)}>{t("common.close")}</ActionButton>
          <ActionButton
            kind="primary"
            startIcon={testRunning ? <CircularProgress size={14} color="inherit" /> : <PlayArrow />}
            onClick={runTest}
            disabled={testRunning || !testMessage.trim()}
          >
            {t("agents.test.run")}
          </ActionButton>
        </DialogActions>
      </Dialog>

      <AlertSnackbar
        open={snackbar.open}
        message={snackbar.message}
        severity={snackbar.severity}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
      />
    </PageLayout>
  );
};

export default AgentsPage;
