import React, { useState, useEffect, useCallback } from "react";
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  Stack,
  Button,
  LinearProgress,
  Divider,
  CircularProgress,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
} from "@mui/material";
import {
  Psychology as PsychologyIcon,
  Shield as ShieldIcon,
  Lock as LockIcon,
  CheckCircle as CheckIcon,
  Error as ErrorIcon,
  PlayArrow as PlayIcon,
  AutoFixHigh as FixIcon,
  Schedule as ScheduleIcon,
} from "@mui/icons-material";
import { claudeAdvisorService } from "../../api/claudeAdvisorService";
import { selfImprovementService } from "../../api/selfImprovementService";

export default function FamilySelfImprovementCard() {
  const [claudeStatus, setClaudeStatus] = useState(null);
  const [siStatus, setSiStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [cRes, sRes, rRes] = await Promise.all([
        claudeAdvisorService.getStatus(),
        selfImprovementService.getStatus(),
        selfImprovementService.getRuns(5, 0),
      ]);
      setClaudeStatus(cRes?.data);
      setSiStatus(sRes?.data);
      setRuns(rRes?.data?.runs || []);
    } catch (err) {
      console.error("Failed to fetch family status:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleTrigger = async () => {
    try {
      await selfImprovementService.triggerRun();
      setTimeout(fetchData, 3000);
    } catch (err) {
      console.error("Trigger failed:", err);
    }
  };

  if (loading) {
    return (
      <Card sx={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <CircularProgress size={24} />
      </Card>
    );
  }

  const usage = claudeStatus?.usage || {};

  return (
    <Card sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <CardContent sx={{ flex: 1, overflow: "auto", p: 1.5 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
          <Typography variant="subtitle2" sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <PsychologyIcon fontSize="small" /> Family & Self-Improvement
          </Typography>
          <Stack direction="row" spacing={0.5}>
            <Chip
              label={claudeStatus?.available ? "Uncle" : "Offline"}
              color={claudeStatus?.available ? "success" : "default"}
              size="small"
              variant="outlined"
            />
            {siStatus?.codebase_locked && (
              <Chip label="Locked" color="error" size="small" icon={<LockIcon />} />
            )}
          </Stack>
        </Stack>

        {/* Stats Row */}
        <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
          <Chip
            label={`SI: ${siStatus?.enabled ? "On" : "Off"}`}
            color={siStatus?.enabled ? "primary" : "default"}
            size="small"
            variant="outlined"
          />
          <Chip
            label={`Fixes: ${siStatus?.total_fixes || 0}`}
            size="small"
            variant="outlined"
            icon={<FixIcon />}
          />
        </Stack>

        {/* Token Budget Mini Bar */}
        {claudeStatus?.available && (
          <Box sx={{ mb: 1 }}>
            <Typography variant="caption" color="text.secondary">
              Tokens: {(usage.budget_used_percent || 0)}%
            </Typography>
            <LinearProgress
              variant="determinate"
              value={Math.min(usage.budget_used_percent || 0, 100)}
              sx={{ height: 4, borderRadius: 2 }}
            />
          </Box>
        )}

        <Divider sx={{ my: 1 }} />

        {/* Recent Runs */}
        <Typography variant="caption" fontWeight="bold">
          Recent Activity
        </Typography>
        {runs.length === 0 ? (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            No self-improvement runs yet
          </Typography>
        ) : (
          <List dense disablePadding sx={{ mt: 0.5 }}>
            {runs.slice(0, 3).map((run) => (
              <ListItem key={run.id} disablePadding sx={{ py: 0.25 }}>
                <ListItemIcon sx={{ minWidth: 24 }}>
                  {run.status === "success" ? (
                    <CheckIcon fontSize="small" color="success" />
                  ) : run.status === "failed" ? (
                    <ErrorIcon fontSize="small" color="error" />
                  ) : (
                    <ScheduleIcon fontSize="small" color="warning" />
                  )}
                </ListItemIcon>
                <ListItemText
                  primary={
                    <Typography variant="caption">
                      {run.trigger} - {run.status}
                    </Typography>
                  }
                  secondary={
                    <Typography variant="caption" color="text.secondary">
                      {run.timestamp ? new Date(run.timestamp).toLocaleString() : ""}
                    </Typography>
                  }
                />
              </ListItem>
            ))}
          </List>
        )}

        {/* Quick Actions */}
        <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
          {siStatus?.enabled && !siStatus?.codebase_locked && (
            <Button size="small" variant="outlined" onClick={handleTrigger} startIcon={<PlayIcon />}>
              Run Check
            </Button>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}
