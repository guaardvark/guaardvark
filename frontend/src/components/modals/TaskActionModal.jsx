// frontend/src/components/modals/TaskActionModal.jsx
// Version 2.0: Complete rewrite with Rules integration and job management
// Unified task creation with built-in job execution for file_generation tasks

import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Grid,
  CircularProgress,
  Box,
  Alert,
  Autocomplete,
  Typography,
  IconButton,
  MenuItem,
  Select,
  FormControl,
  InputLabel,
  Chip,
  LinearProgress,
  Divider,
  Card,
  CardContent,
  Radio,
  RadioGroup,
  FormControlLabel,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import FileCopyIcon from "@mui/icons-material/FileCopy";
import DeleteIcon from "@mui/icons-material/Delete";
import DownloadIcon from "@mui/icons-material/Download";
import InfoIcon from "@mui/icons-material/Info";
import * as apiService from "../../api";
import { getRules } from "../../api/ruleService";
import { getWebsites } from "../../api/websiteService";
import { formatUiError } from "../../utils/uiError";
import { generateStructuredCSV } from "../../api/bulkGenerationService";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import { translate, useUiTranslation } from "../../i18n";

const taskTypes = () => [
  { value: "file_generation", label: translate("dialogs.taskAction.types.fileGeneration"), description: translate("dialogs.taskAction.types.fileGenerationDescription") },
  { value: "code_generation", label: translate("dialogs.taskAction.types.codeGeneration"), description: translate("dialogs.taskAction.types.codeGenerationDescription") },
  { value: "content_generation", label: translate("dialogs.taskAction.types.contentGeneration"), description: translate("dialogs.taskAction.types.contentGenerationDescription") },
  { value: "bulk_content_generation", label: translate("dialogs.taskAction.types.bulkContentGeneration"), description: translate("dialogs.taskAction.types.bulkContentGenerationDescription") },
  { value: "data_analysis", label: translate("dialogs.taskAction.types.dataAnalysis"), description: translate("dialogs.taskAction.types.dataAnalysisDescription") },
  { value: "website_analysis", label: translate("dialogs.taskAction.types.websiteAnalysis"), description: translate("dialogs.taskAction.types.websiteAnalysisDescription") },
  { value: "custom", label: translate("dialogs.taskAction.types.custom"), description: translate("dialogs.taskAction.types.customDescription") },
];

