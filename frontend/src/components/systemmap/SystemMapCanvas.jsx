// frontend/src/components/systemmap/SystemMapCanvas.jsx
//
// The constellation. Same DNA as guaardvark.com's neural-net.js
// (translucent blue, drift, occasional pulses, link alpha falls off
// with distance) but the data is real: each node is a module, edges
// are import dependencies. Section drives hue (within a 40° range to
// keep it cohesive), lifecycle drives alpha, importer count drives size.
//
// Wheel zooms. Drag pans (left, middle, or shift+left — all the same).
// R resets the view. The canvas itself is transparent — the page
// background shows through, so theme changes propagate automatically.
//
// Inputs: a SystemMap dict (see backend/services/system_mapper).
// Notifies parent of hover/click via onNodeHover / onNodeClick.
// Imperative API via ref: flyTo(id), pulseNode(id), resetView().

import React, { useEffect, useRef, useImperativeHandle, forwardRef } from "react";
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  forceX,
  forceY,
} from "d3-force";

// ────────────────────────────────────────────────────────────────────────
// Color palette
// All hues live in a 187-230 range — same blue organism, no Pride confetti.
// Lifecycle drives alpha multiplier on top.
const SECTION_HUE = {
  "backend/api": 195,         // outermost surface, slight cyan
  "backend/services": 207,    // marketing blue (the reference)
  "backend/utils": 215,       // slightly more blue
  "backend/tools": 187,       // cyan-leaning, LLM-reachable
  "backend/tasks": 224,       // slight purple, async
  "backend/agents": 200,      // distinct, slightly more saturated
  "backend/mcp": 192,         // close to tools — they're peers
  "backend/plugins": 218,
  "backend/middleware": 210,
  "backend/orchestration": 222,
  "backend/rag": 188,
  "backend/memory": 205,
  "backend/cluster": 226,
  "backend/self_improvement": 198,
  "backend/routes": 200,
  "frontend/api": 197,
  "frontend/pages": 203,
  "frontend/components": 209,
  "frontend/stores": 213,
  "frontend/hooks": 217,
  "frontend/contexts": 211,
  "frontend/services": 207,
  "frontend/utils": 219,
  "plugins": 230,
  "cli": 225,
  "scripts": 228,
  "training": 220,
  "top-level": 207,
  "other": 207,
};

// Per-lifecycle alpha multiplier and sat/lightness tweak.
const LIFECYCLE = {
  active:      { alpha: 0.85, sat: 75, light: 78 },
  "auto-loaded": { alpha: 0.65, sat: 70, light: 76 },
  dormant:     { alpha: 0.22, sat: 45, light: 70 },
  archived:    { alpha: 0.30, sat: 12, light: 65 },  // desaturated grey-blue
  test:        { alpha: 0.45, sat: 50, light: 75 },
  script:      { alpha: 0.55, sat: 55, light: 76 },
  config:      { alpha: 0.40, sat: 45, light: 72 },
  skip:        { alpha: 0.20, sat: 20, light: 65 },
};

const PALETTE = {
  // Canvas is transparent — page bg shows through, so we never paint our
  // own background. Edges and effects only.
  edge: "rgba(168, 216, 255, 0.18)",
  cycleEdge: "rgba(255, 110, 110, 0.55)",
  highFinding: "rgba(255, 170, 80, 0.95)",
  mediumFinding: "rgba(255, 220, 130, 0.7)",
};

// HSLA string from a hue with lifecycle bias.
function nodeColor(section, lifecycle, alphaMult = 1) {
  const hue = SECTION_HUE[section] ?? SECTION_HUE.other;
  const lc = LIFECYCLE[lifecycle] || LIFECYCLE.active;
  const a = lc.alpha * alphaMult;
  return `hsla(${hue}, ${lc.sat}%, ${lc.light}%, ${a})`;
}

// Higher-importer modules render bigger. Log-scaled, clamped.
function radiusFor(node) {
  const importers = Math.max(0, node.importers || 0);
  const r = 1.6 + Math.log2(1 + importers) * 0.9;
  return Math.min(8, Math.max(2, r));
}

