// frontend/src/pages/RulesPage.jsx
// Version 2.1.0: Enhanced X column with intelligent system prompt detection
// - Added smart detection for qa_default and global_default_chat_system_prompt as SYSTEM type
// - Updated styling: smaller badges (30% reduction), solid colors like TasksPage status items
// - Three types: COMMAND (blue), SYSTEM (red), PROMPT (orange) with solid backgrounds
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from Dean (user/owner).

import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  Box,
  Typography,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  CircularProgress,
  Alert as MuiAlert,
  Chip,
  Tooltip,
  Snackbar,
  Button,
  TableSortLabel,
  IconButton,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import AddIcon from "@mui/icons-material/Add";
import CloseIcon from "@mui/icons-material/Close";
import FileCopyIcon from "@mui/icons-material/FileCopy";
import GavelOutlined from "@mui/icons-material/GavelOutlined";

import * as apiService from "../api";
import PageLayout from "../components/layout/PageLayout";
import EmptyState from "../components/common/EmptyState";
import RuleActionModal from "../components/modals/RuleActionModal";
import LinkingModal from "../components/modals/LinkingModal";
import { useStatus } from "../contexts/StatusContext";

const logger = console;

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

function descendingComparator(a, b, orderBy) {
  let valA = a[orderBy];
  let valB = b[orderBy];

  // Handle nested properties like 'target_models' which is an array
  if (orderBy === "target_models") {
    valA = (
      Array.isArray(valA) ? valA.join(", ") : String(valA ?? "")
    ).toLowerCase();
    valB = (
      Array.isArray(valB) ? valB.join(", ") : String(valB ?? "")
    ).toLowerCase();
  } else if (orderBy === "is_active") {
    // For Status column, sort active first, then inactive
    valA = a[orderBy] ? 1 : 0;
    valB = b[orderBy] ? 1 : 0;
  } else if (orderBy === "x_column") {
    // For X column, sort by type first, then command_label
    const getXValue = (item) => {
      const isSystemPrompt = item.name === "qa_default" || item.name === "global_default_chat_system_prompt";

      if (item.type === "COMMAND_RULE") {
        return `1_${item.command_label || ""}`;
      } else if (isSystemPrompt) {
        return `2_SYSTEM`;
      } else {
        return `3_PROMPT`;
      }
    };
    valA = getXValue(a).toLowerCase();
    valB = getXValue(b).toLowerCase();
  } else if (typeof valA === "string" && typeof valB === "string") {
    valA = valA.toLowerCase();
    valB = valB.toLowerCase();
  } else if (valA == null && valB != null)
    return 1; // nulls last for asc
  else if (valA != null && valB == null)
    return -1; // nulls last for asc
  else if (valA == null && valB == null) return 0;

  if (valB < valA) return -1;
  if (valB > valA) return 1;
  return 0;
}

function getComparator(order, orderBy) {
  return order === "desc"
    ? (a, b) => descendingComparator(a, b, orderBy)
    : (a, b) => -descendingComparator(a, b, orderBy);
}

import { stableSort } from "../utils/sortUtils";

