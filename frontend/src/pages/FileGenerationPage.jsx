// frontend/src/pages/FileGenerationPage.jsx
// Version 2.0.1: Corrected import from filegenService to use generateFileFromChat as generateSingleFile.
// - Resolves "does not provide an export named 'generateSingleFile'" error.
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from Dean (user/owner).

import React, { useState, useEffect, useCallback } from "react";
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
  Card,
  CardContent,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  IconButton,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import PreviewIcon from "@mui/icons-material/Preview";
import WarningIcon from "@mui/icons-material/Warning";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";

import * as apiService from "../api"; // For fetching Rules & Projects
// Corrected import: Import generateFileFromChat and alias it as generateSingleFile
import {
  generateFileFromChat as generateSingleFile,
  generateBatchCsv,
} from "../api/filegenService";
import { getWebsites } from "../api/websiteService"; // For website dropdown
import { generateBulkXML, generateStructuredCSV } from "../api/bulkGenerationService"; // For XML generation
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import { DELIMITER_OPTIONS, BRAND_TONE_OPTIONS, SITE_META_DEFAULTS } from "../config/defaults";
import { validateGenerationBatch, createValidationReport, formatValidationErrors } from "../utils/validation";
import { renderStructuredHTML, previewStructuredHTML } from "../utils/htmlRenderer";
import { arrayToCSV, DELIMITERS } from "../utils/csv";
import { slugify } from "../utils/slugify";

