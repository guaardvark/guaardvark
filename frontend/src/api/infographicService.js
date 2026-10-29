// frontend/src/api/infographicService.js
// Thin fetch wrappers for the infographic backend.
// /api/infographic/batch queues on the image batch queue and returns at once;
// the page follows the batch through /api/batch-image. /api/infographic/generate
// is the synchronous single render: it blocks until the PNG is saved.

import { BASE_URL } from './apiClient';
import { translate } from '../i18n';

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
 * @param {string} [spec.model]   - an id from /status models; omitted = the default
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

/**
 * Queue `quantity` infographics from one form.
 * @param {Object} spec - the generateInfographic fields, plus:
 * @param {number} spec.quantity
 * @param {Object} [spec.ui_config] - the form as typed, for Adjust & Retry
 * @returns {Promise<{batch_id: string, total_images: number, model: string}>}
 */
export async function queueInfographicBatch(spec) {
  const res = await fetch(`${API}/batch`, {
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
  { value: 'editorial',   get label() { return translate('options.infographic.styles.editorial'); } },
  { value: 'flat_vector', get label() { return translate('options.infographic.styles.flatVector'); } },
  { value: 'photo_real',  get label() { return translate('options.infographic.styles.photoReal'); } },
  { value: 'comic',       get label() { return translate('options.infographic.styles.comic'); } },
];

export const INFOGRAPHIC_ASPECTS = [
  { value: '16:9', get label() { return translate('options.infographic.aspects.wide'); } },
  { value: '1:1',  get label() { return translate('options.infographic.aspects.square'); } },
  { value: '9:16', get label() { return translate('options.infographic.aspects.vertical'); } },
  { value: '4:5',  get label() { return translate('options.infographic.aspects.portrait'); } },
  { value: '3:2',  get label() { return translate('options.infographic.aspects.landscape'); } },
];
