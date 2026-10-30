// The row above a FileDesktop: folders or every file below them, which kinds
// of file, the sort and a search. The same row on the Files and Media pages.

import React, { useEffect, useState } from "react";
import {
  Box, Chip, IconButton, InputAdornment, MenuItem, Select, TextField, ToggleButton,
  ToggleButtonGroup, Tooltip, Typography,
} from "@mui/material";
import {
  ArrowDownward as ArrowDownwardIcon,
  ArrowUpward as ArrowUpwardIcon,
  Clear as ClearIcon,
  Search as SearchIcon,
} from "@mui/icons-material";
import { useUiTranslation } from "../../i18n";
import { SORT_FIELDS } from "./desktopView";

const SEARCH_DELAY_MS = 300;

const DesktopToolbar = ({ view, onViewChange, onSort, kinds, itemCount, onSearch }) => {
  const { t } = useUiTranslation();
  const [text, setText] = useState("");

  useEffect(() => {
    const id = setTimeout(() => onSearch(text.trim()), SEARCH_DELAY_MS);
    return () => clearTimeout(id);
  }, [text, onSearch]);

  const kindLabels = {
    image: t("documents.page.kindImage"),
    video: t("documents.page.kindVideo"),
    audio: t("documents.page.kindAudio"),
    other: t("documents.page.kindOther"),
  };
  const activeKind = view.kinds.length === 1 ? view.kinds[0] : null;

  return (
    <Box
      sx={{
        display: "flex", alignItems: "center", flexWrap: "wrap", gap: 1,
        px: 1.5, py: 0.75, borderBottom: 1, borderColor: "divider", flexShrink: 0,
      }}
    >
      <ToggleButtonGroup
        size="small"
        exclusive
        value={view.scope}
        onChange={(_e, scope) => scope && onViewChange({ scope })}
      >
        <ToggleButton value="folders" sx={{ py: 0.25, px: 1, textTransform: "none" }}>
          {t("documents.page.scopeFolders")}
        </ToggleButton>
        <ToggleButton value="all" sx={{ py: 0.25, px: 1, textTransform: "none" }}>
          {t("documents.page.scopeAll")}
        </ToggleButton>
      </ToggleButtonGroup>

      <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
        <Chip
          size="small"
          label={t("documents.page.kindAll")}
          color={activeKind ? "default" : "primary"}
          variant={activeKind ? "outlined" : "filled"}
          onClick={() => onViewChange({ kinds: [] })}
        />
        {kinds.map((kind) => (
          <Chip
            key={kind}
            size="small"
            label={kindLabels[kind]}
            color={activeKind === kind ? "primary" : "default"}
            variant={activeKind === kind ? "filled" : "outlined"}
            onClick={() => onViewChange({ kinds: activeKind === kind ? [] : [kind] })}
          />
        ))}
      </Box>

      <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
        <Select
          size="small"
          value={view.sort.by}
          onChange={(e) => {
            const by = e.target.value;
            onSort({ by, dir: by === "date" || by === "size" ? "desc" : "asc" });
          }}
          sx={{ fontSize: "0.8rem", "& .MuiSelect-select": { py: 0.5 } }}
          inputProps={{ "aria-label": t("documents.page.sortLabel") }}
        >
          {SORT_FIELDS.map((field) => (
            <MenuItem key={field} value={field}>{t(`documents.page.columns.${field}`)}</MenuItem>
          ))}
        </Select>
        <Tooltip title={view.sort.dir === "asc" ? t("documents.page.sortAsc") : t("documents.page.sortDesc")}>
          <IconButton
            size="small"
            onClick={() => onSort({ ...view.sort, dir: view.sort.dir === "asc" ? "desc" : "asc" })}
          >
            {view.sort.dir === "asc" ? <ArrowUpwardIcon fontSize="small" /> : <ArrowDownwardIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
      </Box>

      <TextField
        size="small"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={t("documents.page.search")}
        sx={{ minWidth: 200, flex: "0 1 280px", "& input": { py: 0.6, fontSize: "0.85rem" } }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
          ),
          endAdornment: text ? (
            <InputAdornment position="end">
              <IconButton size="small" onClick={() => setText("")} aria-label={t("documents.page.clearSearch")}>
                <ClearIcon fontSize="small" />
              </IconButton>
            </InputAdornment>
          ) : null,
        }}
      />

      <Typography variant="caption" color="text.secondary" sx={{ ml: "auto" }}>
        {t("documents.page.itemCount", { count: itemCount })}
      </Typography>
    </Box>
  );
};

export default DesktopToolbar;