const FileGenerationPage = () => {
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
  const [csvDelimiter, setCsvDelimiter] = useState(DELIMITERS.COMMA);
  const [structuredHtml, setStructuredHtml] = useState(false);
  const [includeH1InContent, setIncludeH1InContent] = useState(true);
  const [siteMeta, setSiteMeta] = useState({ ...SITE_META_DEFAULTS });
  const [validationReport, setValidationReport] = useState(null);
  const [showValidationModal, setShowValidationModal] = useState(false);
  const [showPreviewModal, setShowPreviewModal] = useState(false);
  const [previewContent, setPreviewContent] = useState("");
  const [generationId, setGenerationId] = useState(null);
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
          `Rules: ${rulesResponse.error.message || rulesResponse.error}`,
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
          `Projects: ${projectsData.error.message || projectsData.error}`,
        );
      setAvailableProjects(Array.isArray(projectsData) ? projectsData : []);

      // Fetch Clients
      const clientsData = await apiService.getClients();
      if (clientsData?.error)
        throw new Error(
          `Clients: ${clientsData.error.message || clientsData.error}`,
        );
      setAvailableClients(Array.isArray(clientsData) ? clientsData : []);

      // Fetch Models
      const modelsData = await apiService.getAvailableModels();
      if (modelsData?.error)
        throw new Error(
          `Models: ${modelsData.error.message || modelsData.error}`,
        );
      setAvailableModels(Array.isArray(modelsData) ? modelsData : []);

      // Fetch Websites
      const websitesData = await getWebsites();
      if (websitesData?.error) {
        const errorMessage = websitesData.error?.message || websitesData.error || 'Unknown error';
        throw new Error(`Websites: ${errorMessage}`);
      }
      setAvailableWebsites(Array.isArray(websitesData) ? websitesData : []);
    } catch (err) {
      console.error(
        "FileGenerationPage: Error fetching required data:",
        err,
      );
      setBatchFeedback({
        message: `Failed to load prerequisite data: ${err?.message || err || 'Unknown error'}`,
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
        message:
          "Output filename and instructions are required for single file generation.",
        severity: "warning",
        open: true,
      });
      return;
    }
    setIsGeneratingSingle(true);
    setSingleFileFeedback({
      message: "Generating single file via code execution path...",
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
          response?.message ||
          "File generation process completed for single file.",
        severity: "success",
        open: true,
      });

      setOutputFilenameSingle("");
      setUserInstructions("");
      setCodeGenRule(null);
    } catch (error) {
      console.error("Single file generation error:", error);
      setSingleFileFeedback({
        message: `Single File Error: ${error.message || "Unknown error"}`,
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
        message: "Please fill in required fields: filename and prompt rule.",
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
        message: "Please either specify items to process OR set a page count greater than 0.",
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
        message: response.message || "XML generation completed successfully!",
        severity: "success",
        open: true,
      });
    } catch (error) {
      console.error("XML generation error:", error);
      setBatchFeedback({
        message: `XML Generation Error: ${error.message || "Unknown error"}`,
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
        response?.message ||
        "Batch CSV generation process completed successfully.";
      const details = response?.details || "";
      const jobId = response?.job_id;
      const taskId = response?.task_id;

      if (jobId) {
        // Progress tracking now handled by backend SocketIO system
      }

      setBatchFeedback({
        message: `${successMsg} ${details} Job ID: ${jobId}, Task ID: ${taskId}. Check Task Manager for progress.`,
        severity: "success",
        open: true,
      });
    } catch (error) {
      console.error("Batch CSV generation error:", error);
      const errMsg =
        error.response?.data?.details ||
        error.response?.data?.error ||
        error.message ||
        "Unknown batch processing error.";
      setBatchFeedback({
        message: `Batch CSV Error: ${errMsg}`,
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
        message: "Please fill in filename and prompt rule before saving as task.",
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
        message: "Configuration saved as task successfully!",
        severity: "success",
        open: true,
      });
    } catch (err) {
      setBatchFeedback({
        message: `Failed to save as task: ${err.message}`,
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
      message: "Configuration duplicated! Update filename and generate.",
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
      message: "Form cleared successfully!",
      severity: "info",
      open: true,
    });
  };

  // Enhanced validation and preview handlers
  const handlePreflightValidation = () => {
    const items = batchItems.split('\n').filter(item => item.trim());
    const mockRows = items.map((item, index) => ({
      title: `${item} - Professional Services`,
      slug: slugify(item),
      content: `Comprehensive ${item} services with expert consultation.`,
      excerpt: `Professional ${item} services for your business needs.`,
      category: siteMeta.primaryService || 'Services',
      tags: [siteMeta.primaryService, 'professional', 'expert'].filter(Boolean)
    }));

    const csvConfig = { delimiter: csvDelimiter, structuredHtml, includeH1: includeH1InContent };
    const report = createValidationReport(mockRows, siteMeta, csvConfig);
    setValidationReport(report);
    setShowValidationModal(true);
  };

  const handlePreviewStructuredHTML = () => {
    const sampleContent = `Professional ${siteMeta.primaryService || 'Services'}

About Our Services:
We provide comprehensive solutions for your business needs.

Our Expertise:
- Expert consultation and guidance
- **Proven track record** of success
- Personalized approach to every project

Why Choose Us:
Years of experience in the industry
*Dedicated customer service*
Competitive pricing and flexible terms

Contact us today for more information.`;

    const preview = previewStructuredHTML(sampleContent, siteMeta, {
      includeH1: includeH1InContent
    });
    setPreviewContent(preview.html);
    setShowPreviewModal(true);
  };

  const handleSiteMetaChange = (field, value) => {
    setSiteMeta(prev => ({ ...prev, [field]: value }));
  };

  const handleSocialLinkAdd = () => {
    setSiteMeta(prev => ({
      ...prev,
      socialLinks: [...prev.socialLinks, { platform: '', url: '' }]
    }));
  };

  const handleSocialLinkChange = (index, field, value) => {
    setSiteMeta(prev => ({
      ...prev,
      socialLinks: prev.socialLinks.map((link, i) =>
        i === index ? { ...link, [field]: value } : link
      )
    }));
  };

  const handleSocialLinkRemove = (index) => {
    setSiteMeta(prev => ({
      ...prev,
      socialLinks: prev.socialLinks.filter((_, i) => i !== index)
    }));
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
      title="File Generation"
      variant="standard"
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel}
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
            Batch File Generation
          </Typography>

          {/* File Format Selector */}
          <FormControl component="fieldset" sx={{ mb: 2 }}>
            <FormLabel component="legend">Output Format</FormLabel>
            <RadioGroup
              row
              value={fileFormat}
              onChange={(e) => setFileFormat(e.target.value)}
              disabled={isGeneratingBatch}
            >
              <FormControlLabel
                value="csv"
                control={<Radio />}
                label="CSV (Enfold WordPress Theme)"
              />
              <FormControlLabel
                value="xml"
                control={<Radio />}
                label="XML (WordPress Import via Llamanator)"
              />
            </RadioGroup>
          </FormControl>

          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {fileFormat === "csv" ? (
              <>
                Generate multiple WordPress pages/posts as CSV rows using the Enfold theme format.
                Output includes: ID, Title, Content, Excerpt, Category, Tags, slug, Image.
              </>
            ) : (
              <>
                Generate XML file compatible with Llamanator WordPress import plugin.
                Supports CDATA sections, custom metadata, and full WordPress post structure.
              </>
            )}
            {" "}Requires a &apos;Single-Item Content Prompt&apos; Rule that generates fields for one item.
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6}>
              <TextField
                fullWidth
                label={`Output ${fileFormat.toUpperCase()} Filename`}
                value={batchOutputFilename}
                onChange={(e) => setBatchOutputFilename(e.target.value)}
                placeholder={`e.g., florida_seo_pages.${fileFormat}`}
                variant="outlined"
                disabled={isGeneratingBatch}
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <Autocomplete
                id="batch-prompt-rule-select"
                options={availableRules}
                getOptionLabel={(option) =>
                  `${option.name} (ID: ${option.id}) - Type: ${option.type}`
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
                    label="Single-Item Content Prompt Rule"
                    variant="outlined"
                    helperText="Select a prompt that generates content for ONE item."
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
                  return option.name || `ID: ${option.id}`;
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
                    label="Assign to Project (Optional)"
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
                  return option.url || `Website ${option.id}`;
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
                    label="Client Website"
                    variant="outlined"
                    helperText="Optional — auto-fills client and filename"
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
                          Client: {option.client?.name || 'Unknown'} | Project: {option.project?.name || 'Unknown'}
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
                  Target/Competitor Website
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
                    <Tooltip title="When enabled, automatically use competitor URLs from the selected client's profile">
                      <Typography variant="caption">
                        Use Client Competitors
                      </Typography>
                    </Tooltip>
                  }
                  labelPlacement="start"
                />
              </Box>
              <TextField
                fullWidth
                label={useClientCompetitors ? "Competitor URL (from client)" : "Competitor URL (manual)"}
                value={batchTargetWebsite}
                onChange={(e) => setBatchTargetWebsite(e.target.value)}
                placeholder={useClientCompetitors ? "Auto-filled from client..." : "e.g., competitor-website.com"}
                variant="outlined"
                helperText={
                  useClientCompetitors
                    ? batchClient && batchClient.competitor_urls && batchClient.competitor_urls.length > 0
                      ? `Using: ${batchClient.competitor_urls[0]}`
                      : "No competitors defined for selected client"
                    : "Manually enter competitor URL for analysis"
                }
                disabled={isGeneratingBatch || (useClientCompetitors && (!batchClient || !batchClient.competitor_urls || batchClient.competitor_urls.length === 0))}
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              <TextField
                fullWidth
                type="number"
                label="Page Count"
                value={batchPageCount}
                onChange={(e) => setBatchPageCount(parseInt(e.target.value) || 10)}
                variant="outlined"
                helperText="Number of CSV rows/pages to generate"
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
                label="Enhanced Context (entity/competitor/doc awareness)"
              />
            </Grid>
            <Grid item xs={12} sm={4}>
              <Autocomplete
                options={availableClients}
                loading={isLoadingClients}
                getOptionLabel={(option) => option.name || `ID: ${option.id}`}
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
                    label="Client (Optional)"
                    variant="outlined"
                    helperText="Select client for this generation"
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
                <InputLabel>Model (Optional)</InputLabel>
                <Select
                  value={batchModel}
                  onChange={(e) => setBatchModel(e.target.value)}
                  label="Model (Optional)"
                  disabled={isGeneratingBatch || isLoadingModels}
                >
                  <MenuItem value="">
                    <em>Use Default Model ({activeModel?.name || 'None'})</em>
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
                Items to Process (optional - leave empty to use Page Count with generic topics):
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
                Content Insert Options
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mb: 2 }}
              >
                Add custom content (contact info, shortcodes, embeds, etc.) to each generated page
              </Typography>

              <FormControl component="fieldset" sx={{ mb: 2 }}>
                <FormLabel component="legend">Insert Position</FormLabel>
                <RadioGroup
                  row
                  value={insertPosition}
                  onChange={(e) => setInsertPosition(e.target.value)}
                  disabled={isGeneratingBatch}
                >
                  <FormControlLabel value="none" control={<Radio />} label="No Insert" />
                  <FormControlLabel value="top" control={<Radio />} label="Top (Before AI Content)" />
                  <FormControlLabel value="bottom" control={<Radio />} label="Bottom (After AI Content)" />
                </RadioGroup>
              </FormControl>

              {insertPosition !== "none" && (
                <TextareaAutosize
                  minRows={3}
                  maxRows={8}
                  placeholder="Enter content to insert into each page&#10;&#10;Examples:&#10;- Contact information&#10;- Shortcodes: [contact_form]&#10;- Embed codes: <iframe>...&#10;- Call-to-action text"
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
                  Save as Task
                </Button>
                <Button
                  variant="outlined"
                  color="info"
                  onClick={handleDuplicate}
                  disabled={isGeneratingBatch || !batchOutputFilename.trim()}
                  sx={{ flex: '1 1 auto', minWidth: '120px' }}
                >
                  Duplicate
                </Button>
                <Button
                  variant="outlined"
                  color="warning"
                  onClick={handleClearForm}
                  disabled={isGeneratingBatch}
                  sx={{ flex: '1 1 auto', minWidth: '120px' }}
                >
                  Clear Form
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
                  "Start"
                )}
              </Button>
            </Grid>
          </Grid>
        </Paper>

        <Divider sx={{ my: 4 }}>
          <Chip label="Advanced: Single File (Code Generation)" />
        </Divider>

        <Paper
          elevation={3}
          sx={{ p: { xs: 1.5, sm: 2, md: 3 }, mb: 3, opacity: 0.7 }}
        >
          <Typography variant="h5" gutterBottom component="div">
            Single File Generation (via Code)
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Generates a single file by having the LLM write and execute Python
            code.
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label="Output Filename (e.g., report.py, data.json)"
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
                  `${option.name} (${option.command_label || 'No command'}) - Type: ${option.type}`
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
                    label="Code Generation Rule (Optional)"
                    variant="outlined"
                    helperText="Select a rule for code generation, or leave empty to use direct instructions."
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
                label="User Instructions / Specifications for Code Generation"
                placeholder="e.g., Write a python script to analyze data.csv (in uploads folder), calculate column averages, and save to output.txt..."
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
                  "Generate Single File (Code Gen)"
                )}
              </Button>
            </Grid>
          </Grid>
        </Paper>
    </PageLayout>
  );
};

export default FileGenerationPage;
