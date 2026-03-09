// CODE_FOLDER/frontend/src/components/modals/WebsiteActionModal.jsx
// Version 1.3:
// - Fixed infinite loop caused by useEffect dependencies.
// - Separated data fetching effects from form initialization effects.
// Based on v1.2.

import React, { useState, useEffect, useCallback } from "react";
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
  Tabs,
  Tab,
  Typography,
  Divider,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  IconButton,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";

import * as apiService from "../../api";
import * as wordpressService from "../../api/wordpressService";
import { BRAND_TONE_OPTIONS, SOCIAL_PLATFORMS } from "../../config/defaults";
import { validateSiteMeta } from "../../utils/validation";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import RefreshIcon from "@mui/icons-material/Refresh";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";

const WebsiteActionModal = ({
  open,
  onClose,
  websiteData,
  onSave,
  onDelete,
  isSaving,
  // projects prop removed as modal fetches its own
}) => {
  const [formData, setFormData] = useState({
    id: null,
    url: "",
    sitemap_url: "",
    competitor_url: "",
    project_id: "",
    client_id: "",
  });

  // Client metadata form data
  const [clientMeta, setClientMeta] = useState({
    name: "",
    phone: "",
    email: "",
    contact_url: "",
    location: "",
    primary_service: "",
    secondary_service: "",
    brand_tone: "neutral",
    business_hours: "",
    social_links: [],
  });
  const [formError, setFormError] = useState(null);

  const [projectsForDropdown, setProjectsForDropdown] = useState([]);
  const [isLoadingProjects, setIsLoadingProjects] = useState(false);
  const [formProjectValue, setFormProjectValue] = useState(null); // For Project Autocomplete

  const [clientsForDropdown, setClientsForDropdown] = useState([]);
  const [isLoadingClients, setIsLoadingClients] = useState(false);
  const [formClientValue, setFormClientValue] = useState(null); // For Client Autocomplete
  const [activeTab, setActiveTab] = useState(0); // Tab navigation: 0=Website, 1=WordPress
  const [validationErrors, setValidationErrors] = useState(null);
  
  // WordPress integration state
  const [wpSite, setWpSite] = useState(null);
  const [isLoadingWpSite, setIsLoadingWpSite] = useState(false);
  const [wpFormData, setWpFormData] = useState({
    url: "",
    site_name: "",
    api_key: "",
    connection_type: "llamanator", // Default to LLAMANATOR2
    status: "active",
  });
  const [wpFormError, setWpFormError] = useState(null);
  const [isTestingWpConnection, setIsTestingWpConnection] = useState(false);
  const [isSavingWpSite, setIsSavingWpSite] = useState(false);

  const isEditMode = Boolean(formData.id && websiteData);

  // Stable useCallback for fetching projects
  const fetchProjectsForDropdown = useCallback(async () => {
    setIsLoadingProjects(true);
    try {
      const projectList = await apiService.getProjects();
      if (projectList.error)
        throw new Error(projectList.error.message || projectList.error);
      setProjectsForDropdown(
        Array.isArray(projectList)
          ? projectList.map((p) => ({ id: p.id, name: p.name }))
          : [],
      );
    } catch (err) {
      console.error("Error fetching projects for Website modal:", err);
      setProjectsForDropdown([]);
      // Optionally set a specific error for project loading if needed
    } finally {
      setIsLoadingProjects(false);
    }
  }, []); // Empty dependency array means this function reference is stable

  // Stable useCallback for fetching clients
  const fetchClientsForDropdown = useCallback(async () => {
    setIsLoadingClients(true);
    try {
      const clientList = await apiService.getClients();
      if (clientList.error)
        throw new Error(clientList.error.message || clientList.error);
      setClientsForDropdown(
        Array.isArray(clientList) ? clientList : [],
      );
    } catch (err) {
      console.error("Error fetching clients for Website modal:", err);
      setClientsForDropdown([]);
    } finally {
      setIsLoadingClients(false);
    }
  }, []); // Empty dependency array means this function reference is stable

  // Fetch WordPress site for this website
  const fetchWordPressSite = useCallback(async () => {
    if (!formData.id) return;
    setIsLoadingWpSite(true);
    try {
      const response = await wordpressService.getWordPressSites();
      if (response.success && response.data) {
        const wpSiteForWebsite = response.data.find(
          (site) => site.website_id === formData.id
        );
        if (wpSiteForWebsite) {
          setWpSite(wpSiteForWebsite);
          setWpFormData({
            url: wpSiteForWebsite.url || "",
            site_name: wpSiteForWebsite.site_name || "",
            api_key: "", // Don't populate for security
            connection_type: wpSiteForWebsite.connection_type || "llamanator",
            status: wpSiteForWebsite.status || "active",
          });
        } else {
          setWpSite(null);
          // Use websiteData.url if available, otherwise formData.url
          const defaultUrl = websiteData?.url || formData.url || "";
          setWpFormData({
            url: defaultUrl,
            site_name: "",
            api_key: "",
            connection_type: "llamanator", // Default to LLAMANATOR2
            status: "active",
          });
        }
      }
    } catch (err) {
      console.error("Error fetching WordPress site:", err);
      setWpSite(null);
    } finally {
      setIsLoadingWpSite(false);
    }
  }, [formData.id, formData.url, websiteData]); // Include websiteData and formData.url for default URL

  // Effect to fetch dropdown data when modal opens
  useEffect(() => {
    if (open) {
      fetchProjectsForDropdown();
      fetchClientsForDropdown();
    }
  }, [open, fetchProjectsForDropdown, fetchClientsForDropdown]); // Depends on open and stable fetch functions
  
  // Fetch WordPress site when formData.id is available
  useEffect(() => {
    if (open && isEditMode && formData.id) {
      fetchWordPressSite();
    } else {
      // Reset WordPress state if not in edit mode or no formData.id
      setWpSite(null);
      setWpFormData({
        url: formData.url || "",
        site_name: "",
        api_key: "",
        connection_type: "llamanator",
        status: "active",
      });
      setWpFormError(null);
    }
  }, [open, isEditMode, formData.id, fetchWordPressSite]);

  // Effect to initialize/reset form when modal opens or websiteData changes
  useEffect(() => {
    if (open) {
      if (websiteData) {
        // Edit mode
        setFormData({
          id: websiteData.id,
          url: websiteData.url || "",
          sitemap_url: websiteData.sitemap_url || websiteData.sitemap || "",
          competitor_url: websiteData.competitor_url || "",
          project_id: websiteData.project_id || "",
          client_id: websiteData.client_id || "",
        });

        // Initialize client metadata if available
        if (websiteData.client) {
          setClientMeta({
            name: websiteData.client.name || "",
            phone: websiteData.client.phone || "",
            email: websiteData.client.email || "",
            contact_url: websiteData.client.contact_url || "",
            location: websiteData.client.location || "",
            primary_service: websiteData.client.primary_service || "",
            secondary_service: websiteData.client.secondary_service || "",
            brand_tone: websiteData.client.brand_tone || "neutral",
            business_hours: websiteData.client.business_hours || "",
            // FIX: Parse social_links if it's a JSON string with error handling
            social_links: (() => {
              try {
                if (typeof websiteData.client.social_links === 'string' && websiteData.client.social_links) {
                  return JSON.parse(websiteData.client.social_links);
                }
                return Array.isArray(websiteData.client.social_links) ? websiteData.client.social_links : [];
              } catch (e) {
                console.warn("Failed to parse social_links:", e);
                return [];
              }
            })(),
          });
        }
      } else {
        // Add mode
        setFormData({
          id: null,
          url: "",
          sitemap_url: "",
          competitor_url: "",
          project_id: "",
          client_id: "",
        });
        setClientMeta({
          name: "",
          phone: "",
          email: "",
          contact_url: "",
          location: "",
          primary_service: "",
          secondary_service: "",
          brand_tone: "neutral",
          business_hours: "",
          social_links: [],
        });
        setFormClientValue(null); // Reset client autocomplete value
        setFormProjectValue(null);
      }
      setFormError(null); // Clear previous errors
    } else {
      // Modal is closed, reset everything
      setFormData({
        id: null,
        url: "",
        sitemap_url: "",
        competitor_url: "",
        project_id: "",
        client_id: "",
      });
      setClientMeta({
        name: "",
        phone: "",
        email: "",
        contact_url: "",
        location: "",
        primary_service: "",
        secondary_service: "",
        brand_tone: "neutral",
        business_hours: "",
        social_links: [],
      });
      setFormClientValue(null);
      setFormProjectValue(null);
      setFormError(null);
      // Reset WordPress state when modal closes
      setWpSite(null);
      setWpFormData({
        url: "",
        site_name: "",
        api_key: "",
        connection_type: "llamanator",
        status: "active",
      });
      setWpFormError(null);
      setActiveTab(0); // Reset to first tab
    }
  }, [open, websiteData]); // Only depend on open and websiteData

  // Separate effect to set client value when dropdown is populated
  useEffect(() => {
    if (open && websiteData && clientsForDropdown.length > 0 && websiteData.client_id) {
      const clientObj = clientsForDropdown.find(
        (c) => String(c.id) === String(websiteData.client_id),
      );
      // Only update if the value is actually different to prevent loops
      if (formClientValue?.id !== clientObj?.id) {
        setFormClientValue(clientObj || null);
      }
    }
  }, [open, websiteData?.client_id, clientsForDropdown.length]); // Use websiteData?.client_id to prevent dependency on full object

  // Sync formProjectValue when projects are loaded or project_id changes
  useEffect(() => {
    if (open) {
      if (formData.project_id && projectsForDropdown.length > 0) {
        const projObj = projectsForDropdown.find(
          (p) => String(p.id) === String(formData.project_id),
        );
        if (
          projObj &&
          (!formProjectValue || formProjectValue.id !== projObj.id)
        ) {
          setFormProjectValue(projObj);
        }
      } else if (!formData.project_id) {
        setFormProjectValue(null);
      }
    }
  }, [open, projectsForDropdown, formData.project_id]);

  const handleInputChange = (event) => {
    const { name, value } = event.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    if (
      name === "url" &&
      value.trim() &&
      (value.startsWith("http://") || value.startsWith("https://"))
    ) {
      setFormError(null);
    }
  };

  const handleProjectChange = (event, newValue) => {
    setFormProjectValue(newValue);
    if (newValue && typeof newValue === "object" && newValue.id) {
      setFormData((prev) => ({ ...prev, project_id: newValue.id }));
    } else if (!newValue) {
      setFormData((prev) => ({ ...prev, project_id: "" }));
    }
    if (newValue && formError && formError.toLowerCase().includes("project")) {
      setFormError(null);
    }
  };

  const handleClientChange = (event, newValue) => {
    setFormClientValue(newValue);
    if (newValue && typeof newValue === 'object' && newValue.id) {
      // If existing client selected, populate client metadata from the full client object
      const client = clientsForDropdown.find(c => c.id === newValue.id);
      if (client) {
        setClientMeta({
          name: client.name || "",
          phone: client.phone || "",
          email: client.email || "",
          contact_url: client.contact_url || "",
          location: client.location || "",
          primary_service: client.primary_service || "",
          secondary_service: client.secondary_service || "",
          brand_tone: client.brand_tone || "neutral",
          business_hours: client.business_hours || "",
          // FIX: Parse social_links if it's a string with error handling
          social_links: (() => {
            try {
              if (typeof client.social_links === 'string' && client.social_links) {
                return JSON.parse(client.social_links);
              }
              return Array.isArray(client.social_links) ? client.social_links : [];
            } catch (e) {
              console.warn("Failed to parse social_links:", e);
              return [];
            }
          })(),
        });
      }
    } else if (typeof newValue === 'string') {
      // New client name entered
      setClientMeta(prev => ({ ...prev, name: newValue }));
    }
    if (newValue && formError && formError.toLowerCase().includes("client")) {
      setFormError(null);
    }
  };

  const handleClientMetaChange = (field, value) => {
    setClientMeta(prev => ({ ...prev, [field]: value }));
    setValidationErrors(null);
  };

  const handleSocialLinkAdd = () => {
    setClientMeta(prev => ({
      ...prev,
      social_links: [...prev.social_links, { platform: '', url: '' }]
    }));
  };

  const handleSocialLinkChange = (index, field, value) => {
    setClientMeta(prev => ({
      ...prev,
      social_links: prev.social_links.map((link, i) =>
        i === index ? { ...link, [field]: value } : link
      )
    }));
  };

  const handleSocialLinkRemove = (index) => {
    setClientMeta(prev => ({
      ...prev,
      social_links: prev.social_links.filter((_, i) => i !== index)
    }));
  };

  const handleTabChange = (event, newValue) => {
    setActiveTab(newValue);
    // Clear WordPress errors when switching tabs
    if (newValue === 0) {
      setWpFormError(null);
    }
  };
  
  const handleWpInputChange = (field, value) => {
    setWpFormData((prev) => ({ ...prev, [field]: value }));
    if (wpFormError) setWpFormError(null);
  };
  
  const handleTestWpConnection = async () => {
    if (!wpFormData.url || !wpFormData.username || !wpFormData.api_key) {
      setWpFormError("URL, username, and API key are required for testing");
      return;
    }
    
    setIsTestingWpConnection(true);
    setWpFormError(null);
    try {
      // First register a temporary site to test, then delete it
      const testPayload = {
        url: wpFormData.url,
        username: wpFormData.username,
        api_key: wpFormData.api_key,
      };
      
      // Use the test endpoint if available, or register then delete
      if (wpSite) {
        const response = await wordpressService.testWordPressConnection(wpSite.id);
        if (response.success) {
          setWpFormError(null);
          alert("Connection test successful!");
        } else {
          setWpFormError(response.error || "Connection test failed. Please check your credentials.");
        }
      } else {
        // For new sites, we need to register first to test
        // The backend will test the connection during registration
        setWpFormError("Please register the site first to test connection");
      }
    } catch (err) {
      setWpFormError(`Connection test error: ${err.message}`);
    } finally {
      setIsTestingWpConnection(false);
    }
  };
  
  const handleSaveWpSite = async () => {
    // Validate required fields
    if (!wpFormData.url || !wpFormData.url.trim()) {
      setWpFormError("WordPress URL is required");
      return;
    }
    if (!wpFormData.url.startsWith("http://") && !wpFormData.url.startsWith("https://")) {
      setWpFormError("URL must start with http:// or https://");
      return;
    }
    if (!wpFormData.api_key && !wpSite) {
      setWpFormError("LLAMANATOR2 API key is required for new WordPress sites");
      return;
    }
    
    setIsSavingWpSite(true);
    setWpFormError(null);
    try {
      const payload = {
        url: wpFormData.url.trim(),
        site_name: wpFormData.site_name?.trim() || null,
        api_key: wpFormData.api_key?.trim() || undefined,
        connection_type: wpFormData.connection_type || "llamanator",
        client_id: formData.client_id || null,
        project_id: formData.project_id || null,
        website_id: formData.id || null, // Allow null for standalone WordPress sites
        status: wpFormData.status,
      };
      
      let response;
      if (wpSite) {
        response = await wordpressService.updateWordPressSite(wpSite.id, payload);
      } else {
        response = await wordpressService.registerWordPressSite(payload);
      }
      
      if (response.success) {
        setWpFormError(null);
        await fetchWordPressSite();
        alert(wpSite ? "WordPress site updated successfully!" : "WordPress site registered successfully!");
      } else {
        setWpFormError(response.error || "Failed to save WordPress site");
      }
    } catch (err) {
      setWpFormError(err.message || "Failed to save WordPress site");
    } finally {
      setIsSavingWpSite(false);
    }
  };

  const handleSaveClick = async () => {
    setFormError(null);
    let currentFormError = null;

    if (!formData.url.trim()) currentFormError = "Website URL is required.";
    else if (
      !formData.url.startsWith("http://") &&
      !formData.url.startsWith("https://")
    )
      currentFormError = "URL must start with http:// or https://";
    else if (!formProjectValue)
      currentFormError = "Project assignment is required.";

    if (currentFormError) {
      setFormError(currentFormError);
      return;
    }

    let finalClientId = null;
    if (formClientValue) {
      if (typeof formClientValue === "object" && formClientValue.id) {
        finalClientId = formClientValue.id;
        // FIX: Update existing client with metadata if it has changed
        try {
          if (clientMeta.name || clientMeta.phone || clientMeta.email) {
            const updatePayload = {
              name: clientMeta.name || formClientValue.name,
              phone: clientMeta.phone || null,
              email: clientMeta.email || null,
              contact_url: clientMeta.contact_url || null,
              location: clientMeta.location || null,
              primary_service: clientMeta.primary_service || null,
              secondary_service: clientMeta.secondary_service || null,
              brand_tone: clientMeta.brand_tone || 'neutral',
              business_hours: clientMeta.business_hours || null,
              social_links: clientMeta.social_links && clientMeta.social_links.length > 0 
                ? (typeof clientMeta.social_links === 'string' 
                    ? clientMeta.social_links 
                    : JSON.stringify(clientMeta.social_links))
                : null
            };
            await apiService.updateClient(formClientValue.id, updatePayload);
          }
        } catch (err) {
          console.warn("Could not update client metadata:", err);
          // Don't fail the whole operation if metadata update fails
        }
      } else if (
        typeof formClientValue === "string" &&
        formClientValue.trim() !== ""
      ) {
        try {
          const newClient = await apiService.createClient({
            name: formClientValue.trim(),
            phone: clientMeta.phone || null,
            email: clientMeta.email || null,
            contact_url: clientMeta.contact_url || null,
            location: clientMeta.location || null,
            primary_service: clientMeta.primary_service || null,
            secondary_service: clientMeta.secondary_service || null,
            brand_tone: clientMeta.brand_tone || 'neutral',
            business_hours: clientMeta.business_hours || null,
            social_links: clientMeta.social_links && clientMeta.social_links.length > 0 
              ? (typeof clientMeta.social_links === 'string' 
                  ? clientMeta.social_links 
                  : JSON.stringify(clientMeta.social_links))
              : null
          });
          if (newClient && newClient.id) {
            finalClientId = newClient.id;
            fetchClientsForDropdown();
          } else {
            throw new Error(newClient.error || "Failed to create new client.");
          }
        } catch (err) {
          setFormError(`Client creation error: ${err.message}`);
          return;
        }
      }
    }

    let finalProjectId = null;
    if (formProjectValue) {
      if (typeof formProjectValue === "object" && formProjectValue.id) {
        finalProjectId = formProjectValue.id;
      } else if (
        typeof formProjectValue === "string" &&
        formProjectValue.trim() !== ""
      ) {
        try {
          const newProject = await apiService.createProject({
            name: formProjectValue.trim(),
          });
          if (newProject && newProject.id) {
            finalProjectId = newProject.id;
            fetchProjectsForDropdown();
          } else {
            throw new Error(
              newProject.error || "Failed to create new project.",
            );
          }
        } catch (err) {
          setFormError(`Project creation error: ${err.message}`);
          return;
        }
      }
    }

    const payload = {
      url: formData.url.trim(),
      sitemap_url: formData.sitemap_url.trim() || null,
      competitor_url: formData.competitor_url.trim() || null,
      project_id: finalProjectId,
      client_id: finalClientId,
    };

    if (isEditMode) {
      onSave(formData.id, payload);
    } else {
      onSave(payload);
    }
  };

  const handleDeleteClick = () => {
    if (isEditMode && onDelete) {
      onDelete(formData.id, formData.url);
    }
  };

  const getTitle = () =>
    isEditMode ? `Edit Website: ${formData.url || "N/A"}` : "Add New Website";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="website-action-modal-title"
    >
      <DialogTitle id="website-action-modal-title">{getTitle()}</DialogTitle>
      <DialogContent dividers>
        <Tabs value={activeTab} onChange={handleTabChange} sx={{ mb: 2 }}>
          <Tab label="Website" />
          <Tab label="WordPress Integration" disabled={!isEditMode} />
        </Tabs>
        
        {activeTab === 0 && (
          <>
            {formError && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {formError}
              </Alert>
            )}
            <Box component="form" noValidate autoComplete="off" sx={{ mt: 1 }}>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <TextField
                autoFocus={!isEditMode}
                required
                fullWidth
                margin="dense"
                id="website-url-modal"
                name="url"
                label="Website URL"
                type="url"
                placeholder="https://example.com"
                value={formData.url}
                onChange={handleInputChange}
                error={!!(formError && formError.toLowerCase().includes("url"))}
                helperText={
                  formError && formError.toLowerCase().includes("url")
                    ? formError
                    : "Must start with http:// or https://"
                }
                disabled={isSaving}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                margin="dense"
                id="website-sitemap-modal"
                name="sitemap_url"
                label="Sitemap URL (Optional)"
                type="url"
                placeholder="https://example.com/sitemap.xml"
                value={formData.sitemap_url}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                margin="dense"
                id="website-competitor-modal"
                name="competitor_url"
                label="Competitor URL (Optional)"
                type="url"
                placeholder="https://competitor.com"
                value={formData.competitor_url}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText="Reference competitor for content generation and analysis"
              />
            </Grid>
            <Grid item xs={12}>
              <Autocomplete
                id="project-select-for-website-modal"
                options={projectsForDropdown}
                loading={isLoadingProjects}
                getOptionLabel={(option) => {
                  if (typeof option === "string") return option;
                  return option.name || `ID: ${option.id}`;
                }}
                value={formProjectValue}
                onChange={handleProjectChange}
                isOptionEqualToValue={(option, value) => {
                  if (!option || !value) return false;
                  if (typeof value === "string") return false;
                  return option.id === value.id;
                }}
                freeSolo
                selectOnFocus
                clearOnBlur
                handleHomeEndKeys
                renderInput={(params) => (
                  <TextField
                    {...params}
                    required
                    label="Assign to Project"
                    variant="outlined"
                    margin="dense"
                    name="project"
                    error={
                      !!(
                        formError && formError.toLowerCase().includes("project")
                      )
                    }
                    helperText={
                      formError && formError.toLowerCase().includes("project")
                        ? formError
                        : ""
                    }
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
                disabled={isSaving || isLoadingProjects}
              />
            </Grid>
            <Grid item xs={12}>
              <Autocomplete
                id="client-select-for-website-modal"
                value={formClientValue}
                onChange={handleClientChange}
                options={clientsForDropdown}
                loading={isLoadingClients}
                getOptionLabel={(option) => {
                  if (typeof option === "string") return option;
                  if (!option || !option.name) return "";
                  return option.name;
                }}
                isOptionEqualToValue={(option, value) => {
                  if (!option || !value) return false;
                  if (typeof value === "string") return false; // A string input won't equal an existing option object
                  return option.id === value.id;
                }}
                freeSolo
                selectOnFocus
                clearOnBlur
                handleHomeEndKeys
                renderOption={(props, option) => (
                  <Box component="li" {...props} key={option.id || option.name}>
                    {option.name}
                  </Box>
                )}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Assign to Client (Optional)"
                    variant="outlined"
                    margin="dense"
                    name="client"
                    helperText="Select existing client or type to create new."
                    error={
                      !!(
                        formError &&
                        formError.toLowerCase().includes("client") &&
                        !formError.toLowerCase().includes("creation")
                      )
                    }
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
                disabled={isSaving || isLoadingClients}
              />
            </Grid>
          </Grid>
        </Box>
        </>
        )}
        
        {activeTab === 1 && isEditMode && (
          <Box sx={{ mt: 1 }}>
            {wpFormError && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {wpFormError}
              </Alert>
            )}
            {isLoadingWpSite ? (
              <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
                <CircularProgress />
              </Box>
            ) : (
              <Grid container spacing={2}>
                <Grid item xs={12}>
                  <Alert severity="info">
                    Connect this website to WordPress via LLAMANATOR2 plugin for secure content management, SEO optimization, and automated publishing.
                    <br />
                    <strong>Note:</strong> The LLAMANATOR2 plugin must be installed and activated on your WordPress site. Get the API key from the plugin settings page.
                  </Alert>
                </Grid>
                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    label="WordPress Site URL"
                    value={wpFormData.url}
                    onChange={(e) => handleWpInputChange("url", e.target.value)}
                    error={!!(wpFormError && wpFormError.toLowerCase().includes("url"))}
                    helperText="Full URL including https://"
                    required
                    disabled={isSavingWpSite || isTestingWpConnection}
                  />
                </Grid>
                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    label="Site Name (Optional)"
                    value={wpFormData.site_name}
                    onChange={(e) => handleWpInputChange("site_name", e.target.value)}
                    helperText="Display name for this WordPress site"
                    disabled={isSavingWpSite || isTestingWpConnection}
                  />
                </Grid>
                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    label="LLAMANATOR2 API Key"
                    type="password"
                    value={wpFormData.api_key}
                    onChange={(e) => handleWpInputChange("api_key", e.target.value)}
                    helperText={
                      wpSite
                        ? "Leave blank to keep existing API key"
                        : "Get this from LLAMANATOR2 plugin settings page on your WordPress site"
                    }
                    required={!wpSite}
                    disabled={isSavingWpSite || isTestingWpConnection}
                  />
                </Grid>
                <Grid item xs={12}>
                  <FormControl fullWidth>
                    <InputLabel>Status</InputLabel>
                    <Select
                      value={wpFormData.status}
                      label="Status"
                      onChange={(e) => handleWpInputChange("status", e.target.value)}
                      disabled={isSavingWpSite || isTestingWpConnection}
                    >
                      <MenuItem value="active">Active</MenuItem>
                      <MenuItem value="inactive">Inactive</MenuItem>
                    </Select>
                  </FormControl>
                </Grid>
                {wpSite && (
                  <Grid item xs={12}>
                    <Alert severity="success" icon={<CheckCircleIcon />}>
                      WordPress site registered. Last tested: {wpSite.last_test_at ? new Date(wpSite.last_test_at).toLocaleString() : "Never"}
                    </Alert>
                  </Grid>
                )}
                <Grid item xs={12}>
                  <Box sx={{ display: "flex", gap: 2 }}>
                    {wpSite && (
                      <Button
                        variant="outlined"
                        startIcon={<RefreshIcon />}
                        onClick={handleTestWpConnection}
                        disabled={isSavingWpSite || isTestingWpConnection}
                      >
                        {isTestingWpConnection ? "Testing..." : "Test Connection"}
                      </Button>
                    )}
                    <Button
                      variant="contained"
                      onClick={handleSaveWpSite}
                      disabled={isSavingWpSite || isTestingWpConnection}
                    >
                      {isSavingWpSite ? "Saving..." : wpSite ? "Update WordPress Site" : "Register WordPress Site"}
                    </Button>
                  </Box>
                </Grid>
              </Grid>
            )}
          </Box>
        )}
      </DialogContent>
      <DialogActions
        sx={{ px: 3, pb: 2, pt: 2, justifyContent: "space-between" }}
      >
        <Box>
          {isEditMode && onDelete && (
            <Button
              onClick={handleDeleteClick}
              disabled={isSaving}
              color="inherit"
              variant="text"
              size="small"
              startIcon={
                <CloseIcon fontSize="small" sx={{ color: "text.secondary" }} />
              }
              sx={{ mr: "auto", color: "text.secondary" }}
            >
              Delete
            </Button>
          )}
        </Box>
        <Box>
          <Button onClick={onClose} disabled={isSaving} color="inherit">
            Cancel
          </Button>
          <Button
            onClick={handleSaveClick}
            variant="contained"
            disabled={isSaving}
          >
            {isSaving ? (
              <CircularProgress size={24} color="inherit" />
            ) : isEditMode ? (
              "Save Changes"
            ) : (
              "Create Website"
            )}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default WebsiteActionModal;
