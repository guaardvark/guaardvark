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
import { useUiTranslation } from "../../i18n";

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
  const { t } = useUiTranslation();
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
      setFormError(t("dialogs.clientAction.nameRequired"));
      return;
    }
    if (formData.email && !/\S+@\S+\.\S+/.test(formData.email)) {
      setFormError(t("dialogs.clientAction.invalidEmail"));
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
          t("dialogs.clientAction.deleteConfirm", { name: formData.name || t("dialogs.clientAction.thisClient") }),
        )
      ) {
        onDelete(clientData.id);
      }
    }
  };

  // Removed global Enter key handler - each field now handles its own Enter behavior

  const getTitle = () => {
    if (isEditMode) {
      return t("dialogs.clientAction.editTitle", { name: clientData?.name || t("dialogs.clientAction.idFallback", { id: clientData?.id }) });
    }
    return t("dialogs.clientAction.addTitle");
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
      setFormError(t("dialogs.clientAction.linksUnavailable"));
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
                label={t("dialogs.clientAction.clientName")}
                name="name"
                value={formData.name}
                onChange={handleInputChange}
                error={!!formError && !formData.name.trim()} // Specific error for name
                helperText={
                  !!formError && !formData.name.trim()
                    ? t("dialogs.clientAction.nameEmpty")
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
                label={t("dialogs.clientAction.email")}
                name="email"
                type="email"
                value={formData.email}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText={t("dialogs.clientAction.emailHelp")}
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <TextField
                fullWidth
                margin="dense"
                id="client-phone"
                label={t("dialogs.clientAction.phone")}
                name="phone"
                value={formData.phone}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText={t("dialogs.clientAction.phoneHelp")}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField
                fullWidth
                margin="dense"
                id="client-location"
                label={t("dialogs.clientAction.location")}
                name="location"
                placeholder={t("dialogs.clientAction.locationPlaceholder")}
                value={formData.location}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText={t("dialogs.clientAction.locationHelp")}
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
                    alt={t("dialogs.clientAction.logoPreview")}
                    style={{
                      width: "100%",
                      height: "100%",
                      objectFit: "cover",
                    }}
                  />
                ) : (
                  <Box sx={{ color: "text.secondary", fontSize: "0.75rem" }}>
                    {t("dialogs.clientAction.uploadLogo")}
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
                label={t("dialogs.clientAction.notes")}
                name="notes"
                multiline
                rows={3}
                value={formData.notes}
                onChange={handleInputChange}
                disabled={isSaving}
                helperText={t("dialogs.clientAction.notesHelp")}
              />
            </Grid>

            {/* RAG Enhancement Section */}
            <Grid item xs={12} sx={{ mt: 2 }}>
              <Accordion>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography variant="subtitle1" fontWeight="bold">
                    {t("dialogs.clientAction.ragSection")}
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
                        label={t("dialogs.clientAction.addIndustry")}
                        value={industryInput}
                        onChange={(e) => setIndustryInput(e.target.value)}
                        onKeyPress={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddIndustry();
                          }
                        }}
                        disabled={isSaving}
                        placeholder={t("dialogs.clientAction.industryPlaceholder")}
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
                        helperText={t("dialogs.clientAction.industryHelp")}
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
                        label={t("dialogs.clientAction.addLocation")}
                        value={geographicInput}
                        onChange={(e) => setGeographicInput(e.target.value)}
                        onKeyPress={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddGeographic();
                          }
                        }}
                        disabled={isSaving}
                        placeholder={t("dialogs.clientAction.geoPlaceholder")}
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
                        helperText={t("dialogs.clientAction.geoHelp")}
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
                            label={t("dialogs.clientAction.targetAudience")}
                            placeholder={t("dialogs.clientAction.audiencePlaceholder")}
                            helperText={t("dialogs.clientAction.audienceHelp")}
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
                            label={t("dialogs.clientAction.usps")}
                            placeholder={t("dialogs.clientAction.uspPlaceholder")}
                            helperText={t("dialogs.clientAction.uspHelp")}
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
                            label={t("dialogs.clientAction.contentGoals")}
                            placeholder={t("dialogs.clientAction.goalsPlaceholder")}
                            helperText={t("dialogs.clientAction.goalsHelp")}
                          />
                        )}
                      />
                    </Grid>
                    <Grid item xs={12}>
                      <TextField
                        fullWidth
                        margin="dense"
                        id="client-brand-voice-examples"
                        label={t("dialogs.clientAction.brandVoice")}
                        name="brand_voice_examples"
                        multiline
                        rows={3}
                        value={formData.brand_voice_examples}
                        onChange={handleInputChange}
                        disabled={isSaving}
                        placeholder={t("dialogs.clientAction.brandVoicePlaceholder")}
                        helperText={t("dialogs.clientAction.brandVoiceHelp")}
                      />
                    </Grid>
                    <Grid item xs={12}>
                      <TextField
                        fullWidth
                        margin="dense"
                        id="client-regulatory-constraints"
                        label={t("dialogs.clientAction.regulatory")}
                        name="regulatory_constraints"
                        multiline
                        rows={2}
                        value={formData.regulatory_constraints}
                        onChange={handleInputChange}
                        disabled={isSaving}
                        placeholder={t("dialogs.clientAction.regulatoryPlaceholder")}
                        helperText={t("dialogs.clientAction.regulatoryHelp")}
                      />
                    </Grid>

                    {/* Target Keywords */}
                    <Grid item xs={12}>
                      <TextField
                        fullWidth
                        margin="dense"
                        id="keyword-input"
                        label={t("dialogs.clientAction.addKeyword")}
                        value={keywordInput}
                        onChange={(e) => setKeywordInput(e.target.value)}
                        onKeyPress={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddKeyword();
                          }
                        }}
                        disabled={isSaving}
                        placeholder={t("dialogs.clientAction.keywordPlaceholder")}
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
                        helperText={t("dialogs.clientAction.keywordHelp")}
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
                        label={t("dialogs.clientAction.addCompetitor")}
                        value={competitorUrlInput}
                        onChange={(e) => setCompetitorUrlInput(e.target.value)}
                        onKeyPress={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddCompetitorUrl();
                          }
                        }}
                        disabled={isSaving}
                        placeholder={t("dialogs.clientAction.competitorPlaceholder")}
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
                        helperText={t("dialogs.clientAction.competitorHelp")}
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
            <Tooltip title={t("dialogs.clientAction.deleteTooltip")}>
              <span>
                <Button
                  variant="text"
                  color="inherit"
                  onClick={handleDeleteClick}
                  disabled={isSaving}
                  size="small"
                >
                  {t("dialogs.shared.delete")}
                </Button>
              </span>
            </Tooltip>
          )}
          {isEditMode &&
            onOpenLinker && ( // Only show if in edit mode and handler exists
              <Tooltip title={t("dialogs.clientAction.manageLinksTooltip")}>
                <Button
                  variant="contained"
                  size="small"
                  startIcon={<LinkIcon />}
                  onClick={handleManageLinks} // Use the new handler
                  disabled={isSaving}
                >
                  {t("dialogs.clientAction.manageLinks")}
                </Button>
              </Tooltip>
            )}
        </Box>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button onClick={onClose} disabled={isSaving} color="inherit">
            {t("dialogs.shared.cancel")}
          </Button>
          <Button
            type="submit" // To allow Enter key submission from the form
            onClick={handleSaveClick} // Still keep onClick for explicit button press
            variant="contained"
            disabled={
              isSaving ||
              !formData.name?.trim() ||
              Boolean(formData.email && !/\S+@\S+\.\S+/.test(formData.email))
            }
          >
            {isSaving ? (
              <CircularProgress size={24} color="inherit" />
            ) : isEditMode ? (
              t("dialogs.clientAction.saveChanges")
            ) : (
              t("dialogs.clientAction.createClient")
            )}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default ClientActionModal;
