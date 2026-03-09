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
} from "@mui/material";
import {
  Search,
  FindReplace,
} from "@mui/icons-material";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";

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
      ...props
    },
    ref
  ) => {
    const [searchTerm, setSearchTerm] = useState("");
    const [replaceTerm, setReplaceTerm] = useState("");

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

    return (
      <DashboardCardWrapper
        ref={ref}
        title="Search"
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
              placeholder="Search..."
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
              placeholder="Replace with..."
              value={replaceTerm}
              onChange={(e) => setReplaceTerm(e.target.value)}
              InputProps={{
                endAdornment: (
                  <IconButton size="small">
                    <FindReplace fontSize="small" />
                  </IconButton>
                )
              }}
            />
          </Box>

          <Box sx={{ flex: 1, overflow: 'auto' }}>
            {!searchResults || searchResults.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', mt: 2 }}>
                No search results
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
                            Line {result.lineNumber}
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