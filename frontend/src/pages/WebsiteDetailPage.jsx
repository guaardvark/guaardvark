// frontend/src/pages/WebsiteDetailPage.jsx
// Per-website detail page (replaces the settings-only modal as the primary click target).
// Phase 1 tabs: Overview + Indexing. Settings live behind the ⚙ button (the existing
// WebsiteActionModal, now demoted to settings-only). Reads existing endpoints — no backend change.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Alert as MuiAlert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Grid,
  Paper,
  Snackbar,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import SettingsIcon from "@mui/icons-material/Settings";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import EventIcon from "@mui/icons-material/Event";

import { getWebsite, updateWebsite, deleteWebsite, scrapeWebsite } from "../api/websiteService";
import * as wordpressService from "../api/wordpressService";
import WebsiteActionModal from "../components/modals/WebsiteActionModal";
import IndexingPanel from "../components/website/IndexingPanel";
import CrawledPagesPanel from "../components/website/CrawledPagesPanel";
import PageLayout from "../components/layout/PageLayout";
import { useStatus } from "../contexts/StatusContext";
import { translate, uiLocale, useUiTranslation } from "../i18n";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

const formatDate = (iso) => {
  if (!iso) return translate("websites.detail.never");
  try {
    return new Date(iso).toLocaleString(uiLocale());
  } catch {
    return iso;
  }
};

const statusLabels = () => ({
  active: translate("websites.status.active"),
  online: translate("websites.status.online"),
  indexed: translate("websites.status.indexed"),
  error: translate("websites.status.error"),
  offline: translate("websites.status.offline"),
  crawling: translate("websites.status.crawling"),
  indexing: translate("websites.status.indexing"),
});

const StatusChip = ({ status }) => {
  useUiTranslation();
  if (!status) return null;
  const normalized = status.toLowerCase();
  let color = "default";
  if (["active", "online", "indexed"].includes(normalized)) color = "success";
  else if (["error", "offline"].includes(normalized)) color = "error";
  else if (["crawling", "indexing"].includes(normalized)) color = "warning";
  return (
    <Chip
      label={statusLabels()[status] || status}
      color={color}
      size="small"
      sx={{ textTransform: "capitalize" }}
    />
  );
};

const Field = ({ label, children }) => (
  <Box sx={{ mb: 1.5 }}>
    <Typography variant="caption" color="text.secondary" display="block">
      {label}
    </Typography>
    <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
      {children}
    </Typography>
  </Box>
);