const RulesPage = () => {
  const theme = useTheme();
  const { activeModel } = useStatus();

  const [rules, setRules] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState({
    open: false,
    message: "",
    severity: "success",
  });

  const [actionModalOpen, setActionModalOpen] = useState(false);
  const [selectedRuleForModal, setSelectedRuleForModal] = useState(null);
  const [isModalSaving, setIsModalSaving] = useState(false);

  const [isLinkingModalOpen, setIsLinkingModalOpen] = useState(false);
  const [linkingModalRule, setLinkingModalRule] = useState(null);

  // Load sorting state from localStorage or use defaults
  const loadSortingState = () => {
    try {
      const savedState = localStorage.getItem("rulesPage_sortingState");
      if (savedState) {
        const parsed = JSON.parse(savedState);
        return {
          order: parsed.order || "asc",
          orderBy: parsed.orderBy || "name"
        };
      }
    } catch (e) {
      logger.error("Failed to load sorting state:", e);
    }
    return { order: "asc", orderBy: "name" };
  };

  const initialSortingState = loadSortingState();
  const [order, setOrder] = useState(initialSortingState.order);
  const [orderBy, setOrderBy] = useState(initialSortingState.orderBy);

  // Save sorting state to localStorage whenever it changes
  const saveSortingState = useCallback((newOrder, newOrderBy) => {
    try {
      const state = {
        order: newOrder,
        orderBy: newOrderBy,
        savedAt: new Date().toISOString()
      };
      localStorage.setItem("rulesPage_sortingState", JSON.stringify(state));
    } catch (e) {
      logger.error("Failed to save sorting state:", e);
    }
  }, []);

  const loadRules = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const allItems = await apiService.getRules();
      if (allItems.error) throw new Error(allItems.error);

      const processedItems = allItems.map((item) => ({
        ...item,
        target_models:
          Array.isArray(item.target_models) && item.target_models.length > 0
            ? item.target_models
            : ["__ALL__"],
        description: item.description || "",
        rule_text: item.rule_text || "",
        command_label: item.command_label || "",
      }));
      setRules(processedItems);
    } catch (e) {
      logger.error("Failed to load rules:", e);
      setError(e.message || "Failed to load rules");
      setFeedback({
        open: true,
        message: e.message || "Failed to load rules",
        severity: "error",
      });
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  const handleOpenActionModal = (ruleItem = null) => {
    setSelectedRuleForModal(ruleItem);
    setActionModalOpen(true);
  };

  const handleCloseActionModal = () => {
    if (isModalSaving) return;
    setActionModalOpen(false);
    setTimeout(() => {
      if (!isLinkingModalOpen) setSelectedRuleForModal(null);
    }, 150);
  };

  const handleSaveRule = async (ruleDataToSave) => {
    setIsModalSaving(true);
    try {
      let result;
      if (ruleDataToSave.id) {
        result = await apiService.updateRule(ruleDataToSave.id, ruleDataToSave);
      } else {
        result = await apiService.createRule(ruleDataToSave);
      }
      if (result && (result.error || result.errors || result.detail)) {
        const errorMsg =
          result.error?.message ||
          result.error?.toString() ||
          result.errors?.toString() ||
          result.detail ||
          "Could not save rule";
        throw new Error(errorMsg);
      }
      setFeedback({
        open: true,
        message: `Rule ${ruleDataToSave.id ? "updated" : "created"} successfully!`,
        severity: "success",
      });
      handleCloseActionModal();
      loadRules();
      return result;
    } catch (err) {
      logger.error("Save rule failed:", err);
      setFeedback({
        open: true,
        message: err.message || "Failed to save rule",
        severity: "error",
      });
      return { error: err };
    } finally {
      setIsModalSaving(false);
    }
  };

  const handleDeleteRule = async (ruleId) => {
    if (
      !window.confirm(
        "Are you sure you want to delete this rule? This action cannot be undone.",
      )
    )
      return;
    try {
      await apiService.deleteRule(ruleId);
      setFeedback({
        open: true,
        message: "Rule deleted successfully.",
        severity: "success",
      });
      if (selectedRuleForModal?.id === ruleId) {
        handleCloseActionModal();
      }
      loadRules();
    } catch (err) {
      logger.error("Delete rule failed:", err);
      setFeedback({
        open: true,
        message: err.message || "Failed to delete rule",
        severity: "error",
      });
    }
  };

  const handleDuplicateRule = async (rule) => {
    const { id, created_at, updated_at, ...fields } = rule;
    let newCommandLabel = fields.command_label || "";
    if (newCommandLabel) {
      newCommandLabel = `${newCommandLabel}_copy_${Math.floor(Math.random() * 10000)}`;
    } else {
      newCommandLabel = "";
    }
    const newRule = {
      ...fields,
      is_active: false,
      name: `${rule.name} (Copy)`,
      command_label: newCommandLabel,
    };
    try {
      const result = await apiService.createRule(newRule);
      if (result && (result.error || result.errors || result.detail)) {
        throw new Error(
          result.error?.message ||
            result.errors?.toString() ||
            result.detail ||
            "Could not duplicate rule",
        );
      }
      setFeedback({
        open: true,
        message: "Rule duplicated (inactive).",
        severity: "success",
      });
      loadRules();
    } catch (err) {
      logger.error("Duplicate rule failed:", err);
      setFeedback({
        open: true,
        message: err.message || "Failed to duplicate rule",
        severity: "error",
      });
    }
  };

  const handleToggleActive = async (rule) => {
    // Optimistically update the UI immediately
    setRules(prevRules =>
      prevRules.map(r =>
        r.id === rule.id ? { ...r, is_active: !r.is_active } : r
      )
    );

    try {
      const updatedRule = { ...rule, is_active: !rule.is_active };
      const result = await apiService.updateRule(rule.id, updatedRule);
      if (result && (result.error || result.errors || result.detail)) {
        throw new Error(
          result.error?.message ||
            result.errors?.toString() ||
            result.detail ||
            "Could not update rule status",
        );
      }
      setFeedback({
        open: true,
        message: `Rule ${updatedRule.is_active ? "activated" : "deactivated"}.`,
        severity: "success",
      });
    } catch (err) {
      logger.error("Update rule status failed:", err);
      // Revert the optimistic update on error
      setRules(prevRules =>
        prevRules.map(r =>
          r.id === rule.id ? { ...r, is_active: rule.is_active } : r
        )
      );
      setFeedback({
        open: true,
        message: err.message || "Failed to update rule status",
        severity: "error",
      });
    }
  };

  const handleOpenLinkingModal = (rule) => {
    setLinkingModalRule(rule);
    setIsLinkingModalOpen(true);
    if (actionModalOpen) setActionModalOpen(false);
  };
  const handleCloseLinkingModal = () => {
    setIsLinkingModalOpen(false);
    setLinkingModalRule(null);
  };
  const handleLinksUpdated = () => {
    loadRules();
    setFeedback({
      open: true,
      message: "Links updated successfully!",
      severity: "success",
    });
  };

  const handleSortRequest = (property) => {
    const isAsc = orderBy === property && order === "asc";
    const newOrder = isAsc ? "desc" : "asc";
    setOrder(newOrder);
    setOrderBy(property);
    saveSortingState(newOrder, property);
  };

  const sortedRules = useMemo(() => {
    return stableSort(rules, getComparator(order, orderBy));
  }, [rules, order, orderBy]);

  const headCells = [
    { id: "id", label: "ID", sortable: true, minWidth: 10 }, // Added ID column
    { id: "name", label: "Name / Description", sortable: true, minWidth: 300 }, // Made wider
    { id: "x_column", label: "X", sortable: true }, // Merged column
    { id: "target_models", label: "Target Model(s)", sortable: true }, // Now sortable
    { id: "is_active", label: "Status", sortable: true, minWidth: 80, align: "center" }, // Status Column (clickable chip)
    { id: "actions", label: "", sortable: false, align: "right" }, // Removed Column Title "Actions"
  ];

  const handleCloseFeedback = (event, reason) => {
    if (reason === "clickaway") return;
    setFeedback((prev) => ({ ...prev, open: false }));
  };

  return (
    <PageLayout
      title="Rules Management"
      variant="standard"
      actions={
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => handleOpenActionModal(null)}
          disabled={isLoading || isModalSaving}
          size="small"
        >
          New
        </Button>
      }
      modelStatus
      activeModel={activeModel}
    >
        <Snackbar
          open={feedback.open}
          autoHideDuration={6000}
          onClose={handleCloseFeedback}
          anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        >
          <AlertSnackbar
            onClose={handleCloseFeedback}
            severity={feedback.severity || "info"}
            sx={{ width: "100%" }}
          >
            {feedback.message}
          </AlertSnackbar>
        </Snackbar>
        {error && !isLoading && (
          <MuiAlert severity="error" sx={{ mb: 2 }}>
            {error}
          </MuiAlert>
        )}
        <Paper elevation={2} sx={{ mb: 1, overflow: "hidden" }}>
          <TableContainer
            sx={{ maxHeight: "calc(100vh - 64px - 48px - 70px - 48px)" }}
          >
            <Table stickyHeader size="small">
              <TableHead>
                <TableRow>
                  {headCells.map((headCell) => (
                    <TableCell
                      key={headCell.id}
                      align={headCell.align || "left"}
                      sortDirection={orderBy === headCell.id ? order : false}
                      sx={{
                        fontWeight: "bold",
                        minWidth: headCell.minWidth
                          ? headCell.minWidth
                          : "auto",
                      }}
                    >
                      {headCell.sortable ? (
                        <TableSortLabel
                          active={orderBy === headCell.id}
                          direction={orderBy === headCell.id ? order : "asc"}
                          onClick={() => handleSortRequest(headCell.id)}
                        >
                          {headCell.label}
                        </TableSortLabel>
                      ) : (
                        headCell.label
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {isLoading && sortedRules.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={headCells.length}
                      align="center"
                      sx={{ py: 3 }}
                    >
                      <CircularProgress size={24} sx={{ mr: 1 }} /> Loading
                      rules...
                    </TableCell>
                  </TableRow>
                ) : !isLoading && sortedRules.length === 0 && !error ? (
                  <TableRow>
                    <TableCell
                      colSpan={headCells.length}
                      align="center"
                      sx={{ py: 0 }}
                    >
                      <EmptyState
                        icon={<GavelOutlined />}
                        title="No rules found"
                        description="Create a rule to define system prompts and commands"
                      />
                    </TableCell>
                  </TableRow>
                ) : (
                  sortedRules.map((rule) => (
                    <TableRow
                      key={rule.id}
                      hover
                      sx={{
                        "&:last-child td, &:last-child th": { border: 0 },
                      }}
                      onClick={() => handleOpenActionModal(rule)}
                    >
                      <TableCell sx={{ minWidth: 10, maxWidth: 50 }}>
                        <Tooltip title={`Rule ID: ${rule.id}`}>
                          <Typography 
                            variant="body2" 
                            sx={{ 
                              fontFamily: 'monospace',
                              fontSize: '0.875rem',
                              color: 'text.secondary'
                            }}
                          >
                            {rule.id}
                          </Typography>
                        </Tooltip>
                      </TableCell>
                      <TableCell sx={{ minWidth: 300, maxWidth: 400 }}>
                        {" "}
                        {/* Wider Name/Description Column */}
                        <Tooltip title={rule.name || "N/A"}>
                          <Typography variant="body2" noWrap>
                            {rule.name || "Unnamed Rule"}
                          </Typography>
                        </Tooltip>
                        <Typography
                          variant="caption"
                          display="block"
                          color="text.secondary"
                          noWrap
                        >
                          {rule.description || "No description"}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        {(() => {
                          const isSystemPrompt = rule.name === "qa_default" || rule.name === "global_default_chat_system_prompt";
                          
                          if (rule.type === "COMMAND_RULE") {
                            return (
                              <Typography
                                variant="body2"
                                sx={{
                                  backgroundColor: 'primary.main',
                                  color: 'common.white',
                                  px: 0.75,
                                  py: 0.25,
                                  borderRadius: '3px',
                                  fontWeight: 'medium',
                                  fontSize: '11px',
                                  display: 'inline-block',
                                  maxWidth: 80
                                }}
                                noWrap
                              >
                                {rule.command_label || "/cmd"}
                              </Typography>
                            );
                          } else if (isSystemPrompt) {
                            return (
                              <Typography
                                variant="body2"
                                sx={{
                                  backgroundColor: 'error.main',
                                  color: 'common.white',
                                  px: 0.75,
                                  py: 0.25,
                                  borderRadius: '3px',
                                  fontWeight: 'medium',
                                  fontSize: '11px',
                                  display: 'inline-block'
                                }}
                              >
                                SYSTEM
                              </Typography>
                            );
                          } else {
                            return (
                              <Typography
                                variant="body2"
                                sx={{
                                  backgroundColor: 'warning.main',
                                  color: 'common.white',
                                  px: 0.75,
                                  py: 0.25,
                                  borderRadius: '3px',
                                  fontWeight: 'medium',
                                  fontSize: '11px',
                                  display: 'inline-block'
                                }}
                              >
                                PROMPT
                              </Typography>
                            );
                          }
                        })()}
                      </TableCell>
                      <TableCell sx={{ maxWidth: 150 }}>
                        <Tooltip
                          title={
                            rule.target_models.includes("__ALL__")
                              ? "All Models"
                              : rule.target_models.join(", ")
                          }
                        >
                          <Box
                            sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}
                          >
                            {rule.target_models.includes("__ALL__") ||
                            rule.target_models.length === 0 ? (
                              <Chip
                                label="All Models"
                                size="small"
                                variant="outlined"
                                color="primary"
                              />
                            ) : (
                              rule.target_models
                                .slice(0, 2)
                                .map((model) => (
                                  <Chip
                                    key={model}
                                    label={model}
                                    size="small"
                                    variant="outlined"
                                  />
                                ))
                            )}
                            {rule.target_models.length > 2 &&
                              !rule.target_models.includes("__ALL__") && (
                                <Chip
                                  label={`+${rule.target_models.length - 2}`}
                                  size="small"
                                />
                              )}
                          </Box>
                        </Tooltip>
                      </TableCell>
                      <TableCell
                        align="center"
                        sx={{ minWidth: 80 }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Tooltip
                          title={`Click to ${rule.is_active ? "deactivate" : "activate"}`}
                        >
                          <Chip
                            label={rule.is_active ? "Active" : "Inactive"}
                            size="small"
                            color={rule.is_active ? "success" : "default"}
                            variant={rule.is_active ? "filled" : "outlined"}
                            onClick={() => handleToggleActive(rule)}
                            sx={{
                              fontWeight: 'medium',
                              fontSize: '11px',
                              height: '20px',
                              backgroundColor: rule.is_active
                                ? theme.palette.success.main
                                : theme.palette.mode === "dark"
                                  ? theme.palette.grey[700]
                                  : theme.palette.grey[200],
                              color: rule.is_active
                                ? 'white'
                                : theme.palette.text.secondary,
                              cursor: 'pointer',
                              '&:hover': {
                                opacity: 0.8,
                                transform: 'scale(1.05)',
                              },
                              transition: 'all 0.2s ease-in-out',
                            }}
                          />
                        </Tooltip>
                      </TableCell>
                      <TableCell
                        align="right"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Tooltip title="Duplicate Rule">
                          <IconButton
                            onClick={() => handleDuplicateRule(rule)}
                            size="small"
                            color="primary"
                          >
                            <FileCopyIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Delete Rule">
                          <IconButton
                            onClick={() => handleDeleteRule(rule.id)}
                            size="small"
                            color="primary"
                            sx={{
                              "&:hover": { color: theme.palette.error.light },
                            }}
                          >
                            <CloseIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </TableContainer>
          {rules.length > 0 && !isLoading && (
            <Typography
              variant="caption"
              display="block"
              sx={{
                textAlign: "right",
                p: 1,
                color: "text.secondary",
                borderTop: 1,
                borderColor: "divider",
              }}
            >
              Total Rules: {rules.length}
            </Typography>
          )}
        </Paper>
        {actionModalOpen && (
          <RuleActionModal
            open={actionModalOpen}
            onClose={handleCloseActionModal}
            ruleData={selectedRuleForModal}
            onSave={handleSaveRule}
            onDelete={handleDeleteRule}
            onOpenLinker={handleOpenLinkingModal}
            isSaving={isModalSaving}
          />
        )}
        {isLinkingModalOpen && linkingModalRule && (
          <LinkingModal
            open={isLinkingModalOpen}
            onClose={handleCloseLinkingModal}
            primaryEntityType="rule"
            primaryEntityId={linkingModalRule.id}
            primaryEntityName={linkingModalRule.name}
            linkableTypesConfig={[
              {
                entityType: "project",
                singularLabel: "Project",
                pluralLabel: "Projects",
                apiServiceFunction: apiService.getProjects,
              },
            ]}
            apiGetLinkedItems={apiService.getCurrentlyLinkedItems} // Ensure this is adapted or correctly implemented
            apiUpdateLinks={apiService.updateEntityLinks} // Ensure this is adapted or correctly implemented
            onLinksUpdated={handleLinksUpdated}
          />
        )}
    </PageLayout>
  );
};

export default RulesPage;
