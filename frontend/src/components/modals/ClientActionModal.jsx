// frontend/src/components/modals/ClientActionModal.jsx
// Version 1.3:
// - Changed delete icon to a "Delete Client" Button.
// - Ensured onOpenLinker passes clientData.
// Based on v1.2.

import React, { useState, useEffect, useRef } from "react";
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
  // IconButton, // No longer needed for delete
  Tooltip,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Typography,
  Chip,
  InputAdornment,
  IconButton,
  Autocomplete,
} from "@mui/material";
// import CancelIcon from '@mui/icons-material/Cancel'; // Replaced by Button text
import LinkIcon from "@mui/icons-material/Link";
import _DeleteOutlineIcon from "@mui/icons-material/DeleteOutline"; // For the delete button icon
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import AddIcon from "@mui/icons-material/Add";
import { getLogoUrl } from "../../config/logoConfig";

// Common dropdown options for multi-select fields
const CONTENT_GOALS_OPTIONS = [
  "SEO/Organic Traffic",
  "Lead Generation",
  "Brand Awareness",
  "Thought Leadership",
  "Customer Education",
  "Product/Service Promotion",
  "Community Building",
  "Customer Retention",
  "Conversion Optimization",
  "Local Market Dominance",
];

const USP_OPTIONS = [
  "Years of Experience",
  "Specialized Expertise",
  "Award-Winning Service",
  "Competitive Pricing",
  "Personalized Approach",
];

const TARGET_AUDIENCE_OPTIONS = [
  "Small Business Owners",
  "Enterprise/Corporate",
  "Individuals/Consumers",
  "Healthcare Professionals",
  "Legal Professionals",
  "Property Owners/Real Estate",
  "Technology Companies",
  "Non-Profits/Government",
  "Startups/Entrepreneurs",
  "Senior Citizens/Retirees",
];

const initialFormState = {
  id: null,
  name: "",
  email: "",
  phone: "",
  location: "",
  notes: "",
  // RAG Enhancement fields
  industry: [],
  target_audience: [],
  unique_selling_points: [],
  competitor_urls: [],
  brand_voice_examples: "",
  keywords: [],
  content_goals: [],
  regulatory_constraints: "",
  geographic_coverage: [],
};

