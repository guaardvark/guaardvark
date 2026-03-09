// frontend/src/components/forms/UnifiedTaskCreationForm.jsx
// Unified task creation form that combines TaskActionModal and FileGenerationPage functionality

import React, { useState, useEffect, useCallback } from "react";
import {
  Box,
  Button,
  Card,
  CardContent,
  Collapse,
  Divider,
  FormControl,
  FormControlLabel,
  Grid,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Switch,
  TextField,
  Typography,
  Autocomplete,
  CircularProgress,
  Alert,
  Chip,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import AddIcon from "@mui/icons-material/Add";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";

import * as apiService from "../../api";
import { getRules } from "../../api/ruleService";
import { getWebsites } from "../../api/websiteService";
import { getClients } from "../../api/clientService";
import { generateStructuredCSV } from "../../api/bulkGenerationService";
import { useStatus } from "../../contexts/StatusContext";

const TASK_TYPES = [
  { value: "file_generation", label: "File Generation (CSV)", description: "Generate CSV files with bulk content", quickAction: true },
  { value: "code_generation", label: "Code Generation", description: "Generate .jsx, .py, .js, and other code files", quickAction: true },
  { value: "content_generation", label: "Content Generation", description: "Generate individual content pieces" },
  { value: "bulk_content_generation", label: "Bulk Content Generation", description: "Generate bulk content with multiple items" },
  { value: "data_analysis", label: "Data Analysis", description: "Analyze and process data" },
  { value: "website_analysis", label: "Website Analysis", description: "Analyze website content and structure" },
  { value: "custom", label: "Custom Task", description: "Custom task configuration" },
];

// Quick actions moved to TaskPage header bar

const UnifiedTaskCreationForm = ({ 
  onTaskCreated, 
  onCancel,
  editingTask = null,
  isVisible = false 
}) => {
  const theme = useTheme();
  const { activeModel } = useStatus();
  const [isExpanded, setIsExpanded] = useState(isVisible);

  // Update expanded state when isVisible prop changes
  useEffect(() => {
    setIsExpanded(isVisible);
  }, [isVisible]);
  // selectedQuickAction removed - quick actions now in header
  
  // Form state
  const [formData, setFormData] = useState({
    name: "",
    description: "",
    type: "file_generation",
    client_id: "",
    project_id: "",
    website_id: "",
    competitor_url: "", // For enhanced context generation
    output_filename: "",
    model_name: activeModel?.name || "",
    prompt_rule_id: null,
    page_count: 50,
    auto_start_job: true,
    items: "", // For batch items
  });

  // Loading and data states
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [availableClients, setAvailableClients] = useState([]);
  const [availableProjects, setAvailableProjects] = useState([]);
  const [availableWebsites, setAvailableWebsites] = useState([]);
  const [availableRules, setAvailableRules] = useState([]);
  const [availableModels, setAvailableModels] = useState([]);
  
  // Selected values for dropdowns
  const [selectedClient, setSelectedClient] = useState(null);
  const [selectedProject, setSelectedProject] = useState(null);
  const [selectedWebsite, setSelectedWebsite] = useState(null);
  const [formRuleValue, setFormRuleValue] = useState(null);
  
  // Filtered data for cascading dropdowns
  const [filteredProjects, setFilteredProjects] = useState([]);
  const [filteredWebsites, setFilteredWebsites] = useState([]);

  const isEditMode = !!editingTask;
  const isFileGeneration = formData.type === "file_generation";

  // Load data
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [clients, projects, websites, rules, models] = await Promise.all([
        getClients(),
        apiService.getProjects(),
        getWebsites(),
        getRules({ active: true }),
        apiService.getAvailableModels(),
      ]);

      setAvailableClients(Array.isArray(clients) ? clients : []);
      setAvailableProjects(Array.isArray(projects) ? projects : []);
      setAvailableWebsites(Array.isArray(websites) ? websites : []);
      setAvailableRules(Array.isArray(rules) ? rules : []);
      setAvailableModels(Array.isArray(models) ? models : []);
    } catch (err) {
      setError(`Failed to load data: ${err.message}`);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isExpanded) {
      fetchData();
    }
  }, [isExpanded, fetchData]);

  // Initialize form for editing
  useEffect(() => {
    if (editingTask && availableClients.length > 0) {
      setFormData({
        name: editingTask.name || "",
        description: editingTask.description || "",
        type: editingTask.type || "file_generation",
        client_id: editingTask.client_id || editingTask.client_ref?.id || editingTask.client?.id || "",
        project_id: editingTask.project_id || editingTask.project?.id || "",
        website_id: editingTask.website_id || editingTask.website_ref?.id || editingTask.website?.id || "",
        competitor_url: editingTask.competitor_url || "",
        output_filename: editingTask.output_filename || "",
        model_name: editingTask.model_name || activeModel?.name || "",
        prompt_rule_id: editingTask.prompt_rule_id || null,
        page_count: editingTask.page_count || 50,
        auto_start_job: false,
        items: "",
      });
      
      // Set selected dropdown values for editing - handle both client_id and client_ref object
      const clientId = editingTask.client_id || editingTask.client_ref?.id || editingTask.client?.id;
      if (clientId) {
        const client = availableClients.find(c => c.id === clientId);
        if (client) {
          setSelectedClient(client);
          const clientProjects = availableProjects.filter(p => p.client_id === client.id);
          setFilteredProjects(clientProjects);
          
          const projectId = editingTask.project_id || editingTask.project?.id;
          if (projectId) {
            const project = clientProjects.find(p => p.id === projectId);
            if (project) {
              setSelectedProject(project);
              const projectWebsites = availableWebsites.filter(w => w.project_id === project.id);
              setFilteredWebsites(projectWebsites);
              
              const websiteId = editingTask.website_id || editingTask.website_ref?.id || editingTask.website?.id;
              if (websiteId) {
                const website = projectWebsites.find(w => w.id === websiteId);
                if (website) setSelectedWebsite(website);
              }
            }
          }
        }
      }
      
      setIsExpanded(true);
    }
  }, [editingTask, activeModel, availableClients, availableProjects, availableWebsites]);

  // Cascading dropdown handlers
  const handleClientChange = (event, newValue) => {
    setSelectedClient(newValue);
    setSelectedProject(null);
    setSelectedWebsite(null);
    
    if (newValue) {
      setFormData(prev => ({ 
        ...prev, 
        client_id: newValue.id,
        project_id: "",
        website_id: ""
      }));
      
      // Filter projects for this client
      const clientProjects = availableProjects.filter(project => 
        project.client_id === newValue.id
      );
      setFilteredProjects(clientProjects);
      setFilteredWebsites([]);
    } else {
      setFormData(prev => ({ 
        ...prev, 
        client_id: "",
        project_id: "",
        website_id: ""
      }));
      setFilteredProjects([]);
      setFilteredWebsites([]);
    }
  };

  const handleProjectChange = (event, newValue) => {
    setSelectedProject(newValue);
    setSelectedWebsite(null);
    
    if (newValue) {
      setFormData(prev => ({ 
        ...prev, 
        project_id: newValue.id,
        website_id: ""
      }));
      
      // Filter websites for this project
      const projectWebsites = availableWebsites.filter(website => 
        website.project_id === newValue.id
      );
      setFilteredWebsites(projectWebsites);
    } else {
      setFormData(prev => ({ 
        ...prev, 
        project_id: "",
        website_id: ""
      }));
      setFilteredWebsites([]);
    }
  };

  const handleWebsiteChange = (event, newValue) => {
    setSelectedWebsite(newValue);
    
    if (newValue) {
      setFormData(prev => ({ 
        ...prev, 
        website_id: newValue.id,
        output_filename: prev.output_filename || generateFilename(newValue.url)
      }));
    } else {
      setFormData(prev => ({ 
        ...prev, 
        website_id: ""
      }));
    }
  };

  // Helper function to generate filename from website URL
  const generateFilename = (url) => {
    if (!url) return "";
    const domain = url.replace(/https?:\/\/(www\.)?/, '').replace(/\/$/, '');
    const cleanDomain = domain.replace(/^www\./, '');
    return `${cleanDomain}_001.csv`;
  };

  // Form handlers
  const handleInputChange = (event) => {
    const { name, value, type, checked } = event.target;
    setFormData(prev => ({ 
      ...prev, 
      [name]: type === 'checkbox' ? checked : value 
    }));
  };

  // Old handleWebsiteSelection removed - now using cascading dropdowns

  // Validation
  const validateForm = () => {
    if (!formData.name.trim()) return "Task name is required";
    if (isFileGeneration) {
      if (!formData.client_id) return "Client is required for file generation";
      if (!formData.output_filename.trim()) return "Output filename is required";
    }
    return null;
  };

  // Submit handlers
  const handleCreateTask = async () => {
    const validationError = validateForm();
    if (validationError) {
      setError(validationError);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      // Create task
      const selectedClientData = availableClients.find(c => c.id === formData.client_id);
      const selectedWebsiteData = availableWebsites.find(w => w.id === formData.website_id);
      
      const taskPayload = {
        name: formData.name.trim(),
        description: formData.description.trim(),
        type: formData.type,
        client_id: formData.client_id || null,
        project_id: formData.project_id || null,
        website_id: formData.website_id || null,
        client_name: selectedClientData?.name || "",
        target_website: selectedWebsiteData?.url || "",
        competitor_url: formData.competitor_url.trim() || "",
        output_filename: formData.output_filename.trim(),
        model_name: formData.model_name || null,
        prompt_rule_id: formData.prompt_rule_id,
        page_count: isFileGeneration ? formData.page_count : null,
      };

      const createdTask = await apiService.createTask(taskPayload);

      // Auto-start job if enabled and file generation
      if (!isEditMode && isFileGeneration && formData.auto_start_job && createdTask) {
        await startFileGenerationJob(createdTask);
      }

      // Reset form
      handleReset();
      
      if (onTaskCreated) {
        onTaskCreated(createdTask);
      }
    } catch (err) {
      setError(`Failed to create task: ${err.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const startFileGenerationJob = async (task) => {
    const items = formData.items.split('\n').filter(item => item.trim());
    const topics = items.length > 0 ? items : 
      Array.from({ length: task.page_count }, (_, i) => `${task.client_name} content topic ${i + 1}`);

    await generateStructuredCSV({
      output_filename: task.output_filename,
      client: task.client_name,
      project: task.name,
      website: task.target_website || "",
      competitor_url: task.competitor_url || "",
      topics,
      num_items: task.page_count,
      target_word_count: 500,
      concurrent_workers: 5,
      batch_size: 25,
      model_name: task.model_name || activeModel?.name,
      existing_task_id: task.id,  // Pass existing task ID to prevent duplicate creation
    });
  };

  const handleDirectGeneration = async () => {
    const validationError = validateForm();
    if (validationError) {
      setError(validationError);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const items = formData.items.split('\n').filter(item => item.trim());
      const clientName = selectedClient?.name || "";
      const websiteUrl = selectedWebsite?.url || "";
      const topics = items.length > 0 ? items : 
        Array.from({ length: formData.page_count }, (_, i) => `${clientName} content topic ${i + 1}`);

      await generateStructuredCSV({
        output_filename: formData.output_filename,
        client: clientName,
        project: formData.name || "Direct Generation",
        website: websiteUrl,
        competitor_url: formData.competitor_url.trim() || "",
        topics,
        num_items: formData.page_count,
        target_word_count: 500,
        concurrent_workers: 5,
        batch_size: 25,
        model_name: formData.model_name || activeModel?.name,
      });

      handleReset();
      if (onTaskCreated) {
        onTaskCreated({ message: "Direct generation started" });
      }
    } catch (err) {
      setError(`Failed to start generation: ${err.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleReset = () => {
    setFormData({
      name: "",
      description: "",
      type: "file_generation",
      client_id: "",
      project_id: "",
      website_id: "",
      competitor_url: "",
      output_filename: "",
      model_name: activeModel?.name || "",
      prompt_rule_id: null,
      page_count: 50,
      auto_start_job: true,
      items: "",
    });
    setSelectedClient(null);
    setSelectedProject(null);
    setSelectedWebsite(null);
    setFilteredProjects([]);
    setFilteredWebsites([]);
    setFormRuleValue(null);
    setError(null);
    setIsExpanded(false);
    
    // Also reset editing state
    if (onCancel) {
      onCancel();
    }
  };

  if (!isExpanded) {
    return null;
  }

  return (
    <Card sx={{ mb: 2 }}>
      <CardContent>
        <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 2 }}>
          <Typography variant="h6">
            {isEditMode ? `Edit Task: ${editingTask?.name || 'Task'}` : "Create New Task"}
          </Typography>
          <IconButton onClick={() => setIsExpanded(false)}>
            <ExpandLessIcon />
          </IconButton>
        </Box>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        <Grid container spacing={2}>
          {/* Basic Task Info */}
          <Grid item xs={12} md={8}>
            <TextField
              fullWidth
              label="Task Name"
              name="name"
              value={formData.name}
              onChange={handleInputChange}
              disabled={isLoading}
              required
            />
          </Grid>
          <Grid item xs={12} md={4}>
            <FormControl fullWidth>
              <InputLabel>Task Type</InputLabel>
              <Select
                name="type"
                value={formData.type}
                onChange={handleInputChange}
                disabled={isLoading}
                label="Task Type"
              >
                {TASK_TYPES.map((type) => (
                  <MenuItem key={type.value} value={type.value}>
                    {type.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>

          {/* File Generation Specific Fields */}
          {isFileGeneration && (
            <>
              <Grid item xs={12}>
                <Divider sx={{ my: 1 }}>
                  <Chip label="File Generation Settings" size="small" />
                </Divider>
              </Grid>
              
              <Grid item xs={12} md={4}>
                <Autocomplete
                  options={availableClients}
                  getOptionLabel={(option) => option.name || ""}
                  value={selectedClient}
                  onChange={handleClientChange}
                  disabled={isLoading}
                  isOptionEqualToValue={(option, value) => {
                    // Compare by ID to handle object reference differences
                    if (!option || !value) return false;
                    return option.id === value.id;
                  }}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label="Client"
                      required
                      helperText="Select client to filter projects"
                      data-testid="client-autocomplete"
                    />
                  )}
                />
              </Grid>
              
              <Grid item xs={12} md={4}>
                <Autocomplete
                  options={filteredProjects}
                  getOptionLabel={(option) => option.name || ""}
                  value={selectedProject}
                  onChange={handleProjectChange}
                  disabled={isLoading || !selectedClient}
                  isOptionEqualToValue={(option, value) => {
                    // Compare by ID to handle object reference differences
                    if (!option || !value) return false;
                    return option.id === value.id;
                  }}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label="Project"
                      helperText="Filtered by selected client"
                      data-testid="project-autocomplete"
                    />
                  )}
                />
              </Grid>
              
              <Grid item xs={12} md={4}>
                <Autocomplete
                  options={filteredWebsites}
                  getOptionLabel={(option) => option.url || ""}
                  value={selectedWebsite}
                  onChange={handleWebsiteChange}
                  disabled={isLoading || !selectedProject}
                  isOptionEqualToValue={(option, value) => {
                    // Compare by ID to handle object reference differences
                    if (!option || !value) return false;
                    return option.id === value.id;
                  }}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label="Website"
                      helperText="Filtered by selected project"
                      data-testid="website-autocomplete"
                    />
                  )}
                />
              </Grid>
              
              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Competitor/Target Website URL (Optional)"
                  name="competitor_url"
                  value={formData.competitor_url}
                  onChange={handleInputChange}
                  disabled={isLoading}
                  placeholder="https://competitor.com/products"
                  helperText="URL to analyze for context and competitive intelligence"
                />
              </Grid>
              
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="Output Filename"
                  name="output_filename"
                  value={formData.output_filename}
                  onChange={handleInputChange}
                  disabled={isLoading}
                  required
                  placeholder="client_domain_001.csv"
                />
              </Grid>

              <Grid item xs={12} md={4}>
                <TextField
                  fullWidth
                  type="number"
                  label="Page Count"
                  name="page_count"
                  value={formData.page_count}
                  onChange={handleInputChange}
                  disabled={isLoading}
                  inputProps={{ min: 1, max: 1000 }}
                />
              </Grid>

              {/* Target Website removed - now auto-populated from Website dropdown */}

              <Grid item xs={12} md={6}>
                <FormControl fullWidth>
                  <InputLabel>Model (Optional)</InputLabel>
                  <Select
                    name="model_name"
                    value={formData.model_name}
                    onChange={handleInputChange}
                    disabled={isLoading}
                    label="Model (Optional)"
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
                <TextField
                  fullWidth
                  multiline
                  rows={3}
                  label="Items to Process (one per line, optional)"
                  name="items"
                  value={formData.items}
                  onChange={handleInputChange}
                  disabled={isLoading}
                  placeholder="Tampa&#10;Orlando&#10;Miami"
                />
              </Grid>

              <Grid item xs={12}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={formData.auto_start_job}
                      onChange={handleInputChange}
                      name="auto_start_job"
                      disabled={isLoading}
                    />
                  }
                  label="Auto-start job after creating task"
                />
              </Grid>
            </>
          )}

          {/* Description */}
          <Grid item xs={12}>
            <TextField
              fullWidth
              multiline
              rows={2}
              label="Description"
              name="description"
              value={formData.description}
              onChange={handleInputChange}
              disabled={isLoading}
            />
          </Grid>
        </Grid>

        {/* Action Buttons */}
        <Box sx={{ display: "flex", gap: 1, mt: 3, justifyContent: "flex-end" }}>
          <Button
            variant="outlined"
            onClick={onCancel || handleReset}
            disabled={isLoading}
          >
            Cancel
          </Button>
          
          {isFileGeneration && !isEditMode && formData.auto_start_job === false && (
            <Button
              variant="outlined"
              color="secondary"
              onClick={handleDirectGeneration}
              disabled={isLoading}
              startIcon={<PlayArrowIcon />}
            >
              Generate Now
            </Button>
          )}
          
          <Button
            variant="contained"
            onClick={handleCreateTask}
            disabled={isLoading}
            startIcon={isLoading ? <CircularProgress size={20} /> : <AddIcon />}
          >
            {isEditMode ? "Save Changes" : "Create Task"}
          </Button>
        </Box>
      </CardContent>
    </Card>
  );
};

export default UnifiedTaskCreationForm;