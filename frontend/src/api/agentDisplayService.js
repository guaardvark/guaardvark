// Agent Display dependency detector + installer + start/stop controls.
// Mirrors voiceService.installWhisper for the virtual display stack.

import { BASE_URL, handleResponse } from './apiClient';

const ROOT = `${BASE_URL}/agent-control`;

export async function getDisplayStatus() {
  const response = await fetch(`${ROOT}/display-status`);
  return handleResponse(response);
}

export async function installDisplay() {
  const response = await fetch(`${ROOT}/install-display`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  return handleResponse(response);
}

export async function startDisplay() {
  const response = await fetch(`${ROOT}/start-display`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  return handleResponse(response);
}

export async function stopDisplay() {
  const response = await fetch(`${ROOT}/stop-display`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  return handleResponse(response);
}

export default { getDisplayStatus, installDisplay, startDisplay, stopDisplay };
