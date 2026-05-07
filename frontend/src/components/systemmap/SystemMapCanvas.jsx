// frontend/src/components/systemmap/SystemMapCanvas.jsx
//
// The constellation. Same DNA as guaardvark.com's neural-net.js
// (translucent blue, drift, occasional pulses, link alpha falls off
// with distance) but the data is real: each node is a module, edges
// are import dependencies, color encodes lifecycle, size encodes
// importer count. Pseudo-3D via radius+blur+alpha and parallax on
// mouse-move.
//
// Rendering: bespoke canvas2d. Layout: d3-force on a separate ticker
// that runs until the simulation cools, then we just render at 60fps.
//
// Inputs: a SystemMap dict (see backend/services/system_mapper).
// Notifies parent of hover/click via onNodeHover / onNodeClick.

import React, { useEffect, useRef, useImperativeHandle, forwardRef } from "react";
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
} from "d3-force";

// Marketing-site palette. 168/216/255 is exactly the existing blue.
const PALETTE = {
  bg: "rgb(8, 14, 26)",
  bgGradientTo: "rgb(14, 22, 40)",
  // Lifecycle → node color
  active: "rgba(168, 216, 255, 0.85)",
  autoLoaded: "rgba(180, 210, 255, 0.65)",
  dormant: "rgba(168, 216, 255, 0.22)",
  archived: "rgba(140, 145, 160, 0.30)",
  test: "rgba(168, 216, 255, 0.45)",
  script: "rgba(168, 216, 255, 0.55)",
  config: "rgba(168, 216, 255, 0.40)",
  skip: "rgba(140, 145, 160, 0.20)",
  // Edge default
  edge: "rgba(168, 216, 255, 0.18)",
  // Severity highlights
  cycleEdge: "rgba(255, 110, 110, 0.55)",
  highFinding: "rgba(255, 170, 80, 0.95)",
  mediumFinding: "rgba(255, 220, 130, 0.7)",
};

const lifecycleColor = (lifecycle) => PALETTE[lifecycle] || PALETTE.dormant;

// Higher-importer modules render bigger. Clamped so nothing goes
// dwarf or whale.
const radiusFor = (node) => {
  const importers = Math.max(0, node.importers || 0);
  // log scale so a hot module (importers=80) doesn't flatten everyone else
  const r = 1.6 + Math.log2(1 + importers) * 0.9;
  return Math.min(8, Math.max(2, r));
};

// Build the graph the simulation will run on.
function buildGraph(systemMap) {
  if (!systemMap) return { nodes: [], links: [], nodeIndex: {}, cycleEdges: new Set() };

  const dep = systemMap.dependency_graph || {};
  const rel = systemMap.stats?.reachability || {};

  // Dependency map: every src in dep is a node. Targets that aren't
  // already keys we ignore (they're outside the analyzed set).
  const moduleNames = new Set(Object.keys(dep));
  // Pull a per-module summary from findings + lifecycle hints.
  // We don't get a flat node list from system_mapper today, so we
  // synthesize one from dep_graph + findings.
  const nodeMeta = {};
  for (const name of moduleNames) {
    nodeMeta[name] = {
      lifecycle: "active",
      layer: "module",
      findings: [],
      importers: 0,
    };
  }

  // importer count = how many other modules import this one
  for (const [src, targets] of Object.entries(dep)) {
    for (const t of targets || []) {
      if (nodeMeta[t]) nodeMeta[t].importers++;
    }
  }

  // Findings → severity + lifecycle hints + cycle edges
  const cycleEdges = new Set(); // "src||dst" pairs to render in red
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
      const p = (f.paths || [])[0];
      const m = pathToModuleName(p);
      if (m && nodeMeta[m]) nodeMeta[m].lifecycle = "dormant";
    }
    // Tag nodes with their max-severity finding for visual pulse
    for (const p of f.paths || []) {
      const m = pathToModuleName(p);
      if (m && nodeMeta[m]) {
        nodeMeta[m].findings.push({ kind: f.kind, severity: f.severity });
      }
    }
  }

  const nodes = [];
  const nodeIndex = {};
  for (const name of moduleNames) {
    const meta = nodeMeta[name];
    const sevPriority = { high: 3, medium: 2, low: 1, info: 0 };
    const topSev = (meta.findings || []).reduce(
      (acc, f) =>
        (sevPriority[f.severity] || 0) > (sevPriority[acc] || 0) ? f.severity : acc,
      null,
    );
    const node = {
      id: name,
      lifecycle: meta.lifecycle,
      importers: meta.importers,
      findings: meta.findings,
      topSeverity: topSev,
      // d3-force fills these in
      x: 0,
      y: 0,
      vx: 0,
      vy: 0,
      pulse: 0, // 0..1, decays
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
      links.push({
        source: src,
        target: t,
        cycle: isCycle,
      });
    }
  }

  // Stats overlay
  const counts = { high: 0, medium: 0, low: 0, info: 0 };
  for (const f of systemMap.findings || []) {
    if (counts[f.severity] !== undefined) counts[f.severity]++;
  }

  return { nodes, links, nodeIndex, cycleEdges, severityCounts: counts, reachability: rel };
}

