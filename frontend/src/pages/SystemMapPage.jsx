// frontend/src/pages/SystemMapPage.jsx
//
// X-ray of the running codebase, rendered as a living constellation.
// Same visual DNA as guaardvark.com's hero (translucent blue nodes,
// faint blue links, occasional pulses) but the data is real:
// system_mapper analyzes ~700 modules + their import edges + findings,
// the canvas paints them.
//
// Layout:
//   ┌──────────────────────────────────────┬──────────────┐
//   │                                      │ Side panel:  │
//   │      <SystemMapCanvas />             │ selected /   │
//   │                                      │ hovered node │
//   │   [HUD: severity · cache age · ↻]    │ details      │
//   └──────────────────────────────────────┴──────────────┘
//
//                  [/] = focus search box
//                ESC = clear selection / search

/* eslint-env browser */
import React, { useEffect, useState, useRef, useCallback, useMemo } from "react";
import {
  Box,
  Paper,
  Typography,
  Chip,
  IconButton,
  TextField,
  CircularProgress,
  Alert,
  Tooltip,
  Stack,
  InputAdornment,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import SearchIcon from "@mui/icons-material/Search";
import CloseIcon from "@mui/icons-material/Close";
import HubIcon from "@mui/icons-material/Hub";

import PageLayout from "../components/layout/PageLayout";
import { SystemMapCanvas } from "../components/systemmap";
import { fetchSystemMap } from "../api/systemMapService";

const SEVERITY_COLOR = {
  high: "#ff6e6e",
  medium: "#ffb84d",
  low: "rgba(168, 216, 255, 0.7)",
  info: "rgba(168, 216, 255, 0.4)",
};

function severityCounts(map) {
  const c = { high: 0, medium: 0, low: 0, info: 0 };
  for (const f of map?.findings || []) {
    if (c[f.severity] !== undefined) c[f.severity]++;
  }
  return c;
}

export default function SystemMapPage() {
  const [map, setMap] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [hovered, setHovered] = useState(null);
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState("");
  const searchRef = useRef(null);
  const canvasRef = useRef(null);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchSystemMap({ refresh });
      // handleResponse returns the parsed body directly.
      if (data && data.file_count != null) {
        setMap(data);
      } else if (data && data.success === false) {
        setError(data.error || "Snapshot failed");
      } else {
        setMap(data);
      }
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(false);
  }, [load]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e) => {
      if (e.target?.tagName === "INPUT" || e.target?.tagName === "TEXTAREA") {
        if (e.key === "Escape") {
          if (search) {
            setSearch("");
          } else {
            e.target.blur();
          }
        }
        return;
      }
      if (e.key === "/" || (e.metaKey && e.key === "k") || (e.ctrlKey && e.key === "k")) {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "Escape") {
        setSelected(null);
        setSearch("");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [search]);

  // When the user types a search and presses Enter, fly to the first match.
  const onSearchSubmit = useCallback(
    (e) => {
      e.preventDefault();
      if (!search || !map) return;
      const q = search.toLowerCase();
      const moduleNames = Object.keys(map.dependency_graph || {});
      const match = moduleNames.find((m) => m.toLowerCase().includes(q));
      if (match && canvasRef.current) {
        canvasRef.current.flyTo(match);
        setSelected(match);
      }
    },
    [search, map],
  );

  const sev = useMemo(() => severityCounts(map), [map]);
  const cacheInfo = map?._cache;

  // Build a per-module finding lookup so the side panel doesn't re-scan every render
  const findingsByModule = useMemo(() => {
    const out = new Map();
    if (!map?.findings) return out;
    for (const f of map.findings) {
      for (const p of f.paths || []) {
        const m = p.endsWith(".py") ? p.slice(0, -3).replace(/\//g, ".") : null;
        if (!m) continue;
        if (!out.has(m)) out.set(m, []);
        out.get(m).push(f);
      }
    }
    return out;
  }, [map]);

  const activeNode = hovered || (selected ? { id: selected } : null);
  const activeNodeId = activeNode?.id;
  const activeFindings = activeNodeId ? findingsByModule.get(activeNodeId) || [] : [];

  return (
    <PageLayout>
      <Box
        sx={{
          height: "calc(100vh - 64px)",
          display: "flex",
          flexDirection: "column",
          bgcolor: "rgb(8, 14, 26)",
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* Header strip */}
        <Box
          sx={{
            position: "absolute",
            top: 16,
            left: 16,
            right: 16,
            zIndex: 10,
            display: "flex",
            alignItems: "center",
            gap: 2,
            pointerEvents: "none",
          }}
        >
          <Stack direction="row" spacing={1} alignItems="center" sx={{ pointerEvents: "auto" }}>
            <HubIcon sx={{ color: "rgba(168, 216, 255, 0.85)", fontSize: 28 }} />
            <Typography
              variant="h6"
              sx={{
                color: "rgba(168, 216, 255, 0.95)",
                fontWeight: 300,
                letterSpacing: 1.5,
              }}
            >
              System Map
            </Typography>
            {map && (
              <Typography
                variant="caption"
                sx={{ color: "rgba(168, 216, 255, 0.55)", ml: 1 }}
              >
                {map.file_count} modules · {(map.dependency_graph &&
                  Object.values(map.dependency_graph).reduce((a, b) => a + (b?.length || 0), 0)) || 0} edges
              </Typography>
            )}
          </Stack>
          <Box sx={{ flex: 1 }} />
          {/* Severity HUD */}
          {map && (
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{ pointerEvents: "auto" }}
            >
              {sev.high > 0 && (
                <Chip
                  size="small"
                  label={`${sev.high} critical`}
                  sx={{
                    bgcolor: "rgba(255, 110, 110, 0.18)",
                    color: SEVERITY_COLOR.high,
                    border: "1px solid rgba(255, 110, 110, 0.4)",
                  }}
                />
              )}
              {sev.medium > 0 && (
                <Chip
                  size="small"
                  label={`${sev.medium} medium`}
                  sx={{
                    bgcolor: "rgba(255, 184, 77, 0.15)",
                    color: SEVERITY_COLOR.medium,
                    border: "1px solid rgba(255, 184, 77, 0.35)",
                  }}
                />
              )}
              <Chip
                size="small"
                label={`${sev.low} hygiene`}
                sx={{
                  bgcolor: "rgba(168, 216, 255, 0.10)",
                  color: SEVERITY_COLOR.low,
                  border: "1px solid rgba(168, 216, 255, 0.25)",
                }}
              />
            </Stack>
          )}
          {/* Search */}
          <Box
            component="form"
            onSubmit={onSearchSubmit}
            sx={{ pointerEvents: "auto", width: 260 }}
          >
            <TextField
              size="small"
              placeholder="Search ( / )"
              fullWidth
              value={search}
              inputRef={searchRef}
              onChange={(e) => setSearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon sx={{ color: "rgba(168, 216, 255, 0.5)", fontSize: 18 }} />
                  </InputAdornment>
                ),
                endAdornment: search && (
                  <InputAdornment position="end">
                    <IconButton size="small" onClick={() => setSearch("")}>
                      <CloseIcon sx={{ color: "rgba(168, 216, 255, 0.5)", fontSize: 16 }} />
                    </IconButton>
                  </InputAdornment>
                ),
                sx: {
                  bgcolor: "rgba(20, 30, 50, 0.6)",
                  color: "rgba(168, 216, 255, 0.85)",
                  "& fieldset": { borderColor: "rgba(168, 216, 255, 0.2)" },
                  "&:hover fieldset": { borderColor: "rgba(168, 216, 255, 0.4)" },
                  fontFamily: "monospace",
                  fontSize: "0.8rem",
                },
              }}
            />
          </Box>
          {/* Refresh */}
          <Tooltip
            title={
              cacheInfo?.hit
                ? `Cached ${cacheInfo.age_seconds}s ago — click to re-compute`
                : `Just computed in ${cacheInfo?.computed_in_seconds || "?"}s`
            }
          >
            <IconButton
              onClick={() => load(true)}
              disabled={loading}
              sx={{
                color: "rgba(168, 216, 255, 0.7)",
                pointerEvents: "auto",
                "&:hover": { color: "rgba(168, 216, 255, 1)" },
              }}
            >
              {loading ? (
                <CircularProgress size={20} sx={{ color: "rgba(168, 216, 255, 0.7)" }} />
              ) : (
                <RefreshIcon />
              )}
            </IconButton>
          </Tooltip>
        </Box>

        {error && (
          <Alert
            severity="error"
            sx={{
              position: "absolute",
              top: 70,
              left: 16,
              right: 16,
              zIndex: 10,
              bgcolor: "rgba(255, 100, 100, 0.15)",
              color: "rgba(255, 200, 200, 0.95)",
              border: "1px solid rgba(255, 100, 100, 0.3)",
            }}
          >
            {error}
          </Alert>
        )}

        {/* Canvas */}
        <Box sx={{ flex: 1, position: "relative" }}>
          {map && (
            <SystemMapCanvas
              ref={canvasRef}
              systemMap={map}
              onNodeHover={setHovered}
              onNodeClick={(n) => setSelected(n?.id || null)}
              selectedNodeId={selected}
              searchQuery={search}
            />
          )}
        </Box>

        {/* Side panel */}
        {activeNode && (
          <Paper
            elevation={0}
            sx={{
              position: "absolute",
              right: 16,
              top: 80,
              width: 360,
              maxHeight: "calc(100vh - 120px)",
              overflowY: "auto",
              p: 2,
              bgcolor: "rgba(14, 22, 40, 0.92)",
              backdropFilter: "blur(12px)",
              border: "1px solid rgba(168, 216, 255, 0.15)",
              color: "rgba(168, 216, 255, 0.85)",
              zIndex: 9,
            }}
          >
            <Stack direction="row" alignItems="flex-start" spacing={1}>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography
                  variant="overline"
                  sx={{ color: "rgba(168, 216, 255, 0.5)", letterSpacing: 1.2 }}
                >
                  {hovered ? "Hovered" : "Selected"}
                </Typography>
                <Typography
                  variant="body2"
                  sx={{
                    fontFamily: "monospace",
                    fontSize: "0.85rem",
                    wordBreak: "break-all",
                    color: "rgba(168, 216, 255, 0.95)",
                  }}
                >
                  {activeNodeId}
                </Typography>
              </Box>
              {selected && !hovered && (
                <IconButton size="small" onClick={() => setSelected(null)}>
                  <CloseIcon sx={{ color: "rgba(168, 216, 255, 0.5)", fontSize: 18 }} />
                </IconButton>
              )}
            </Stack>

            <Box sx={{ mt: 1.5 }}>
              {hovered && hovered.lifecycle && (
                <Chip
                  size="small"
                  label={hovered.lifecycle}
                  sx={{
                    bgcolor: "rgba(168, 216, 255, 0.10)",
                    color: "rgba(168, 216, 255, 0.85)",
                    fontSize: "0.7rem",
                    height: 22,
                    mr: 0.5,
                  }}
                />
              )}
              {hovered && hovered.importers != null && (
                <Chip
                  size="small"
                  label={`${hovered.importers} importer${hovered.importers === 1 ? "" : "s"}`}
                  sx={{
                    bgcolor: "rgba(168, 216, 255, 0.06)",
                    color: "rgba(168, 216, 255, 0.7)",
                    fontSize: "0.7rem",
                    height: 22,
                    mr: 0.5,
                  }}
                />
              )}
            </Box>

            {activeFindings.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Typography
                  variant="overline"
                  sx={{ color: "rgba(168, 216, 255, 0.5)", letterSpacing: 1.2 }}
                >
                  Findings ({activeFindings.length})
                </Typography>
                {activeFindings.slice(0, 8).map((f, i) => (
                  <Box
                    key={i}
                    sx={{
                      mt: 0.5,
                      p: 1,
                      borderLeft: `2px solid ${SEVERITY_COLOR[f.severity] || SEVERITY_COLOR.low}`,
                      bgcolor: "rgba(168, 216, 255, 0.04)",
                    }}
                  >
                    <Typography
                      variant="caption"
                      sx={{
                        color: SEVERITY_COLOR[f.severity] || SEVERITY_COLOR.low,
                        fontWeight: 500,
                        textTransform: "uppercase",
                        letterSpacing: 1,
                      }}
                    >
                      {f.severity} · {f.kind}
                    </Typography>
                    <Typography
                      variant="body2"
                      sx={{
                        color: "rgba(168, 216, 255, 0.8)",
                        fontSize: "0.78rem",
                        mt: 0.25,
                      }}
                    >
                      {f.summary}
                    </Typography>
                  </Box>
                ))}
                {activeFindings.length > 8 && (
                  <Typography
                    variant="caption"
                    sx={{ color: "rgba(168, 216, 255, 0.4)", mt: 0.5, display: "block" }}
                  >
                    … and {activeFindings.length - 8} more
                  </Typography>
                )}
              </Box>
            )}

            {!hovered && selected && (
              <Typography
                variant="caption"
                sx={{ color: "rgba(168, 216, 255, 0.4)", mt: 2, display: "block" }}
              >
                Press <code>Esc</code> to clear · click an empty area to deselect
              </Typography>
            )}
          </Paper>
        )}

        {/* Loading overlay (initial only) */}
        {loading && !map && (
          <Box
            sx={{
              position: "absolute",
              inset: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 2,
            }}
          >
            <CircularProgress sx={{ color: "rgba(168, 216, 255, 0.7)" }} />
            <Typography
              variant="caption"
              sx={{
                color: "rgba(168, 216, 255, 0.5)",
                letterSpacing: 2,
                textTransform: "uppercase",
              }}
            >
              Mapping the system…
            </Typography>
          </Box>
        )}
      </Box>
    </PageLayout>
  );
}
