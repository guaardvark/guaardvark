/**
 * Clips a chat reply is still waiting for. The backend saves a queued video
 * on its reply as `pendingMedia` ({batch_id, caption, studio_url, status,
 * error}) and sends `chat:media_ready` ({message_id, batch_id, media, error})
 * when the render ends; `applyMediaReady` is the change that event makes to a
 * message list.
 */
export const pendingMediaOf = (message) =>
  message?.pendingMedia ?? message?.extra_data?.pendingMedia ?? [];

export const applyMediaReady = (messages, data) => {
  if (!Array.isArray(messages) || !data?.batch_id) return messages;
  const waitsFor = (m) => pendingMediaOf(m).some((p) => p.batch_id === data.batch_id);
  let index = data.message_id == null
    ? -1
    : messages.findIndex((m) => m.message_id === data.message_id && waitsFor(m));
  if (index < 0) index = messages.findIndex(waitsFor);
  if (index < 0) return messages;

  const message = messages[index];
  const pending = pendingMediaOf(message)
    .map((p) => {
      if (p.batch_id !== data.batch_id) return p;
      return data.error ? { ...p, status: "failed", error: data.error } : null;
    })
    .filter(Boolean);
  const media = message.generatedImages || [];
  const have = new Set(media.map((item) => item.url));
  const next = [...messages];
  next[index] = {
    ...message,
    generatedImages: [...media, ...(data.media || []).filter((item) => !have.has(item.url))],
    pendingMedia: pending,
  };
  return next;
};

/** The `chat:media_ready` payload for a finished batch, from
 *  /api/batch-video/status (its `data`); null while it is still rendering.
 *  For a chat that keeps its messages in the browser and was closed when the
 *  event went out. */
export const mediaReadyFromStatus = (status) => {
  if (!status?.batch_id || !["completed", "error", "cancelled"].includes(status.status)) return null;
  const media = (status.results || [])
    .filter((r) => r.success && r.video_path)
    .map((r) => ({
      url: `/api/batch-video/video/${status.batch_id}/${r.video_path}`,
      type: "video",
      alt: "Generated video",
      batch_id: status.batch_id,
      ...(r.thumbnail_path ? { poster: `/api/batch-video/video/${status.batch_id}/${r.thumbnail_path}` } : {}),
    }));
  if (media.length) return { batch_id: status.batch_id, media };
  const failed = (status.results || []).find((r) => !r.success);
  return {
    batch_id: status.batch_id,
    media: [],
    error: status.error || failed?.failure?.message || failed?.error || status.status,
  };
};
