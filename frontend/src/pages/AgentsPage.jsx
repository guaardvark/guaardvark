// frontend/src/pages/AgentsPage.jsx
// Agents Management Page - View and configure specialized agents
// Version 1.0
/* eslint-env browser */

import React, { useEffect, useMemo, useState } from "react";
import PageLayout from "../components/layout/PageLayout";
import {
  Box,
  Typography,
  Grid,
  Card,
  CardContent,
  CardActions,
  Button,
  Chip,
  Alert,
  CircularProgress,
  Divider,
  IconButton,
  Tooltip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Switch,
  FormControlLabel,
  Paper,
} from "@mui/material";
import {
  Refresh,
  Edit,
  SmartToy,
  PlayArrow,
  ContentCopy,
  CheckCircle,
  Error as ErrorIcon,
} from "@mui/icons-material";
import AlertSnackbar from "../components/common/AlertSnackbar";
import EmptyState from "../components/common/EmptyState";
import SmartToyOutlined from "@mui/icons-material/SmartToyOutlined";
import { getAgents, toggleAgent, updateAgent, executeAgent } from "../api/agentsService";
import { useStatus } from "../contexts/StatusContext";
import { ContextualLoader } from "../components/common/LoadingStates";

const AgentsPage = () => {
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [snackbar, setSnackbar] = useState({
    open: false,
    message: "",
    severity: "info",
  });

  const [editOpen, setEditOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState(null);
  const [editValues, setEditValues] = useState({ max_iterations: 10, system_prompt: "" });
  const [editSaving, setEditSaving] = useState(false);

  const [testOpen, setTestOpen] = useState(false);
  const [testAgent, setTestAgent] = useState(null);
  const [testMessage, setTestMessage] = useState("");
  const [testContextJson, setTestContextJson] = useState("{}");
  const [testRunning, setTestRunning] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const loadAgents = async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await getAgents();
      if (response?.success) {
        setAgents(response.agents || []);
      } else {
        setError(response?.error || "Failed to load agents");
      }
    } catch (err) {
      setError(err?.message || "Failed to load agents");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAgents();
  }, []);

  const groupedAgents = useMemo(() => {
    const groups = {};
    for (const a of agents) {
      const type = a.agent_type || "unknown";
      if (!groups[type]) groups[type] = [];
      groups[type].push(a);
    }
    return groups;
  }, [agents]);

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
    setSnackbar({ open: true, message: "Copied to clipboard", severity: "success" });
  };

  const handleToggle = async (agent) => {
    try {
      const res = await toggleAgent(agent.id);
      if (res?.success) {
        setAgents((prev) =>
          prev.map((a) => (a.id === agent.id ? { ...a, enabled: res.enabled } : a)),
        );
        setSnackbar({
          open: true,
          message: `Agent ${agent.name} ${res.enabled ? "enabled" : "disabled"}`,
          severity: "success",
        });
      } else {
        setSnackbar({ open: true, message: res?.error || "Toggle failed", severity: "error" });
      }
    } catch (err) {
      setSnackbar({ open: true, message: err?.message || "Toggle failed", severity: "error" });
    }
  };

  const openEdit = (agent) => {
    setEditingAgent(agent);
    setEditValues({
      max_iterations: agent.max_iterations ?? 10,
      system_prompt: agent.system_prompt ?? "",
    });
    setEditOpen(true);
  };

  const saveEdit = async () => {
    if (!editingAgent) return;
    setEditSaving(true);

    try {
      const updates = {
        max_iterations: Number(editValues.max_iterations) || 1,
        system_prompt: editValues.system_prompt || "",
      };

      const res = await updateAgent(editingAgent.id, updates);
      if (res?.success) {
        setAgents((prev) => prev.map((a) => (a.id === editingAgent.id ? res.agent : a)));
        setSnackbar({ open: true, message: "Agent updated", severity: "success" });
        setEditOpen(false);
      } else {
        setSnackbar({ open: true, message: res?.error || "Update failed", severity: "error" });
      }
    } catch (err) {
      setSnackbar({ open: true, message: err?.message || "Update failed", severity: "error" });
    } finally {
      setEditSaving(false);
    }
  };

  const openTest = (agent) => {
    setTestAgent(agent);
    setTestMessage("");
    setTestContextJson("{}");
    setTestResult(null);
    setTestOpen(true);
  };

  const runTest = async () => {
    if (!testAgent) return;

    setTestRunning(true);
    setTestResult(null);

    let context = {};
    try {
      context = testContextJson?.trim() ? JSON.parse(testContextJson) : {};
    } catch (_e) {
      setTestRunning(false);
      setTestResult({ success: false, error: "Context must be valid JSON" });
      return;
    }

    try {
      const res = await executeAgent({
        agent_id: testAgent.id,
        message: testMessage,
        context,
      });
      setTestResult(res);

      if (res?.success) {
        setSnackbar({ open: true, message: "Agent executed", severity: "success" });
      } else {
        setSnackbar({ open: true, message: res?.error || "Agent execution failed", severity: "error" });
      }
    } catch (err) {
      setTestResult({ success: false, error: err?.message || "Agent execution failed" });
      setSnackbar({ open: true, message: err?.message || "Agent execution failed", severity: "error" });
    } finally {
      setTestRunning(false);
    }
  };

  const renderAgentCard = (agent) => (
    <Card
      key={agent.id}
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        opacity: agent.enabled ? 1 : 0.65,
        "&:hover": { boxShadow: 4 },
      }}
    >
      <CardContent sx={{ flexGrow: 1 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
          <SmartToy fontSize="small" color="action" />
          <Typography variant="h6" sx={{ flexGrow: 1 }}>
            {agent.name}
          </Typography>
          <Tooltip title="Copy agent id">
            <IconButton size="small" onClick={() => copyToClipboard(agent.id)}>
              <ContentCopy fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>

        <Typography variant="body2" color="text.secondary" sx={{ mb: 2, minHeight: 40 }}>
          {agent.description}
        </Typography>

        <Divider sx={{ my: 1 }} />

        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", alignItems: "center", mt: 1 }}>
          <Chip label={agent.agent_type || "unknown"} size="small" variant="outlined" />
          <Chip label={`max: ${agent.max_iterations ?? 10}`} size="small" variant="outlined" />
          {Array.isArray(agent.tools) && agent.tools.slice(0, 6).map((t) => (
            <Chip key={t} label={t} size="small" />
          ))}
          {Array.isArray(agent.tools) && agent.tools.length > 6 && (
            <Chip label={`+${agent.tools.length - 6} more`} size="small" variant="outlined" />
          )}
        </Box>

        <Box sx={{ mt: 2 }}>
          <FormControlLabel
            control={<Switch checked={!!agent.enabled} onChange={() => handleToggle(agent)} />}
            label={agent.enabled ? "Enabled" : "Disabled"}
          />
        </Box>
      </CardContent>

      <CardActions sx={{ justifyContent: "space-between", px: 2, pb: 2 }}>
        <Button size="small" startIcon={<Edit />} onClick={() => openEdit(agent)} variant="outlined">
          Edit
        </Button>
        <Button size="small" startIcon={<PlayArrow />} onClick={() => openTest(agent)} variant="contained">
          Test
        </Button>
      </CardActions>
    </Card>
  );

  return (
    <PageLayout
      title="Agents"
      variant="standard"
      actions={
        <Button size="small" startIcon={<Refresh />} onClick={loadAgents} disabled={loading}>
          Refresh
        </Button>
      }
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel}
    >

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <ContextualLoader loading message="Loading agents..." showProgress={false} inline />
        </Box>
      ) : agents.length === 0 ? (
        <EmptyState
          icon={<SmartToyOutlined />}
          title="No agents found"
          description="Agents will appear here once configured"
        />
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {Object.entries(groupedAgents).map(([agentType, list]) => (
            <Paper key={agentType} elevation={0} sx={{ p: 2, border: 1, borderColor: "divider" }}>
              <Typography variant="h6" sx={{ textTransform: "capitalize", mb: 2 }}>
                {agentType.replaceAll("_", " ")} ({list.length})
              </Typography>
              <Grid container spacing={3}>
                {list.map((agent) => (
                  <Grid item xs={12} sm={6} md={4} key={agent.id}>
                    {renderAgentCard(agent)}
                  </Grid>
                ))}
              </Grid>
            </Paper>
          ))}
        </Box>
      )}

      {/* Edit Dialog */}
      <Dialog open={editOpen} onClose={() => setEditOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Edit Agent: {editingAgent?.name}</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Max iterations"
            type="number"
            value={editValues.max_iterations}
            onChange={(e) => setEditValues((p) => ({ ...p, max_iterations: e.target.value }))}
            sx={{ mt: 1, mb: 2 }}
            inputProps={{ min: 1, max: 50 }}
          />
          <TextField
            fullWidth
            label="System prompt"
            value={editValues.system_prompt}
            onChange={(e) => setEditValues((p) => ({ ...p, system_prompt: e.target.value }))}
            multiline
            minRows={8}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={saveEdit} disabled={editSaving}>
            {editSaving ? "Saving..." : "Save"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Test Dialog */}
      <Dialog open={testOpen} onClose={() => setTestOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Test Agent: {testAgent?.name}</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Message"
            value={testMessage}
            onChange={(e) => setTestMessage(e.target.value)}
            sx={{ mt: 1, mb: 2 }}
            multiline
            minRows={3}
          />
          <TextField
            fullWidth
            label="Context (JSON)"
            value={testContextJson}
            onChange={(e) => setTestContextJson(e.target.value)}
            sx={{ mb: 2 }}
            multiline
            minRows={4}
          />

          {testResult && (
            <Box sx={{ mt: 2 }}>
              <Alert
                severity={testResult?.success && testResult?.result?.success ? "success" : "error"}
                icon={testResult?.success && testResult?.result?.success ? <CheckCircle /> : <ErrorIcon />}
                sx={{ mb: 2 }}
              >
                {testResult?.success && testResult?.result?.success
                  ? `Execution complete (${testResult?.result?.iterations || 0} iterations)`
                  : testResult?.error || testResult?.result?.error || "Execution failed"}
              </Alert>
              <Paper
                sx={{
                  p: 2,
                  bgcolor: "grey.100",
                  fontFamily: "monospace",
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                  maxHeight: 300,
                  overflow: "auto",
                }}
              >
                {JSON.stringify(testResult, null, 2)}
              </Paper>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setTestOpen(false)}>Close</Button>
          <Button
            variant="contained"
            startIcon={testRunning ? <CircularProgress size={16} /> : <PlayArrow />}
            onClick={runTest}
            disabled={testRunning || !testMessage.trim()}
          >
            Run
          </Button>
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
