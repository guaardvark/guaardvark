// frontend/src/api/mediaRecipeService.js
// What a generated file was made with, and making it again
// (backend/api/media_recipe_api.py).

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

const call = async (url, options) => {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.success === false) throw new Error(data.error || data.message || `HTTP ${res.status}`);
  return data;
};

/** The recipe of a Library document: {kind, media, prompt, rows, regenerate, adjust, ...}. */
export async function getDocumentRecipe(documentId) {
  return (await call(`${API_BASE}/media/recipe/${documentId}`)).recipe;
}

/** The recipe of one clip of a video batch. */
export async function getClipRecipe(batchId, itemId) {
  return (await call(`${API_BASE}/media/clip-recipe/${encodeURIComponent(batchId)}/${encodeURIComponent(itemId)}`)).recipe;
}

/**
 * Queue one more of an item with the settings it was made with.
 * @param {{document_id?: number, batch_id?: string, item_id?: string, same_seed?: boolean}} target
 * @returns {Promise<{queue: string, batch_id?: string, job_id?: string, page: string}>}
 */
export async function regenerateMedia(target) {
  return call(`${API_BASE}/media/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(target),
  });
}