// Turn "backend/services/foo.py" → "backend.services.foo" (matches dep_graph keys).
function pathToModuleName(path) {
  if (!path) return null;
  if (!path.endsWith(".py")) return null;
  return path.slice(0, -3).replace(/\//g, ".");
}

// Build neighbor index for click-spotlight.
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

const SystemMapCanvas = forwardRef(function SystemMapCanvas(
  {
    systemMap,
    onNodeHover,
    onNodeClick,
    selectedNodeId,
    searchQuery,
  },
  ref,
) {
  const canvasRef = useRef(null);
  const stateRef = useRef({
    graph: { nodes: [], links: [], nodeIndex: {}, cycleEdges: new Set() },
    sim: null,
    raf: null,
    width: 0,
    height: 0,
    dpr: 1,
    camera: { x: 0, y: 0, zoom: 1 }, // world translation + scale
    parallax: { x: 0, y: 0 },        // mouse-driven offset
    hover: null,
    spotlight: null, // node id; when set, fade non-neighbors
    spotlightNeighbors: null,
    pulseClockMs: performance.now(),
  });

  // Imperative API for parent (camera glide on search).
  useImperativeHandle(ref, () => ({
    flyTo(nodeId) {
      const st = stateRef.current;
      const node = st.graph.nodeIndex[nodeId];
      if (!node) return;
      // Animate the camera by easing towards the node's center over ~600ms.
      const startX = st.camera.x;
      const startY = st.camera.y;
      const targetX = -node.x;
      const targetY = -node.y;
      const t0 = performance.now();
      const dur = 600;
      const ease = (t) => 1 - Math.pow(1 - t, 3); // cubic out
      function step(now) {
        const t = Math.min(1, (now - t0) / dur);
        st.camera.x = startX + (targetX - startX) * ease(t);
        st.camera.y = startY + (targetY - startY) * ease(t);
        if (t < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
      // Pulse it so the user sees where it landed.
      node.pulse = 1;
    },
  }));

  // Build / rebuild graph when systemMap changes.
  useEffect(() => {
    const st = stateRef.current;
    st.graph = buildGraph(systemMap);

    // Stop any existing simulation first.
    if (st.sim) {
      st.sim.stop();
      st.sim = null;
    }

    if (!st.graph.nodes.length) return;

    // Simulation. d3-force mutates node x/y in place.
    const sim = forceSimulation(st.graph.nodes)
      .force(
        "link",
        forceLink(st.graph.links)
          .id((n) => n.id)
          .distance(60)
          .strength(0.4),
      )
      .force("charge", forceManyBody().strength(-90).distanceMax(400))
      .force("center", forceCenter(0, 0))
      .force("collide", forceCollide().radius((n) => radiusFor(n) + 4))
      .alpha(1)
      .alphaDecay(0.018)
      .velocityDecay(0.55);

    st.sim = sim;
  }, [systemMap]);

  // Update spotlight when selection changes.
  useEffect(() => {
    const st = stateRef.current;
    if (!selectedNodeId) {
      st.spotlight = null;
      st.spotlightNeighbors = null;
      return;
    }
    st.spotlight = selectedNodeId;
    st.spotlightNeighbors = neighborsOf(selectedNodeId, st.graph.links);
    // Pulse the selected node
    const n = st.graph.nodeIndex[selectedNodeId];
    if (n) n.pulse = 1;
  }, [selectedNodeId]);

  // Search highlight: dim everything except matches, briefly pulse them.
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
    // Pulse all matches
    for (const id of matches) {
      const n = st.graph.nodeIndex[id];
      if (n) n.pulse = 1;
    }
  }, [searchQuery]);

  // Setup canvas + render loop.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    const st = stateRef.current;

    const resize = () => {
      const r = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      st.dpr = dpr;
      st.width = r.width;
      st.height = r.height;
      canvas.width = r.width * dpr;
      canvas.height = r.height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    resize();
    window.addEventListener("resize", resize);

    // Mouse parallax + hover detection.
    const onMove = (e) => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      st.parallax.x = (mx - st.width / 2) * 0.04;
      st.parallax.y = (my - st.height / 2) * 0.04;

      // Hover hit-test in world coords
      const wx = mx - st.width / 2 - st.camera.x - st.parallax.x;
      const wy = my - st.height / 2 - st.camera.y - st.parallax.y;
      let nearest = null;
      let bestD2 = 12 * 12; // 12px slop
      for (const n of st.graph.nodes) {
        const dx = n.x - wx;
        const dy = n.y - wy;
        const d2 = dx * dx + dy * dy;
        const r = radiusFor(n) + 6;
        if (d2 < bestD2 && d2 < r * r * 4) {
          bestD2 = d2;
          nearest = n;
        }
      }
      const prev = st.hover;
      st.hover = nearest ? nearest.id : null;
      if (prev !== st.hover && onNodeHover) {
        onNodeHover(nearest);
      }
    };
    canvas.addEventListener("mousemove", onMove);

    const onLeave = () => {
      st.hover = null;
      if (onNodeHover) onNodeHover(null);
    };
    canvas.addEventListener("mouseleave", onLeave);

    const onClick = () => {
      if (st.hover && onNodeClick) {
        onNodeClick(st.graph.nodeIndex[st.hover]);
      } else if (onNodeClick) {
        onNodeClick(null); // clicking empty space deselects
      }
    };
    canvas.addEventListener("click", onClick);

    // Render loop
    const tick = (now) => {
      const dt = Math.min(48, now - (st.pulseClockMs || now)) / 16.666;
      st.pulseClockMs = now;

      const w = st.width;
      const h = st.height;

      // Background gradient
      const g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, PALETTE.bg);
      g.addColorStop(1, PALETTE.bgGradientTo);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);

      // Idle camera drift — slow, almost imperceptible
      st.camera.x += Math.sin(now / 9000) * 0.04;
      st.camera.y += Math.cos(now / 11000) * 0.04;

      // Translate to center + camera + parallax
      ctx.save();
      ctx.translate(
        w / 2 + st.camera.x + st.parallax.x,
        h / 2 + st.camera.y + st.parallax.y,
      );

      // Edges first (behind nodes)
      ctx.lineWidth = 1;
      const spotlight = st.spotlight;
      const neighbors = st.spotlightNeighbors;
      for (const l of st.graph.links) {
        const a = typeof l.source === "object" ? l.source : st.graph.nodeIndex[l.source];
        const b = typeof l.target === "object" ? l.target : st.graph.nodeIndex[l.target];
        if (!a || !b) continue;
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d = Math.sqrt(dx * dx + dy * dy);
        let alpha = Math.max(0, 1 - d / 280) * 0.55;

        // Spotlight: dim non-neighbor edges
        if (spotlight && !(neighbors.has(a.id) && neighbors.has(b.id))) {
          alpha *= 0.18;
        }

        if (l.cycle) {
          ctx.strokeStyle = `rgba(255, 110, 110, ${Math.max(0.18, alpha * 1.2)})`;
        } else {
          ctx.strokeStyle = `rgba(168, 216, 255, ${alpha * 0.4})`;
        }
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }

      // Nodes
      for (const n of st.graph.nodes) {
        // Pulse decay
        if (n.pulse > 0) n.pulse -= 0.012 * dt;
        if (n.pulse < 0) n.pulse = 0;

        // Severity-driven occasional pulses for unselected high-sev nodes
        if (
          (n.topSeverity === "high" || n.topSeverity === "medium") &&
          Math.random() < (n.topSeverity === "high" ? 0.0009 : 0.0004)
        ) {
          n.pulse = 1;
        }

        let alpha = 1;
        if (spotlight && !neighbors.has(n.id)) alpha = 0.12;
        if (st.searchMatches && !st.searchMatches.has(n.id)) alpha = Math.min(alpha, 0.18);

        const baseColor = lifecycleColor(n.lifecycle);
        const pulseAdd = n.pulse * 4;
        const r = radiusFor(n) + pulseAdd;

        // Soft glow when pulsing
        if (n.pulse > 0.05) {
          const glow = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, r * 4);
          const glowColor =
            n.topSeverity === "high"
              ? PALETTE.highFinding
              : n.topSeverity === "medium"
                ? PALETTE.mediumFinding
                : "rgba(168, 216, 255, 0.55)";
          glow.addColorStop(0, glowColor);
          glow.addColorStop(1, "rgba(168, 216, 255, 0)");
          ctx.globalAlpha = n.pulse * 0.5 * alpha;
          ctx.fillStyle = glow;
          ctx.beginPath();
          ctx.arc(n.x, n.y, r * 4, 0, Math.PI * 2);
          ctx.fill();
          ctx.globalAlpha = 1;
        }

        // Hover ring
        if (st.hover === n.id) {
          ctx.strokeStyle = `rgba(255, 255, 255, ${0.75 * alpha})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.arc(n.x, n.y, r + 3, 0, Math.PI * 2);
          ctx.stroke();
        }

        // Body
        ctx.globalAlpha = alpha;
        ctx.fillStyle = baseColor;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      ctx.restore();

      st.raf = requestAnimationFrame(tick);
    };
    st.raf = requestAnimationFrame(tick);

    return () => {
      window.removeEventListener("resize", resize);
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
      canvas.removeEventListener("click", onClick);
      if (st.raf) cancelAnimationFrame(st.raf);
      if (st.sim) st.sim.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{
        width: "100%",
        height: "100%",
        display: "block",
        cursor: "crosshair",
      }}
    />
  );
});

export default SystemMapCanvas;
