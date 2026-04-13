// frontend/src/components/swarm/SwarmGraph.jsx
// Visualizes the Agent Swarm DAG (Directed Acyclic Graph) using React Flow

import React, { useMemo, useEffect } from "react";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  useNodesState,
  useEdgesState,
} from "reactflow";
import "reactflow/dist/style.css";
import { Box, Paper, Typography, useTheme, Chip } from "@mui/material";

const STATUS_COLORS = {
  pending: "#9e9e9e",
  blocked: "#757575",
  queued: "#0288d1",
  running: "#1976d2",
  done: "#2e7d32",
  failed: "#d32f2f",
  needs_review: "#ed6c02",
  merged: "#2e7d32",
  cancelled: "#757575",
};

/**
 * Custom Node for Swarm Tasks
 */
const TaskNode = ({ data }) => {
  const theme = useTheme();
  const statusColor = STATUS_COLORS[data.status] || "#9e9e9e";
  
  return (
    <Paper
      elevation={3}
      sx={{
        p: 1.5,
        minWidth: 150,
        maxWidth: 200,
        border: "2px solid",
        borderColor: data.isActive ? "primary.main" : statusColor,
        bgcolor: "background.paper",
        borderRadius: 2,
        position: "relative",
      }}
    >
      <Typography variant="caption" fontWeight={600} sx={{ display: "block", mb: 0.5, color: "text.secondary" }}>
        {data.id}
      </Typography>
      <Typography variant="body2" fontWeight={600} sx={{ mb: 1, lineHeight: 1.2 }}>
        {data.title}
      </Typography>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Chip 
          label={data.status} 
          size="small" 
          sx={{ 
            height: 18, 
            fontSize: "0.65rem", 
            bgcolor: statusColor, 
            color: "#fff",
            fontWeight: 600
          }} 
        />
        {data.elapsed && (
          <Typography variant="caption" color="text.secondary">
            {data.elapsed}
          </Typography>
        )}
      </Box>
    </Paper>
  );
};

const nodeTypes = {
  task: TaskNode,
};

const SwarmGraph = ({ tasks = [], height = 400 }) => {
  const theme = useTheme();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  // Transform tasks into nodes and edges
  useEffect(() => {
    if (!tasks || tasks.length === 0) return;

    const newNodes = [];
    const newEdges = [];

    // Simple layering algorithm
    // In a real app, use dagre for better layout
    const layers = {};
    const processed = new Set();
    
    const getLayer = (task) => {
      if (layers[task.id] !== undefined) return layers[task.id];
      if (!task.dependencies || task.dependencies.length === 0) {
        layers[task.id] = 0;
        return 0;
      }
      
      const depLayers = task.dependencies.map(depId => {
        const depTask = tasks.find(t => t.id === depId);
        return depTask ? getLayer(depTask) : 0;
      });
      
      const layer = Math.max(...depLayers) + 1;
      layers[task.id] = layer;
      return layer;
    };

    tasks.forEach(task => getLayer(task));

    // Position nodes by layer
    const layerCounts = {};
    tasks.forEach(task => {
      const layer = layers[task.id];
      const index = layerCounts[layer] || 0;
      layerCounts[layer] = index + 1;

      newNodes.push({
        id: task.id,
        type: "task",
        data: { 
          id: task.id,
          title: task.title, 
          status: task.status,
          isActive: task.status === "running",
          elapsed: task.elapsed
        },
        position: { x: layer * 250 + 50, y: index * 120 + 50 },
      });

      if (task.dependencies) {
        task.dependencies.forEach(depId => {
          newEdges.push({
            id: `e-${depId}-${task.id}`,
            source: depId,
            target: task.id,
            animated: task.status === "running",
            style: { stroke: task.status === "running" ? theme.palette.primary.main : "#b1b1b7" },
            markerEnd: {
              type: MarkerType.ArrowClosed,
              color: task.status === "running" ? theme.palette.primary.main : "#b1b1b7",
            },
          });
        });
      }
    });

    setNodes(newNodes);
    setEdges(newEdges);
  }, [tasks, theme, setNodes, setEdges]);

  return (
    <Box sx={{ height, width: "100%", border: "1px solid", borderColor: "divider", borderRadius: 1, bgcolor: "background.default", overflow: "hidden" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        attributionPosition="bottom-right"
      >
        <Background color="#aaa" gap={20} />
        <Controls />
      </ReactFlow>
    </Box>
  );
};

export default SwarmGraph;
