// Live ComfyUI sampler frames arrive as {job_id, mime, b64} on job_preview.
// Object URLs live in a Map so React can <img src> them; revoke on replace/drop.

export function blobUrlFromPreviewPayload(data) {
  if (!data || !data.job_id || !data.b64) return null;
  try {
    const binary = atob(data.b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    const mime = data.mime || "image/jpeg";
    return URL.createObjectURL(new Blob([bytes], { type: mime }));
  } catch {
    return null;
  }
}

export function applyPreviewFrame(store, data) {
  const url = blobUrlFromPreviewPayload(data);
  if (!url) return store;
  const next = new Map(store);
  const prev = next.get(data.job_id);
  if (prev) URL.revokeObjectURL(prev);
  next.set(data.job_id, url);
  return next;
}

export function dropPreview(store, jobId) {
  if (!store.has(jobId)) return store;
  const next = new Map(store);
  const prev = next.get(jobId);
  if (prev) URL.revokeObjectURL(prev);
  next.delete(jobId);
  return next;
}

export function dropStalePreviews(store, liveJobIds, previouslyLive) {
  // A frame can beat the first progress event. Only revoke after a job_id was
  // live in UPS and then disappeared — not because it has not appeared yet.
  let next = store;
  store.forEach((url, jobId) => {
    if (previouslyLive.has(jobId) && !liveJobIds.has(jobId)) {
      if (next === store) next = new Map(store);
      URL.revokeObjectURL(url);
      next.delete(jobId);
    }
  });
  return next;
}

export function clearAllPreviews(store) {
  store.forEach((url) => URL.revokeObjectURL(url));
  return new Map();
}