// Convert "backend/services/foo.py" → "backend/services" (or other section).
function pathToSection(path) {
  if (!path || typeof path !== "string") return "other";
  if (!path.includes("/")) return "top-level";
  if (path.startsWith("backend/")) {
    const sub = path.slice("backend/".length).split("/")[0];
    return `backend/${sub}`;
  }
  if (path.startsWith("frontend/src/")) {
    const sub = path.slice("frontend/src/".length).split("/")[0];
    return `frontend/${sub}`;
  }
  if (path.startsWith("frontend/")) return "frontend/other";
  if (path.startsWith("plugins/")) return "plugins";
  if (path.startsWith("scripts/")) return "scripts";
  if (path.startsWith("cli/")) return "cli";
  if (path.startsWith("training/")) return "training";
  return "other";
}

// Module name "backend.services.foo" → path "backend/services/foo.py".
function moduleNameToPath(name) {
  if (!name) return "";
  return name.replace(/\./g, "/") + ".py";
}

// Build the graph the simulation will run on.
function buildGraph(systemMap) {
  if (!systemMap) return { nodes: [], links: [], nodeIndex: {}, severityCounts: {} };

  const dep = systemMap.dependency_graph || {};
  const moduleNames = Object.keys(dep);

  const nodeMeta = {};
  for (const name of moduleNames) {
    nodeMeta[name] = {
      lifecycle: "active",
      section: pathToSection(moduleNameToPath(name)),
      findings: [],
      importers: 0,
    };
  }

  for (const [, targets] of Object.entries(dep)) {
    for (const t of targets || []) {
      if (nodeMeta[t]) nodeMeta[t].importers++;
    }
  }

  const cycleEdges = new Set();
  for (const f of systemMap.findings || []) {
    if (f.kind === "import-cycle" && f.evidence?.cycle) {
      const cyc = f.evidence.cycle;
      for (let i = 0; i < cyc.length; i++) {
        const a = cyc[i];
        const b = cyc[(i + 1) % cyc.length];
        cycleEdges.add(`${a}||${b}`);
        cycleEdges.add(`${b}||${a}`);
      }
    }
    if (f.kind === "dormant-module") {
      const m = pathToModuleName((f.paths || [])[0]);
      if (m && nodeMeta[m]) nodeMeta[m].lifecycle = "dormant";
    }
    for (const p of f.paths || []) {
      const m = pathToModuleName(p);
      if (m && nodeMeta[m]) {
        nodeMeta[m].findings.push({ kind: f.kind, severity: f.severity });
      }
    }
  }

  const nodes = [];
  const nodeIndex = {};
  const sevPriority = { high: 3, medium: 2, low: 1, info: 0 };
  for (const name of moduleNames) {
    const meta = nodeMeta[name];
    const topSev = (meta.findings || []).reduce(
      (acc, f) =>
        (sevPriority[f.severity] || 0) > (sevPriority[acc] || 0) ? f.severity : acc,
      null,
    );
    // Seed positions in a disc rather than all at (0,0) — d3-force can't
    // escape a delta distribution at origin, which is what produced the
    // "vertical galaxy" effect on first ship. Random in a ~600px disc gives
    // the simulation room to relax into a roughly circular blob.
    const a = Math.random() * Math.PI * 2;
    const r0 = Math.sqrt(Math.random()) * 300;
    const node = {
      id: name,
      lifecycle: meta.lifecycle,
      section: meta.section,
      importers: meta.importers,
      findings: meta.findings,
      topSeverity: topSev,
      x: Math.cos(a) * r0,
      y: Math.sin(a) * r0,
      vx: 0, vy: 0,
      sx: 0, sy: 0,        // screen coords (post-transform); used by hit-test
      depth: 0,            // current z after rotation; affects render
      pulse: 0,
    };
    nodes.push(node);
    nodeIndex[name] = node;
  }

  const links = [];
  for (const [src, targets] of Object.entries(dep)) {
    if (!nodeIndex[src]) continue;
    for (const t of targets || []) {
      if (!nodeIndex[t]) continue;
      const isCycle = cycleEdges.has(`${src}||${t}`);
      links.push({ source: src, target: t, cycle: isCycle });
    }
  }

  const counts = { high: 0, medium: 0, low: 0, info: 0 };
  for (const f of systemMap.findings || []) {
    if (counts[f.severity] !== undefined) counts[f.severity]++;
  }
  return { nodes, links, nodeIndex, severityCounts: counts };
}

