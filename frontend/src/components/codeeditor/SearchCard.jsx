// frontend/src/components/codeeditor/SearchCard.jsx
// Code search and replace functionality

import React, { useState, useCallback } from "react";
import {
  Box,
  TextField,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Typography,
  Chip,
  Tooltip,
} from "@mui/material";
import {
  Search,
  FindReplace,
} from "@mui/icons-material";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";
import { replaceInTabs } from "./searchReplace";
import { useUiTranslation } from "../../i18n";

const SearchCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      searchResults,
      setSearchResults,
      openTabs,
      setOpenTabs,
      ...props
    },
    ref
  ) => {
    const { t } = useUiTranslation();
    const [searchTerm, setSearchTerm] = useState("");
    const [replaceTerm, setReplaceTerm] = useState("");
    // What the last Replace all did, worded at render: { term } or { replaced, files }.
    const [replaceNote, setReplaceNote] = useState(null);

    // Replace every match of the search term in the open, editable tabs.
    const handleReplaceAll = useCallback(() => {
      if (!searchTerm.trim() || !setOpenTabs) return;
      const { tabs, replaced, files } = replaceInTabs(openTabs, searchTerm, replaceTerm);
      if (replaced === 0) {
        setReplaceNote({ term: searchTerm });
        return;
      }
      setOpenTabs(tabs);
      setSearchResults([]);
      setReplaceNote({ replaced, files });
    }, [searchTerm, replaceTerm, openTabs, setOpenTabs, setSearchResults]);

    const handleSearch = useCallback(() => {
      if (!searchTerm.trim() || !openTabs || !Array.isArray(openTabs)) return;

      const results = [];
      openTabs.forEach((tab, tabIndex) => {
        if (!tab || typeof tab.content !== 'string') return;
        const lines = tab.content.split('\n');
        lines.forEach((line, lineIndex) => {
          if (line.toLowerCase().includes(searchTerm.toLowerCase())) {
            results.push({
              tabIndex,
              fileName: (tab.filePath || "untitled").split('/').pop(),
              lineNumber: lineIndex + 1,
              line: line.trim(),
              matchIndex: line.toLowerCase().indexOf(searchTerm.toLowerCase())
            });
          }
        });
      });

      setSearchResults(results);
    }, [searchTerm, openTabs, setSearchResults]);

    const replaceNoteText = !replaceNote
      ? ""
      : "term" in replaceNote
        ? t("codeEditor.search.noMatches", { term: replaceNote.term })
        : t("codeEditor.search.replaced", {
            matches: t("codeEditor.search.matchCount", { count: replaceNote.replaced }),
            files: t("codeEditor.search.fileCount", { count: replaceNote.files }),
          });

    return (
      <DashboardCardWrapper
        ref={ref}
        title={t("codeEditor.search.title")}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        style={style}
        {...props}
      >
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 1 }}>
          <Box sx={{ mb: 2 }}>
            <TextField
              fullWidth
              size="small"
              placeholder={t("codeEditor.search.placeholder")}
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
              InputProps={{
                endAdornment: (
                  <IconButton size="small" onClick={handleSearch}>
                    <Search fontSize="small" />
                  </IconButton>
                )
              }}
              sx={{ mb: 1 }}
            />
            <TextField
              fullWidth
              size="small"
              placeholder={t("codeEditor.search.replacePlaceholder")}
              value={replaceTerm}
              onChange={(e) => setReplaceTerm(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && handleReplaceAll()}
              InputProps={{
                endAdornment: (
                  <Tooltip title={searchTerm.trim() ? t("codeEditor.search.replaceAll") : t("codeEditor.search.needTerm")}>
                    <span>
                      <IconButton size="small" onClick={handleReplaceAll} disabled={!searchTerm.trim()} aria-label={t("codeEditor.search.replaceAll")}>
                        <FindReplace fontSize="small" />
                      </IconButton>
                    </span>
                  </Tooltip>
                )
              }}
            />
            {replaceNote && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                {replaceNoteText}
              </Typography>
            )}
          </Box>

          <Box sx={{ flex: 1, overflow: 'auto' }}>
            {!searchResults || searchResults.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', mt: 2 }}>
                {t("codeEditor.search.noResults")}
              </Typography>
            ) : (
              <List dense>
                {searchResults.map((result, index) => (
                  <ListItem key={`${result.fileName}-${result.lineNumber}-${index}`} sx={{ py: 0.5 }}>
                    <ListItemText
                      primary={
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          <Chip label={result.fileName} size="small" variant="outlined" />
                          <Typography variant="caption" color="text.secondary">
                            {t("codeEditor.search.line", { line: result.lineNumber })}
                          </Typography>
                        </Box>
                      }
                      secondary={
                        <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>
                          {result.line}
                        </Typography>
                      }
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </Box>
        </Box>
      </DashboardCardWrapper>
    );
  }
);

SearchCard.displayName = "SearchCard";

export default SearchCard;