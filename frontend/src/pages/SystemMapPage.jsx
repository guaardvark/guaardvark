// frontend/src/pages/SystemMapPage.jsx
//
// X-ray of the running codebase as a living constellation.
// Same DNA as guaardvark.com's hero (translucent blue nodes, faint blue
// links, occasional pulses) but the data is real: system_mapper analyzes
// ~715 modules + their import edges + findings, the canvas paints them.
//
// Right-side panel toggles between:
//   - Activity log (default): shows live tool calls flowing through chat
//   - Detail view: when hovering or selecting a node
//
// Section-color legend lives in the top HUD strip.
// Bottom-left card has a one-line cheat sheet for the mouse controls.

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
  Divider,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import SearchIcon from "@mui/icons-material/Search";
import CloseIcon from "@mui/icons-material/Close";
import BubbleChartIcon from "@mui/icons-material/BubbleChart";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import BoltIcon from "@mui/icons-material/Bolt";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import SendIcon from "@mui/icons-material/Send";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";
import { io } from "socket.io-client";

import PageLayout from "../components/layout/PageLayout";
import { SystemMapCanvas } from "../components/systemmap";
import { pathToSection, moduleNameToPath } from "../components/systemmap/pathUtils";
import { useMapPalette } from "../components/systemmap/palette";
import {
  fetchSystemMap,
  fetchFindings,
  dispatchFinding,
  dismissFinding,
} from "../api/systemMapService";
import { SOCKET_URL } from "../api/apiClient";

// Mirror of the SECTION_HUE in SystemMapCanvas — keep these in sync.
// `prefix` is what the canvas matches each node's section against
// (startsWith). Click toggles the prefix in the highlightedPrefixes Set.
const LEGEND = [
  { label: "API", prefix: "backend/api", hue: 195 },
  { label: "Services", prefix: "backend/services", hue: 207 },
  { label: "Utils", prefix: "backend/utils", hue: 215 },
  { label: "Tools", prefix: "backend/tools", hue: 187 },
  { label: "Tasks", prefix: "backend/tasks", hue: 224 },
  { label: "Frontend", prefix: "frontend/", hue: 209 },
  { label: "Plugins", prefix: "plugins", hue: 230 },
];

function severityCounts(map) {
  const c = { high: 0, medium: 0, low: 0, info: 0 };
  for (const f of map?.findings || []) {
    if (c[f.severity] !== undefined) c[f.severity]++;
  }
  return c;
}

// Tool name → module path, best-effort. We look for any module name in
// the dependency graph that ends with `.<tool_name>` (the conventional
// layout: tools live at backend.tools.<tool_name>).
function findModuleForTool(toolName, moduleNames) {
  if (!toolName) return null;
  // Native MCP proxies look like 'filesystem_list_directory' — strip the
  // server prefix and try matching the remainder too.
  const candidates = [toolName];
  if (toolName.includes("_")) {
    const parts = toolName.split("_");
    if (parts.length >= 2) candidates.push(parts.slice(1).join("_"));
  }
  for (const c of candidates) {
    const exact = moduleNames.find((m) => m.endsWith(`.${c}`));
    if (exact) return exact;
  }
  // Substring fallback
  for (const c of candidates) {
    const sub = moduleNames.find((m) => m.toLowerCase().includes(c.toLowerCase()));
    if (sub) return sub;
  }
  return null;
}

// Pill-shaped toggle for an overlay (ghost endpoints / tool graph). Same chip
// primitive as the section legend; `activeColor` is an "r, g, b" triplet so the
// active state tints to the overlay's render color.
function OverlayChip({ label, active, activeColor, onToggle }) {
  const P = useMapPalette();
  return (
    <Box
      role="button"
      tabIndex={0}
      aria-pressed={active}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onToggle();
        }
      }}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.7,
        px: 0.9,
        py: 0.4,
        borderRadius: "999px",
        cursor: "pointer",
        userSelect: "none",
        border: active
          ? `1px solid rgba(${activeColor}, 0.85)`
          : `1px solid ${P.ink(0.12)}`,
        bgcolor: active ? `rgba(${activeColor}, 0.18)` : P.ink(0.04),
        transition: "all 160ms ease",
        "&:hover": {
          bgcolor: active ? `rgba(${activeColor}, 0.26)` : P.ink(0.10),
          borderColor: `rgba(${activeColor}, 0.55)`,
        },
      }}
    >
      <Box
        sx={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          bgcolor: `rgba(${activeColor}, ${active ? 1 : 0.5})`,
          boxShadow: active ? `0 0 12px rgba(${activeColor}, 0.7)` : "none",
        }}
      />
      <Typography
        variant="caption"
        sx={{
          color: active ? `rgba(${activeColor}, 0.95)` : P.ink(0.55),
          fontSize: "0.65rem",
          letterSpacing: 0.5,
          textTransform: "uppercase",
        }}
      >
        {label}
      </Typography>
    </Box>
  );
}