function pathToModuleName(path) {
  if (!path) return null;
  if (!path.endsWith(".py")) return null;
  return path.slice(0, -3).replace(/\//g, ".");
}

function neighborsOf(nodeId, links) {
  const n = new Set([nodeId]);
  for (const l of links) {
    const sId = typeof l.source === "object" ? l.source.id : l.source;
    const tId = typeof l.target === "object" ? l.target.id : l.target;
    if (sId === nodeId) n.add(tId);
    if (tId === nodeId) n.add(sId);
  }
  return n;
}

// ────────────────────────────────────────────────────────────────────────

const SystemMapCanvas = forwardRef(function SystemMapCanvas(
  { systemMap, onNodeHover, onNodeClick, selectedNodeId, searchQuery, highlightedPrefixes },
  ref,
) {
  const canvasRef = useRef(null);
  const stateRef = useRef({
    graph: { nodes: [], links: [], nodeIndex: {}, severityCounts: {} },
    sim: null,
    raf: null,
    width: 0,
    height: 0,
    dpr: 1,
    // View state — pan + zoom only, no rotation, no mouse parallax
    camera: { x: 0, y: 0 },
    zoom: 1,
    // Interaction state
    drag: null,                // {startX, startY, baseCamX, baseCamY}
    lastInteractionTime: 0,
    // Hover/spotlight
    hover: null,
    spotlight: null,
    spotlightNeighbors: null,
    searchMatches: null,
    highlightedPrefixes: null, // mirror of prop, read inside the render loop
    pulseClockMs: performance.now(),
  });

  // Reflect highlightedPrefixes into the ref so the render loop sees updates
  // without re-binding the loop on every prop change.
  useEffect(() => {
    stateRef.current.highlightedPrefixes = highlightedPrefixes;
  }, [highlightedPrefixes]);

  // Imperative API (parent calls these via ref).
  useImperativeHandle(ref, () => ({
    flyTo(nodeId) {
      const st = stateRef.current;
      const node = st.graph.nodeIndex[nodeId];
      if (!node) return;
      const startX = st.camera.x;
      const startY = st.camera.y;
      const targetX = -node.x * st.zoom;
      const targetY = -node.y * st.zoom;
      const t0 = performance.now();
      const dur = 600;
      const ease = (t) => 1 - Math.pow(1 - t, 3);
      function step(now) {
        const t = Math.min(1, (now - t0) / dur);
        st.camera.x = startX + (targetX - startX) * ease(t);
        st.camera.y = startY + (targetY - startY) * ease(t);
        if (t < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
      node.pulse = 1;
    },
    pulseNode(nodeId) {
      const st = stateRef.current;
      const node = st.graph.nodeIndex[nodeId];
      if (node) node.pulse = 1;
    },
    resetView() {
      const st = stateRef.current;
      const t0 = performance.now();
      const dur = 400;
      const startCx = st.camera.x;
      const startCy = st.camera.y;
      const startZoom = st.zoom;
      const ease = (t) => 1 - Math.pow(1 - t, 3);
      function step(now) {
        const t = Math.min(1, (now - t0) / dur);
        const e = ease(t);
        st.camera.x = startCx * (1 - e);
        st.camera.y = startCy * (1 - e);
        st.zoom = startZoom + (1 - startZoom) * e;
        if (t < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    },
  }));

  // Build graph + run d3-force when systemMap changes.
  useEffect(() => {
    const st = stateRef.current;
    st.graph = buildGraph(systemMap);

    if (st.sim) {
      st.sim.stop();
      st.sim = null;
    }
    if (!st.graph.nodes.length) return;

    // Center force is the dominant anchor; X/Y nudges keep outliers from
    // dragging the centroid off-axis if the graph is asymmetric.
    const sim = forceSimulation(st.graph.nodes)
      .force(
        "link",
        forceLink(st.graph.links).id((n) => n.id).distance(60).strength(0.4),
      )
      .force("charge", forceManyBody().strength(-90).distanceMax(400))
      .force("center", forceCenter(0, 0))
      // Soft anchors — too strong and the cloud collapses on one axis
      // (that's how the "galaxy sliver" first showed up). 0.025 keeps
      // the centroid honest without squashing the layout.
      .force("centerX", forceX(0).strength(0.025))
      .force("centerY", forceY(0).strength(0.025))
      .force("collide", forceCollide().radius((n) => radiusFor(n) + 4))
      .alpha(1)
      .alphaDecay(0.018)
      .velocityDecay(0.55);
    st.sim = sim;
  }, [systemMap]);

  // Spotlight on selection change.
  useEffect(() => {
    const st = stateRef.current;
    if (!selectedNodeId) {
      st.spotlight = null;
      st.spotlightNeighbors = null;
      return;
    }
    st.spotlight = selectedNodeId;
    st.spotlightNeighbors = neighborsOf(selectedNodeId, st.graph.links);
    const n = st.graph.nodeIndex[selectedNodeId];
    if (n) n.pulse = 1;
  }, [selectedNodeId]);

  // Search highlight.
  useEffect(() => {
    const st = stateRef.current;
    if (!searchQuery || searchQuery.length < 2) {
      st.searchMatches = null;
      return;
    }
    const q = searchQuery.toLowerCase();
    const matches = new Set();
    for (const n of st.graph.nodes) {
      if (n.id.toLowerCase().includes(q)) matches.add(n.id);
    }
    st.searchMatches = matches;
    for (const id of matches) {
      const n = st.graph.nodeIndex[id];
      if (n) n.pulse = 1;
    }
  }, [searchQuery]);

  // Setup canvas + observers + render loop.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    const st = stateRef.current;

    function applySize() {
      const r = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      st.dpr = dpr;
      st.width = r.width;
      st.height = r.height;
      canvas.width = r.width * dpr;
      canvas.height = r.height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    applySize();

    // ResizeObserver catches the initial-mount layout race that window.resize
    // alone misses. The canvas's parent is what we observe; canvas itself is
    // 100% width/height inside it.
    const ro = new ResizeObserver(() => {
      applySize();
      // Re-anchor camera. If the graph cooled in a previous size, the camera
      // was correct then but might be off now. Snap to 0 so the centroid is
      // back at screen-center; the user can re-pan if they want.
      st.camera.x = 0;
      st.camera.y = 0;
    });
    if (canvas.parentElement) ro.observe(canvas.parentElement);
    window.addEventListener("resize", applySize);

    // ── Mouse: hover detection (uses last-frame screen coords stored on each node) ──
    function onMove(e) {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;

      // Drag handling — any button is pan now (rotation removed)
      if (st.drag) {
        st.lastInteractionTime = performance.now();
        const dx = mx - st.drag.startX;
        const dy = my - st.drag.startY;
        st.camera.x = st.drag.baseCamX + dx;
        st.camera.y = st.drag.baseCamY + dy;
        return;
      }

      // Hit-test against each node's last-frame screen coords (computed in tick()).
      let nearest = null;
      let bestD2 = 14 * 14;
      for (const n of st.graph.nodes) {
        const dx = n.sx - mx;
        const dy = n.sy - my;
        const d2 = dx * dx + dy * dy;
        if (d2 < bestD2) {
          bestD2 = d2;
          nearest = n;
        }
      }
      const prev = st.hover;
      st.hover = nearest ? nearest.id : null;
      if (prev !== st.hover && onNodeHover) onNodeHover(nearest);
    }

    function onLeave() {
      st.hover = null;
      if (onNodeHover) onNodeHover(null);
    }

    function onDown(e) {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      // Any drag = pan (rotation was removed). Right-click is suppressed below.
      if (e.button === 0 || e.button === 1) {
        st.drag = {
          startX: mx,
          startY: my,
          baseCamX: st.camera.x,
          baseCamY: st.camera.y,
        };
        if (e.button === 1) e.preventDefault();
      }
    }

    function onUp(e) {
      if (!st.drag) return;
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const dist = Math.hypot(mx - st.drag.startX, my - st.drag.startY);
      const wasDrag = dist > 4;
      st.drag = null;
      // If the mouse barely moved, treat as a click (hover already set st.hover)
      if (!wasDrag && e.button === 0) {
        if (st.hover && onNodeClick) {
          onNodeClick(st.graph.nodeIndex[st.hover]);
        } else if (onNodeClick) {
          onNodeClick(null);
        }
      }
    }

    function onWheel(e) {
      e.preventDefault();
      st.lastInteractionTime = performance.now();
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;

      // Zoom factor: trackpad pinch sends ctrlKey=true with small deltas
      const factor = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015));
      const newZoom = clamp(st.zoom * factor, 0.3, 3);
      const ratio = newZoom / st.zoom;
      // Anchor zoom at cursor: keep the world point under the cursor fixed.
      // worldPoint = (mx - w/2 - camera.x) / zoom
      // After zoom: we want camera s.t. cursor still over the same world point.
      const cx = mx - st.width / 2;
      const cy = my - st.height / 2;
      st.camera.x = cx - (cx - st.camera.x) * ratio;
      st.camera.y = cy - (cy - st.camera.y) * ratio;
      st.zoom = newZoom;
    }

    // contextmenu suppress on right-click (keeps right-button reserved)
    function onCtx(e) {
      e.preventDefault();
    }

    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);
    canvas.addEventListener("mousedown", onDown);
    window.addEventListener("mouseup", onUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("contextmenu", onCtx);

    // Returns true when at least one prefix is highlighted AND this node's
    // section starts with one of them. When the set is empty, no highlighting
    // is in effect and every node renders at full alpha.
    function isInHighlight(node, set) {
      if (!set || set.size === 0) return null; // null = "highlighting inactive"
      for (const p of set) if (node.section && node.section.startsWith(p)) return true;
      return false;
    }

    // ── Render loop ──
    function tick(now) {
      const dt = Math.min(48, now - (st.pulseClockMs || now)) / 16.666;
      st.pulseClockMs = now;

      const w = st.width;
      const h = st.height;

      // Canvas is transparent — page bg shows through. Just clear each frame.
      ctx.clearRect(0, 0, w, h);

      // Idle drift only when user hasn't interacted in 3s+
      const idle = !st.drag && now - (st.lastInteractionTime || 0) > 3000;
      if (idle) {
        st.camera.x += Math.sin(now / 9000) * 0.04;
        st.camera.y += Math.cos(now / 11000) * 0.04;
      }

      // Project each node into screen space (pure 2D — rotation removed).
      const cxScreen = w / 2 + st.camera.x;
      const cyScreen = h / 2 + st.camera.y;
      const z = st.zoom;
      for (const n of st.graph.nodes) {
        n.sx = cxScreen + n.x * z;
        n.sy = cyScreen + n.y * z;
      }

      // Edges (drawn first, behind nodes).
      ctx.lineWidth = 1;
      const spotlight = st.spotlight;
      const neighbors = st.spotlightNeighbors;
      const hl = st.highlightedPrefixes;
      for (const l of st.graph.links) {
        const a = typeof l.source === "object" ? l.source : st.graph.nodeIndex[l.source];
        const b = typeof l.target === "object" ? l.target : st.graph.nodeIndex[l.target];
        if (!a || !b) continue;
        const dx = a.sx - b.sx;
        const dy = a.sy - b.sy;
        const d = Math.sqrt(dx * dx + dy * dy);
        const linkRange = 280 * z;
        let alpha = Math.max(0, 1 - d / linkRange) * 0.55;
        if (spotlight && !(neighbors.has(a.id) && neighbors.has(b.id))) {
          alpha *= 0.18;
        }
        // Section highlight: edges where neither endpoint is in highlight fade
        if (hl && hl.size > 0) {
          const aIn = isInHighlight(a, hl);
          const bIn = isInHighlight(b, hl);
          if (!aIn && !bIn) alpha *= 0.18;
        }
        if (l.cycle) {
          ctx.strokeStyle = `rgba(255, 110, 110, ${Math.max(0.18, alpha * 1.2)})`;
        } else {
          ctx.strokeStyle = `rgba(168, 216, 255, ${alpha * 0.4})`;
        }
        ctx.beginPath();
        ctx.moveTo(a.sx, a.sy);
        ctx.lineTo(b.sx, b.sy);
        ctx.stroke();
      }

      for (const n of st.graph.nodes) {
        if (n.pulse > 0) n.pulse -= 0.012 * dt;
        if (n.pulse < 0) n.pulse = 0;
        if (
          (n.topSeverity === "high" || n.topSeverity === "medium") &&
          Math.random() < (n.topSeverity === "high" ? 0.0009 : 0.0004)
        ) {
          n.pulse = 1;
        }

        const r = (radiusFor(n) + n.pulse * 4) * z;
        if (r < 0.5) continue;

        let alpha = 1;
        if (spotlight && !neighbors.has(n.id)) alpha *= 0.12;
        if (st.searchMatches && !st.searchMatches.has(n.id)) alpha = Math.min(alpha, 0.18);

        // Section-prefix highlighting from the legend chips
        const inHl = isInHighlight(n, hl);
        if (inHl === false) alpha *= 0.22;       // fade non-matching when highlight active
        // (inHl === true or null → no penalty)

        const baseColor = nodeColor(n.section, n.lifecycle, alpha);

        // Soft glow on pulse
        if (n.pulse > 0.05) {
          const glow = ctx.createRadialGradient(n.sx, n.sy, 0, n.sx, n.sy, r * 4);
          const glowColor =
            n.topSeverity === "high"
              ? PALETTE.highFinding
              : n.topSeverity === "medium"
                ? PALETTE.mediumFinding
                : "rgba(168, 216, 255, 0.55)";
          glow.addColorStop(0, glowColor);
          glow.addColorStop(1, "rgba(168, 216, 255, 0)");
          ctx.globalAlpha = n.pulse * 0.5;
          ctx.fillStyle = glow;
          ctx.beginPath();
          ctx.arc(n.sx, n.sy, r * 4, 0, Math.PI * 2);
          ctx.fill();
          ctx.globalAlpha = 1;
        }

        // Highlighted nodes get a soft pulsing aura so the eye finds them
        if (inHl === true) {
          const auraR = r * 3;
          const aura = ctx.createRadialGradient(n.sx, n.sy, 0, n.sx, n.sy, auraR);
          aura.addColorStop(0, "rgba(255, 255, 255, 0.18)");
          aura.addColorStop(1, "rgba(168, 216, 255, 0)");
          ctx.fillStyle = aura;
          ctx.beginPath();
          ctx.arc(n.sx, n.sy, auraR, 0, Math.PI * 2);
          ctx.fill();
        }

        // Hover ring
        if (st.hover === n.id) {
          ctx.strokeStyle = `rgba(255, 255, 255, 0.75)`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.arc(n.sx, n.sy, r + 3, 0, Math.PI * 2);
          ctx.stroke();
        }

        ctx.fillStyle = baseColor;
        ctx.beginPath();
        ctx.arc(n.sx, n.sy, Math.max(0.5, r), 0, Math.PI * 2);
        ctx.fill();
      }

      st.raf = requestAnimationFrame(tick);
    }
    st.raf = requestAnimationFrame(tick);

    return () => {
      ro.disconnect();
      window.removeEventListener("resize", applySize);
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
      canvas.removeEventListener("mousedown", onDown);
      window.removeEventListener("mouseup", onUp);
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("contextmenu", onCtx);
      if (st.raf) cancelAnimationFrame(st.raf);
      if (st.sim) st.sim.stop();
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{
        width: "100%",
        height: "100%",
        display: "block",
        cursor: "crosshair",
        userSelect: "none",
        touchAction: "none",
      }}
    />
  );
});

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

export default SystemMapCanvas;