const ClientActionModal = ({
  open,
  onClose,
  clientData, // This is the client being edited or null for new
  onSave,
  onDelete,
  onOpenLinker, // This function will be called with clientData
  isSaving,
}) => {
  const [formData, setFormData] = useState(initialFormState);
  const [formError, setFormError] = useState(null);
  const [logoFile, setLogoFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    return () => {
      if (previewUrl && previewUrl.startsWith("blob:")) {
        URL.revokeObjectURL(previewUrl);
      }
    };
  }, [previewUrl]);

  const isEditMode = Boolean(clientData?.id);

  // Helper function to ensure array format
  const ensureArray = (value) => {
    if (!value) return [];
    if (Array.isArray(value)) return value;
    if (typeof value === 'string') {
      // If it's a string, try to parse as JSON or split by comma
      try {
        const parsed = JSON.parse(value);
        return Array.isArray(parsed) ? parsed : [value];
      } catch {
        return value.split(',').map(v => v.trim()).filter(Boolean);
      }
    }
    return [];
  };

  useEffect(() => {
    if (open) {
      if (clientData) {
        console.log('ClientActionModal - clientData received:', {
          id: clientData.id,
          name: clientData.name,
          industry: clientData.industry,
          keywords: clientData.keywords,
          competitor_urls: clientData.competitor_urls,
          keywordsType: typeof clientData.keywords,
          competitorUrlsType: typeof clientData.competitor_urls
        });
        setFormData({
          id: clientData.id,
          name: clientData.name || "",
          email: clientData.email || "",
          phone: clientData.phone || "",
          location: clientData.location || "",
          notes: clientData.notes || "",
          // RAG Enhancement fields - ensure arrays
          industry: ensureArray(clientData.industry),
          target_audience: ensureArray(clientData.target_audience),
          unique_selling_points: ensureArray(clientData.unique_selling_points),
          competitor_urls: ensureArray(clientData.competitor_urls),
          brand_voice_examples: clientData.brand_voice_examples || "",
          keywords: ensureArray(clientData.keywords),
          content_goals: ensureArray(clientData.content_goals),
          regulatory_constraints: clientData.regulatory_constraints || "",
          geographic_coverage: ensureArray(clientData.geographic_coverage),
        });
        setLogoFile(null);
        setPreviewUrl(
          clientData.logo_path
            ? getLogoUrl(clientData.logo_path)
            : null,
        );
      } else {
        setFormData(initialFormState);
        setLogoFile(null);
        setPreviewUrl(null);
      }
      setFormError(null); // Reset error when modal opens or clientData changes
    } else {
      setPreviewUrl(null);
      setLogoFile(null);
    }
  }, [clientData, open]);

  const handleInputChange = (event) => {
    const { name, value } = event.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    if (name === "name" && value.trim()) {
      setFormError(null); // Clear general error if name becomes valid
    }
    // Optional: Live email validation feedback
    if (name === "email" && value && !/\S+@\S+\.\S+/.test(value)) {
      // Could set a specific error field: setFieldErrors(prev => ({...prev, email: 'Invalid format'}))
    } else if (name === "email") {
      // Clear specific email error: setFieldErrors(prev => ({...prev, email: null}))
    }
  };

  const handleLogoChange = (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) {
      setLogoFile(file);
      setPreviewUrl(URL.createObjectURL(file));
    } else {
      setLogoFile(null);
      setPreviewUrl(null);
    }
  };

  const handleSaveClick = (event) => {
    if (event) event.preventDefault();

    if (!formData.name.trim()) {
      setFormError("Client Name is required.");
      return;
    }
    if (formData.email && !/\S+@\S+\.\S+/.test(formData.email)) {
      setFormError("Invalid email format. Please correct or leave empty.");
      return;
    }
    setFormError(null);
    onSave(formData, logoFile); // pass logoFile
  };

  const handleDeleteClick = () => {
    if (isEditMode && clientData?.id) {
      // Consider using a more robust confirmation dialog for deletions
      if (
        window.confirm(
          `Are you sure you want to delete client "${formData.name || "this client"}"? This action cannot be undone.`,
        )
      ) {
        onDelete(clientData.id);
      }
    }
  };

  // Removed global Enter key handler - each field now handles its own Enter behavior

  const getTitle = () => {
    if (isEditMode) {
      return `Edit Client: ${clientData?.name || `ID: ${clientData?.id}`}`;
    }
    return "Add New Client";
  };

  // Correctly call onOpenLinker with the current clientData
  const handleManageLinks = () => {
    if (onOpenLinker && clientData) {
      // Ensure clientData is available
      onOpenLinker(clientData); // Pass the client data to the handler
    } else {
      // This case should ideally not be hit if the button is only shown in edit mode
      console.error(
        "ClientActionModal: clientData is missing for onOpenLinker call.",
      );
      setFormError("Cannot manage links: Client data not available.");
    }
  };

  // Handle chip arrays (keywords, competitor_urls, industry, geographic_coverage, content_goals, usps, target_audience)
  const [keywordInput, setKeywordInput] = useState("");
  const [competitorUrlInput, setCompetitorUrlInput] = useState("");
  const [industryInput, setIndustryInput] = useState("");
  const [geographicInput, setGeographicInput] = useState("");

  const handleAddKeyword = () => {
    if (keywordInput.trim() && !formData.keywords.includes(keywordInput.trim())) {
      setFormData((prev) => ({
        ...prev,
        keywords: [...prev.keywords, keywordInput.trim()],
      }));
      setKeywordInput("");
    }
  };

  const handleDeleteKeyword = (keywordToDelete) => {
    setFormData((prev) => ({
      ...prev,
      keywords: prev.keywords.filter((kw) => kw !== keywordToDelete),
    }));
  };

  const handleAddCompetitorUrl = () => {
    if (competitorUrlInput.trim() && !formData.competitor_urls.includes(competitorUrlInput.trim())) {
      setFormData((prev) => ({
        ...prev,
        competitor_urls: [...prev.competitor_urls, competitorUrlInput.trim()],
      }));
      setCompetitorUrlInput("");
    }
  };

  const handleDeleteCompetitorUrl = (urlToDelete) => {
    setFormData((prev) => ({
      ...prev,
      competitor_urls: prev.competitor_urls.filter((url) => url !== urlToDelete),
    }));
  };

  // Handlers for Industry
  const handleAddIndustry = () => {
    if (industryInput.trim() && !formData.industry.includes(industryInput.trim())) {
      setFormData((prev) => ({
        ...prev,
        industry: [...prev.industry, industryInput.trim()],
      }));
      setIndustryInput("");
    }
  };

  const handleDeleteIndustry = (itemToDelete) => {
    setFormData((prev) => ({
      ...prev,
      industry: prev.industry.filter((item) => item !== itemToDelete),
    }));
  };

  // Handlers for Geographic Coverage
  const handleAddGeographic = () => {
    if (geographicInput.trim() && !formData.geographic_coverage.includes(geographicInput.trim())) {
      setFormData((prev) => ({
        ...prev,
        geographic_coverage: [...prev.geographic_coverage, geographicInput.trim()],
      }));
      setGeographicInput("");
    }
  };

  const handleDeleteGeographic = (itemToDelete) => {
    setFormData((prev) => ({
      ...prev,
      geographic_coverage: prev.geographic_coverage.filter((item) => item !== itemToDelete),
    }));
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="client-action-modal-title"
    >
      <DialogTitle id="client-action-modal-title">{getTitle()}</DialogTitle>
      <DialogContent dividers>
        {formError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formError}
          </Alert>
        )}
        <Box
          component="form"
          noValidate
          autoComplete="off"
          sx={{ mt: 1 }}
          onSubmit={handleSaveClick}
        >
          {" "}
          {/* Wrap in form for Enter key */}
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <TextField
                autoFocus={!isEditMode} // Autofocus only for new clients
                required
                fullWidth
                margin="dense"
                id="client-name"
                label="Client Name"
                name="name"
                value={formData.name}
                onChange={handleInputChange}
                error={!!formError && !formData.name.trim()} // Specific error for name
                helperText={
                  !!formError && !formData.name.trim()
                    ? "Name cannot be empty"
                    : ""
                }
                disabled={isSaving}
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <TextField
                fullWidth
                margin="dense"
                id="client-email"
                label="Email Address"
                name="email"
                type="email"
                value={formData.email}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText="Optional: Client's primary email."
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <TextField
                fullWidth
                margin="dense"
                id="client-phone"
                label="Phone Number"
                name="phone"
                value={formData.phone}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText="Optional: Client's contact phone."
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                margin="dense"
                id="client-location"
                label="Location"
                name="location"
                placeholder="City, State (e.g., Miami, FL)"
                value={formData.location}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText="Optional: Client's primary business location."
              />
            </Grid>
            <Grid item xs={12} sx={{ textAlign: "center" }}>
              <Box
                sx={{
                  width: 120,
                  height: 120,
                  border: "1px dashed",
                  borderColor: "divider",
                  borderRadius: 2,
                  mx: "auto",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: isSaving ? "default" : "pointer",
                  overflow: "hidden",
                }}
                onClick={() => !isSaving && fileInputRef.current?.click()}
              >
                {previewUrl ? (
                  <img
                    src={previewUrl}
                    alt="Client logo preview"
                    style={{
                      width: "100%",
                      height: "100%",
                      objectFit: "cover",
                    }}
                  />
                ) : (
                  <Box sx={{ color: "text.secondary", fontSize: "0.75rem" }}>
                    Upload Logo
                  </Box>
                )}
              </Box>
              <input
                type="file"
                accept="image/*"
                onChange={handleLogoChange}
                ref={fileInputRef}
                hidden
                disabled={isSaving}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                margin="dense"
                id="client-notes"
                label="Notes"
                name="notes"
                multiline
                rows={3}
                value={formData.notes}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText="Any relevant notes about this client."
              />
            </Grid>

            {/* RAG Enhancement Section */}
            <Grid item xs={12} sx={{ mt: 2 }}>
              <Accordion>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography variant="subtitle1" fontWeight="bold">
                    RAG Training Data (Optional)
                  </Typography>
                </AccordionSummary>
                <AccordionDetails>
                  <Grid container spacing={2}>
                    {/* Industry - Multi-select chip interface */}
                    <Grid item xs={12} sm={6}>
                      <TextField
                        fullWidth
                        margin="dense"
                        id="industry-input"
                        label="Add Industry/Category"
                        value={industryInput}
                        onChange={(e) => setIndustryInput(e.target.value)}
                        onKeyPress={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddIndustry();
                          }
                        }}
                        disabled={isSaving}
                        placeholder="e.g., Healthcare, Legal, E-commerce"
                        InputProps={{
                          endAdornment: (
                            <InputAdornment position="end">
                              <IconButton
                                onClick={handleAddIndustry}
                                disabled={!industryInput.trim() || isSaving}
                                edge="end"
                              >
                                <AddIcon />
                              </IconButton>
                            </InputAdornment>
                          ),
                        }}
                        helperText="Industry/market classification"
                      />
                      <Box sx={{ mt: 1, display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                        {formData.industry.map((item, index) => (
                          <Chip
                            key={index}
                            label={item}
                            onDelete={() => handleDeleteIndustry(item)}
                            disabled={isSaving}
                            size="small"
                          />
                        ))}
                      </Box>
                    </Grid>

                    {/* Geographic Coverage - Multi-select chip interface */}
                    <Grid item xs={12} sm={6}>
                      <TextField
                        fullWidth
                        margin="dense"
                        id="geographic-input"
                        label="Add Location"
                        value={geographicInput}
                        onChange={(e) => setGeographicInput(e.target.value)}
                        onKeyPress={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddGeographic();
                          }
                        }}
                        disabled={isSaving}
                        placeholder="City, State, or Zip Code"
                        InputProps={{
                          endAdornment: (
                            <InputAdornment position="end">
                              <IconButton
                                onClick={handleAddGeographic}
                                disabled={!geographicInput.trim() || isSaving}
                                edge="end"
                              >
                                <AddIcon />
                              </IconButton>
                            </InputAdornment>
                          ),
                        }}
                        helperText="Service areas (cities/states/zips)"
                      />
                      <Box sx={{ mt: 1, display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                        {formData.geographic_coverage.map((item, index) => (
                          <Chip
                            key={index}
                            label={item}
                            onDelete={() => handleDeleteGeographic(item)}
                            disabled={isSaving}
                            size="small"
                          />
                        ))}
                      </Box>
                    </Grid>
                    {/* Target Audience - Autocomplete with common options */}
                    <Grid item xs={12}>
                      <Autocomplete
                        multiple
                        freeSolo
                        options={TARGET_AUDIENCE_OPTIONS}
                        value={formData.target_audience}
                        onChange={(event, newValue) => {
                          setFormData((prev) => ({ ...prev, target_audience: newValue }));
                        }}
                        disabled={isSaving}
                        renderTags={(value, getTagProps) =>
                          value.map((option, index) => {
                            const { key, ...otherProps } = getTagProps({ index });
                            return (
                              <Chip
                                key={key}
                                label={option}
                                size="small"
                                {...otherProps}
                              />
                            );
                          })
                        }
                        renderInput={(params) => (
                          <TextField
                            {...params}
                            margin="dense"
                            label="Target Audience"
                            placeholder="Select or type custom audience..."
                            helperText="Who is this client's target customer?"
                          />
                        )}
                      />
                    </Grid>

                    {/* Unique Selling Points - Autocomplete with common options */}
                    <Grid item xs={12}>
                      <Autocomplete
                        multiple
                        freeSolo
                        options={USP_OPTIONS}
                        value={formData.unique_selling_points}
                        onChange={(event, newValue) => {
                          setFormData((prev) => ({ ...prev, unique_selling_points: newValue }));
                        }}
                        disabled={isSaving}
                        renderTags={(value, getTagProps) =>
                          value.map((option, index) => {
                            const { key, ...otherProps } = getTagProps({ index });
                            return (
                              <Chip
                                key={key}
                                label={option}
                                size="small"
                                {...otherProps}
                              />
                            );
                          })
                        }
                        renderInput={(params) => (
                          <TextField
                            {...params}
                            margin="dense"
                            label="Unique Selling Points"
                            placeholder="Select or type custom USPs..."
                            helperText="Key differentiators and value propositions"
                          />
                        )}
                      />
                    </Grid>

                    {/* Content Goals - Autocomplete with common options */}
                    <Grid item xs={12}>
                      <Autocomplete
                        multiple
                        freeSolo
                        options={CONTENT_GOALS_OPTIONS}
                        value={formData.content_goals}
                        onChange={(event, newValue) => {
                          setFormData((prev) => ({ ...prev, content_goals: newValue }));
                        }}
                        disabled={isSaving}
                        renderTags={(value, getTagProps) =>
                          value.map((option, index) => {
                            const { key, ...otherProps } = getTagProps({ index });
                            return (
                              <Chip
                                key={key}
                                label={option}
                                size="small"
                                {...otherProps}
                              />
                            );
                          })
                        }
                        renderInput={(params) => (
                          <TextField
                            {...params}
                            margin="dense"
                            label="Content Goals"
                            placeholder="Select or type custom goals..."
                            helperText="Content marketing objectives"
                          />
                        )}
                      />
                    </Grid>
                    <Grid item xs={12}>
                      <TextField
                        fullWidth
                        margin="dense"
                        id="client-brand-voice-examples"
                        label="Brand Voice Examples"
                        name="brand_voice_examples"
                        multiline
                        rows={3}
                        value={formData.brand_voice_examples}
                        onChange={handleInputChange}
                        disabled={isSaving}
                        placeholder="Paste sample content showing desired tone/voice..."
                        helperText="Example content that demonstrates the brand voice"
                      />
                    </Grid>
                    <Grid item xs={12}>
                      <TextField
                        fullWidth
                        margin="dense"
                        id="client-regulatory-constraints"
                        label="Regulatory/Compliance Requirements"
                        name="regulatory_constraints"
                        multiline
                        rows={2}
                        value={formData.regulatory_constraints}
                        onChange={handleInputChange}
                        disabled={isSaving}
                        placeholder="e.g., HIPAA, GDPR, FDA guidelines..."
                        helperText="Industry-specific compliance requirements"
                      />
                    </Grid>

                    {/* Target Keywords */}
                    <Grid item xs={12}>
                      <TextField
                        fullWidth
                        margin="dense"
                        id="keyword-input"
                        label="Add Target Keyword"
                        value={keywordInput}
                        onChange={(e) => setKeywordInput(e.target.value)}
                        onKeyPress={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddKeyword();
                          }
                        }}
                        disabled={isSaving}
                        placeholder="Enter keyword and press Enter or click +"
                        InputProps={{
                          endAdornment: (
                            <InputAdornment position="end">
                              <IconButton
                                onClick={handleAddKeyword}
                                disabled={!keywordInput.trim() || isSaving}
                                edge="end"
                              >
                                <AddIcon />
                              </IconButton>
                            </InputAdornment>
                          ),
                        }}
                        helperText="SEO keywords for content generation"
                      />
                      <Box sx={{ mt: 1, display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                        {formData.keywords.map((keyword, index) => (
                          <Chip
                            key={index}
                            label={keyword}
                            onDelete={() => handleDeleteKeyword(keyword)}
                            disabled={isSaving}
                            size="small"
                          />
                        ))}
                      </Box>
                    </Grid>

                    {/* Competitor URLs */}
                    <Grid item xs={12}>
                      <TextField
                        fullWidth
                        margin="dense"
                        id="competitor-url-input"
                        label="Add Competitor URL"
                        value={competitorUrlInput}
                        onChange={(e) => setCompetitorUrlInput(e.target.value)}
                        onKeyPress={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddCompetitorUrl();
                          }
                        }}
                        disabled={isSaving}
                        placeholder="Enter competitor URL and press Enter or click +"
                        InputProps={{
                          endAdornment: (
                            <InputAdornment position="end">
                              <IconButton
                                onClick={handleAddCompetitorUrl}
                                disabled={!competitorUrlInput.trim() || isSaving}
                                edge="end"
                              >
                                <AddIcon />
                              </IconButton>
                            </InputAdornment>
                          ),
                        }}
                        helperText="Competitor websites for analysis"
                      />
                      <Box sx={{ mt: 1, display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                        {formData.competitor_urls.map((url, index) => (
                          <Chip
                            key={index}
                            label={url}
                            onDelete={() => handleDeleteCompetitorUrl(url)}
                            disabled={isSaving}
                            size="small"
                          />
                        ))}
                      </Box>
                    </Grid>
                  </Grid>
                </AccordionDetails>
              </Accordion>
            </Grid>
          </Grid>
        </Box>
      </DialogContent>

      <DialogActions
        sx={{ justifyContent: "space-between", px: { xs: 2, sm: 3 }, py: 2 }}
      >
        <Box sx={{ display: "flex", gap: 1 }}>
          {isEditMode && (
            // MODIFIED: Changed IconButton to Button for Delete
            <Tooltip title="Delete this client">
              <Button
                variant="text"
                color="inherit"
                onClick={handleDeleteClick}
                disabled={isSaving}
                size="small"
              >
                Delete
              </Button>
            </Tooltip>
          )}
          {isEditMode &&
            onOpenLinker && ( // Only show if in edit mode and handler exists
              <Tooltip title="Manage Linked Items (e.g., Projects)">
                <Button
                  variant="contained"
                  size="small"
                  startIcon={<LinkIcon />}
                  onClick={handleManageLinks} // Use the new handler
                  disabled={isSaving}
                >
                  Manage Links
                </Button>
              </Tooltip>
            )}
        </Box>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button onClick={onClose} disabled={isSaving} color="inherit">
            Cancel
          </Button>
          <Button
            type="submit" // To allow Enter key submission from the form
            onClick={handleSaveClick} // Still keep onClick for explicit button press
            variant="contained"
            disabled={
              isSaving ||
              !formData.name?.trim() ||
              (formData.email && !/\S+@\S+\.\S+/.test(formData.email))
            }
          >
            {isSaving ? (
              <CircularProgress size={24} color="inherit" />
            ) : isEditMode ? (
              "Save Changes"
            ) : (
              "Create Client"
            )}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default ClientActionModal;
