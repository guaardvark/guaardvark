// frontend/src/pages/ToolsPage.jsx
// Tools Management Page - View, test, and manage agent tools
// Version 1.1 - Added list view with sortable table
/* eslint-env browser */

import React, { useEffect, useState } from "react";
import PageLayout from "../components/layout/PageLayout";
import {
  Box,
  Typography,
  Grid,
  Button,
  CircularProgress,
  Card,
  CardContent,
  CardActions,
  IconButton,
  Tooltip,
  Chip,
  Alert,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Paper,
  Tabs,
  Tab,
  Divider,
  InputAdornment,
  ToggleButtonGroup,
  ToggleButton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
} from "@mui/material";
import {
  Refresh,
  PlayArrow,
  Code,
  Search,
  ContentCopy,
  CheckCircle,
  Error as ErrorIcon,
  Build,
  Category,
  ViewList,
  ViewModule,
} from "@mui/icons-material";
import {
  getTools,
  executeTool,
  getToolSchemas,
  getToolCategories,
} from "../api/toolsService";
import AlertSnackbar from "../components/common/AlertSnackbar";
import { useStatus } from "../contexts/StatusContext";
import { ContextualLoader } from "../components/common/LoadingStates";

const ToolsPage = () => {
  const { activeModel, isLoadingModel, modelError } = useStatus();
  // State
  const [tools, setTools] = useState([]);
  const [categories, setCategories] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedTool, setSelectedTool] = useState(null);
  const [testDialogOpen, setTestDialogOpen] = useState(false);
  const [testParams, setTestParams] = useState({});
  const [testResult, setTestResult] = useState(null);
  const [testLoading, setTestLoading] = useState(false);
  const [schemaFormat, setSchemaFormat] = useState("xml");
  const [schemas, setSchemas] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [activeTab, setActiveTab] = useState(0);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: "",
    severity: "info",
  });
  const [viewMode, setViewMode] = useState(() => {
    return localStorage.getItem("toolsPageViewMode") || "card";
  });
  const [orderBy, setOrderBy] = useState("name");
  const [order, setOrder] = useState("asc");

  // Load tools on mount
  useEffect(() => {
    loadTools();
    loadCategories();
  }, []);

  const loadTools = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getTools();
      if (response.success) {
        setTools(response.tools || []);
      } else {
        setError(response.error || "Failed to load tools");
      }
    } catch (err) {
      setError(err.message || "Failed to load tools");
    } finally {
      setLoading(false);
    }
  };

  const loadCategories = async () => {
    try {
      const response = await getToolCategories();
      if (response.success) {
        setCategories(response.categories || {});
      }
    } catch (err) {
      console.error("Failed to load categories:", err);
    }
  };

  const loadSchemas = async (format) => {
    try {
      const response = await getToolSchemas(format);
      if (response.success) {
        setSchemas(response.schemas || "");
      }
    } catch (err) {
      console.error("Failed to load schemas:", err);
    }
  };

  useEffect(() => {
    if (activeTab === 1) {
      loadSchemas(schemaFormat);
    }
  }, [activeTab, schemaFormat]);

  const handleTestTool = (tool) => {
    setSelectedTool(tool);
    // Initialize params with defaults
    const initialParams = {};
    Object.entries(tool.parameters || {}).forEach(([name, param]) => {
      if (param.default !== null && param.default !== undefined) {
        initialParams[name] = param.default;
      } else if (param.type === "int") {
        initialParams[name] = param.required ? 1 : "";
      } else if (param.type === "bool") {
        initialParams[name] = false;
      } else {
        initialParams[name] = "";
      }
    });
    setTestParams(initialParams);
    setTestResult(null);
    setTestDialogOpen(true);
  };

  const handleExecuteTest = async () => {
    if (!selectedTool) return;

    setTestLoading(true);
    setTestResult(null);

    try {
      // Convert string numbers to actual numbers
      const processedParams = {};
      Object.entries(testParams).forEach(([key, value]) => {
        const paramDef = selectedTool.parameters[key];
        if (paramDef?.type === "int" && value !== "") {
          processedParams[key] = parseInt(value, 10);
        } else if (paramDef?.type === "bool") {
          processedParams[key] = value === true || value === "true";
        } else if (value !== "" && value !== null && value !== undefined) {
          processedParams[key] = value;
        }
      });

      const response = await executeTool(selectedTool.name, processedParams);
      setTestResult(response);

      if (response.success && response.result?.success) {
        setSnackbar({
          open: true,
          message: "Tool executed successfully",
          severity: "success",
        });
      } else {
        setSnackbar({
          open: true,
          message: response.result?.error || "Tool execution failed",
          severity: "error",
        });
      }
    } catch (err) {
      setTestResult({ success: false, error: err.message });
      setSnackbar({
        open: true,
        message: err.message || "Tool execution failed",
        severity: "error",
      });
    } finally {
      setTestLoading(false);
    }
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
    setSnackbar({
      open: true,
      message: "Copied to clipboard",
      severity: "success",
    });
  };

  const getCategoryColor = (category) => {
    const colors = {
      content: "primary",
      generation: "secondary",
      code: "warning",
      other: "default",
    };
    return colors[category] || "default";
  };

  const getToolCategory = (toolName) => {
    for (const [cat, toolList] of Object.entries(categories)) {
      if (toolList.includes(toolName)) return cat;
    }
    return "other";
  };

  const handleViewModeChange = (event, newView) => {
    if (newView) {
      setViewMode(newView);
      localStorage.setItem("toolsPageViewMode", newView);
    }
  };

  const handleSortRequest = (property) => {
    const isAsc = orderBy === property && order === "asc";
    setOrder(isAsc ? "desc" : "asc");
    setOrderBy(property);
  };

  const sortTools = (toolsArray) => {
    return [...toolsArray].sort((a, b) => {
      let aValue, bValue;
      if (orderBy === "name") {
        aValue = a.name.toLowerCase();
        bValue = b.name.toLowerCase();
      } else if (orderBy === "category") {
        aValue = getToolCategory(a.name);
        bValue = getToolCategory(b.name);
      } else if (orderBy === "params") {
        aValue = Object.keys(a.parameters || {}).length;
        bValue = Object.keys(b.parameters || {}).length;
      } else {
        aValue = a[orderBy] || "";
        bValue = b[orderBy] || "";
      }
      if (order === "asc") {
        return aValue < bValue ? -1 : aValue > bValue ? 1 : 0;
      }
      return aValue > bValue ? -1 : aValue < bValue ? 1 : 0;
    });
  };

  const filteredTools = tools.filter(
    (tool) =>
      tool.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      tool.description.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const sortedFilteredTools = sortTools(filteredTools);

  // Render tool card
  const renderToolCard = (tool) => {
    const category = getToolCategory(tool.name);
    const requiredParams = Object.entries(tool.parameters || {}).filter(
      ([, p]) => p.required
    );
    const optionalParams = Object.entries(tool.parameters || {}).filter(
      ([, p]) => !p.required
    );

    return (
      <Card
        key={tool.name}
        sx={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          "&:hover": { boxShadow: 4 },
        }}
      >
        <CardContent sx={{ flexGrow: 1 }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
            <Build fontSize="small" color="action" />
            <Typography variant="h6" component="div" sx={{ flexGrow: 1 }}>
              {tool.name}
            </Typography>
            <Chip
              label={category}
              size="small"
              color={getCategoryColor(category)}
            />
          </Box>

          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mb: 2, minHeight: 40 }}
          >
            {tool.description}
          </Typography>

          <Divider sx={{ my: 1 }} />

          <Typography variant="caption" color="text.secondary">
            Parameters:
          </Typography>

          <Box sx={{ mt: 1 }}>
            {requiredParams.length > 0 && (
              <Box sx={{ mb: 1 }}>
                {requiredParams.map(([name]) => (
                  <Chip
                    key={name}
                    label={`${name}*`}
                    size="small"
                    sx={{ mr: 0.5, mb: 0.5 }}
                    color="error"
                    variant="outlined"
                  />
                ))}
              </Box>
            )}
            {optionalParams.length > 0 && (
              <Box>
                {optionalParams.map(([name]) => (
                  <Chip
                    key={name}
                    label={name}
                    size="small"
                    sx={{ mr: 0.5, mb: 0.5 }}
                    variant="outlined"
                  />
                ))}
              </Box>
            )}
          </Box>
        </CardContent>

        <CardActions sx={{ justifyContent: "space-between", px: 2, pb: 2 }}>
          <Button
            size="small"
            startIcon={<PlayArrow />}
            onClick={() => handleTestTool(tool)}
            variant="contained"
          >
            Test
          </Button>
          <Tooltip title="Copy tool name">
            <IconButton size="small" onClick={() => copyToClipboard(tool.name)}>
              <ContentCopy fontSize="small" />
            </IconButton>
          </Tooltip>
        </CardActions>
      </Card>
    );
  };

  // Render tools table (list view)
  const renderToolTable = () => {
    return (
      <TableContainer component={Paper} sx={{ maxHeight: 600 }}>
        <Table stickyHeader size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={{ fontWeight: "bold", width: 200 }}>
                <TableSortLabel
                  active={orderBy === "name"}
                  direction={orderBy === "name" ? order : "asc"}
                  onClick={() => handleSortRequest("name")}
                >
                  Name
                </TableSortLabel>
              </TableCell>
              <TableCell sx={{ fontWeight: "bold", width: 120 }}>
                <TableSortLabel
                  active={orderBy === "category"}
                  direction={orderBy === "category" ? order : "asc"}
                  onClick={() => handleSortRequest("category")}
                >
                  Category
                </TableSortLabel>
              </TableCell>
              <TableCell sx={{ fontWeight: "bold" }}>Description</TableCell>
              <TableCell sx={{ fontWeight: "bold", width: 100 }}>
                <TableSortLabel
                  active={orderBy === "params"}
                  direction={orderBy === "params" ? order : "asc"}
                  onClick={() => handleSortRequest("params")}
                >
                  Params
                </TableSortLabel>
              </TableCell>
              <TableCell sx={{ fontWeight: "bold", width: 120 }} align="right">
                Actions
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {sortedFilteredTools.map((tool) => {
              const category = getToolCategory(tool.name);
              const requiredParams = Object.entries(tool.parameters || {}).filter(
                ([, p]) => p.required
              );
              const optionalParams = Object.entries(tool.parameters || {}).filter(
                ([, p]) => !p.required
              );
              const totalParams = Object.keys(tool.parameters || {}).length;

              return (
                <TableRow
                  key={tool.name}
                  hover
                  sx={{ "&:hover": { cursor: "pointer" } }}
                >
                  <TableCell>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Build fontSize="small" color="action" />
                      <Typography variant="body2" fontWeight="medium">
                        {tool.name}
                      </Typography>
                    </Box>
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={category}
                      size="small"
                      color={getCategoryColor(category)}
                    />
                  </TableCell>
                  <TableCell>
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        maxWidth: 400,
                      }}
                    >
                      {tool.description}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Tooltip
                      title={
                        <Box>
                          {requiredParams.length > 0 && (
                            <Box>
                              Required: {requiredParams.map(([n]) => n).join(", ")}
                            </Box>
                          )}
                          {optionalParams.length > 0 && (
                            <Box>
                              Optional: {optionalParams.map(([n]) => n).join(", ")}
                            </Box>
                          )}
                        </Box>
                      }
                    >
                      <Box sx={{ display: "flex", gap: 0.5 }}>
                        {requiredParams.length > 0 && (
                          <Chip
                            label={`${requiredParams.length} req`}
                            size="small"
                            color="error"
                            variant="outlined"
                          />
                        )}
                        {optionalParams.length > 0 && (
                          <Chip
                            label={`${optionalParams.length} opt`}
                            size="small"
                            variant="outlined"
                          />
                        )}
                        {totalParams === 0 && (
                          <Typography variant="body2" color="text.secondary">
                            None
                          </Typography>
                        )}
                      </Box>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right">
                    <Box sx={{ display: "flex", justifyContent: "flex-end", gap: 0.5 }}>
                      <Tooltip title="Test tool">
                        <IconButton
                          size="small"
                          color="primary"
                          onClick={() => handleTestTool(tool)}
                        >
                          <PlayArrow fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Copy tool name">
                        <IconButton
                          size="small"
                          onClick={() => copyToClipboard(tool.name)}
                        >
                          <ContentCopy fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Box>
                  </TableCell>
                </TableRow>
              );
            })}
            {sortedFilteredTools.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} align="center" sx={{ py: 4 }}>
                  <Typography color="text.secondary">
                    No tools found matching &quot;{searchQuery}&quot;
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>
    );
  };

  return (
    <PageLayout
      title="Agent Tools"
      variant="standard"
      actions={
        <Button
          size="small"
          startIcon={<Refresh />}
          onClick={loadTools}
          disabled={loading}
        >
          Refresh
        </Button>
      }
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel}
    >
      {/* Tabs */}
      <Tabs
        value={activeTab}
        onChange={(e, v) => setActiveTab(v)}
        sx={{ mb: 3 }}
      >
        <Tab label={`Tools (${tools.length})`} icon={<Build />} iconPosition="start" />
        <Tab label="Schemas" icon={<Code />} iconPosition="start" />
        <Tab label="Categories" icon={<Category />} iconPosition="start" />
      </Tabs>

      {/* Error Display */}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Tab Content */}
      {activeTab === 0 && (
        <>
          {/* Search and View Toggle */}
          <Box sx={{ display: "flex", gap: 2, mb: 3, alignItems: "center" }}>
            <TextField
              fullWidth
              placeholder="Search tools..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Search />
                  </InputAdornment>
                ),
              }}
            />
            <ToggleButtonGroup
              value={viewMode}
              exclusive
              onChange={handleViewModeChange}
              size="small"
              aria-label="view mode"
            >
              <ToggleButton value="card" aria-label="card view">
                <Tooltip title="Card View">
                  <ViewModule />
                </Tooltip>
              </ToggleButton>
              <ToggleButton value="list" aria-label="list view">
                <Tooltip title="List View">
                  <ViewList />
                </Tooltip>
              </ToggleButton>
            </ToggleButtonGroup>
          </Box>

          {/* Tools Display */}
          {loading ? (
            <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
              <ContextualLoader loading message="Loading tools..." showProgress={false} inline />
            </Box>
          ) : viewMode === "card" ? (
            <Grid container spacing={3}>
              {sortedFilteredTools.map((tool) => (
                <Grid item xs={12} sm={6} md={4} key={tool.name}>
                  {renderToolCard(tool)}
                </Grid>
              ))}
              {sortedFilteredTools.length === 0 && (
                <Grid item xs={12}>
                  <Typography
                    color="text.secondary"
                    textAlign="center"
                    sx={{ py: 4 }}
                  >
                    No tools found matching &quot;{searchQuery}&quot;
                  </Typography>
                </Grid>
              )}
            </Grid>
          ) : (
            renderToolTable()
          )}
        </>
      )}

      {activeTab === 1 && (
        <Box>
          <Box sx={{ display: "flex", gap: 2, mb: 2 }}>
            <Button
              variant={schemaFormat === "xml" ? "contained" : "outlined"}
              onClick={() => setSchemaFormat("xml")}
            >
              XML
            </Button>
            <Button
              variant={schemaFormat === "json" ? "contained" : "outlined"}
              onClick={() => setSchemaFormat("json")}
            >
              JSON
            </Button>
            <Box sx={{ flexGrow: 1 }} />
            <Button
              startIcon={<ContentCopy />}
              onClick={() => copyToClipboard(schemas)}
            >
              Copy All
            </Button>
          </Box>
          <Paper
            sx={{
              p: 2,
              bgcolor: "grey.900",
              color: "grey.100",
              fontFamily: "monospace",
              fontSize: 12,
              whiteSpace: "pre-wrap",
              maxHeight: 600,
              overflow: "auto",
            }}
          >
            {schemas || "Loading schemas..."}
          </Paper>
        </Box>
      )}

      {activeTab === 2 && (
        <Grid container spacing={3}>
          {Object.entries(categories).map(([category, toolList]) => (
            <Grid item xs={12} md={6} key={category}>
              <Card>
                <CardContent>
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 2 }}>
                    <Category />
                    <Typography variant="h6" sx={{ textTransform: "capitalize" }}>
                      {category}
                    </Typography>
                    <Chip label={toolList.length} size="small" />
                  </Box>
                  <Box>
                    {toolList.map((toolName) => (
                      <Chip
                        key={toolName}
                        label={toolName}
                        sx={{ mr: 1, mb: 1 }}
                        onClick={() => {
                          const tool = tools.find((t) => t.name === toolName);
                          if (tool) handleTestTool(tool);
                        }}
                        clickable
                      />
                    ))}
                  </Box>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      {/* Test Dialog */}
      <Dialog
        open={testDialogOpen}
        onClose={() => setTestDialogOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          Test Tool: {selectedTool?.name}
        </DialogTitle>
        <DialogContent>
          {selectedTool && (
            <>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                {selectedTool.description}
              </Typography>

              <Typography variant="subtitle2" sx={{ mb: 2 }}>
                Parameters
              </Typography>

              {Object.entries(selectedTool.parameters || {}).map(
                ([name, param]) => (
                  <TextField
                    key={name}
                    fullWidth
                    label={`${name}${param.required ? " *" : ""}`}
                    helperText={`${param.description} (${param.type})`}
                    value={testParams[name] || ""}
                    onChange={(e) =>
                      setTestParams((prev) => ({
                        ...prev,
                        [name]: e.target.value,
                      }))
                    }
                    sx={{ mb: 2 }}
                    required={param.required}
                    type={param.type === "int" ? "number" : "text"}
                    multiline={param.type === "string" && name.includes("content")}
                    rows={param.type === "string" && name.includes("content") ? 3 : 1}
                  />
                )
              )}

              {testResult && (
                <Box sx={{ mt: 3 }}>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    Result
                  </Typography>
                  <Alert
                    severity={testResult.result?.success ? "success" : "error"}
                    icon={
                      testResult.result?.success ? (
                        <CheckCircle />
                      ) : (
                        <ErrorIcon />
                      )
                    }
                    sx={{ mb: 2 }}
                  >
                    {testResult.result?.success
                      ? "Tool executed successfully"
                      : testResult.result?.error || "Execution failed"}
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
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setTestDialogOpen(false)}>Close</Button>
          <Button
            variant="contained"
            onClick={handleExecuteTest}
            disabled={testLoading}
            startIcon={testLoading ? <CircularProgress size={16} /> : <PlayArrow />}
          >
            Execute
          </Button>
        </DialogActions>
      </Dialog>

      {/* Snackbar */}
      <AlertSnackbar
        open={snackbar.open}
        message={snackbar.message}
        severity={snackbar.severity}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
      />
    </PageLayout>
  );
};

export default ToolsPage;
