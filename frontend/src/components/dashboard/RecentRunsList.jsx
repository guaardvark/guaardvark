// The recent-runs list on the Image, CSV and Code Generation dashboard cards.
// Rows come from recentRuns.js; each opens the run on its own page.

import React from "react";
import { Alert, Box, Button, Chip, CircularProgress, List, ListItemButton, ListItemText, Typography } from "@mui/material";
import { RECENT_LIMIT, runStatus } from "./recentRuns";
import { uiLocale, useUiTranslation } from "../../i18n";

const formatDate = (iso) => (iso ? new Date(iso).toLocaleDateString(uiLocale()) : "");

/**
 * @param {Array<{key, name, status, date, detail, path}>} rows
 * @param {boolean} loading
 * @param {string|null} error
 * @param {string} emptyText   shown when the feed answered with no runs
 * @param {function(string): void} onOpen   navigate to a path
 * @param {{label: string, path: string}} viewAll  the full list, linked when there are more rows
 */
const RecentRunsList = ({ rows, loading, error, emptyText, onOpen, viewAll }) => {
  // Status chips come from runStatus(), which reads the interface language.
  useUiTranslation();
  if (loading) return <CircularProgress size={22} sx={{ display: "block", mx: "auto", my: 2 }} />;
  if (error) {
    return (
      <Alert severity="warning" sx={{ my: 1 }}>
        {error}
      </Alert>
    );
  }
  if (rows.length === 0) {
    return (
      <Typography variant="body2" sx={{ color: "text.secondary", mt: 2, textAlign: "center" }}>
        {emptyText}
      </Typography>
    );
  }
  return (
    <>
      <List dense sx={{ pt: 0, overflowY: "auto", maxHeight: "calc(100% - 80px)" }}>
        {rows.slice(0, RECENT_LIMIT).map((row) => {
          const status = runStatus(row.status);
          return (
            <ListItemButton
              key={row.key}
              onClick={() => onOpen(row.path)}
              className="non-draggable"
              sx={{ py: 0.5, px: 0.5, borderRadius: 1 }}
            >
              <ListItemText
                primary={
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <Typography variant="body2" noWrap sx={{ fontWeight: "medium", fontSize: "0.8rem", flexGrow: 1 }}>
                      {row.name}
                    </Typography>
                    <Chip label={status.label} color={status.color} size="small" sx={{ fontSize: "0.6rem", height: 18 }} />
                  </Box>
                }
                secondary={[formatDate(row.date), row.detail].filter(Boolean).join(" • ")}
                secondaryTypographyProps={{ noWrap: true, sx: { fontSize: "0.65rem" } }}
              />
            </ListItemButton>
          );
        })}
      </List>
      {rows.length > RECENT_LIMIT && viewAll && (
        <Box sx={{ textAlign: "center", mt: 1 }}>
          <Button
            variant="text"
            size="small"
            onClick={() => onOpen(viewAll.path)}
            className="non-draggable"
            sx={{ fontSize: "0.75rem", textTransform: "none" }}
          >
            {viewAll.label} ({rows.length})
          </Button>
        </Box>
      )}
    </>
  );
};

export default RecentRunsList;
