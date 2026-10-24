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
  FormControl,
  InputLabel,
  Select,
  MenuItem,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";

import * as apiService from "../../api";
import * as wordpressService from "../../api/wordpressService";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import RefreshIcon from "@mui/icons-material/Refresh";
import { uiLocale, useUiTranslation, Trans } from "../../i18n";

const WebsiteActionModal = ({
  open,
  onClose,
  websiteData,
  onSave,
  onDelete,
  isSaving,
  // projects prop removed as modal fetches its own
}) => {
  const { t } = useUiTranslation();
  const [formData, setFormData] = useState({
    id: null,
    url: "",
    sitemap_url: "",
    competitor_url: "",
    local_path: "",
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
  const [_validationErrors, setValidationErrors] = useState(null);
  
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
          local_path: websiteData.local_path || "",
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
          local_path: "",
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
        local_path: "",
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

  const _handleClientMetaChange = (field, value) => {
    setClientMeta(prev => ({ ...prev, [field]: value }));
    setValidationErrors(null);
  };

  const _handleSocialLinkAdd = () => {
    setClientMeta(prev => ({
      ...prev,
      social_links: [...prev.social_links, { platform: '', url: '' }]
    }));
  };

  const _handleSocialLinkChange = (index, field, value) => {
    setClientMeta(prev => ({
      ...prev,
      social_links: prev.social_links.map((link, i) =>
        i === index ? { ...link, [field]: value } : link
      )
    }));
  };

  const _handleSocialLinkRemove = (index) => {
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
      setWpFormError(t("dialogs.websiteAction.testFieldsRequired"));
      return;
    }
    
    setIsTestingWpConnection(true);
    setWpFormError(null);
    try {
      // First register a temporary site to test, then delete it
      const _testPayload = {
        url: wpFormData.url,
        username: wpFormData.username,
        api_key: wpFormData.api_key,
      };

      // Use the test endpoint if available, or register then delete
      if (wpSite) {
        const response = await wordpressService.testWordPressConnection(wpSite.id);
        if (response.success) {
          setWpFormError(null);
          alert(t("dialogs.websiteAction.testSuccess"));
        } else {
          setWpFormError(response.error || t("dialogs.websiteAction.testFailed"));
        }
      } else {
        // For new sites, we need to register first to test
        // The backend will test the connection during registration
        setWpFormError(t("dialogs.websiteAction.registerFirst"));
      }
    } catch (err) {
      setWpFormError(t("dialogs.websiteAction.testError", { error: err.message }));
    } finally {
      setIsTestingWpConnection(false);
    }
  };
  
  const handleSaveWpSite = async () => {
    // Validate required fields
    if (!wpFormData.url || !wpFormData.url.trim()) {
      setWpFormError(t("dialogs.websiteAction.wpUrlRequired"));
      return;
    }
    if (!wpFormData.url.startsWith("http://") && !wpFormData.url.startsWith("https://")) {
      setWpFormError(t("dialogs.websiteAction.urlScheme"));
      return;
    }
    if (!wpFormData.api_key && !wpSite) {
      setWpFormError(t("dialogs.websiteAction.apiKeyRequired"));
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
        alert(wpSite ? t("dialogs.websiteAction.wpUpdated") : t("dialogs.websiteAction.wpRegistered"));
      } else {
        setWpFormError(response.error || t("dialogs.websiteAction.wpSaveFailed"));
      }
    } catch (err) {
      setWpFormError(err.message || t("dialogs.websiteAction.wpSaveFailed"));
    } finally {
      setIsSavingWpSite(false);
    }
  };

  const handleSaveClick = async () => {
    setFormError(null);
    let currentFormError = null;

    if (!formData.url.trim()) currentFormError = t("dialogs.websiteAction.urlRequired");
    else if (
      !formData.url.startsWith("http://") &&
      !formData.url.startsWith("https://")
    )
      currentFormError = t("dialogs.websiteAction.urlScheme");
    else if (!formProjectValue)
      currentFormError = t("dialogs.websiteAction.projectRequired");

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
            throw new Error(newClient.error || t("dialogs.websiteAction.createClientFailed"));
          }
        } catch (err) {
          setFormError(t("dialogs.websiteAction.clientCreationError", { error: err.message }));
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
              newProject.error || t("dialogs.websiteAction.createProjectFailed"),
            );
          }
        } catch (err) {
          setFormError(t("dialogs.websiteAction.projectCreationError", { error: err.message }));
          return;
        }
      }
    }

    const payload = {
      url: formData.url.trim(),
      sitemap_url: formData.sitemap_url.trim() || null,
      competitor_url: formData.competitor_url.trim() || null,
      local_path: (formData.local_path || "").trim() || null,
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
    isEditMode ? t("dialogs.websiteAction.editTitle", { url: formData.url || t("dialogs.websiteAction.notAvailable") }) : t("dialogs.websiteAction.addTitle");

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
          <Tab label={t("dialogs.websiteAction.websiteTab")} />
          <Tab label={t("dialogs.websiteAction.wordPressTab")} disabled={!isEditMode} />
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
                label={t("dialogs.websiteAction.websiteUrl")}
                type="url"
                placeholder="https://example.com"
                value={formData.url}
                onChange={handleInputChange}
                error={!!(formError && formError.toLowerCase().includes("url"))}
                helperText={
                  formError && formError.toLowerCase().includes("url")
                    ? formError
                    : t("dialogs.websiteAction.urlHelp")
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
                label={t("dialogs.websiteAction.sitemapUrl")}
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
                label={t("dialogs.websiteAction.competitorUrl")}
                type="url"
                placeholder="https://competitor.com"
                value={formData.competitor_url}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText={t("dialogs.websiteAction.competitorHelp")}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                margin="dense"
                id="website-local-path-modal"
                name="local_path"
                label={t("dialogs.websiteAction.localPath")}
                placeholder="/path/to/your/site/outputs/example.com"
                value={formData.local_path}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText={t("dialogs.websiteAction.localPathHelp")}
              />
            </Grid>
            <Grid item xs={12}>
              <Autocomplete
                id="project-select-for-website-modal"
                options={projectsForDropdown}
                loading={isLoadingProjects}
                getOptionLabel={(option) => {
                  if (typeof option === "string") return option;
                  return option.name || t("dialogs.websiteAction.idFallback", { id: option.id });
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
                    label={t("dialogs.websiteAction.assignProject")}
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
                    label={t("dialogs.websiteAction.assignClient")}
                    variant="outlined"
                    margin="dense"
                    name="client"
                    helperText={t("dialogs.websiteAction.clientHelp")}
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
                    {t("dialogs.websiteAction.wpIntro")}
                    <br />
                    <Trans i18nKey="dialogs.websiteAction.wpNote" components={{ bold: <strong /> }} />
                  </Alert>
                </Grid>
                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    label={t("dialogs.websiteAction.wpSiteUrl")}
                    value={wpFormData.url}
                    onChange={(e) => handleWpInputChange("url", e.target.value)}
                    error={!!(wpFormError && wpFormError.toLowerCase().includes("url"))}
                    helperText={t("dialogs.websiteAction.wpUrlHelp")}
                    required
                    disabled={isSavingWpSite || isTestingWpConnection}
                  />
                </Grid>
                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    label={t("dialogs.websiteAction.siteName")}
                    value={wpFormData.site_name}
                    onChange={(e) => handleWpInputChange("site_name", e.target.value)}
                    helperText={t("dialogs.websiteAction.siteNameHelp")}
                    disabled={isSavingWpSite || isTestingWpConnection}
                  />
                </Grid>
                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    label={t("dialogs.websiteAction.apiKey")}
                    type="password"
                    value={wpFormData.api_key}
                    onChange={(e) => handleWpInputChange("api_key", e.target.value)}
                    helperText={
                      wpSite
                        ? t("dialogs.websiteAction.apiKeyKeep")
                        : t("dialogs.websiteAction.apiKeyHelp")
                    }
                    required={!wpSite}
                    disabled={isSavingWpSite || isTestingWpConnection}
                  />
                </Grid>
                <Grid item xs={12}>
                  <FormControl fullWidth>
                    <InputLabel>{t("dialogs.websiteAction.status")}</InputLabel>
                    <Select
                      value={wpFormData.status}
                      label={t("dialogs.websiteAction.status")}
                      onChange={(e) => handleWpInputChange("status", e.target.value)}
                      disabled={isSavingWpSite || isTestingWpConnection}
                    >
                      <MenuItem value="active">{t("dialogs.websiteAction.active")}</MenuItem>
                      <MenuItem value="inactive">{t("dialogs.websiteAction.inactive")}</MenuItem>
                    </Select>
                  </FormControl>
                </Grid>
                {wpSite && (
                  <Grid item xs={12}>
                    <Alert severity="success" icon={<CheckCircleIcon />}>
                      {t("dialogs.websiteAction.lastTested", {
                        date: wpSite.last_test_at ? new Date(wpSite.last_test_at).toLocaleString(uiLocale()) : t("dialogs.websiteAction.never"),
                      })}
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
                        {isTestingWpConnection ? t("dialogs.websiteAction.testing") : t("dialogs.websiteAction.testConnection")}
                      </Button>
                    )}
                    <Button
                      variant="contained"
                      onClick={handleSaveWpSite}
                      disabled={isSavingWpSite || isTestingWpConnection}
                    >
                      {isSavingWpSite ? t("dialogs.websiteAction.saving") : wpSite ? t("dialogs.websiteAction.updateWp") : t("dialogs.websiteAction.registerWp")}
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
              {t("dialogs.shared.delete")}
            </Button>
          )}
        </Box>
        <Box>
          <Button onClick={onClose} disabled={isSaving} color="inherit">
            {t("dialogs.shared.cancel")}
          </Button>
          <Button
            onClick={handleSaveClick}
            variant="contained"
            disabled={isSaving}
          >
            {isSaving ? (
              <CircularProgress size={24} color="inherit" />
            ) : isEditMode ? (
              t("dialogs.websiteAction.saveChanges")
            ) : (
              t("dialogs.websiteAction.createWebsite")
            )}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default WebsiteActionModal;
