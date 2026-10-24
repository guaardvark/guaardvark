// The documents linked to one client or website, shown over the Files desktop
// when the page is opened with ?client_id= or ?website_id= (utils/entityLinks.js).
// Documents are linked through File or Folder Properties.

import React, { useEffect, useState } from "react";
import {
  Box,
  CircularProgress,
  IconButton,
  List,
  ListItemButton,
  ListItemText,
  Paper,
  Tooltip,
  Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { getDocuments } from "../../api/documentService";
import { BASE_URL, handleResponse } from "../../api/apiClient";
import { useUiTranslation } from "../../i18n";

// The kind word only fills the generic sentences; each known kind has its own
// whole sentence in locales/en/documents.json (the `context` option picks it).
const KIND_WORD = { client: "client", website: "website" };

async function entityName(kind, id) {
  const path = kind === "client" ? `clients/${id}` : `websites/${id}`;
  try {
    const data = await handleResponse(await fetch(`${BASE_URL}/${path}`));
    const entity = data?.data || data;
    return entity?.name || entity?.url || null;
  } catch {
    return null;
  }
}

/**
 * @param {"client"|"website"} kind
 * @param {number|string} id
 * @param {function(object): void} onOpenFile  opens a document the way the desktop does
 * @param {function(): void} onClose
 */
const EntityFilesPanel = ({ kind, id, onOpenFile, onClose }) => {
  const { t } = useUiTranslation();
  const [docs, setDocs] = useState([]);
  const [name, setName] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    Promise.all([
      getDocuments({ [`${kind}_id`]: id, perPage: 500, sort_by: "filename", sort_order: "asc" }),
      entityName(kind, id),
    ]).then(([result, entity]) => {
      if (!live) return;
      if (result?.error) setError(result.error);
      setDocs(result?.documents || []);
      setName(entity);
      setLoading(false);
    });
    return () => {
      live = false;
    };
  }, [kind, id]);

  const word = KIND_WORD[kind] || kind;

  return (
    <Paper
      elevation={8}
      role="region"
      aria-label={t("documents.entityFiles.regionLabel", { context: kind, kind: word })}
      sx={{
        position: "absolute",
        top: 12,
        right: 12,
        width: 360,
        maxWidth: "calc(100% - 24px)",
        maxHeight: "60%",
        display: "flex",
        flexDirection: "column",
        zIndex: 1100,
        bgcolor: "background.paper",
        backgroundImage: "none",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, px: 1.5, py: 1, borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="subtitle2" sx={{ flex: 1, minWidth: 0 }} noWrap>
          {t("documents.entityFiles.title", { context: name ? `${kind}Named` : kind, name: name || word })}
        </Typography>
        {!loading && (
          <Typography variant="caption" color="text.secondary">
            {docs.length}
          </Typography>
        )}
        <Tooltip title={t("documents.actions.close")}>
          <IconButton size="small" onClick={onClose} aria-label={t("documents.actions.close")}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
      <Box sx={{ overflow: "auto" }}>
        {loading && (
          <Box sx={{ display: "flex", justifyContent: "center", p: 2 }}>
            <CircularProgress size={20} />
          </Box>
        )}
        {!loading && error && (
          <Typography variant="body2" color="error" sx={{ p: 1.5 }}>
            {t("documents.entityFiles.loadFailed", { error })}
          </Typography>
        )}
        {!loading && !error && docs.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ p: 1.5 }}>
            {t("documents.entityFiles.empty", { context: kind, kind: word })}
          </Typography>
        )}
        {!loading && docs.length > 0 && (
          <List dense disablePadding>
            {docs.map((doc) => (
              <ListItemButton key={doc.id} onClick={() => onOpenFile(doc)}>
                <ListItemText
                  primary={doc.filename}
                  secondary={doc.folder?.path || t("documents.entityFiles.filesRoot")}
                  primaryTypographyProps={{ noWrap: true }}
                  secondaryTypographyProps={{ noWrap: true }}
                />
              </ListItemButton>
            ))}
          </List>
        )}
      </Box>
    </Paper>
  );
};

export default EntityFilesPanel;
