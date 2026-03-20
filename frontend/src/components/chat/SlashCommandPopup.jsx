import React, { useRef, useEffect } from "react";
import { Popper, Paper, Box, Typography, Chip, Fade } from "@mui/material";

/**
 * Autocomplete dropdown for slash commands.
 * Rendered via MUI Popper (portal) anchored to the chat input.
 *
 * Props:
 *   commands — array of command objects to display
 *   selectedIndex — currently highlighted row index
 *   onSelect — callback when a command is clicked
 *   anchorEl — DOM element to anchor the popper to
 *   open — boolean visibility
 */
export default function SlashCommandPopup({ commands, selectedIndex, onSelect, anchorEl, open }) {
  const listRef = useRef(null);

  // Scroll selected item into view
  useEffect(() => {
    if (listRef.current && selectedIndex >= 0) {
      const items = listRef.current.querySelectorAll("[data-command-row]");
      if (items[selectedIndex]) {
        items[selectedIndex].scrollIntoView({ block: "nearest" });
      }
    }
  }, [selectedIndex]);

  if (!commands || commands.length === 0) return null;

  const categoryColors = {
    generation: "secondary",
    model: "info",
    utility: "default",
    custom: "warning",
  };

  return (
    <Popper
      open={open}
      anchorEl={anchorEl}
      placement="top-start"
      style={{ zIndex: 1500 }}
      transition
    >
      {({ TransitionProps }) => (
        <Fade {...TransitionProps} timeout={150}>
          <Paper
            elevation={8}
            sx={{
              maxHeight: 320,       // ~8 rows
              overflow: "auto",
              width: 380,
              mb: 0.5,
              border: "1px solid",
              borderColor: "divider",
            }}
            ref={listRef}
          >
            {commands.map((cmd, idx) => (
              <Box
                key={cmd.name}
                data-command-row
                onClick={() => onSelect(cmd)}
                sx={{
                  px: 1.5,
                  py: 0.75,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  bgcolor: idx === selectedIndex ? "action.selected" : "transparent",
                  "&:hover": { bgcolor: "action.hover" },
                  borderBottom: idx < commands.length - 1 ? "1px solid" : "none",
                  borderColor: "divider",
                }}
              >
                <Typography
                  variant="body2"
                  sx={{ fontFamily: "monospace", fontWeight: "bold", minWidth: 100, flexShrink: 0 }}
                >
                  {cmd.name}
                </Typography>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                >
                  {cmd.description}
                </Typography>
                <Chip
                  label={cmd.category}
                  size="small"
                  color={categoryColors[cmd.category] || "default"}
                  variant="outlined"
                  sx={{ height: 20, fontSize: "0.65rem" }}
                />
              </Box>
            ))}
          </Paper>
        </Fade>
      )}
    </Popper>
  );
}
