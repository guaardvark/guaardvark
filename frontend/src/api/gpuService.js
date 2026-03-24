// frontend/src/api/gpuService.js
// API client for GPU Memory Orchestrator endpoints.

import { BASE_URL, handleResponse } from "./apiClient";

export const getGpuStatus = async () => {
  const res = await fetch(`${BASE_URL}/gpu/memory/status`);
  return handleResponse(res);
};

export const signalGpuIntent = async (route) => {
  const res = await fetch(`${BASE_URL}/gpu/memory/intent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ route }),
  });
  return handleResponse(res);
};

export const getGpuTier = async () => {
  const res = await fetch(`${BASE_URL}/gpu/memory/tier`);
  return handleResponse(res);
};

export const setGpuTier = async (tier) => {
  const res = await fetch(`${BASE_URL}/gpu/memory/tier`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tier }),
  });
  return handleResponse(res);
};

export const evictGpuModel = async (slotId) => {
  const res = await fetch(`${BASE_URL}/gpu/memory/evict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slot_id: slotId }),
  });
  return handleResponse(res);
};

export const preloadGpuModel = async (slotId, vramMb = 4000, priority = 50) => {
  const res = await fetch(`${BASE_URL}/gpu/memory/preload`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slot_id: slotId, vram_mb: vramMb, priority }),
  });
  return handleResponse(res);
};
