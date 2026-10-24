// frontend/src/components/dashboard/ClientsDashboardCard.jsx
// Version 1.0: Basic clients list card for dashboard.

import React, { useState, useEffect, useCallback } from "react";
import {
  CircularProgress,
  Alert,
  List,
  ListItem,
  ListItemText,
  Typography,
  Avatar,
  ListItemAvatar,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
import EntityContextMenu from "../common/EntityContextMenu";
import { entityLinkActions } from "../../utils/entityLinks";
import useContextMenu from "../../hooks/useContextMenu";
import { getClients } from "../../api";
import { getLogoUrl } from "../../config/logoConfig";
import { useUiTranslation } from "../../i18n";

const ClientsDashboardCard = React.forwardRef(
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
    const [clients, setClients] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState(null);
    const navigate = useNavigate();
    const rowMenu = useContextMenu();

    const fetchClients = useCallback(async () => {
      setIsLoading(true);
      setError(null);
      try {
        const result = await getClients();
        if (result && result.error) {
          throw new Error(result.error);
        }
        setClients(Array.isArray(result) ? result : []);
      } catch (err) {
        console.error("ClientsDashboardCard fetch error:", err);
        setError(err.message || t("dashboard.clients.loadFailed"));
        setClients([]);
      } finally {
        setIsLoading(false);
      }
    }, []);

    useEffect(() => {
      fetchClients();
    }, [fetchClients]);

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.clients.title")}
        {...props}
        contextMenuActions={[{ label: t("common.refresh"), onClick: fetchClients }]}
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
        {!isLoading && !error && clients.length === 0 && (
          <Typography
            variant="body2"
            sx={{ color: "text.secondary", mt: 2, textAlign: "center" }}
          >
            {t("dashboard.clients.empty")}
          </Typography>
        )}
        {!isLoading && !error && clients.length > 0 && (
          <List
            dense
            sx={{ pt: 0, overflowY: "auto", maxHeight: "calc(100% - 16px)" }}
          >
            {clients.map((client) => (
              <ListItem
                key={client.id}
                disableGutters
                sx={{
                  py: 0.5,
                  cursor: "pointer",
                  "&:hover": {
                    backgroundColor: "action.hover",
                    borderRadius: 1,
                  },
                }}
                onClick={() => navigate(`/clients?clientId=${client.id}`)}
                onContextMenu={(e) => rowMenu.open(e, client)}
                className="non-draggable"
              >
                {client.logo_path && (
                  <ListItemAvatar>
                    <Avatar
                      variant="rounded"
                      src={getLogoUrl(client.logo_path)}
                      alt={client.name}
                      sx={{ width: 32, height: 32 }}
                    />
                  </ListItemAvatar>
                )}
                <ListItemText
                  primary={client.name || t("dashboard.clients.unnamed")}
                  secondary={t("dashboard.clients.projectCount", { count: client.project_count ?? 0 })}
                  primaryTypographyProps={{
                    style: {
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    },
                  }}
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
          </List>
        )}
        <EntityContextMenu
          anchorPosition={rowMenu.anchorPosition}
          onClose={rowMenu.close}
          actions={
            rowMenu.payload
              ? [
                  { label: t("common.open"), onClick: () => navigate(`/clients?clientId=${rowMenu.payload.id}`) },
                  ...entityLinkActions("client", rowMenu.payload.id, navigate),
                ]
              : []
          }
        />
      </DashboardCardWrapper>
    );
  },
);

ClientsDashboardCard.displayName = "ClientsDashboardCard";
export default ClientsDashboardCard;