const TaskActionModal = ({
  open,
  onClose,
  taskData,
  onSave,
  isSaving,
  onTaskCreated, // New callback for when task is created and job started
  onTaskDeleted, // Callback for when task is deleted
  onTaskDuplicated, // Callback for when task is duplicated
  defaults = null, // New task only: { project_id, client_id, client_name, website_id } to start from
}) => {
  const { t } = useUiTranslation();
  const [formData, setFormData] = useState({
    id: null,
    name: "",
    description: "",
    type: "file_generation",
    project_id: "",
    client_name: "",
    target_website: "",
    output_filename: "",
    model_name: "",
    prompt_rule_id: null,
    
    // File generation specific fields
    page_count: 50,
    auto_start_job: true,
  });

  const [formError, setFormError] = useState(null);
  // Lists that could not be loaded (models, rules, websites): their dropdowns
  // are empty then, so say why without blocking the form.
  const [lookupWarnings, setLookupWarnings] = useState({});
  const noteLookup = useCallback((key, text) =>
    setLookupWarnings((prev) => {
      const next = { ...prev };
      if (text) next[key] = text;
      else delete next[key];
      return next;
    }), []);
  const [projects, setProjects] = useState([]);
  const [availableModels, setAvailableModels] = useState([]);
  const [availableRules, setAvailableRules] = useState([]);
  const [availableWebsites, setAvailableWebsites] = useState([]);
  const [isLoadingProjects, setIsLoadingProjects] = useState(false);
  const [isLoadingModels, setIsLoadingModels] = useState(false);
  const [isLoadingRules, setIsLoadingRules] = useState(false);
  const [isLoadingWebsites, setIsLoadingWebsites] = useState(false);
  const [formProjectValue, setFormProjectValue] = useState(null);
  const [formRuleValue, setFormRuleValue] = useState(null);
  const [selectedWebsite, setSelectedWebsite] = useState(null);
  const [isCreatingJob, setIsCreatingJob] = useState(false);
  
  // Task management state
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDuplicating, setIsDuplicating] = useState(false);
  const [isStartingJob, setIsStartingJob] = useState(false);
  const [isStoppingJob, setIsStoppingJob] = useState(false);
  const [fileInfo, setFileInfo] = useState(null);
  const [isLoadingFileInfo, setIsLoadingFileInfo] = useState(false);

  // FileGenerationPage-specific fields (Bug fixes #1-5)
  const [batchItems, setBatchItems] = useState("");
  const [insertContent, setInsertContent] = useState("");
  const [insertPosition, setInsertPosition] = useState("none");
  const [clientWebsite, setClientWebsite] = useState("");

  // Enhanced CSV generation parameters (Phase 1 - Session Handoff)
  const [clientNotes, setClientNotes] = useState("");
  const [competitorUrl, setCompetitorUrl] = useState("");
  const [targetWordCount, setTargetWordCount] = useState(1000);
  const [concurrentWorkers, setConcurrentWorkers] = useState(3);
  const [batchSize, setBatchSize] = useState(10);

  // Progress integration
  const { _activeProcesses, getProcess } = useUnifiedProgress();
  
  // Editing needs a saved task. taskData without an id is a template (New Job's
  // quick actions): the form opens in create mode with its values filled in.
  const isEditMode = Boolean(taskData?.id);
  const isFileGeneration = formData.type === "file_generation";
  const isCodeGeneration = formData.type === "code_generation";
  
  // Get real-time progress data
  const processData = taskData?.job_id ? getProcess(taskData.job_id) : null;
  const realTimeProgress = processData?.progress || 0;
  const realTimeStatus = processData?.status || taskData?.status;
  const isRunning = realTimeStatus === "in-progress" || realTimeStatus === "running" || realTimeStatus === "processing";
  const isCompleted = realTimeStatus === "completed" || realTimeStatus === "complete";
  const isFailed = realTimeStatus === "failed" || realTimeStatus === "error";

  // Fetch data for dropdowns
  const fetchProjects = useCallback(async () => {
    setIsLoadingProjects(true);
    try {
      const projectList = await apiService.getProjects();
      if (projectList.error) {
        throw new Error(projectList.error.message || projectList.error);
      }
      setProjects(Array.isArray(projectList) ? projectList : []);
    } catch (err) {
      console.error("Error fetching projects:", err);
      setProjects([]);
      setFormError(translate("dialogs.taskAction.errors.loadProjects", { error: err.message }));
    } finally {
      setIsLoadingProjects(false);
    }
  }, []);

  const fetchModels = useCallback(async () => {
    setIsLoadingModels(true);
    try {
      const modelList = await apiService.getAvailableModels();
      if (modelList.error) {
        throw new Error(modelList.error.message || modelList.error);
      }
      setAvailableModels(Array.isArray(modelList) ? modelList : []);
      noteLookup("models", null);
    } catch (err) {
      console.error("Error fetching models:", err);
      setAvailableModels([]);
      noteLookup("models", translate("dialogs.taskAction.errors.loadModels", { error: err.message }));
    } finally {
      setIsLoadingModels(false);
    }
  }, []);

  // Fetch rules for dropdown
  const fetchRules = useCallback(async () => {
    setIsLoadingRules(true);
    try {
      const rulesList = await getRules({ active: true }); // Only fetch active rules
      if (rulesList.error) {
        console.error("Failed to fetch rules:", rulesList.error);
        setAvailableRules([]);
        noteLookup("rules", translate("dialogs.taskAction.errors.loadRules", { error: formatUiError(rulesList.error) }));
      } else {
        setAvailableRules(rulesList);
        noteLookup("rules", null);
      }
    } catch (err) {
      console.error("Failed to fetch rules:", err);
      setAvailableRules([]);
      noteLookup("rules", translate("dialogs.taskAction.errors.loadRules", { error: formatUiError(err) }));
    } finally {
      setIsLoadingRules(false);
    }
  }, []);

  // Fetch websites for dropdown
  const fetchWebsites = useCallback(async () => {
    setIsLoadingWebsites(true);
    try {
      const websitesList = await getWebsites();
      if (websitesList.error) {
        console.error("Failed to fetch websites:", websitesList.error);
        setAvailableWebsites([]);
        noteLookup("websites", translate("dialogs.taskAction.errors.loadWebsites", { error: formatUiError(websitesList.error) }));
      } else {
        setAvailableWebsites(Array.isArray(websitesList) ? websitesList : []);
        noteLookup("websites", null);
      }
    } catch (err) {
      console.error("Failed to fetch websites:", err);
      setAvailableWebsites([]);
      noteLookup("websites", translate("dialogs.taskAction.errors.loadWebsites", { error: formatUiError(err) }));
    } finally {
      setIsLoadingWebsites(false);
    }
  }, []);

  // Fetch file info function - declared early to avoid hoisting issues
  const fetchFileInfo = useCallback(async () => {
    if (!taskData?.id || !taskData?.output_filename) return;
    
    setIsLoadingFileInfo(true);
    try {
      const response = await fetch(`/api/tasks/${taskData.id}/file-info`);
      
      if (response.ok) {
        const data = await response.json();
        setFileInfo(data);
      }
    } catch (err) {
      console.error('Failed to fetch file info:', err);
    } finally {
      setIsLoadingFileInfo(false);
    }
  }, [taskData?.id, taskData?.output_filename]);

  // Initialize form data
  useEffect(() => {
    if (open) {
      fetchProjects();
      fetchModels();
      fetchRules();
      fetchWebsites();
      
      if (isEditMode && taskData) {
        // BUG FIX #9: Safely parse workflow config with try-catch
        let workflowConfig = {};
        try {
          if (taskData.workflow_config_parsed && typeof taskData.workflow_config_parsed === 'object') {
            workflowConfig = taskData.workflow_config_parsed;
          } else if (taskData.workflow_config && typeof taskData.workflow_config === 'string') {
            workflowConfig = JSON.parse(taskData.workflow_config);
          }
        } catch (parseError) {
          console.error('Failed to parse workflow_config:', parseError);
          workflowConfig = {};
        }

        setFormData({
          id: taskData.id,
          name: taskData.name || "",
          description: taskData.description || "",
          type: taskData.type || "file_generation",
          project_id: taskData.project_id || "",
          client_name: taskData.client_name || workflowConfig.client_name || "",
          target_website: taskData.target_website || workflowConfig.target_website || "",
          output_filename: taskData.output_filename || "",
          model_name: taskData.model_name || "",
          prompt_rule_id: taskData.prompt_rule_id || workflowConfig.prompt_rule_id || null,
          page_count: taskData.page_count || workflowConfig.page_count || 50,
          auto_start_job: false, // Don't auto-start for existing tasks
        });

        // Populate FileGenerationPage-specific fields from workflow_config
        if (workflowConfig.items && Array.isArray(workflowConfig.items)) {
          setBatchItems(workflowConfig.items.join('\n'));
        }
        if (workflowConfig.insert_content) {
          setInsertContent(workflowConfig.insert_content);
        }
        if (workflowConfig.insert_position) {
          setInsertPosition(workflowConfig.insert_position);
        }
        if (workflowConfig.client_website) {
          setClientWebsite(workflowConfig.client_website);
        }
        // Enhanced CSV generation parameters
        if (workflowConfig.client_notes) {
          setClientNotes(workflowConfig.client_notes);
        }
        if (workflowConfig.competitor_url) {
          setCompetitorUrl(workflowConfig.competitor_url);
        }
        if (workflowConfig.target_word_count) {
          setTargetWordCount(workflowConfig.target_word_count);
        }
        if (workflowConfig.concurrent_workers) {
          setConcurrentWorkers(workflowConfig.concurrent_workers);
        }
        if (workflowConfig.batch_size) {
          setBatchSize(workflowConfig.batch_size);
        }
      } else {
        // Create mode with smart defaults, or with a quick action's template
        const template = taskData || {};
        const type = template.type || "file_generation";
        const timestamp = new Date().toISOString().slice(0, 16).replace('T', '_').replace(/[-:]/g, '');
        setFormData({
          id: null,
          name: template.name || "",
          description: template.description || "",
          type,
          project_id: defaults?.project_id ?? "",
          client_name: defaults?.client_name ?? "",
          target_website: "",
          output_filename: template.output_filename
            || (type === "code_generation" ? `generated_file.jsx` : `content_${timestamp}.csv`),
          model_name: "",
          prompt_rule_id: null,
          page_count: 50,
          auto_start_job: template.auto_start_job ?? true,
          client_id: defaults?.client_id ?? null,
          website_id: defaults?.website_id ?? null,
        });
        setSelectedWebsite(null);

        // Reset FileGenerationPage-specific fields for new tasks
        setBatchItems("");
        setInsertContent("");
        setInsertPosition("none");
        setClientWebsite("");
        // Reset enhanced CSV parameters
        setClientNotes("");
        setCompetitorUrl("");
        setTargetWordCount(1000);
        setConcurrentWorkers(3);
        setBatchSize(10);
      }
      
      setFormError(null);
      setIsCreatingJob(false);
      
      // Fetch file info for existing tasks
      if (isEditMode && taskData?.output_filename) {
        fetchFileInfo();
      }
    }
  }, [open, taskData, isEditMode, fetchProjects, fetchModels, fetchRules, fetchWebsites, fetchFileInfo, defaults]);

  // Sync dropdown values with form data
  useEffect(() => {
    if (open && projects.length > 0 && formData.project_id) {
      const projObj = projects.find(p => String(p.id) === String(formData.project_id));
      if (projObj && (!formProjectValue || formProjectValue.id !== projObj.id)) {
        setFormProjectValue(projObj);
      }
    } else if (!formData.project_id) {
      setFormProjectValue(null);
    }
  }, [open, projects, formData.project_id, formProjectValue]);

  useEffect(() => {
    if (open && availableRules.length > 0 && formData.prompt_rule_id) {
      const ruleObj = availableRules.find(r => r.id === formData.prompt_rule_id);
      if (ruleObj && (!formRuleValue || formRuleValue.id !== ruleObj.id)) {
        setFormRuleValue(ruleObj);
      }
    } else if (!formData.prompt_rule_id) {
      setFormRuleValue(null);
    }
  }, [open, availableRules, formData.prompt_rule_id, formRuleValue]);

  // Form handlers
  const handleInputChange = (event) => {
    const { name, value, type, checked } = event.target;
    setFormData(prev => ({ 
      ...prev, 
      [name]: type === 'checkbox' ? checked : value 
    }));
  };

  const handleProjectChange = (event, newValue) => {
    setFormProjectValue(newValue);
    if (newValue && typeof newValue === "object" && newValue.id) {
      setFormData(prev => ({ ...prev, project_id: newValue.id }));
    } else if (!newValue) {
      setFormData(prev => ({ ...prev, project_id: "" }));
    }
  };

  const handleRuleChange = (event, newValue) => {
    setFormRuleValue(newValue);
    if (newValue && typeof newValue === "object" && newValue.id) {
      setFormData(prev => ({ ...prev, prompt_rule_id: newValue.id }));
    } else if (!newValue) {
      setFormData(prev => ({ ...prev, prompt_rule_id: null }));
    }
  };

  // Handle website selection with automatic client and filename setting
  const handleWebsiteSelection = (event, newValue) => {
    setSelectedWebsite(newValue);
    
    if (newValue) {
      // Auto-fill target website URL and client name
      setFormData(prev => ({
        ...prev,
        target_website: newValue.url,
        client_name: newValue.client?.name || "",
        project_id: newValue.project?.id || prev.project_id,
        website_id: newValue.id ?? null,
        client_id: newValue.client?.id ?? newValue.client_id ?? prev.client_id ?? null,
      }));
      
      // Auto-set project if available
      if (newValue.project) {
        setFormProjectValue(newValue.project);
      }
      
      // Auto-generate filename based on website URL
      if (!formData.output_filename || formData.output_filename === "") {
        const domain = newValue.url.replace(/https?:\/\/(www\.)?/, '').replace(/\/$/, '');
        const cleanDomain = domain.replace(/^www\./, '');
        const baseFilename = `${cleanDomain}_001.csv`;
        setFormData(prev => ({ ...prev, output_filename: baseFilename }));
      }
    } else {
      setFormData(prev => ({
        ...prev,
        target_website: "",
        client_name: "",
        website_id: null,
      }));
    }
  };

  // A new task started from a website picks that website once the list is in.
  const appliedDefaultWebsiteRef = useRef(null);
  useEffect(() => {
    if (!open) {
      appliedDefaultWebsiteRef.current = null;
      return;
    }
    const wanted = defaults?.website_id;
    if (isEditMode || !wanted || appliedDefaultWebsiteRef.current === wanted) return;
    const site = availableWebsites.find((w) => String(w.id) === String(wanted));
    if (!site) return;
    appliedDefaultWebsiteRef.current = wanted;
    handleWebsiteSelection(null, site);
  }, [open, isEditMode, defaults, availableWebsites]);

  // Validation function
  const validateForm = () => {
    if (!formData.name.trim()) {
      return t("dialogs.taskAction.errors.nameRequired");
    }
    
    if (isFileGeneration) {
      if (!formData.client_name.trim()) {
        return t("dialogs.taskAction.errors.clientRequired");
      }
      if (!formData.output_filename.trim()) {
        return t("dialogs.taskAction.errors.fileOutputRequired");
      }
      if (formData.page_count < 1 || formData.page_count > 1000) {
        return t("dialogs.taskAction.errors.pageCountRange");
      }
    }

    if (isCodeGeneration) {
      if (!formData.output_filename.trim()) {
        return t("dialogs.taskAction.errors.codeOutputRequired");
      }
      if (!formData.description.trim()) {
        return t("dialogs.taskAction.errors.codeDescriptionRequired");
      }
      // Validate file extension for code generation
      const filename = formData.output_filename.toLowerCase();
      if (!filename.endsWith('.jsx') && !filename.endsWith('.py') && !filename.endsWith('.js') &&
          !filename.endsWith('.ts') && !filename.endsWith('.tsx') && !filename.endsWith('.html') &&
          !filename.endsWith('.css') && !filename.endsWith('.php')) {
        return t("dialogs.taskAction.errors.codeExtension");
      }
    }
    
    return null;
  };

  // Main action handler
  const handleCreateTask = async () => {
    setFormError(null);
    
    const validationError = validateForm();
    if (validationError) {
      setFormError(validationError);
      return;
    }

    let finalProjectId = null;
    if (formProjectValue) {
      if (typeof formProjectValue === "object" && formProjectValue.id) {
        finalProjectId = formProjectValue.id;
      } else if (typeof formProjectValue === "string" && formProjectValue.trim() !== "") {
        try {
          const newProject = await apiService.createProject({
            name: formProjectValue.trim(),
          });
          if (newProject && newProject.id) {
            finalProjectId = newProject.id;
            await fetchProjects();
          } else {
            throw new Error(newProject.error || t("dialogs.taskAction.errors.createProjectFailed"));
          }
        } catch (err) {
          setFormError(t("dialogs.taskAction.errors.projectCreation", { error: err.message }));
          return;
        }
      }
    }

    // Prepare task payload
    const taskPayload = {
      name: formData.name.trim(),
      description: formData.description.trim(),
      type: formData.type,
      project_id: finalProjectId || null,
      client_name: formData.client_name.trim(),
      target_website: formData.target_website.trim(),
      output_filename: formData.output_filename.trim(),
      model_name: formData.model_name || null,
      prompt_rule_id: formData.prompt_rule_id,
      page_count: isFileGeneration ? formData.page_count : null,
    };
    // Links to the client and website records; the update endpoint does not take them.
    if (!isEditMode) {
      if (formData.client_id) taskPayload.client_id = formData.client_id;
      if (formData.website_id) taskPayload.website_id = formData.website_id;
    }

    // BUG FIX: Create/update workflow_config with all FileGenerationPage fields
    if (isFileGeneration) {
      const workflowConfig = {
        page_count: formData.page_count,
        items: batchItems.split('\n').filter(item => item.trim()),
        prompt_rule_id: formData.prompt_rule_id,
        client_name: formData.client_name.trim(),
        client_website: clientWebsite.trim(),
        target_website: formData.target_website.trim(),
        insert_content: insertPosition !== "none" ? insertContent : null,
        insert_position: insertPosition !== "none" ? insertPosition : null,
        // Enhanced CSV generation parameters (Phase 1)
        client_notes: clientNotes.trim(),
        competitor_url: competitorUrl.trim(),
        target_word_count: targetWordCount,
        concurrent_workers: concurrentWorkers,
        batch_size: batchSize,
      };
      taskPayload.workflow_config = JSON.stringify(workflowConfig);
    } else if (isEditMode && taskData && taskData.workflow_config) {
      // Preserve existing workflow_config for non-file-generation tasks
      taskPayload.workflow_config = taskData.workflow_config;
    }

    // A new task this form starts itself (below) is created queued. Both task
    // schedulers run any pending task with no job_id within seconds, so a
    // pending create followed by the start ran the task twice.
    const startsHere = !isEditMode && (isFileGeneration || isCodeGeneration) && formData.auto_start_job;
    if (startsHere) taskPayload.status = "queued";

    try {
      let createdTask;
      
      if (isEditMode) {
        createdTask = await onSave(formData.id, taskPayload);
      } else {
        createdTask = await onSave(null, taskPayload);
      }

      // If this is a new task with auto-start enabled, start the job
      if (startsHere && createdTask) {
        setIsCreatingJob(true);
        try {
          if (isFileGeneration) {
            await startFileGenerationJob(createdTask);
          } else if (isCodeGeneration) {
            await startCodeGenerationJob(createdTask);
          }
        } catch (jobError) {
          console.error("Failed to start job:", jobError);
          // No scheduler takes a queued task, so mark it failed instead of leaving it waiting.
          fetch(`/api/tasks/${createdTask.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: "failed" }),
          }).catch(() => {});
          setFormError(t("dialogs.taskAction.errors.jobStartAfterCreate", { error: jobError.message }));
          setIsCreatingJob(false);
          return;
        }
        setIsCreatingJob(false);
      }

      // Notify parent component
      if (onTaskCreated && createdTask) {
        onTaskCreated(createdTask);
      }

      onClose();
    } catch (err) {
      setFormError(isEditMode ? t("dialogs.taskAction.errors.updateFailed", { error: err.message }) : t("dialogs.taskAction.errors.createFailed", { error: err.message }));
    }
  };

  // Start code generation job
  const startCodeGenerationJob = async (task) => {
    try {
      // Use the task scheduler to start the task directly
      const response = await fetch(`/api/tasks/${task.id}/start`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || t("dialogs.taskAction.errors.startCodeGeneration"));
      }

      const result = await response.json();
      console.log("Code generation task started:", result);
      return result;
    } catch (error) {
      console.error("Error starting code generation task:", error);
      throw error;
    }
  };

  // Start file generation job using generateStructuredCSV
  const startFileGenerationJob = async (task) => {
    // BUG FIX #10: Validate page_count and other required fields
    if (!task || !task.id) {
      throw new Error(t("dialogs.taskAction.errors.invalidTask"));
    }

    const pageCount = task.page_count && typeof task.page_count === 'number' && task.page_count > 0
      ? task.page_count
      : 50; // Default to 50 if invalid

    const clientName = task.client_name || 'Unknown Client';
    const taskName = task.name || 'Unnamed Task';

    // Parse workflow_config to get batch items if available
    let workflowConfig = {};
    try {
      if (task.workflow_config && typeof task.workflow_config === 'string') {
        workflowConfig = JSON.parse(task.workflow_config);
      } else if (task.workflow_config && typeof task.workflow_config === 'object') {
        workflowConfig = task.workflow_config;
      }
    } catch (parseError) {
      console.error('Failed to parse workflow_config:', parseError);
    }

    // Use items from workflow_config if available, otherwise generate generic topics
    let topics = [];
    if (workflowConfig.items && Array.isArray(workflowConfig.items) && workflowConfig.items.length > 0) {
      topics = workflowConfig.items;
    } else {
      topics = Array.from({ length: pageCount }, (_, i) => `${clientName} content topic ${i + 1}`);
    }

    // Enhanced job payload with all advanced parameters
    const jobPayload = {
      output_filename: task.output_filename,
      topics: topics,
      num_items: pageCount,
      client: clientName,
      project: taskName,
      website: task.target_website || workflowConfig.target_website || "",
      client_notes: workflowConfig.client_notes || "",
      competitor_url: workflowConfig.competitor_url || "",
      concurrent_workers: task.concurrent_workers || workflowConfig.concurrent_workers || 3,
      target_word_count: task.target_word_count || workflowConfig.target_word_count || 1000,
      batch_size: task.batch_size || workflowConfig.batch_size || 10,
      prompt_rule_id: task.prompt_rule_id || null,
      model_name: task.model_name || null,
      existing_task_id: task.id, // Link to the database task
    };

    const result = await generateStructuredCSV(jobPayload);
    console.log("TaskActionModal: Job started successfully with generateStructuredCSV:", result);
    return result;
  };

  // Task management action handlers
  const handleStartJob = async () => {
    if (!taskData?.id) return;
    
    setIsStartingJob(true);
    try {
      const response = await fetch(`/api/tasks/${taskData.id}/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || t("dialogs.taskAction.errors.startTaskFallback"));
      }
      
      const result = await response.json();
      setFormError(null);
      
      // Update task data with new job_id
      if (result.task) {
        setFormData(prev => ({ ...prev, job_id: result.task.job_id, status: 'in-progress' }));
      }
      
    } catch (err) {
      setFormError(t("dialogs.taskAction.errors.startTask", { error: err.message }));
    } finally {
      setIsStartingJob(false);
    }
  };

  const handleStopJob = async () => {
    if (!taskData?.job_id) return;
    
    setIsStoppingJob(true);
    try {
      const response = await fetch(`/api/meta/cancel_job/${taskData.job_id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || t("dialogs.taskAction.errors.stopJobFallback"));
      }
      
      setFormError(null);
      
      // Update task status
      setFormData(prev => ({ ...prev, status: 'cancelled' }));
      
    } catch (err) {
      setFormError(t("dialogs.taskAction.errors.stopJob", { error: err.message }));
    } finally {
      setIsStoppingJob(false);
    }
  };

  const handleDuplicateTask = async () => {
    if (!taskData?.id) return;
    
    setIsDuplicating(true);
    try {
      const response = await fetch(`/api/tasks/${taskData.id}/duplicate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || t("dialogs.taskAction.errors.duplicateFallback"));
      }
      
      const result = await response.json();
      setFormError(null);
      
      if (onTaskDuplicated) {
        onTaskDuplicated(result);
      }
      
    } catch (err) {
      setFormError(t("dialogs.taskAction.errors.duplicate", { error: err.message }));
    } finally {
      setIsDuplicating(false);
    }
  };

  const handleDeleteTask = async () => {
    if (!taskData?.id) return;
    
    const confirmed = window.confirm(t("dialogs.taskAction.deleteConfirm", { name: taskData.name }));
    if (!confirmed) return;
    
    setIsDeleting(true);
    try {
      const response = await fetch(`/api/tasks/${taskData.id}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' }
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || t("dialogs.taskAction.errors.deleteFallback"));
      }
      
      setFormError(null);
      
      if (onTaskDeleted) {
        onTaskDeleted(taskData.id);
      }
      
      onClose();
      
    } catch (err) {
      setFormError(t("dialogs.taskAction.errors.delete", { error: err.message }));
    } finally {
      setIsDeleting(false);
    }
  };

  const handleDownloadFile = async () => {
    if (!taskData?.id) return;
    
    try {
      const response = await fetch(`/api/tasks/${taskData.id}/download`);
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || t("dialogs.taskAction.errors.downloadFallback"));
      }
      
      // Create blob and download
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = taskData.output_filename || 'task_output';
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      
    } catch (err) {
      setFormError(t("dialogs.taskAction.errors.download", { error: err.message }));
    }
  };

  const getTitleText = () => {
    if (isEditMode) {
      return t("dialogs.taskAction.manageTitle", { name: formData.name || t("dialogs.taskAction.notAvailable") });
    }
    return isFileGeneration ? t("dialogs.taskAction.createFileTitle") : t("dialogs.taskAction.createTitle");
  };

  const anyActionInProgress = isSaving || isCreatingJob || isDeleting || isDuplicating || isStartingJob || isStoppingJob;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="lg"
      aria-labelledby="task-action-modal-title"
    >
      <DialogTitle
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          pt: 1.5,
          pb: 1,
          m: 0,
        }}
      >
        <Typography variant="h6" component="div" noWrap>
          {getTitleText()}
        </Typography>
        <IconButton
          onClick={onClose}
          size="small"
          disabled={anyActionInProgress}
        >
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers>
        {formError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formError}
          </Alert>
        )}
        {Object.keys(lookupWarnings).length > 0 && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            {Object.values(lookupWarnings).join(". ")}.
          </Alert>
        )}

        {/* Task Management Section - Only show for existing tasks */}
        {isEditMode && (
          <Card sx={{ mb: 3, border: '1px solid', borderColor: 'divider' }}>
            <CardContent>
              <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <InfoIcon />
                {t("dialogs.taskAction.taskManagement")}
              </Typography>
              
              {/* Real-time Status and Progress */}
              <Box sx={{ mb: 2 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography variant="body2" color="text.secondary">
                      {t("dialogs.taskAction.status")}:
                    </Typography>
                    <Chip
                      label={realTimeStatus || t("dialogs.taskAction.unknown")}
                      size="small"
                      color={
                        isCompleted ? 'success' :
                        isFailed ? 'warning' :
                        isRunning ? 'info' : 'default'
                      }
                      variant={isRunning ? 'filled' : 'outlined'}
                    />
                  </Box>
                  {taskData?.job_id && (
                    <Typography variant="caption" color="text.secondary">
                      {t("dialogs.taskAction.jobId", { id: taskData.job_id })}
                    </Typography>
                  )}
                </Box>
                
                {isRunning && (
                  <Box>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                      <Typography variant="body2" color="text.secondary">
                        {t("dialogs.taskAction.progress", { progress: realTimeProgress })}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {processData?.message || t("dialogs.taskAction.processing")}
                      </Typography>
                    </Box>
                    <LinearProgress 
                      variant="determinate" 
                      value={realTimeProgress} 
                      sx={{ height: 8, borderRadius: 4 }}
                    />
                  </Box>
                )}
              </Box>

              {/* File Information */}
              {taskData?.output_filename && (
                <Box sx={{ mb: 2 }}>
                  <Typography variant="subtitle2" gutterBottom>
                    {t("dialogs.taskAction.outputFile", { name: taskData.output_filename })}
                  </Typography>
                  {isLoadingFileInfo ? (
                    <CircularProgress size={20} />
                  ) : fileInfo ? (
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Typography variant="body2" color="text.secondary">
                        {t("dialogs.taskAction.fileDetails", { size: fileInfo.file_size_mb, type: fileInfo.file_type, status: fileInfo.task_status })}
                      </Typography>
                      {fileInfo.can_download && (
                        <Button
                          size="small"
                          startIcon={<DownloadIcon />}
                          onClick={handleDownloadFile}
                          disabled={anyActionInProgress}
                        >
                          {t("dialogs.shared.download")}
                        </Button>
                      )}
                    </Box>
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      {t("dialogs.taskAction.notGenerated")}
                    </Typography>
                  )}
                </Box>
              )}

              <Divider sx={{ my: 2 }} />

              {/* Action Buttons */}
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                {/* Job Control Buttons */}
                {!isCompleted && !isFailed && (
                  <>
                    {!isRunning ? (
                      <Button
                        variant="contained"
                        color="success"
                        startIcon={<PlayArrowIcon />}
                        onClick={handleStartJob}
                        disabled={anyActionInProgress}
                        size="small"
                      >
                        {isStartingJob ? <CircularProgress size={16} /> : t("dialogs.taskAction.startJob")}
                      </Button>
                    ) : (
                      <Button
                        variant="contained"
                        color="warning"
                        startIcon={<StopIcon />}
                        onClick={handleStopJob}
                        disabled={anyActionInProgress}
                        size="small"
                      >
                        {isStoppingJob ? <CircularProgress size={16} /> : t("dialogs.taskAction.stopJob")}
                      </Button>
                    )}
                  </>
                )}

                {/* Management Actions */}
                <Button
                  variant="outlined"
                  startIcon={<FileCopyIcon />}
                  onClick={handleDuplicateTask}
                  disabled={anyActionInProgress}
                  size="small"
                >
                  {isDuplicating ? <CircularProgress size={16} /> : t("dialogs.taskAction.duplicate")}
                </Button>

                <Button
                  variant="outlined"
                  startIcon={<DeleteIcon />}
                  onClick={handleDeleteTask}
                  disabled={anyActionInProgress}
                  size="small"
                >
                  {isDeleting ? <CircularProgress size={16} /> : t("dialogs.shared.delete")}
                </Button>
              </Box>
            </CardContent>
          </Card>
        )}

        <Box component="form" noValidate autoComplete="off" sx={{ mt: 1 }}>
          <Grid container spacing={2}>
            {/* Basic Task Information */}
            <Grid item xs={12} md={8}>
              <TextField
                fullWidth
                margin="dense"
                label={t("dialogs.taskAction.taskName")}
                name="name"
                value={formData.name}
                onChange={handleInputChange}
                disabled={anyActionInProgress}
                required
              />
            </Grid>
            <Grid item xs={12} md={4}>
              <FormControl fullWidth margin="dense">
                <InputLabel>{t("dialogs.taskAction.taskType")}</InputLabel>
                <Select
                  name="type"
                  value={formData.type}
                  onChange={handleInputChange}
                  disabled={anyActionInProgress}
                  label={t("dialogs.taskAction.taskType")}
                >
                  {taskTypes().map((type) => (
                    <MenuItem key={type.value} value={type.value}>
                      <Box>
                        <Typography variant="body2">{type.label}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {type.description}
                        </Typography>
                      </Box>
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>

            <Grid item xs={12}>
              <TextField
                fullWidth
                margin="dense"
                label={['code_generation', 'content_generation', 'data_analysis'].includes(formData.type) ? t("dialogs.taskAction.prompt") : t("dialogs.taskAction.description")}
                name="description"
                value={formData.description}
                onChange={handleInputChange}
                disabled={anyActionInProgress}
                multiline
                rows={['code_generation', 'content_generation', 'data_analysis'].includes(formData.type) ? 6 : 3}
              />
            </Grid>

            {/* Project and Rule Selection */}
            <Grid item xs={12} md={6}>
              <Autocomplete
                options={projects}
                loading={isLoadingProjects}
                getOptionLabel={(option) => {
                  if (typeof option === "string") return option;
                  return option.name || t("dialogs.taskAction.projectId", { id: option.id });
                }}
                value={formProjectValue}
                onChange={handleProjectChange}
                freeSolo
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={t("dialogs.taskAction.project")}
                    margin="dense"
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <>
                          {isLoadingProjects ? <CircularProgress size={20} /> : null}
                          {params.InputProps.endAdornment}
                        </>
                      ),
                    }}
                  />
                )}
                disabled={anyActionInProgress || isLoadingProjects}
              />
            </Grid>
            <Grid item xs={12} md={6}>
              <Autocomplete
                options={availableRules}
                loading={isLoadingRules}
                getOptionLabel={(option) => option.name || t("dialogs.taskAction.ruleId", { id: option.id })}
                value={formRuleValue}
                onChange={handleRuleChange}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={t("dialogs.taskAction.promptRule")}
                    margin="dense"
                    helperText={t("dialogs.taskAction.promptRuleHelp")}
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <>
                          {isLoadingRules ? <CircularProgress size={20} /> : null}
                          {params.InputProps.endAdornment}
                        </>
                      ),
                    }}
                  />
                )}
                renderOption={(props, option) => (
                  <Box component="li" {...props}>
                    <Box>
                      <Typography variant="body2">{option.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {option.description || option.rule_text?.substring(0, 100) + "..."}
                      </Typography>
                    </Box>
                  </Box>
                )}
                disabled={anyActionInProgress || isLoadingRules}
              />
            </Grid>

            {/* File Generation Specific Fields */}
            {(isFileGeneration || isCodeGeneration) && (
              <>
                <Grid item xs={12}>
                  <Typography variant="h6" sx={{ mt: 2, mb: 1 }}>
                    {isFileGeneration ? t("dialogs.taskAction.fileSettings") : t("dialogs.taskAction.codeSettings")}
                  </Typography>
                </Grid>
                {isFileGeneration && (
                  <>
                  <Grid item xs={12}>
                    <Autocomplete
                      options={availableWebsites}
                      loading={isLoadingWebsites}
                    getOptionLabel={(option) => {
                      if (typeof option === "string") return option;
                      return option.url || t("dialogs.taskAction.websiteId", { id: option.id });
                    }}
                    value={selectedWebsite}
                    onChange={handleWebsiteSelection}
                    renderInput={(params) => (
                      <TextField
                        {...params}
                        label={t("dialogs.taskAction.selectWebsite")}
                        margin="dense"
                        helperText={t("dialogs.taskAction.selectWebsiteHelp")}
                        InputProps={{
                          ...params.InputProps,
                          endAdornment: (
                            <>
                              {isLoadingWebsites ? <CircularProgress size={20} /> : null}
                              {params.InputProps.endAdornment}
                            </>
                          ),
                        }}
                      />
                    )}
                    renderOption={(props, option) => (
                      <Box component="li" {...props}>
                        <Box>
                          <Typography variant="body2">{option.url}</Typography>
                          <Typography variant="caption" color="text.secondary">
                            {t("dialogs.taskAction.websiteOption", { client: option.client?.name || t("dialogs.taskAction.unknown"), project: option.project?.name || t("dialogs.taskAction.unknown") })}
                          </Typography>
                        </Box>
                      </Box>
                    )}
                    disabled={anyActionInProgress || isLoadingWebsites}
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.clientName")}
                    name="client_name"
                    value={formData.client_name}
                    onChange={handleInputChange}
                    disabled={anyActionInProgress}
                    required
                    helperText={t("dialogs.taskAction.clientNameHelp")}
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.targetWebsite")}
                    name="target_website"
                    value={formData.target_website}
                    onChange={handleInputChange}
                    disabled={anyActionInProgress}
                    placeholder="https://example.com"
                    helperText={t("dialogs.taskAction.targetWebsiteHelp")}
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.pageCount")}
                    name="page_count"
                    type="number"
                    value={formData.page_count}
                    onChange={handleInputChange}
                    disabled={anyActionInProgress}
                    inputProps={{ min: 1, max: 1000 }}
                  />
                </Grid>

                {/* BUG FIX #4: Add Client Website field (separate from Target Website) */}
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.clientWebsite")}
                    value={clientWebsite}
                    onChange={(e) => setClientWebsite(e.target.value)}
                    disabled={anyActionInProgress}
                    placeholder="https://client-website.com"
                    helperText={t("dialogs.taskAction.clientWebsiteHelp")}
                  />
                </Grid>

                {/* BUG FIX #1: Add Items/Topics field */}
                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.items")}
                    multiline
                    rows={4}
                    value={batchItems}
                    onChange={(e) => setBatchItems(e.target.value)}
                    disabled={anyActionInProgress}
                    placeholder="Tampa\nOrlando\nMiami\nJacksonville"
                    helperText={t("dialogs.taskAction.itemsHelp")}
                  />
                </Grid>

                {/* BUG FIX #2 & #3: Add Insert Content and Position fields */}
                <Grid item xs={12}>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    {t("dialogs.taskAction.contentInsertion")}
                  </Typography>
                  <FormControl component="fieldset" disabled={anyActionInProgress}>
                    <RadioGroup
                      row
                      value={insertPosition}
                      onChange={(e) => setInsertPosition(e.target.value)}
                    >
                      <FormControlLabel value="none" control={<Radio />} label={t("dialogs.taskAction.noInsert")} />
                      <FormControlLabel value="top" control={<Radio />} label={t("dialogs.taskAction.insertTop")} />
                      <FormControlLabel value="bottom" control={<Radio />} label={t("dialogs.taskAction.insertBottom")} />
                    </RadioGroup>
                  </FormControl>
                </Grid>

                {insertPosition !== "none" && (
                  <Grid item xs={12}>
                    <TextField
                      fullWidth
                      margin="dense"
                      label={t("dialogs.taskAction.insertContent")}
                      multiline
                      rows={3}
                      value={insertContent}
                      onChange={(e) => setInsertContent(e.target.value)}
                      disabled={anyActionInProgress}
                      placeholder={t("dialogs.taskAction.insertPlaceholder")}
                      helperText={t("dialogs.taskAction.insertHelp")}
                    />
                  </Grid>
                )}

                {/* Enhanced CSV Generation Parameters (Phase 1) */}
                <Grid item xs={12}>
                  <Typography variant="subtitle2" sx={{ mt: 2, mb: 1, fontWeight: 600 }}>
                    {t("dialogs.taskAction.advancedSettings")}
                  </Typography>
                </Grid>

                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.competitorUrl")}
                    value={competitorUrl}
                    onChange={(e) => setCompetitorUrl(e.target.value)}
                    disabled={anyActionInProgress}
                    placeholder="https://competitor-website.com"
                    helperText={t("dialogs.taskAction.competitorHelp")}
                  />
                </Grid>

                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.wordCount")}
                    type="number"
                    value={targetWordCount}
                    onChange={(e) => setTargetWordCount(parseInt(e.target.value) || 1000)}
                    disabled={anyActionInProgress}
                    inputProps={{ min: 100, max: 5000 }}
                    helperText={t("dialogs.taskAction.wordCountHelp")}
                  />
                </Grid>

                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.clientNotes")}
                    multiline
                    rows={3}
                    value={clientNotes}
                    onChange={(e) => setClientNotes(e.target.value)}
                    disabled={anyActionInProgress}
                    placeholder={t("dialogs.taskAction.clientNotesPlaceholder")}
                    helperText={t("dialogs.taskAction.clientNotesHelp")}
                  />
                </Grid>

                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.workers")}
                    type="number"
                    value={concurrentWorkers}
                    onChange={(e) => setConcurrentWorkers(parseInt(e.target.value) || 3)}
                    disabled={anyActionInProgress}
                    inputProps={{ min: 1, max: 10 }}
                    helperText={t("dialogs.taskAction.workersHelp")}
                  />
                </Grid>

                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.batchSize")}
                    type="number"
                    value={batchSize}
                    onChange={(e) => setBatchSize(parseInt(e.target.value) || 10)}
                    disabled={anyActionInProgress}
                    inputProps={{ min: 1, max: 50 }}
                    helperText={t("dialogs.taskAction.batchSizeHelp")}
                  />
                </Grid>
                  </>
                )}

                {/* Common fields for both file and code generation */}
                <Grid item xs={12} md={6}>
                  <TextField
                    fullWidth
                    margin="dense"
                    label={t("dialogs.taskAction.outputFilename")}
                    name="output_filename"
                    value={formData.output_filename}
                    onChange={handleInputChange}
                    disabled={anyActionInProgress}
                    required
                    helperText={isCodeGeneration ? t("dialogs.taskAction.outputFilenameHelp") : ""}
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <FormControl fullWidth margin="dense">
                    <InputLabel>{t("dialogs.taskAction.model")}</InputLabel>
                    <Select
                      name="model_name"
                      value={formData.model_name}
                      onChange={handleInputChange}
                      disabled={anyActionInProgress || isLoadingModels}
                      label={t("dialogs.taskAction.model")}
                    >
                      <MenuItem value="">
                        <em>{t("dialogs.taskAction.defaultModel")}</em>
                      </MenuItem>
                      {availableModels.map((model) => (
                        <MenuItem key={model.name} value={model.name}>
                          {model.name}
                        </MenuItem>
                      ))}
                      {/* Add current model if it's not in available models (for editing) */}
                      {formData.model_name && 
                       !availableModels.find(m => m.name === formData.model_name) && (
                        <MenuItem value={formData.model_name}>
                          {t("dialogs.taskAction.modelUnavailable", { model: formData.model_name })}
                        </MenuItem>
                      )}
                    </Select>
                  </FormControl>
                </Grid>
              </>
            )}

            {/* Auto-start option for file and code generation */}
            {!isEditMode && (isFileGeneration || isCodeGeneration) && (
              <Grid item xs={12}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <PlayArrowIcon fontSize="small" />
                  <Typography variant="body2">
                    {t("dialogs.taskAction.autoStart")}
                  </Typography>
                </Box>
              </Grid>
            )}
          </Grid>
        </Box>
      </DialogContent>

      <DialogActions sx={{ justifyContent: "space-between", px: 3, pb: 2, pt: 2 }}>
        <Box>
          {isCreatingJob && (
            <Typography variant="body2" color="text.secondary">
              {t("dialogs.taskAction.creatingAndStarting")}
            </Typography>
          )}
          {isStartingJob && (
            <Typography variant="body2" color="text.secondary">
              {t("dialogs.taskAction.startingJob")}
            </Typography>
          )}
          {isStoppingJob && (
            <Typography variant="body2" color="text.secondary">
              {t("dialogs.taskAction.stoppingJob")}
            </Typography>
          )}
          {isDuplicating && (
            <Typography variant="body2" color="text.secondary">
              {t("dialogs.taskAction.duplicating")}
            </Typography>
          )}
          {isDeleting && (
            <Typography variant="body2" color="text.secondary">
              {t("dialogs.taskAction.deleting")}
            </Typography>
          )}
        </Box>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button
            onClick={onClose}
            disabled={anyActionInProgress}
            color="inherit"
          >
            {isEditMode ? t("dialogs.shared.close") : t("dialogs.shared.cancel")}
          </Button>
          {!isEditMode && (
            <Button
              onClick={handleCreateTask}
              variant="contained"
              disabled={anyActionInProgress}
              startIcon={anyActionInProgress ? <CircularProgress size={20} /> : null}
            >
              {(isFileGeneration || isCodeGeneration) && formData.auto_start_job ? t("dialogs.taskAction.createAndStart") : t("dialogs.taskAction.createTask")}
            </Button>
          )}
          {isEditMode && (
            <Button
              onClick={handleCreateTask}
              variant="contained"
              disabled={anyActionInProgress}
              startIcon={anyActionInProgress ? <CircularProgress size={20} /> : null}
            >
              {t("dialogs.taskAction.saveChanges")}
            </Button>
          )}
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default TaskActionModal;