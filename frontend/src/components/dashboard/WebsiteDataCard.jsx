// frontend/src/components/dashboard/WebsiteDataCard.jsx
// Version 3.1: Make website list scrollable, remove item limit.

import React, { useState, useEffect, useCallback } from "react";
import {
  CircularProgress,
  Alert,
  List,
  ListItem,
  ListItemText,
  Typography,
  Link,
  Avatar,
  ListItemAvatar,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
import WebsiteActionModal from "../modals/WebsiteActionModal";
import EntityContextMenu from "../common/EntityContextMenu";
import { entityLinkActions } from "../../utils/entityLinks";
import useContextMenu from "../../hooks/useContextMenu";
import copyText from "../../utils/copyText";
import {
  getWebsites,
  updateWebsite,
  createWebsite,
  deleteWebsite,
} from "../../api";
import { getLogoUrl } from "../../config/logoConfig";
import { useUiTranslation } from "../../i18n";

const WebsiteDataCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      ...props
    },
    ref,
  ) => {
    const { t } = useUiTranslation();
    const [websites, setWebsites] = useState([]);
    const navigate = useNavigate();
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState(null);
    const [actionModalOpen, setActionModalOpen] = useState(false);
    const [currentWebsite, setCurrentWebsite] = useState(null);
    const [isSaving, setIsSaving] = useState(false);
    const rowMenu = useContextMenu();

    const fetchWebsites = useCallback(async () => {
      setIsLoading(true);
      setError(null);
      try {
        const result = await getWebsites();
        if (result && result.error) {
          throw new Error(result.error);
        }
        setWebsites(Array.isArray(result) ? result : []);
      } catch (err) {
        console.error("WebsiteDataCard fetch error:", err);
        setError(err.message || t("dashboard.website.loadFailed"));
        setWebsites([]);
      } finally {
        setIsLoading(false);
      }
    }, []);

    useEffect(() => {
      fetchWebsites();
    }, [fetchWebsites]);

    const handleOpenModal = (site) => {
      setCurrentWebsite(site);
      setActionModalOpen(true);
    };

    const handleCloseModal = () => {
      if (isSaving) return;
      setActionModalOpen(false);
      setCurrentWebsite(null);
    };

    const handleSave = async (idOrData, maybeData) => {
      setIsSaving(true);
      try {
        let response;
        if (maybeData !== undefined) {
          response = await updateWebsite(idOrData, maybeData);
        } else {
          response = await createWebsite(idOrData);
        }
        if (response && response.error) throw new Error(response.error);
        handleCloseModal();
        fetchWebsites();
      } catch (err) {
        console.error("WebsiteDataCard save error:", err);
      } finally {
        setIsSaving(false);
      }
    };

    const handleDelete = async (id) => {
      if (!window.confirm(t("dashboard.website.deleteConfirm"))) return;
      setIsSaving(true);
      try {
        await deleteWebsite(id);
        handleCloseModal();
        fetchWebsites();
      } catch (err) {
        console.error("WebsiteDataCard delete error:", err);
      } finally {
        setIsSaving(false);
      }
    };

    return (
      <>
        <DashboardCardWrapper
          ref={ref}
          style={style}
          isMinimized={isMinimized}
          onToggleMinimize={onToggleMinimize}
          cardColor={cardColor}
          onCardColorChange={onCardColorChange}
          title={t("dashboard.website.title")}
          {...props}
          contextMenuActions={[{ label: t("common.refresh"), onClick: fetchWebsites }]}
        >
          {isLoading && (
            <CircularProgress
              size={22}
              sx={{ display: "block", mx: "auto", my: 2 }}
            />
          )}
          {error && (
            <Alert severity="error" sx={{ my: 1 }}>
              {error}
            </Alert>
          )}
          {!isLoading && !error && websites.length === 0 && (
            <Typography
              variant="body2"
              sx={{ color: "text.secondary", mt: 2, textAlign: "center" }}
            >
              {" "}
              {t("dashboard.website.empty")}{" "}
            </Typography>
          )}

          {!isLoading && !error && websites.length > 0 && (
            // --- MODIFIED: Removed slice() and "...and X more" ListItem ---
            <List
              dense
              sx={{ pt: 0, overflowY: "auto", maxHeight: "calc(100% - 16px)" }}
            >
              {websites.map((site) => (
                <ListItem
                  key={site.id}
                  disableGutters
                  sx={{
                    py: 0.5,
                    cursor: "pointer",
                    "&:hover": {
                      backgroundColor: "action.hover",
                      borderRadius: 1,
                    },
                  }}
                  onClick={() => navigate(`/websites?websiteId=${site.id}`)}
                  onContextMenu={(e) => rowMenu.open(e, site)}
                  className="non-draggable"
                >
                  {site.client?.logo_path && (
                    <ListItemAvatar>
                      <Avatar
                        variant="rounded"
                        src={getLogoUrl(site.client.logo_path)}
                        alt={site.client.name}
                        sx={{ width: 32, height: 32 }}
                      />
                    </ListItemAvatar>
                  )}
                  <ListItemText
                    primary={
                      <Link
                        href={site.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        underline="hover"
                        color="inherit"
                        sx={{
                          display: "block",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {" "}
                        {site.url || t("dashboard.website.unnamed")}{" "}
                      </Link>
                    }
                    secondary={t("dashboard.website.details", {
                      project: site.project?.name || t("layout.notAvailable"),
                      docs: site.document_count ?? t("layout.notAvailable"),
                    })}
                    secondaryTypographyProps={{
                      style: {
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      },
                    }}
                  />
                </ListItem>
              ))}
              {/* Removed the "...and X more" ListItem */}
            </List>
            // --- END MODIFICATION ---
          )}
          <EntityContextMenu
            anchorPosition={rowMenu.anchorPosition}
            onClose={rowMenu.close}
            actions={
              rowMenu.payload
                ? [
                    { label: t("common.open"), onClick: () => navigate(`/websites?websiteId=${rowMenu.payload.id}`) },
                    rowMenu.payload.url && {
                      label: t("dashboard.website.openUrl"),
                      onClick: () => window.open(rowMenu.payload.url, "_blank", "noopener,noreferrer"),
                    },
                    rowMenu.payload.url && { label: t("dashboard.website.copyUrl"), onClick: () => copyText(rowMenu.payload.url) },
                    { label: t("dashboard.edit"), onClick: () => handleOpenModal(rowMenu.payload), dividerBefore: true },
                    { label: t("common.delete"), onClick: () => handleDelete(rowMenu.payload.id), color: "error.main" },
                    ...entityLinkActions("website", rowMenu.payload.id, navigate),
                  ]
                : []
            }
          />
        </DashboardCardWrapper>

        {actionModalOpen && (
          <WebsiteActionModal
            open={actionModalOpen}
            onClose={handleCloseModal}
            websiteData={currentWebsite}
            onSave={handleSave}
            onDelete={handleDelete}
            isSaving={isSaving}
          />
        )}
      </>
    );
  },
);

WebsiteDataCard.displayName = "WebsiteDataCard";
export default WebsiteDataCard;
