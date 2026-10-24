// frontend/src/pages/FileGenerationPage.jsx
// Version 2.0.1: Corrected import from filegenService to use generateFileFromChat as generateSingleFile.
// - Resolves "does not provide an export named 'generateSingleFile'" error.
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from the project maintainer.

import React, { useState, useEffect, useCallback } from "react";
import { formatUiError } from "../utils/uiError";
import {
  Box,
  Typography,
  TextField,
  Button,
  CircularProgress,
  Alert,
  Paper,
  Grid,
  TextareaAutosize,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Autocomplete,
  Chip,
  Divider,
  Snackbar,
  Tooltip,
  Radio,
  RadioGroup,
  FormControlLabel,
  FormLabel,
  Switch,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";

import * as apiService from "../api"; // For fetching Rules & Projects
// Corrected import: Import generateFileFromChat and alias it as generateSingleFile
import {
  generateFileFromChat as generateSingleFile,
} from "../api/filegenService";
import { getWebsites } from "../api/websiteService"; // For website dropdown
import { generateBulkXML, generateStructuredCSV } from "../api/bulkGenerationService"; // For XML generation
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import { useUiTranslation } from "../i18n";

const FileGenerationPage = () => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const { activeModel, isLoadingModel, modelError } = useStatus();

  // State for single file generation (existing functionality)
  const [userInstructions, setUserInstructions] = useState("");
  const [outputFilenameSingle, setOutputFilenameSingle] = useState("");
  // const [inputCsvFileSingle, setInputCsvFileSingle] = useState(null); // Not fully used in v1.0.0
  // const [inputXmlFileSingle, setInputXmlFileSingle] = useState(null); // Not fully used in v1.0.0
  const [isGeneratingSingle, setIsGeneratingSingle] = useState(false);
  const [singleFileFeedback, setSingleFileFeedback] = useState({
    message: "",
    severity: "info",
    open: false,
  });

  // State for NEW Batch File Generation (CSV/XML)
  const [fileFormat, setFileFormat] = useState("csv"); // 'csv' or 'xml'
  const [batchOutputFilename, setBatchOutputFilename] = useState("");
  const [batchItems, setBatchItems] = useState(""); // Multiline string for items
  const [batchPromptRule, setBatchPromptRule] = useState(null); // Will be Rule object { id, name }
  const [codeGenRule, setCodeGenRule] = useState(null); // Rule for code generation section
  const [batchProject, setBatchProject] = useState(null); // Project object or string
  const [selectedWebsite, setSelectedWebsite] = useState(null); // Selected website object  
  const [batchClientWebsite, setBatchClientWebsite] = useState(""); // Client's website (will be auto-filled)
  const [batchTargetWebsite, setBatchTargetWebsite] = useState(""); // Competitor website for reference
  const [useClientCompetitors, setUseClientCompetitors] = useState(true); // Toggle to use client's competitor URLs
  const [batchPageCount, setBatchPageCount] = useState(10); // Number of CSV rows/pages
  const [batchClient, setBatchClient] = useState(null); // Client object
  const [batchModel, setBatchModel] = useState(""); // Model name
  const [insertContent, setInsertContent] = useState(""); // Content to insert into each page
  const [insertPosition, setInsertPosition] = useState("none"); // Position: 'top', 'bottom', 'none'
  const [isGeneratingBatch, setIsGeneratingBatch] = useState(false);
  const [batchFeedback, setBatchFeedback] = useState({
    message: "",
    severity: "info",
    open: false,
  });

  const [availableRules, setAvailableRules] = useState([]);
  const [isLoadingRules, setIsLoadingRules] = useState(false);
  const [availableProjects, setAvailableProjects] = useState([]);
  const [isLoadingProjects, setIsLoadingProjects] = useState(false);
  const [availableClients, setAvailableClients] = useState([]);
  const [isLoadingClients, setIsLoadingClients] = useState(false);
  const [availableModels, setAvailableModels] = useState([]);
  const [isLoadingModels, setIsLoadingModels] = useState(false);
  const [availableWebsites, setAvailableWebsites] = useState([]);
  const [isLoadingWebsites, setIsLoadingWebsites] = useState(false);

  // New Enhanced Features State
  const [enhancedContext, setEnhancedContext] = useState(false);

  // Fetch all required data for dropdowns/autocomplete
  const fetchRequiredData = useCallback(async () => {
    setIsLoadingRules(true);
    setIsLoadingProjects(true);
    setIsLoadingClients(true);
    setIsLoadingModels(true);
    setIsLoadingWebsites(true);
    try {
      // Fetch Rules
      const rulesResponse = await apiService.getRules({ is_active: true }); // Fetch only active rules
      if (rulesResponse?.error)
        throw new Error(
          t("fileGeneration.page.loadErrors.rules", {
            error: rulesResponse.error.message || rulesResponse.error,
          }),
        );
      // Filter for suitable rule types for batch generation
      const suitableRuleTypes = [
        "COMMAND_RULE",
        "PROMPT_TEMPLATE",
      ]; // Actual rule types from database
      setAvailableRules(
        Array.isArray(rulesResponse)
          ? rulesResponse.filter(
              (r) => r.is_active && suitableRuleTypes.includes(r.type),
            )
          : [],
      );

      // Fetch Projects
      const projectsData = await apiService.getProjects();
      if (projectsData?.error)
        throw new Error(
          t("fileGeneration.page.loadErrors.projects", {
            error: projectsData.error.message || projectsData.error,
          }),
        );
      setAvailableProjects(Array.isArray(projectsData) ? projectsData : []);

      // Fetch Clients
      const clientsData = await apiService.getClients();
      if (clientsData?.error)
        throw new Error(
          t("fileGeneration.page.loadErrors.clients", {
            error: clientsData.error.message || clientsData.error,
          }),
        );
      setAvailableClients(Array.isArray(clientsData) ? clientsData : []);

      // Fetch Models
      const modelsData = await apiService.getAvailableModels();
      if (modelsData?.error)
        throw new Error(
          t("fileGeneration.page.loadErrors.models", {
            error: modelsData.error.message || modelsData.error,
          }),
        );
      setAvailableModels(Array.isArray(modelsData) ? modelsData : []);

      // Fetch Websites
      const websitesData = await getWebsites();
      if (websitesData?.error) {
        const errorMessage =
          websitesData.error?.message || websitesData.error || t("fileGeneration.page.unknownError");
        throw new Error(t("fileGeneration.page.loadErrors.websites", { error: errorMessage }));
      }
      setAvailableWebsites(Array.isArray(websitesData) ? websitesData : []);
    } catch (err) {
      console.error(
        "FileGenerationPage: Error fetching required data:",
        err,
      );
      setBatchFeedback({
        message: t("fileGeneration.page.prerequisitesFailed", {
          error: err?.message || err || t("fileGeneration.page.unknownError"),
        }),
        severity: "error",
        open: true,
      });
    } finally {
      setIsLoadingRules(false);
      setIsLoadingProjects(false);
      setIsLoadingClients(false);
      setIsLoadingModels(false);
      setIsLoadingWebsites(false);
    }
  }, []);

  useEffect(() => {
    fetchRequiredData();
  }, [fetchRequiredData]);

  const handleGenerateSingleFile = async () => {
    if (!outputFilenameSingle.trim() || !userInstructions.trim()) {
      setSingleFileFeedback({
        message: t("fileGeneration.page.singleRequired"),
        severity: "warning",
        open: true,
      });
      return;
    }
    setIsGeneratingSingle(true);
    setSingleFileFeedback({
      message: t("fileGeneration.page.singleGenerating"),
      severity: "info",
      open: true,
    });

    try {
      const payload = {
        filename: outputFilenameSingle,
        user_instructions: userInstructions,
        project_id: null, // Single file generation doesn't need project association
        tags: null,
        rule_id: codeGenRule?.id || null, // Use selected rule if available
      };
      // generateSingleFile is now an alias for generateFileFromChat
      const response = await generateSingleFile(payload);
      if (response?.error)
        throw new Error(
          response.error.details || response.error.message || response.error,
        );
      setSingleFileFeedback({
        message:
          response?.message || t("fileGeneration.page.singleDone"),
        severity: "success",
        open: true,
      });

      setOutputFilenameSingle("");
      setUserInstructions("");
      setCodeGenRule(null);
    } catch (error) {
      console.error("Single file generation error:", error);
      setSingleFileFeedback({
        message: t("fileGeneration.page.singleError", {
          error: error.message || t("fileGeneration.page.unknownError"),
        }),
        severity: "error",
        open: true,
      });
    } finally {
      setIsGeneratingSingle(false);
    }
  };

  const handleGenerateBatchCsv = async () => {
    if (!batchOutputFilename.trim() || !batchPromptRule) {
      setBatchFeedback({
        message: t("fileGeneration.page.batchRequired"),
        severity: "error",
        open: true,
      });
      return;
    }

    // Validate that either items are provided OR page count is > 0
    const hasItems = batchItems.trim();
    const hasPageCount = batchPageCount && batchPageCount > 0;

    if (!hasItems && !hasPageCount) {
      setBatchFeedback({
        message: t("fileGeneration.page.batchItemsOrCount"),
        severity: "error",
        open: true,
      });
      return;
    }

    // Route to appropriate handler based on file format
    if (fileFormat === "xml") {
      await handleBatchXMLGeneration();
    } else {
      const payload = {
        output_filename: batchOutputFilename,
        items: batchItems.split('\n').filter(item => item.trim()),
        prompt_rule_id: batchPromptRule.id,
        project_id: batchProject?.id || null,
        project_name: batchProject?.name || "Content Generation",
        project_notes: batchProject?.notes || null, // FIX: Include project notes
        client_website: batchClientWebsite || null,
        target_website: batchTargetWebsite || null, // Competitor website (optional)
        page_count: batchPageCount,
        client_id: batchClient?.id || null,
        client_name: batchClient?.name || "Professional Services",
        client_notes: batchClient?.notes || null, // Client business description
        model_name: batchModel || activeModel?.name || null,
        csv_template: "enfold", // Force Enfold WordPress template for all CSV generation
        insert_content: insertPosition !== "none" ? insertContent : null,
        insert_position: insertPosition !== "none" ? insertPosition : null,
        context_mode: enhancedContext ? "enhanced" : "basic",
        use_entity_context: enhancedContext,
        use_competitor_analysis: enhancedContext && !!(batchTargetWebsite || useClientCompetitors),
        use_document_intelligence: enhancedContext,
      };

      await handleBatchGeneration(payload);
    }
  };

  const handleBatchXMLGeneration = async () => {
    setIsGeneratingBatch(true);
    setBatchFeedback({ open: false, message: "" });

    try {
      const params = {
        output_filename: batchOutputFilename,
        client: batchClient?.name || "Professional Services",
        project: batchProject?.name || "Content Generation",
        website: batchClientWebsite || "website.com",
        client_notes: batchClient?.notes || null,
        competitor_url: batchTargetWebsite || null, // FIX: Include competitor URL
        project_notes: batchProject?.notes || null, // FIX: Include project notes
        topics: batchItems.split('\n').filter(item => item.trim()),
        num_items: batchPageCount,
        target_word_count: 500,
        concurrent_workers: 5,
        batch_size: 25,
        model_name: batchModel || activeModel?.name || null,
        prompt_rule_id: batchPromptRule?.id || null,
        insert_content: insertPosition !== "none" ? insertContent : null,
        insert_position: insertPosition !== "none" ? insertPosition : null
      };

      const response = await generateBulkXML(params);

      if (response.error) {
        throw new Error(response.details || response.error);
      }

      setBatchFeedback({
        message: response.message || t("fileGeneration.page.xmlDone"),
        severity: "success",
        open: true,
      });
    } catch (error) {
      console.error("XML generation error:", error);
      setBatchFeedback({
        message: t("fileGeneration.page.xmlError", {
          error: error.message || t("fileGeneration.page.unknownError"),
        }),
        severity: "error",
        open: true,
      });
    } finally {
      setIsGeneratingBatch(false);
    }
  };

  const handleBatchGeneration = async (payload) => {
    setIsGeneratingBatch(true);
    setBatchFeedback({ open: false, message: "" });

    // Progress events now handled by backend SocketIO system

    try {
      // Use structured CSV generation with proper parameters
      const response = await generateStructuredCSV({
        output_filename: payload.output_filename,
        client: payload.client_name || 'Professional Client',
        project: payload.project_name || 'Content Generation',
        website: payload.client_website || 'client-website.com',
        client_notes: payload.client_notes, // Client business description
        competitor_url: payload.target_website, // FIX: Pass competitor URL
        project_notes: payload.project_notes, // FIX: Pass project notes
        topics: payload.items || [],
        num_items: payload.page_count || 10,
        target_word_count: 500,
        concurrent_workers: 5,
        batch_size: 25,
        model_name: payload.model_name,
        insert_content: payload.insert_content,
        insert_position: payload.insert_position,
        context_mode: payload.context_mode,
        use_entity_context: payload.use_entity_context,
        use_competitor_analysis: payload.use_competitor_analysis,
        use_document_intelligence: payload.use_document_intelligence,
        client_id: payload.client_id,
      });
      // generateBatchCsv from filegenService returns the full response
      if (response?.error)
        throw new Error(response.details || response.error);

      const successMsg =
        response?.message || t("fileGeneration.page.csvDone");
      const details = response?.details || "";
      const jobId = response?.job_id;
      const taskId = response?.task_id;

      if (jobId) {
        // Progress tracking now handled by backend SocketIO system
      }

      setBatchFeedback({
        message: t("fileGeneration.page.csvStarted", { message: successMsg, details, jobId, taskId }),
        severity: "success",
        open: true,
      });
    } catch (error) {
      console.error("Batch CSV generation error:", error);
      const errMsg =
        error.response?.data?.details ||
        formatUiError(error.response?.data?.error) ||
        error.message ||
        t("fileGeneration.page.csvUnknownError");
      setBatchFeedback({
        message: t("fileGeneration.page.csvError", { error: errMsg }),
        severity: "error",
        open: true,
      });
      // Error events now handled by backend SocketIO system
    } finally {
      setIsGeneratingBatch(false);
    }
  };

  // Save current configuration as a task
  const handleSaveAsTask = async () => {
    if (!batchOutputFilename.trim() || !batchPromptRule) {
      setBatchFeedback({
        message: t("fileGeneration.page.taskRequired"),
        severity: "warning",
        open: true,
      });
      return;
    }

    try {
      const taskData = {
        name: `CSV Generation: ${batchOutputFilename}`,
        description: `Generate ${batchPageCount} CSV rows using rule: ${batchPromptRule.name}${selectedWebsite ? ` for ${selectedWebsite.client?.name}` : ''}`,
        type: "file_generation",
        priority: 2,
        project_id: batchProject?.id || selectedWebsite?.project?.id || null,
        client_name: batchClient?.name || selectedWebsite?.client?.name || "",
        target_website: batchClientWebsite || selectedWebsite?.url || "",
        prompt_text: `Generate ${batchPageCount} CSV rows for: ${batchItems}`,
        output_filename: batchOutputFilename,
        model_name: batchModel || activeModel?.name || "",
        prompt_rule_id: batchPromptRule.id,
        page_count: batchPageCount,
        workflow_config: JSON.stringify({
          page_count: batchPageCount,
          items: batchItems.split('\n').filter(item => item.trim()),
          prompt_rule_id: batchPromptRule.id,
          client_id: batchClient?.id || selectedWebsite?.client?.id || null,
          client_website: batchClientWebsite || selectedWebsite?.url || null,
          target_website: batchTargetWebsite || null,
          website_id: selectedWebsite?.id || null,
          insert_content: insertPosition !== "none" ? insertContent : null,
          insert_position: insertPosition !== "none" ? insertPosition : null,
          context_mode: enhancedContext ? "enhanced" : "basic",
          use_entity_context: enhancedContext,
          use_competitor_analysis: enhancedContext && !!(batchTargetWebsite || useClientCompetitors),
          use_document_intelligence: enhancedContext,
        })
      };

      await apiService.createTask(taskData);
      setBatchFeedback({
        message: t("fileGeneration.page.taskSaved"),
        severity: "success",
        open: true,
      });
    } catch (err) {
      setBatchFeedback({
        message: t("fileGeneration.page.taskSaveFailed", { error: err.message }),
        severity: "error",
        open: true,
      });
    }
  };

  // Duplicate current configuration
  const handleDuplicate = () => {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
    setBatchOutputFilename(`${batchOutputFilename.replace('.csv', '')}_copy_${timestamp}.csv`);
    setBatchFeedback({
      message: t("fileGeneration.page.duplicated"),
      severity: "info",
      open: true,
    });
  };

  // Clear all fields
  const handleClearForm = () => {
    setBatchOutputFilename("");
    setBatchItems("");
    setBatchPromptRule(null);
    setCodeGenRule(null);
    setBatchProject(null);
    setSelectedWebsite(null);
    setBatchClientWebsite("");
    setBatchTargetWebsite("");
    setBatchPageCount(10);
    setBatchClient(null);
    setBatchModel("");
    setInsertContent("");
    setInsertPosition("none");
    setBatchFeedback({
      message: t("fileGeneration.page.cleared"),
      severity: "info",
      open: true,
    });
  };

  // Handle website selection with automatic client and filename setting
  const handleWebsiteSelection = (event, newValue) => {
    setSelectedWebsite(newValue);

    if (newValue) {
      // Auto-fill client website URL
      setBatchClientWebsite(newValue.url);

      // Auto-set client if available
      if (newValue.client) {
        setBatchClient(newValue.client);
        // Auto-fill competitor URL if toggle is enabled and client has competitors
        if (useClientCompetitors && newValue.client.competitor_urls && newValue.client.competitor_urls.length > 0) {
          setBatchTargetWebsite(newValue.client.competitor_urls[0]);
        }
      }

      // Auto-generate filename based on website URL
      const domain = newValue.url.replace(/https?:\/\/(www\.)?/, '').replace(/\/$/, '');
      // Get next available number for this domain
      generateNextFilename(domain);
    } else {
      setBatchClientWebsite("");
      setBatchClient(null);
      setBatchOutputFilename("");
      if (useClientCompetitors) {
        setBatchTargetWebsite("");
      }
    }
  };

  // Handle client selection changes to update competitor URL
  const handleClientSelection = (event, newValue) => {
    setBatchClient(newValue);

    if (useClientCompetitors && newValue && newValue.competitor_urls && newValue.competitor_urls.length > 0) {
      setBatchTargetWebsite(newValue.competitor_urls[0]);
    } else if (useClientCompetitors && (!newValue || !newValue.competitor_urls || newValue.competitor_urls.length === 0)) {
      setBatchTargetWebsite("");
    }
  };

  // Handle toggle changes
  const handleCompetitorToggle = (event) => {
    const enabled = event.target.checked;
    setUseClientCompetitors(enabled);

    if (enabled && batchClient && batchClient.competitor_urls && batchClient.competitor_urls.length > 0) {
      setBatchTargetWebsite(batchClient.competitor_urls[0]);
    } else if (!enabled) {
      // Optionally clear or keep the current value when disabled
      // setBatchTargetWebsite(""); // Uncomment to clear on disable
    }
  };

  // Generate next available filename with incremental number
  const generateNextFilename = (domain) => {
    try {
      // Clean domain name for filename (remove www, keep dots as dots)
      const cleanDomain = domain.replace(/^www\./, '');
      const extension = fileFormat === 'xml' ? '.xml' : '.csv';
      const baseFilename = `${cleanDomain}_001${extension}`;
      setBatchOutputFilename(baseFilename);

      // TODO: In future, could check backend for existing files and increment number
      // For now, starting with 001 as requested - user can manually change if needed
    } catch (error) {
      console.error("Error generating filename:", error);
      const extension = fileFormat === 'xml' ? '.xml' : '.csv';
      const fallbackFilename = `${domain}_001${extension}`;
      setBatchOutputFilename(fallbackFilename);
    }
  };

  const handleCloseFeedback = (type) => (event, reason) => {
    if (reason === "clickaway") return;
    if (type === "single") {
      setSingleFileFeedback((prev) => ({ ...prev, open: false }));
    } else if (type === "batch") {
      setBatchFeedback((prev) => ({ ...prev, open: false }));
    }
  };

  return (
    <PageLayout
      title={t("fileGeneration.page.title")}
      variant="standard"
      modelStatus
      activeModel={
        isLoadingModel
          ? t("fileGeneration.page.modelLoading")
          : modelError
            ? t("fileGeneration.page.modelError")
            : activeModel
      }
    >
        {singleFileFeedback.open && (
          <Snackbar
            open={singleFileFeedback.open}
            autoHideDuration={6000}
            onClose={handleCloseFeedback("single")}
            anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
          >
            <Alert
              onClose={handleCloseFeedback("single")}
              severity={singleFileFeedback.severity}
              sx={{ width: "100%" }}
            >
              {singleFileFeedback.message}
            </Alert>
          </Snackbar>
        )}
        {batchFeedback.open && (
          <Snackbar
            open={batchFeedback.open}
            autoHideDuration={10000}
            onClose={handleCloseFeedback("batch")}
            anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
          >
            <Alert
              onClose={handleCloseFeedback("batch")}
              severity={batchFeedback.severity}
              sx={{ width: "100%" }}
            >
              {batchFeedback.message}
            </Alert>
          </Snackbar>
        )}

        <Paper elevation={3} sx={{ p: { xs: 1.5, sm: 2, md: 3 }, mb: 3 }}>
          <Typography variant="h5" gutterBottom component="div">
            {t("fileGeneration.page.batchTitle")}
          </Typography>

          {/* File Format Selector */}
          <FormControl component="fieldset" sx={{ mb: 2 }}>
            <FormLabel component="legend">{t("fileGeneration.page.outputFormat")}</FormLabel>
            <RadioGroup
              row
              value={fileFormat}
              onChange={(e) => setFileFormat(e.target.value)}
              disabled={isGeneratingBatch}
            >
              <FormControlLabel
                value="csv"
                control={<Radio />}
                label={t("fileGeneration.page.formatCsv")}
              />
              <FormControlLabel
                value="xml"
                control={<Radio />}
                label={t("fileGeneration.page.formatXml")}
              />
            </RadioGroup>
          </FormControl>

          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {fileFormat === "csv" ? (
              <>{t("fileGeneration.page.csvDescription")}</>
            ) : (
              <>{t("fileGeneration.page.xmlDescription")}</>
            )}
            {" "}{t("fileGeneration.page.requiresRule")}
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6}>
              <TextField
                fullWidth
                label={t("fileGeneration.page.outputFilename", { format: fileFormat.toUpperCase() })}
                value={batchOutputFilename}
                onChange={(e) => setBatchOutputFilename(e.target.value)}
                placeholder={t("fileGeneration.page.outputFilenamePlaceholder", { extension: fileFormat })}
                variant="outlined"
                disabled={isGeneratingBatch}
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <Autocomplete
                id="batch-prompt-rule-select"
                options={availableRules}
                getOptionLabel={(option) =>
                  t("fileGeneration.page.ruleOption", { name: option.name, id: option.id, type: option.type })
                }
                value={batchPromptRule}
                onChange={(event, newValue) => setBatchPromptRule(newValue)}
                loading={isLoadingRules}
                isOptionEqualToValue={(option, value) => {
                  // Compare by ID to handle object reference differences
                  if (!option || !value) return false;
                  return option.id === value.id;
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={t("fileGeneration.page.promptRule")}
                    variant="outlined"
                    helperText={t("fileGeneration.page.promptRuleHelp")}
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <React.Fragment>
                          {isLoadingRules ? (
                            <CircularProgress color="inherit" size={20} />
                          ) : null}
                          {params.InputProps.endAdornment}
                        </React.Fragment>
                      ),
                    }}
                  />
                )}
                disabled={isGeneratingBatch}
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <Autocomplete
                id="batch-project-select"
                options={availableProjects}
                getOptionLabel={(option) => {
                  if (typeof option === "string") return option;
                  return option.name || t("fileGeneration.page.idOption", { id: option.id });
                }}
                value={batchProject}
                onChange={(event, newValue) => setBatchProject(newValue)}
                isOptionEqualToValue={(option, value) => {
                  if (!option || !value) return false;
                  if (typeof value === "string") return false;
                  return option.id === value.id;
                }}
                freeSolo
                selectOnFocus
                clearOnBlur
                handleHomeEndKeys
                loading={isLoadingProjects}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={t("fileGeneration.page.project")}
                    variant="outlined"
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <React.Fragment>
                          {isLoadingProjects ? (
                            <CircularProgress color="inherit" size={20} />
                          ) : null}
                          {params.InputProps.endAdornment}
                        </React.Fragment>
                      ),
                    }}
                  />
                )}
                disabled={isGeneratingBatch}
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <Autocomplete
                options={availableWebsites}
                loading={isLoadingWebsites}
                getOptionLabel={(option) => {
                  if (typeof option === "string") return option;
                  return option.url || t("fileGeneration.page.websiteOption", { id: option.id });
                }}
                value={selectedWebsite}
                onChange={handleWebsiteSelection}
                isOptionEqualToValue={(option, value) => {
                  // Compare by ID to handle object reference differences
                  if (!option || !value) return false;
                  return option.id === value.id;
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={t("fileGeneration.page.clientWebsite")}
                    variant="outlined"
                    helperText={t("fileGeneration.page.clientWebsiteHelp")}
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <React.Fragment>
                          {isLoadingWebsites ? <CircularProgress size={20} /> : null}
                          {params.InputProps.endAdornment}
                        </React.Fragment>
                      ),
                    }}
                  />
                )}
                renderOption={(props, option) => {
                  const { key, ...otherProps } = props;
                  return (
                    <Box component="li" key={key} {...otherProps}>
                      <Box>
                        <Typography variant="body2">{option.url}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {t("fileGeneration.page.websiteOptionDetails", {
                            client: option.client?.name || t("fileGeneration.page.unknown"),
                            project: option.project?.name || t("fileGeneration.page.unknown"),
                          })}
                        </Typography>
                      </Box>
                    </Box>
                  );
                }}
                disabled={isGeneratingBatch || isLoadingWebsites}
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <Typography variant="body2" sx={{ flexGrow: 1 }}>
                  {t("fileGeneration.page.competitorWebsite")}
                </Typography>
                <FormControlLabel
                  control={
                    <Switch
                      checked={useClientCompetitors}
                      onChange={handleCompetitorToggle}
                      disabled={isGeneratingBatch}
                      size="small"
                    />
                  }
                  label={
                    <Tooltip title={t("fileGeneration.page.useCompetitorsTooltip")}>
                      <Typography variant="caption">
                        {t("fileGeneration.page.useCompetitors")}
                      </Typography>
                    </Tooltip>
                  }
                  labelPlacement="start"
                />
              </Box>
              <TextField
                fullWidth
                label={
                  useClientCompetitors
                    ? t("fileGeneration.page.competitorFromClient")
                    : t("fileGeneration.page.competitorManual")
                }
                value={batchTargetWebsite}
                onChange={(e) => setBatchTargetWebsite(e.target.value)}
                placeholder={
                  useClientCompetitors
                    ? t("fileGeneration.page.competitorAutoPlaceholder")
                    : t("fileGeneration.page.competitorManualPlaceholder")
                }
                variant="outlined"
                helperText={
                  useClientCompetitors
                    ? batchClient && batchClient.competitor_urls && batchClient.competitor_urls.length > 0
                      ? t("fileGeneration.page.competitorUsing", { url: batchClient.competitor_urls[0] })
                      : t("fileGeneration.page.competitorNone")
                    : t("fileGeneration.page.competitorManualHelp")
                }
                disabled={isGeneratingBatch || (useClientCompetitors && (!batchClient || !batchClient.competitor_urls || batchClient.competitor_urls.length === 0))}
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              <TextField
                fullWidth
                type="number"
                label={t("fileGeneration.page.pageCount")}
                value={batchPageCount}
                onChange={(e) => setBatchPageCount(parseInt(e.target.value) || 10)}
                variant="outlined"
                helperText={t("fileGeneration.page.pageCountHelp")}
                disabled={isGeneratingBatch}
                inputProps={{ min: 1, max: 1000 }}
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              <FormControlLabel
                control={
                  <Switch
                    checked={enhancedContext}
                    onChange={(e) => setEnhancedContext(e.target.checked)}
                    disabled={isGeneratingBatch}
                    size="small"
                  />
                }
                label={t("fileGeneration.page.enhancedContext")}
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              <Autocomplete
                options={availableClients}
                loading={isLoadingClients}
                getOptionLabel={(option) => option.name || t("fileGeneration.page.idOption", { id: option.id })}
                value={batchClient}
                onChange={handleClientSelection}
                isOptionEqualToValue={(option, value) => {
                  // FIX: Compare by ID to handle object reference differences
                  if (!option || !value) return false;
                  return option.id === value.id;
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={t("fileGeneration.page.client")}
                    variant="outlined"
                    helperText={t("fileGeneration.page.clientHelp")}
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <React.Fragment>
                          {isLoadingClients ? (
                            <CircularProgress color="inherit" size={20} />
                          ) : null}
                          {params.InputProps.endAdornment}
                        </React.Fragment>
                      ),
                    }}
                  />
                )}
                disabled={isGeneratingBatch || isLoadingClients}
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              <FormControl fullWidth variant="outlined">
                <InputLabel>{t("fileGeneration.page.model")}</InputLabel>
                <Select
                  value={batchModel}
                  onChange={(e) => setBatchModel(e.target.value)}
                  label={t("fileGeneration.page.model")}
                  disabled={isGeneratingBatch || isLoadingModels}
                >
                  <MenuItem value="">
                    <em>
                      {t("fileGeneration.page.defaultModel", {
                        model: activeModel?.name || t("fileGeneration.page.none"),
                      })}
                    </em>
                  </MenuItem>
                  {availableModels.map((model) => (
                    <MenuItem key={model.name} value={model.name}>
                      {model.name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={12}>
              <Typography
                variant="caption"
                display="block"
                gutterBottom
                sx={{ color: "text.secondary" }}
              >
                {t("fileGeneration.page.itemsLabel")}
              </Typography>
              <TextareaAutosize
                minRows={5}
                maxRows={15}
                placeholder="Tampa&#10;Orlando&#10;Miami&#10;Jacksonville"
                value={batchItems}
                onChange={(e) => setBatchItems(e.target.value)}
                style={{
                  width: "100%",
                  borderColor: theme.palette.divider,
                  borderRadius: theme.shape.borderRadius,
                  fontFamily: theme.typography.fontFamily,
                  fontSize: "1rem",
                  backgroundColor: theme.palette.background.paper,
                  color: theme.palette.text.primary,
                  padding: theme.spacing(1),
                }}
                disabled={isGeneratingBatch}
              />
            </Grid>
            <Grid item xs={12}>
              <Typography
                variant="h6"
                component="div"
                gutterBottom
                sx={{ mt: 2, mb: 1 }}
              >
                {t("fileGeneration.page.insertTitle")}
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mb: 2 }}
              >
                {t("fileGeneration.page.insertDescription")}
              </Typography>

              <FormControl component="fieldset" sx={{ mb: 2 }}>
                <FormLabel component="legend">{t("fileGeneration.page.insertPosition")}</FormLabel>
                <RadioGroup
                  row
                  value={insertPosition}
                  onChange={(e) => setInsertPosition(e.target.value)}
                  disabled={isGeneratingBatch}
                >
                  <FormControlLabel value="none" control={<Radio />} label={t("fileGeneration.page.insertNone")} />
                  <FormControlLabel value="top" control={<Radio />} label={t("fileGeneration.page.insertTop")} />
                  <FormControlLabel value="bottom" control={<Radio />} label={t("fileGeneration.page.insertBottom")} />
                </RadioGroup>
              </FormControl>

              {insertPosition !== "none" && (
                <TextareaAutosize
                  minRows={3}
                  maxRows={8}
                  placeholder={t("fileGeneration.page.insertPlaceholder")}
                  value={insertContent}
                  onChange={(e) => setInsertContent(e.target.value)}
                  style={{
                    width: "100%",
                    padding: theme.spacing(1.5),
                    borderColor: theme.palette.divider,
                    borderRadius: theme.shape.borderRadius,
                    fontFamily: theme.typography.fontFamily,
                    fontSize: "1rem",
                    backgroundColor: theme.palette.background.paper,
                    color: theme.palette.text.primary,
                    border: `1px solid ${theme.palette.divider}`,
                  }}
                  disabled={isGeneratingBatch}
                />
              )}
            </Grid>
            <Grid item xs={12}>
              <Box sx={{ display: 'flex', gap: 2, mb: 2, flexWrap: 'wrap' }}>
                <Button
                  variant="outlined"
                  color="secondary"
                  onClick={handleSaveAsTask}
                  disabled={isGeneratingBatch || !batchOutputFilename.trim() || !batchPromptRule}
                  sx={{ flex: '1 1 auto', minWidth: '120px' }}
                >
                  {t("fileGeneration.page.saveAsTask")}
                </Button>
                <Button
                  variant="outlined"
                  color="info"
                  onClick={handleDuplicate}
                  disabled={isGeneratingBatch || !batchOutputFilename.trim()}
                  sx={{ flex: '1 1 auto', minWidth: '120px' }}
                >
                  {t("fileGeneration.page.duplicate")}
                </Button>
                <Button
                  variant="outlined"
                  color="warning"
                  onClick={handleClearForm}
                  disabled={isGeneratingBatch}
                  sx={{ flex: '1 1 auto', minWidth: '120px' }}
                >
                  {t("fileGeneration.page.clearForm")}
                </Button>
              </Box>
              <Button
                variant="contained"
                color="primary"
                onClick={handleGenerateBatchCsv}
                disabled={
                  isGeneratingBatch ||
                  isLoadingRules ||
                  isLoadingProjects ||
                  isLoadingClients ||
                  isLoadingModels ||
                  !batchOutputFilename.trim() ||
                  !batchPromptRule ||
                  (!batchItems.trim() && (!batchPageCount || batchPageCount <= 0))
                }
                fullWidth
                sx={{ py: 1.5 }}
              >
                {isGeneratingBatch ? (
                  <CircularProgress size={24} color="inherit" />
                ) : (
                  t("fileGeneration.page.start")
                )}
              </Button>
            </Grid>
          </Grid>
        </Paper>

        <Divider sx={{ my: 4 }}>
          <Chip label={t("fileGeneration.page.advancedChip")} />
        </Divider>

        <Paper
          elevation={3}
          sx={{ p: { xs: 1.5, sm: 2, md: 3 }, mb: 3, opacity: 0.7 }}
        >
          <Typography variant="h5" gutterBottom component="div">
            {t("fileGeneration.page.singleTitle")}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {t("fileGeneration.page.singleDescription")}
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label={t("fileGeneration.page.singleFilename")}
                value={outputFilenameSingle}
                onChange={(e) => setOutputFilenameSingle(e.target.value)}
                variant="outlined"
                disabled={isGeneratingSingle}
              />
            </Grid>
            <Grid item xs={12}>
              <Autocomplete
                id="code-gen-rule-select"
                options={availableRules.filter(rule => 
                  rule.type === 'COMMAND_RULE' && 
                  (rule.command_label?.includes('codegen') || 
                   rule.command_label?.includes('createfile') ||
                   rule.name?.toLowerCase().includes('code'))
                )}
                getOptionLabel={(option) =>
                  t("fileGeneration.page.codeRuleOption", {
                    name: option.name,
                    command: option.command_label || t("fileGeneration.page.noCommand"),
                    type: option.type,
                  })
                }
                value={codeGenRule}
                onChange={(event, newValue) => setCodeGenRule(newValue)}
                loading={isLoadingRules}
                isOptionEqualToValue={(option, value) => {
                  // Compare by ID to handle object reference differences
                  if (!option || !value) return false;
                  return option.id === value.id;
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={t("fileGeneration.page.codeRule")}
                    variant="outlined"
                    helperText={t("fileGeneration.page.codeRuleHelp")}
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <React.Fragment>
                          {isLoadingRules ? (
                            <CircularProgress color="inherit" size={20} />
                          ) : null}
                          {params.InputProps.endAdornment}
                        </React.Fragment>
                      ),
                    }}
                  />
                )}
                sx={{ mb: 1 }}
                disabled={isGeneratingSingle}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                multiline
                rows={6}
                label={t("fileGeneration.page.instructions")}
                placeholder={t("fileGeneration.page.instructionsPlaceholder")}
                value={userInstructions}
                onChange={(e) => setUserInstructions(e.target.value)}
                variant="outlined"
                disabled={isGeneratingSingle}
              />
            </Grid>
            <Grid item xs={12}>
              <Button
                variant="contained"
                color="secondary"
                onClick={handleGenerateSingleFile}
                disabled={
                  isGeneratingSingle ||
                  !outputFilenameSingle.trim() ||
                  !userInstructions.trim()
                }
                fullWidth
              >
                {isGeneratingSingle ? (
                  <CircularProgress size={24} color="inherit" />
                ) : (
                  t("fileGeneration.page.generateSingle")
                )}
              </Button>
            </Grid>
          </Grid>
        </Paper>
    </PageLayout>
  );
};

export default FileGenerationPage;
