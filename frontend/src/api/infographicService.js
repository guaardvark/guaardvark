// frontend/src/api/infographicService.js
// Thin fetch wrappers for the Flux infographic backend.
// /api/infographic/generate is synchronous on the backend — it blocks until
// the PNG is ready (~5s on the target hardware), so the frontend just
// shows a spinner and renders the result when the promise resolves.

import { BASE_URL } from './apiClient';

const API = BASE_URL.replace(/\/api$/, '') + '/api/infographic';

export async function getInfographicStatus() {
  const res = await fetch(`${API}/status`);
  if (!res.ok) throw new Error(`status ${res.status}`);
  return res.json();
}

/**
 * @param {Object} spec
 * @param {string} spec.title
 * @param {string} spec.scene
 * @param {string} [spec.footer]
 * @param {string[]|string} [spec.hashtags]
 * @param {string[]|string} [spec.callouts]  - one per line if string
 * @param {string} [spec.style]   - "editorial" | "flat_vector" | "photo_real" | "comic"
 * @param {string} [spec.aspect]  - "16:9" | "1:1" | "9:16" | "4:5" | "3:2"
 * @param {string} [spec.raw_prompt] - if set, used verbatim instead of composing
 * @param {number} [spec.seed]
 */
export async function generateInfographic(spec) {
  const res = await fetch(`${API}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.success) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

export const INFOGRAPHIC_STYLES = [
  { value: 'editorial',   label: 'Dramatic editorial illustration' },
  { value: 'flat_vector', label: 'Flat vector / clean geometric' },
  { value: 'photo_real',  label: 'Photorealistic' },
  { value: 'comic',       label: 'Comic book' },
];

export const INFOGRAPHIC_ASPECTS = [
  { value: '16:9', label: '16:9  (1280×720)  — social / video thumbnail' },
  { value: '1:1',  label: '1:1   (1024×1024) — square / Instagram' },
  { value: '9:16', label: '9:16  (720×1280)  — vertical / Reels' },
  { value: '4:5',  label: '4:5   (1024×1280) — Instagram portrait' },
  { value: '3:2',  label: '3:2   (1216×832)  — landscape' },
];