export default function SystemMapPage() {
  const P = useMapPalette();
  const SEVERITY_COLOR = P.severity;
  const [map, setMap] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [hovered, setHovered] = useState(null);
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState("");
  const [activity, setActivity] = useState([]);   // rolling event log
  const [highlightedPrefixes, setHighlightedPrefixes] = useState(() => new Set());
  // Overlay toggles — both default OFF so the baseline constellation is unchanged.
  const [showGhostEndpoints, setShowGhostEndpoints] = useState(false);
  const [showToolGraph, setShowToolGraph] = useState(false);

  const toggleSectionHighlight = useCallback((prefix) => {
    setHighlightedPrefixes((prev) => {
      const next = new Set(prev);
      if (next.has(prefix)) next.delete(prefix);
      else next.add(prefix);
      return next;
    });
  }, []);
  const searchRef = useRef(null);
  const canvasRef = useRef(null);

  // Findings panel state
  const [findings, setFindings] = useState([]);
  const [panelTab, setPanelTab] = useState("findings"); // 'findings' | 'activity'
  const [sevFilter, setSevFilter] = useState("high,medium"); // default to actionable
  const [dispatchingId, setDispatchingId] = useState(null);
  const [toast, setToast] = useState(null);

  const loadFindings = useCallback(async () => {
    try {
      const data = await fetchFindings({ severity: sevFilter || null });
      setFindings(Array.isArray(data?.findings) ? data.findings : []);
    } catch {
      /* findings are best-effort; the galaxy still renders without them */
    }
  }, [sevFilter]);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchSystemMap({ refresh });
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

  useEffect(() => {
    loadFindings();
  }, [loadFindings]);

  // Jump the camera to the module a finding points at (first .py path).
  const flyToFinding = useCallback(
    (finding) => {
      const pyPath = (finding.paths || []).find((p) => p.endsWith(".py"));
      if (!pyPath) return;
      const mod = pyPath.slice(0, -3).replace(/\//g, ".");
      if (canvasRef.current) canvasRef.current.flyTo(mod);
      setSelected(mod);
    },
    [],
  );

  const handleDispatch = useCallback(async (finding) => {
    setDispatchingId(finding.id);
    try {
      const res = await dispatchFinding(finding.id);
      // Backend now returns a top-level `reason` in every case: queued (async),
      // gated (locked/disabled/running), not-dispatchable, or enqueue failure.
      const reason = res?.reason || res?.result?.reason;
      if (res?.queued) {
        setToast(reason || "Dispatched to the self-improvement agent — running in the background.");
      } else if (res?.success) {
        setToast(reason || "Dispatched to the self-improvement agent — review the proposed fix in Settings.");
      } else {
        setToast(`Dispatch didn't run — ${reason || "no reason given"}`);
      }
    } catch (e) {
      setToast(`Dispatch failed: ${e?.message || e}`);
    } finally {
      setDispatchingId(null);
    }
  }, []);

  const handleDismiss = useCallback(
    async (finding) => {
      // optimistic remove
      setFindings((prev) => prev.filter((f) => f.id !== finding.id));
      try {
        await dismissFinding(finding.id);
      } catch {
        loadFindings(); // restore truth on failure
      }
    },
    [loadFindings],
  );

  // Socket.IO subscription — pulse nodes when chat tools fire.
  useEffect(() => {
    if (!map) return;
    const moduleNames = Object.keys(map.dependency_graph || {});
    const socket = io(SOCKET_URL, {
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
      transports: ["polling", "websocket"],
    });

    function pushEvent(kind, data) {
      const toolName = data?.tool || data?.tool_name || data?.name;
      if (!toolName) return;
      const module = findModuleForTool(toolName, moduleNames);
      const entry = {
        id: `${performance.now()}-${Math.random()}`,
        kind,
        tool: toolName,
        module,
        sessionId: data?.session_id,
        ts: Date.now(),
      };
      setActivity((prev) => [entry, ...prev].slice(0, 30));
      if (module && canvasRef.current) {
        canvasRef.current.pulseNode(module);
      }
    }

    const handlers = {
      "chat:tool_call": (d) => pushEvent("call", d),
      "chat:tool_result": (d) => pushEvent("result", d),
    };
    for (const [evt, fn] of Object.entries(handlers)) socket.on(evt, fn);
    return () => {
      for (const [evt, fn] of Object.entries(handlers)) socket.off(evt, fn);
      socket.disconnect();
    };
  }, [map]);

  // Keyboard shortcuts: /, cmd-K, ESC, R
  useEffect(() => {
    const handler = (e) => {
      const inField =
        e.target?.tagName === "INPUT" || e.target?.tagName === "TEXTAREA";
      if (inField) {
        if (e.key === "Escape") {
          if (search) setSearch("");
          else e.target.blur();
        }
        return;
      }
      if (e.key === "/" || (e.metaKey && e.key === "k") || (e.ctrlKey && e.key === "k")) {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "Escape") {
        setSelected(null);
        setSearch("");
      } else if (e.key === "r" || e.key === "R") {
        if (canvasRef.current) canvasRef.current.resetView();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [search]);

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

  // Overlay availability — used to label the toggle chips. The overlays still
  // render nothing when these are 0 (nodes skip silently), but the count tells
  // the user whether a toggle will do anything.
  const ghostEndpointCount = useMemo(
    () => (map?.findings || []).filter((f) => f.kind === "ghost-endpoint").length,
    [map],
  );
  const toolCount = useMemo(
    () => (map?.tool_graph?.registered_tools || []).length,
    [map],
  );

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

  // For a selected-but-not-hovered node, the canvas never hands us a rich node
  // object (selection is just an id), so the detail-panel chips had nothing to
  // render. Reconstruct the same shape from map.node_meta[selected] so the
  // section / lifecycle / importers chips show for selected nodes too.
  const selectedMeta = useMemo(() => {
    if (!selected) return null;
    const meta = map?.node_meta?.[selected] || {};
    return {
      id: selected,
      section: pathToSection(meta.path || moduleNameToPath(selected)),
      lifecycle: meta.lifecycle || "active",
      // importers may be 0 (a valid value we must preserve); only fall back to
      // null when the backend didn't annotate this module at all.
      importers: meta.importers != null ? meta.importers : null,
    };
  }, [selected, map]);

  const activeNode = hovered || selectedMeta;
  const activeNodeId = activeNode?.id;
  const activeFindings = activeNodeId ? findingsByModule.get(activeNodeId) || [] : [];

  // Right panel content — detail view if hovering/selecting, otherwise activity log
  const showDetailPanel = !!activeNode;

  return (
    <PageLayout>
      <Box
        sx={{
          height: "calc(100vh - 64px)",
          display: "flex",
          flexDirection: "column",
          bgcolor: "background.default", // inherits the active theme
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* Top HUD strip */}
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
            flexWrap: "wrap",
          }}
        >
          <Stack direction="row" spacing={1} alignItems="center" sx={{ pointerEvents: "auto" }}>
            <BubbleChartIcon sx={{ color: P.ink(0.85), fontSize: 28 }} />
            <Typography
              variant="h6"
              sx={{
                color: P.ink(0.95),
                fontWeight: 300,
                letterSpacing: 1.5,
              }}
            >
              System Map
            </Typography>
            {map && (
              <Typography variant="caption" sx={{ color: P.ink(0.55), ml: 1 }}>
                {map.file_count} modules ·{" "}
                {map.dependency_graph
                  ? Object.values(map.dependency_graph).reduce(
                      (a, b) => a + (b?.length || 0),
                      0,
                    )
                  : 0}{" "}
                edges
              </Typography>
            )}
          </Stack>
          <Box sx={{ flex: 1 }} />
          {/* Severity HUD */}
          {map && (
            <Stack direction="row" spacing={1} alignItems="center" sx={{ pointerEvents: "auto" }}>
              {sev.high > 0 && (
                <Chip
                  size="small"
                  label={`${sev.high} critical`}
                  sx={{
                    bgcolor: P.danger(0.18),
                    color: SEVERITY_COLOR.high,
                    border: `1px solid ${P.danger(0.4)}`,
                  }}
                />
              )}
              {sev.medium > 0 && (
                <Chip
                  size="small"
                  label={`${sev.medium} medium`}
                  sx={{
                    bgcolor: P.warn(0.15),
                    color: SEVERITY_COLOR.medium,
                    border: `1px solid ${P.warn(0.35)}`,
                  }}
                />
              )}
              <Chip
                size="small"
                label={`${sev.low} hygiene`}
                sx={{
                  bgcolor: P.ink(0.10),
                  color: SEVERITY_COLOR.low,
                  border: `1px solid ${P.ink(0.25)}`,
                }}
              />
            </Stack>
          )}
          {/* Search */}
          <Box
            component="form"
            onSubmit={onSearchSubmit}
            sx={{ pointerEvents: "auto", width: 240 }}
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
                    <SearchIcon sx={{ color: P.ink(0.5), fontSize: 18 }} />
                  </InputAdornment>
                ),
                endAdornment: search && (
                  <InputAdornment position="end">
                    <IconButton size="small" onClick={() => setSearch("")}>
                      <CloseIcon sx={{ color: P.ink(0.5), fontSize: 16 }} />
                    </IconButton>
                  </InputAdornment>
                ),
                sx: {
                  bgcolor: P.fieldBg,
                  color: P.ink(0.85),
                  "& fieldset": { borderColor: P.ink(0.2) },
                  "&:hover fieldset": { borderColor: P.ink(0.4) },
                  fontFamily: "monospace",
                  fontSize: "0.8rem",
                },
              }}
            />
          </Box>
          {/* Reset view (R) */}
          <Tooltip title="Reset view (R)">
            <IconButton
              onClick={() => canvasRef.current?.resetView()}
              sx={{
                color: P.ink(0.7),
                pointerEvents: "auto",
                "&:hover": { color: P.ink(1) },
              }}
            >
              <RestartAltIcon />
            </IconButton>
          </Tooltip>
          {/* Refresh */}
          <Tooltip
            title={
              cacheInfo?.hit
                ? `Cached ${cacheInfo.age_seconds}s ago — click to re-compute`
                : `Just computed in ${cacheInfo?.computed_in_seconds || "?"}s`
            }
          >
            <IconButton
              onClick={() => {
                load(true);
                loadFindings();
              }}
              disabled={loading}
              sx={{
                color: P.ink(0.7),
                pointerEvents: "auto",
                "&:hover": { color: P.ink(1) },
              }}
            >
              {loading ? (
                <CircularProgress size={20} sx={{ color: P.ink(0.7) }} />
              ) : (
                <RefreshIcon />
              )}
            </IconButton>
          </Tooltip>
        </Box>

        {/* Section legend — clickable. Each chip toggles a prefix in
            highlightedPrefixes. When at least one prefix is active, matching
            nodes glow + non-matching nodes fade in the canvas. Multiple chips
            stay active simultaneously. Click again to remove from selection. */}
        <Box
          sx={{
            position: "absolute",
            top: 64,
            left: 16,
            right: 16,
            zIndex: 9,
            display: "flex",
            gap: 1,
            flexWrap: "wrap",
          }}
        >
          {LEGEND.map((s) => {
            const active = highlightedPrefixes.has(s.prefix);
            return (
              <Box
                key={s.prefix}
                role="button"
                tabIndex={0}
                onClick={() => toggleSectionHighlight(s.prefix)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggleSectionHighlight(s.prefix);
                  }
                }}
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 0.7,
                  px: 0.9,
                  py: 0.4,
                  borderRadius: "999px",
                  cursor: "pointer",
                  userSelect: "none",
                  border: active
                    ? `1px solid ${P.hue(s.hue, 80, 78, 0.85)}`
                    : `1px solid ${P.ink(0.12)}`,
                  bgcolor: active
                    ? P.hue(s.hue, 70, 70, 0.18)
                    : P.ink(0.04),
                  transition: "all 160ms ease",
                  "&:hover": {
                    bgcolor: active
                      ? P.hue(s.hue, 70, 72, 0.26)
                      : P.ink(0.10),
                    borderColor: P.hue(s.hue, 80, 78, 0.55),
                  },
                }}
              >
                <Box
                  sx={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    bgcolor: P.hue(s.hue, 75, 78, active ? 1 : 0.85),
                    boxShadow: `0 0 ${active ? 12 : 8}px ${P.hue(
                      s.hue, 75, 78, active ? 0.7 : 0.45,
                    )}`,
                  }}
                />
                <Typography
                  variant="caption"
                  sx={{
                    color: active
                      ? P.hue(s.hue, 90, 90, 0.95)
                      : P.ink(0.55),
                    fontSize: "0.65rem",
                    letterSpacing: 0.5,
                    textTransform: "uppercase",
                  }}
                >
                  {s.label}
                </Typography>
              </Box>
            );
          })}
          {highlightedPrefixes.size > 0 && (
            <Box
              role="button"
              tabIndex={0}
              onClick={() => setHighlightedPrefixes(new Set())}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                px: 0.9,
                py: 0.4,
                borderRadius: "999px",
                cursor: "pointer",
                userSelect: "none",
                color: P.ink(0.55),
                fontSize: "0.65rem",
                letterSpacing: 0.5,
                textTransform: "uppercase",
                "&:hover": { color: P.ink(0.9) },
              }}
            >
              clear
            </Box>
          )}

          {/* Overlay toggles — distinct from the section chips. Both default OFF
              so the baseline constellation is unchanged until the user opts in.
              Reuses the same chip primitive as the section legend above. */}
          {map && (
            <>
              <Box
                sx={{
                  width: "1px",
                  alignSelf: "stretch",
                  bgcolor: P.ink(0.15),
                  mx: 0.5,
                }}
              />
              <OverlayChip
                label={`Ghost endpoints${ghostEndpointCount ? ` (${ghostEndpointCount})` : ""}`}
                active={showGhostEndpoints}
                activeColor={P.warnRGB}
                onToggle={() => setShowGhostEndpoints((v) => !v)}
              />
              <OverlayChip
                label={`Tool graph${toolCount ? ` (${toolCount})` : ""}`}
                active={showToolGraph}
                activeColor={P.ghostRGB}
                onToggle={() => setShowToolGraph((v) => !v)}
              />
            </>
          )}
        </Box>

        {error && (
          <Alert
            severity="error"
            sx={{
              position: "absolute",
              top: 100,
              left: 16,
              right: 16,
              zIndex: 10,
              bgcolor: P.errorBg,
              color: P.errorInk,
              border: `1px solid ${P.errorBorder}`,
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
              highlightedPrefixes={highlightedPrefixes}
              showGhostEndpoints={showGhostEndpoints}
              showToolGraph={showToolGraph}
            />
          )}
        </Box>

        {/* Bottom-left controls cheat sheet */}
        <Box
          sx={{
            position: "absolute",
            bottom: 16,
            left: 16,
            zIndex: 8,
            color: P.ink(0.45),
            fontSize: "0.7rem",
            fontFamily: "monospace",
            letterSpacing: 0.4,
            pointerEvents: "none",
          }}
        >
          drag · pan &nbsp;|&nbsp; wheel · zoom &nbsp;|&nbsp; / · search &nbsp;|&nbsp; r · reset &nbsp;|&nbsp; click chips · spotlight sections
        </Box>

        {/* Right side: detail panel OR activity log */}
        <Paper
          elevation={0}
          sx={{
            position: "absolute",
            right: 16,
            top: 100,
            bottom: 40,
            width: 320,
            p: 0,
            // 90% transparent, glass effect via heavier blur + saturate.
            // Text inside stays opaque (set on individual elements).
            bgcolor: P.panelBg,
            backdropFilter: "blur(20px) saturate(1.4)",
            WebkitBackdropFilter: "blur(20px) saturate(1.4)",
            border: `1px solid ${P.ink(0.15)}`,
            color: P.ink(0.85),
            zIndex: 9,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {showDetailPanel ? (
            <Box sx={{ p: 2, overflowY: "auto", flex: 1 }}>
              <Stack direction="row" alignItems="flex-start" spacing={1}>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    variant="overline"
                    sx={{ color: P.ink(0.5), letterSpacing: 1.2 }}
                  >
                    {hovered ? "Hovered" : "Selected"}
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{
                      fontFamily: "monospace",
                      fontSize: "0.85rem",
                      wordBreak: "break-all",
                      color: P.ink(0.95),
                    }}
                  >
                    {activeNodeId}
                  </Typography>
                </Box>
                {selected && !hovered && (
                  <IconButton size="small" onClick={() => setSelected(null)}>
                    <CloseIcon sx={{ color: P.ink(0.5), fontSize: 18 }} />
                  </IconButton>
                )}
              </Stack>

              <Box sx={{ mt: 1.5 }}>
                {activeNode && activeNode.section && (
                  <Chip
                    size="small"
                    label={activeNode.section}
                    sx={{
                      bgcolor: P.ink(0.10),
                      color: P.ink(0.85),
                      fontSize: "0.7rem",
                      height: 22,
                      mr: 0.5,
                    }}
                  />
                )}
                {activeNode && activeNode.lifecycle && (
                  <Chip
                    size="small"
                    label={activeNode.lifecycle}
                    sx={{
                      bgcolor: P.ink(0.10),
                      color: P.ink(0.85),
                      fontSize: "0.7rem",
                      height: 22,
                      mr: 0.5,
                    }}
                  />
                )}
                {activeNode && activeNode.importers != null && (
                  <Chip
                    size="small"
                    label={`${activeNode.importers} importer${activeNode.importers === 1 ? "" : "s"}`}
                    sx={{
                      bgcolor: P.ink(0.06),
                      color: P.ink(0.7),
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
                    sx={{ color: P.ink(0.5), letterSpacing: 1.2 }}
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
                        bgcolor: P.ink(0.04),
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
                          color: P.ink(0.8),
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
                      sx={{ color: P.ink(0.4), mt: 0.5, display: "block" }}
                    >
                      … and {activeFindings.length - 8} more
                    </Typography>
                  )}
                </Box>
              )}

              {!hovered && selected && (
                <Typography
                  variant="caption"
                  sx={{ color: P.ink(0.4), mt: 2, display: "block" }}
                >
                  Press <code>Esc</code> to clear
                </Typography>
              )}
            </Box>
          ) : panelTab === "findings" ? (
            <FindingsView
              findings={findings}
              openCount={sev.high + sev.medium + sev.low}
              sevFilter={sevFilter}
              onSevFilter={setSevFilter}
              onSelect={flyToFinding}
              onDispatch={handleDispatch}
              onDismiss={handleDismiss}
              dispatchingId={dispatchingId}
              activeTab={panelTab}
              onTab={setPanelTab}
            />
          ) : (
            <ActivityLogView
              activity={activity}
              activeTab={panelTab}
              onTab={setPanelTab}
            />
          )}
        </Paper>

        {/* Transient toast for dispatch results */}
        {toast && (
          <Alert
            severity="info"
            onClose={() => setToast(null)}
            sx={{
              position: "absolute",
              bottom: 16,
              right: 16,
              zIndex: 12,
              maxWidth: 360,
              bgcolor: P.tooltipBg,
              color: P.tooltipInk,
              border: `1px solid ${P.ink(0.3)}`,
            }}
          >
            {toast}
          </Alert>
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
            <CircularProgress sx={{ color: P.ink(0.7) }} />
            <Typography
              variant="caption"
              sx={{
                color: P.ink(0.5),
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

// ────────────────────────────────────────────────────────────────────────

// Shared tab header for the right-side panel base views.
function PanelTabs({ activeTab, onTab, badge }) {
  const P = useMapPalette();
  const tab = (key, label) => (
    <Box
      role="button"
      tabIndex={0}
      onClick={() => onTab(key)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onTab(key);
        }
      }}
      sx={{
        cursor: "pointer",
        userSelect: "none",
        px: 1,
        py: 0.5,
        borderBottom:
          activeTab === key
            ? `2px solid ${P.ink(0.85)}`
            : "2px solid transparent",
        color:
          activeTab === key ? P.ink(0.95) : P.ink(0.45),
        fontSize: "0.7rem",
        letterSpacing: 1.2,
        textTransform: "uppercase",
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
      }}
    >
      {label}
      {key === "findings" && badge > 0 && (
        <Box
          component="span"
          sx={{
            fontSize: "0.6rem",
            px: 0.6,
            borderRadius: "999px",
            bgcolor: P.warn(0.2),
            color: P.severity.medium,
          }}
        >
          {badge}
        </Box>
      )}
    </Box>
  );
  return (
    <Stack direction="row" spacing={1} sx={{ px: 1.5, pt: 1.5, pb: 0.5 }}>
      {tab("findings", "Findings")}
      {tab("activity", "Activity")}
    </Stack>
  );
}

function FindingsView({
  findings,
  openCount,
  sevFilter,
  onSevFilter,
  onSelect,
  onDispatch,
  onDismiss,
  dispatchingId,
  activeTab,
  onTab,
}) {
  const P = useMapPalette();
  const SEVERITY_COLOR = P.severity;
  const filters = [
    { key: "high,medium", label: "Actionable" },
    { key: "high", label: "Critical" },
    { key: "high,medium,low", label: "All" },
  ];
  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PanelTabs activeTab={activeTab} onTab={onTab} badge={openCount} />
      <Box sx={{ px: 1.5, pb: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <WarningAmberIcon sx={{ color: P.warn(0.7), fontSize: 16 }} />
          <Typography variant="caption" sx={{ color: P.ink(0.55), fontSize: "0.7rem" }}>
            Ranked findings · click to locate · dispatch to fix
          </Typography>
        </Stack>
        <Stack direction="row" spacing={0.5} sx={{ mt: 0.8 }}>
          {filters.map((f) => (
            <Box
              key={f.key}
              role="button"
              tabIndex={0}
              onClick={() => onSevFilter(f.key)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSevFilter(f.key);
                }
              }}
              sx={{
                cursor: "pointer",
                userSelect: "none",
                px: 0.8,
                py: 0.2,
                borderRadius: "999px",
                fontSize: "0.62rem",
                letterSpacing: 0.4,
                textTransform: "uppercase",
                border:
                  sevFilter === f.key
                    ? `1px solid ${P.ink(0.6)}`
                    : `1px solid ${P.ink(0.15)}`,
                color:
                  sevFilter === f.key
                    ? P.ink(0.95)
                    : P.ink(0.5),
              }}
            >
              {f.label}
            </Box>
          ))}
        </Stack>
      </Box>
      <Divider sx={{ borderColor: P.ink(0.08) }} />
      <Box sx={{ flex: 1, overflowY: "auto", p: 1.5, pt: 1 }}>
        {findings.length === 0 ? (
          <Typography
            variant="caption"
            sx={{
              color: P.ink(0.35),
              fontStyle: "italic",
              display: "block",
              mt: 2,
              textAlign: "center",
            }}
          >
            No findings at this severity. 🎉
          </Typography>
        ) : (
          findings.map((f) => (
            <Box
              key={f.id}
              sx={{
                mt: 0.8,
                p: 1,
                borderLeft: `2px solid ${SEVERITY_COLOR[f.severity] || SEVERITY_COLOR.low}`,
                bgcolor: P.ink(0.04),
                borderRadius: "2px",
              }}
            >
              <Box
                role="button"
                tabIndex={0}
                onClick={() => onSelect(f)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onSelect(f);
                }}
                sx={{ cursor: "pointer" }}
              >
                <Typography
                  variant="caption"
                  sx={{
                    color: SEVERITY_COLOR[f.severity] || SEVERITY_COLOR.low,
                    fontWeight: 500,
                    textTransform: "uppercase",
                    letterSpacing: 1,
                    fontSize: "0.62rem",
                  }}
                >
                  {f.severity} · {f.kind}
                </Typography>
                <Typography
                  variant="body2"
                  sx={{ color: P.ink(0.85), fontSize: "0.78rem", mt: 0.25 }}
                >
                  {f.summary}
                </Typography>
                {(f.paths || []).slice(0, 2).map((p) => (
                  <Typography
                    key={p}
                    variant="caption"
                    sx={{
                      color: P.ink(0.4),
                      fontFamily: "monospace",
                      fontSize: "0.65rem",
                      display: "block",
                    }}
                  >
                    {p}
                  </Typography>
                ))}
              </Box>
              <Stack direction="row" spacing={0.5} sx={{ mt: 0.6 }}>
                {f.dispatchable && (
                  <Tooltip title="Send to the self-improvement agent to propose a fix">
                    <span>
                      <IconButton
                        size="small"
                        disabled={dispatchingId === f.id}
                        onClick={() => onDispatch(f)}
                        sx={{ color: P.ghost(0.8) }}
                      >
                        {dispatchingId === f.id ? (
                          <CircularProgress size={14} sx={{ color: P.ghost(0.8) }} />
                        ) : (
                          <SendIcon sx={{ fontSize: 15 }} />
                        )}
                      </IconButton>
                    </span>
                  </Tooltip>
                )}
                <Tooltip title="Dismiss — acknowledge and stop showing this finding">
                  <IconButton
                    size="small"
                    onClick={() => onDismiss(f)}
                    sx={{ color: P.ink(0.5) }}
                  >
                    <VisibilityOffIcon sx={{ fontSize: 15 }} />
                  </IconButton>
                </Tooltip>
              </Stack>
            </Box>
          ))
        )}
      </Box>
    </Box>
  );
}

function ActivityLogView({ activity, activeTab, onTab }) {
  const P = useMapPalette();
  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {onTab && <PanelTabs activeTab={activeTab} onTab={onTab} />}
      <Box sx={{ p: 2, pb: 1.5 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <BoltIcon sx={{ color: P.ink(0.7), fontSize: 18 }} />
          <Typography
            variant="overline"
            sx={{ color: P.ink(0.7), letterSpacing: 1.5 }}
          >
            Live activity
          </Typography>
          <Box sx={{ flex: 1 }} />
          <Typography
            variant="caption"
            sx={{
              color: P.ink(0.4),
              fontFamily: "monospace",
              fontSize: "0.7rem",
            }}
          >
            {activity.length}/30
          </Typography>
        </Stack>
        <Typography
          variant="caption"
          sx={{
            color: P.ink(0.4),
            fontSize: "0.7rem",
            display: "block",
            mt: 0.5,
          }}
        >
          Tool calls flowing through the chat appear here. Matched modules pulse in the constellation.
        </Typography>
      </Box>
      <Divider sx={{ borderColor: P.ink(0.08) }} />
      <Box sx={{ flex: 1, overflowY: "auto", p: 1.5, pt: 1 }}>
        {activity.length === 0 ? (
          <Typography
            variant="caption"
            sx={{
              color: P.ink(0.35),
              fontStyle: "italic",
              display: "block",
              mt: 2,
              textAlign: "center",
            }}
          >
            Idle. Ask the agent something to start the pulse.
          </Typography>
        ) : (
          activity.map((evt) => (
            <Box
              key={evt.id}
              sx={{
                mt: 0.6,
                p: 0.8,
                borderLeft: `2px solid ${
                  evt.kind === "result"
                    ? P.ghost(0.6)
                    : P.ink(0.55)
                }`,
                bgcolor: P.ink(0.03),
                borderRadius: "2px",
              }}
            >
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography
                  variant="caption"
                  sx={{
                    color:
                      evt.kind === "result"
                        ? P.ghost(0.85)
                        : P.ink(0.8),
                    fontFamily: "monospace",
                    fontSize: "0.72rem",
                  }}
                >
                  {evt.kind === "result" ? "←" : "→"} {evt.tool}
                </Typography>
                <Box sx={{ flex: 1 }} />
                <Typography
                  variant="caption"
                  sx={{
                    color: P.ink(0.35),
                    fontFamily: "monospace",
                    fontSize: "0.65rem",
                  }}
                >
                  {timeAgo(evt.ts)}
                </Typography>
              </Stack>
              {evt.module && (
                <Typography
                  variant="caption"
                  sx={{
                    color: P.ink(0.4),
                    fontFamily: "monospace",
                    fontSize: "0.65rem",
                    display: "block",
                    mt: 0.25,
                  }}
                >
                  {evt.module}
                </Typography>
              )}
            </Box>
          ))
        )}
      </Box>
    </Box>
  );
}

function timeAgo(ts) {
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (s < 1) return "now";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}