const WebsiteDetailPage = () => {
  const { t, language } = useUiTranslation();
  const { websiteId } = useParams();
  const navigate = useNavigate();
  const { activeModel } = useStatus();

  const [website, setWebsite] = useState(null);
  const [wpSite, setWpSite] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tabValue, setTabValue] = useState(0);
  const [crawling, setCrawling] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [feedback, setFeedback] = useState({ open: false, message: "", severity: "info" });

  const loadWebsite = useCallback(async () => {
    if (!websiteId) return;
    setLoading(true);
    setError(null);
    try {
      const [site, wpResp] = await Promise.all([
        getWebsite(websiteId),
        wordpressService.getWordPressSites().catch(() => null),
      ]);
      if (site && site.error) throw new Error(site.error);
      setWebsite(site);
      const wpList = wpResp?.success && Array.isArray(wpResp.data) ? wpResp.data : [];
      setWpSite(wpList.find((wp) => String(wp.website_id) === String(websiteId)) || null);
    } catch (err) {
      setError(err.message || t("websites.detail.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [websiteId]);

  useEffect(() => {
    loadWebsite();
  }, [loadWebsite]);

  const handleCloseFeedback = (event, reason) => {
    if (reason === "clickaway") return;
    setFeedback((prev) => ({ ...prev, open: false }));
  };

  const handleCrawl = async () => {
    setCrawling(true);
    setFeedback({ open: true, message: t("websites.detail.crawlQueuing"), severity: "info" });
    try {
      const res = await scrapeWebsite(website.id);
      setFeedback({
        open: true,
        message: res?.message || t("websites.detail.crawlQueued"),
        severity: "success",
      });
    } catch (err) {
      setFeedback({
        open: true,
        message: t("websites.detail.crawlFailed", {
          error: err.message || t("websites.detail.unknownError"),
        }),
        severity: "error",
      });
    } finally {
      setCrawling(false);
    }
  };

  // WebsiteActionModal calls onSave(id, data) for an existing website.
  const handleSaveSettings = async (id, data) => {
    setIsSaving(true);
    try {
      const resp = await updateWebsite(id, data);
      if (resp && resp.error) throw new Error(resp.error.message || resp.error);
      setFeedback({ open: true, message: t("websites.detail.updated"), severity: "success" });
      setSettingsOpen(false);
      loadWebsite();
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || t("websites.detail.updateError"),
        severity: "error",
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (id, url) => {
    if (
      !window.confirm(
        t("websites.detail.deleteConfirm", { url: url || t("websites.detail.notAvailable"), id }),
      )
    )
      return;
    setIsSaving(true);
    try {
      await deleteWebsite(id);
      navigate("/websites");
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || t("websites.detail.deleteFailed"),
        severity: "error",
      });
      setIsSaving(false);
    }
  };

  const headerContent = useMemo(
    () =>
      website ? (
        <Box sx={{ px: 2, pb: 0.5 }}>
          <Typography variant="caption" color="text.secondary">
            {t("websites.detail.header", {
              id: website.id,
              project: website.project?.name || t("websites.detail.notAvailable"),
              client: website.client?.name || t("websites.detail.notAvailable"),
              lastCrawled: formatDate(website.last_crawled),
            })}
          </Typography>
        </Box>
      ) : null,
    [website, language],
  );

  return (
    <PageLayout
      title={website ? t("websites.detail.title", { url: website.url }) : t("websites.detail.titleFallback")}
      variant="standard"
      actions={
        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            size="small"
            color="inherit"
            startIcon={<ArrowBackIcon />}
            onClick={() => navigate("/websites")}
          >
            {t("websites.detail.back")}
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<SettingsIcon />}
            onClick={() => setSettingsOpen(true)}
            disabled={!website}
          >
            {t("websites.detail.settings")}
          </Button>
        </Stack>
      }
      modelStatus
      activeModel={activeModel}
      headerContent={headerContent}
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

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      )}

      {!loading && error && (
        <MuiAlert severity="error" sx={{ m: 2 }}>
          {error}
        </MuiAlert>
      )}

      {!loading && !error && website && (
        <>
          <Box sx={{ borderBottom: 1, borderColor: "divider", mx: 1.5 }}>
            <Tabs
              value={tabValue}
              onChange={(e, v) => setTabValue(v)}
              aria-label={t("websites.detail.tabsLabel")}
              sx={{
                minHeight: "40px",
                "& .MuiTab-root": { minHeight: "40px", fontSize: "0.875rem", py: 1 },
              }}
            >
              <Tab label={t("websites.detail.tabs.overview")} id="website-tab-overview" />
              <Tab label={t("websites.detail.tabs.pages")} id="website-tab-pages" />
              <Tab label={t("websites.detail.tabs.indexing")} id="website-tab-indexing" />
            </Tabs>
          </Box>

          <Box sx={{ flexGrow: 1, overflowY: "auto", p: 1.5, pt: 2 }}>
            {tabValue === 0 && (
              <Grid container spacing={2}>
                <Grid item xs={12} md={6}>
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" gutterBottom>
                      {t("websites.detail.details")}
                    </Typography>
                    <Field label={t("websites.detail.fields.url")}>
                      <a href={website.url} target="_blank" rel="noreferrer">
                        {website.url}
                      </a>
                    </Field>
                    <Field label={t("websites.detail.fields.status")}>
                      <StatusChip status={website.status} />
                    </Field>
                    <Field label={t("websites.detail.fields.sitemap")}>{website.sitemap || "—"}</Field>
                    <Field label={t("websites.detail.fields.competitorUrl")}>{website.competitor_url || "—"}</Field>
                    <Field label={t("websites.detail.fields.linkedDocuments")}>
                      {website.document_count ?? t("websites.detail.notAvailable")}
                    </Field>
                    <Field label={t("websites.detail.fields.lastCrawled")}>{formatDate(website.last_crawled)}</Field>
                  </Paper>
                </Grid>

                <Grid item xs={12} md={6}>
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" gutterBottom>
                      {t("websites.detail.actions")}
                    </Typography>
                    <Stack spacing={1.5} sx={{ mt: 1 }}>
                      <Button
                        variant="outlined"
                        startIcon={<TravelExploreIcon />}
                        onClick={handleCrawl}
                        disabled={crawling}
                      >
                        {crawling ? t("websites.detail.queuing") : t("websites.detail.crawlNow")}
                      </Button>
                      <Button
                        variant="outlined"
                        startIcon={<EventIcon />}
                        onClick={() => navigate(`/tasks?website_id=${website.id}`)}
                      >
                        {t("websites.detail.scheduleTask")}
                      </Button>
                      {wpSite && (
                        <Button
                          variant="outlined"
                          startIcon={<AutoAwesomeIcon />}
                          onClick={() => navigate(`/wordpress/pages?site_id=${wpSite.id}`)}
                        >
                          {t("websites.detail.manageWordPress")}
                        </Button>
                      )}
                    </Stack>
                    {wpSite && (
                      <Box sx={{ mt: 2 }}>
                        <Chip
                          label={t("websites.detail.wordPressConnected")}
                          color="success"
                          size="small"
                          icon={<AutoAwesomeIcon />}
                        />
                      </Box>
                    )}
                  </Paper>
                </Grid>
              </Grid>
            )}

            {tabValue === 1 && <CrawledPagesPanel website={website} onFeedback={setFeedback} />}

            {tabValue === 2 && <IndexingPanel website={website} onFeedback={setFeedback} />}
          </Box>
        </>
      )}

      {settingsOpen && website && (
        <WebsiteActionModal
          open={settingsOpen}
          onClose={() => {
            if (!isSaving) setSettingsOpen(false);
          }}
          websiteData={website}
          onSave={handleSaveSettings}
          onDelete={handleDelete}
          isSaving={isSaving}
        />
      )}
    </PageLayout>
  );
};

export default WebsiteDetailPage;
