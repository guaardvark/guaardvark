// frontend/src/components/modals/LinkingModal.jsx
// Version 2.0.3:
// - Removed redundant right-hand side checkbox in the list of linkable items.
// Based on v2.0.2.

import React, { useEffect, useState, useCallback } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  List,
  ListItem,
  ListItemIcon,
  Checkbox,
  ListItemText,
  Tabs,
  Tab,
  CircularProgress,
  Box,
  Typography,
  Alert,
  TextField,
  InputAdornment,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import { translate, useUiTranslation } from "../../i18n";

const LinkingModal = ({
  open,
  onClose,
  primaryEntityType,
  primaryEntityId,
  primaryEntityName,
  linkableTypesConfig, // Expected: [{ entityType, singularLabel, pluralLabel, apiServiceFunction }]
  apiGetLinkedItems, // Expected: func(primaryEntityType, primaryEntityId, linkableEntityType) -> Promise<linkedItemIdsOrObjects[]>
  apiUpdateLinks, // Expected: func(primaryEntityType, primaryEntityId, linkableEntityType, targetEntityIdsToLink) -> Promise<void>
  onLinksUpdated,
}) => {
  const { t } = useUiTranslation();
  const [activeTabConfig, setActiveTabConfig] = useState(null);
  const [availableItems, setAvailableItems] = useState([]);
  const [checkedItemIds, setCheckedItemIds] = useState(new Set());
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [currentTabIndex, setCurrentTabIndex] = useState(0);

  useEffect(() => {
    if (open) {
      setError(null);
      setSearchTerm("");
      if (linkableTypesConfig && linkableTypesConfig.length > 0) {
        const newTabIndex =
          currentTabIndex >= 0 && currentTabIndex < linkableTypesConfig.length
            ? currentTabIndex
            : 0;
        setCurrentTabIndex(newTabIndex);
        setActiveTabConfig(linkableTypesConfig[newTabIndex]);
      } else {
        setActiveTabConfig(null);
        setCurrentTabIndex(0);
        setAvailableItems([]);
        setCheckedItemIds(new Set());
      }
    } else {
      setAvailableItems([]);
      setCheckedItemIds(new Set());
      setError(null);
      setSearchTerm("");
    }
  }, [open, primaryEntityId, linkableTypesConfig]);

  const loadDataForTab = useCallback(async () => {
    if (
      !open ||
      !primaryEntityId ||
      !activeTabConfig ||
      !activeTabConfig.apiServiceFunction ||
      !apiGetLinkedItems
    ) {
      setAvailableItems([]);
      setCheckedItemIds(new Set());
      if (
        open &&
        primaryEntityId &&
        activeTabConfig &&
        !activeTabConfig.apiServiceFunction
      ) {
        console.error(
          `LinkingModal: Configuration error - Missing apiServiceFunction for ${activeTabConfig.pluralLabel}.`,
        );
        setError(translate("dialogs.linking.configError", { items: activeTabConfig.pluralLabel }));
      }
      return;
    }

    setIsLoading(true);
    setError(null);
    try {
      const allItemsResult = await activeTabConfig.apiServiceFunction({
        name: searchTerm,
      });
      let fetchedAvailableItems = [];
      if (allItemsResult) {
        if (allItemsResult.error)
          throw new Error(allItemsResult.error.message || allItemsResult.error);
        fetchedAvailableItems = Array.isArray(allItemsResult)
          ? allItemsResult
          : Array.isArray(allItemsResult.items)
            ? allItemsResult.items
            : Array.isArray(allItemsResult.documents)
              ? allItemsResult.documents
              : Array.isArray(allItemsResult.clients)
                ? allItemsResult.clients
                : [];
      }
      setAvailableItems(fetchedAvailableItems);

      const linkedItemsResult = await apiGetLinkedItems(
        primaryEntityType,
        primaryEntityId,
        activeTabConfig.entityType,
      );
      let linkedIds = [];
      if (linkedItemsResult) {
        if (linkedItemsResult.error)
          throw new Error(
            linkedItemsResult.error.message || linkedItemsResult.error,
          );
        linkedIds = Array.isArray(linkedItemsResult)
          ? linkedItemsResult.map((item) =>
              typeof item === "object" ? item.id : item,
            )
          : [];
      }
      setCheckedItemIds(new Set(linkedIds));
    } catch (err) {
      console.error(
        `LinkingModal: Error loading items for linking (${activeTabConfig?.pluralLabel || "unknown type"}):`,
        err,
      );
      setError(
        err.message ||
          translate("dialogs.linking.loadFailed", { items: activeTabConfig?.pluralLabel || translate("dialogs.linking.itemsLower") }),
      );
      setAvailableItems([]);
      setCheckedItemIds(new Set());
    } finally {
      setIsLoading(false);
    }
  }, [
    open,
    primaryEntityId,
    primaryEntityType,
    activeTabConfig,
    apiGetLinkedItems,
    searchTerm,
  ]);

  useEffect(() => {
    if (open && activeTabConfig) {
      const debounceTimer = setTimeout(() => {
        loadDataForTab();
      }, 300);
      return () => clearTimeout(debounceTimer);
    }
  }, [loadDataForTab, open, activeTabConfig, searchTerm]);

  // Add keyboard handling
  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Escape' && open) {
        onClose();
      }
    };

    if (open) {
      document.addEventListener('keydown', handleKeyDown);
      return () => document.removeEventListener('keydown', handleKeyDown);
    }
  }, [open, onClose]);

  const handleTabChange = (event, newTabIndex) => {
    if (linkableTypesConfig && linkableTypesConfig[newTabIndex]) {
      setCurrentTabIndex(newTabIndex);
      setActiveTabConfig(linkableTypesConfig[newTabIndex]);
      setSearchTerm("");
    }
  };

  const handleSaveLinks = async () => {
    if (!primaryEntityId || !activeTabConfig || !apiUpdateLinks) {
      setError(
        t("dialogs.linking.missingInfo"),
      );
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const result = await apiUpdateLinks(
        primaryEntityType,
        primaryEntityId,
        activeTabConfig.entityType,
        Array.from(checkedItemIds),
      );
      // A helper that reports failure as a value instead of throwing must
      // not be announced as success.
      if (result && result.success === false) {
        throw new Error(result.error || t("dialogs.linking.saveFailed"));
      }
      if (onLinksUpdated) onLinksUpdated();
      onClose(true);
    } catch (err) {
      console.error("LinkingModal: Error saving links:", err);
      setError(err.message || t("dialogs.linking.saveFailed"));
    } finally {
      setIsLoading(false);
    }
  };

  const handleToggleChecked = (id) => {
    setCheckedItemIds((prev) => {
      const nextChecked = new Set(prev);
      if (nextChecked.has(id)) nextChecked.delete(id);
      else nextChecked.add(id);
      return nextChecked;
    });
  };

  const handleSearchChange = (event) => {
    setSearchTerm(event.target.value);
  };

  if (!open) return null;

  if (!primaryEntityId && open) {
    return (
      <Dialog open={open} onClose={() => onClose(false)}>
        <DialogTitle>{t("dialogs.linking.errorTitle")}</DialogTitle>
        <DialogContent>
          <Alert severity="error">
            {t("dialogs.linking.missingEntity")}
          </Alert>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => onClose(false)}>{t("dialogs.shared.close")}</Button>
        </DialogActions>
      </Dialog>
    );
  }

  const hasLinkableTypes =
    linkableTypesConfig && linkableTypesConfig.length > 0;

  return (
    <Dialog
      open={open}
      onClose={() => onClose(false)}
      maxWidth="md"
      fullWidth
      scroll="paper"
    >
      <DialogTitle>
        {t("dialogs.linking.title", {
          items: activeTabConfig?.pluralLabel || t("dialogs.linking.itemsTitle"),
          type: primaryEntityType,
          name: primaryEntityName || t("dialogs.linking.idFallback", { id: primaryEntityId }),
        })}
      </DialogTitle>
      <DialogContent
        dividers
        sx={{ minHeight: "400px", display: "flex", flexDirection: "column" }}
      >
        {!hasLinkableTypes ? (
          <Alert severity="warning" sx={{ mt: 2 }}>
            {t("dialogs.linking.noTypes", { type: primaryEntityType })}
          </Alert>
        ) : (
          <>
            <Tabs
              value={currentTabIndex}
              onChange={handleTabChange}
              sx={{
                mb: 1,
                borderBottom: 1,
                borderColor: "divider",
                flexShrink: 0,
              }}
              variant="scrollable"
              scrollButtons="auto"
              allowScrollButtonsMobile
            >
              {linkableTypesConfig.map((config, index) => (
                <Tab
                  key={config.entityType}
                  label={t("dialogs.linking.tab", { items: config.pluralLabel })}
                  id={`link-tab-${config.entityType}`}
                  aria-controls={`link-panel-${config.entityType}`}
                  value={index}
                />
              ))}
            </Tabs>

            <TextField
              fullWidth
              variant="outlined"
              size="small"
              placeholder={t("dialogs.linking.search", { items: activeTabConfig?.pluralLabel || t("dialogs.linking.itemsLower") })}
              value={searchTerm}
              onChange={handleSearchChange}
              sx={{ mb: 1, flexShrink: 0 }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              }}
            />

            {error && (
              <Alert
                severity="error"
                sx={{ mb: 2, flexShrink: 0 }}
                onClose={() => setError(null)}
              >
                {error}
              </Alert>
            )}

            <Box sx={{ flexGrow: 1, overflowY: "auto" }}>
              {isLoading ? (
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    height: 150,
                  }}
                >
                  <CircularProgress />
                  <Typography sx={{ ml: 2 }}>
                    {t("dialogs.linking.loading", { items: activeTabConfig?.pluralLabel || t("dialogs.linking.itemsLower") })}
                  </Typography>
                </Box>
              ) : availableItems.length === 0 && !error ? (
                <Typography
                  sx={{ textAlign: "center", mt: 2, fontStyle: "italic" }}
                >
                  {searchTerm
                    ? t("dialogs.linking.noneMatching", { items: activeTabConfig?.pluralLabel || t("dialogs.linking.itemsLower") })
                    : t("dialogs.linking.noneToLink", { items: activeTabConfig?.pluralLabel || t("dialogs.linking.itemsLower") })}
                </Typography>
              ) : (
                <List dense>
                  {availableItems.map((item) => (
                    <ListItem
                      key={item.id}
                      onClick={() => handleToggleChecked(item.id)}
                      // MODIFIED: Removed secondaryAction prop to delete right-hand checkbox
                      sx={{
                        cursor: "pointer",
                        "&:hover": { backgroundColor: "action.hover" },
                      }}
                      button
                    >
                      <ListItemIcon sx={{ minWidth: "auto", mr: 1.5 }}>
                        <Checkbox
                          edge="start"
                          checked={checkedItemIds.has(item.id)}
                          tabIndex={-1}
                          disableRipple
                          inputProps={{
                            "aria-labelledby": `checkbox-list-label-${item.id}`,
                          }}
                        />
                      </ListItemIcon>
                      <ListItemText
                        id={`checkbox-list-label-${item.id}`}
                        primary={
                          item.name ||
                          item.title ||
                          item.filename ||
                          t("dialogs.linking.idFallback", { id: item.id })
                        }
                        secondary={
                          item.description ||
                          item.type ||
                          item.url ||
                          item.email ||
                          t("dialogs.linking.idFallback", { id: item.id })
                        }
                        primaryTypographyProps={{ noWrap: true }}
                        secondaryTypographyProps={{
                          noWrap: true,
                          fontSize: "0.75rem",
                        }}
                      />
                    </ListItem>
                  ))}
                </List>
              )}
            </Box>
          </>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2, pt: 2 }}>
        <Button
          onClick={() => onClose(false)}
          disabled={isLoading}
          color="inherit"
        >
          {t("dialogs.shared.cancel")}
        </Button>
        <Button
          onClick={handleSaveLinks}
          variant="contained"
          disabled={
            isLoading || !!error || !activeTabConfig || !hasLinkableTypes
          }
        >
          {isLoading ? (
            <CircularProgress size={24} color="inherit" />
          ) : (
            t("dialogs.linking.saveLinks")
          )}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default LinkingModal;
