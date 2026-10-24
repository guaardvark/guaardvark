// Downscale a chat image before it rides on chat:send / POST /api/chat/unified.
// Socket.IO drops an oversized payload with no client event (1 MB today; a
// parallel backend change raises the buffer to 16 MB and emits chat:error).
// Longest edge 2048 px, JPEG 0.9 for photos; PNG only when the source has
// transparency. Until GET /api/chat/config reports CHAT_ATTACHMENT_MAX_BYTES,
// the send-side ceiling is 8 MB.

import { BASE_URL } from "../api/apiClient";
import { translate } from "../i18n";

export const CHAT_ATTACHMENT_MAX_EDGE = 2048;
export const CHAT_ATTACHMENT_JPEG_QUALITY = 0.9;
export const CHAT_ATTACHMENT_MAX_BYTES_FALLBACK = 8 * 1024 * 1024;

export function resolveAttachmentMaxBytes(config) {
  if (config == null) return CHAT_ATTACHMENT_MAX_BYTES_FALLBACK;
  const raw =
    config.CHAT_ATTACHMENT_MAX_BYTES ??
    config.chat_attachment_max_bytes ??
    config.data?.CHAT_ATTACHMENT_MAX_BYTES ??
    config.data?.chat_attachment_max_bytes ??
    config.data?.data?.CHAT_ATTACHMENT_MAX_BYTES;
  const n = Number(raw);
  if (Number.isFinite(n) && n > 0) return n;
  return CHAT_ATTACHMENT_MAX_BYTES_FALLBACK;
}

export function attachmentExceedsLimit(byteLength, maxBytes = CHAT_ATTACHMENT_MAX_BYTES_FALLBACK) {
  const bytes = Number(byteLength);
  const max = Number(maxBytes);
  if (!Number.isFinite(bytes) || bytes < 0) return true;
  if (!Number.isFinite(max) || max <= 0) return bytes > CHAT_ATTACHMENT_MAX_BYTES_FALLBACK;
  return bytes > max;
}

export function formatAttachmentSize(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${Math.round(n)} B`;
  if (n < 1024 * 1024) {
    const kb = n / 1024;
    return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  }
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function refuseAttachmentMessage(byteLength, maxBytes = CHAT_ATTACHMENT_MAX_BYTES_FALLBACK) {
  return translate("messages.attachment.tooLarge", { size: formatAttachmentSize(byteLength), limit: formatAttachmentSize(maxBytes) });
}

// Several pictures on one turn share the limit (the server counts them
// together), so the refusal names the total, not one picture.
export function refuseAttachmentsTotalMessage(count, totalBytes, maxBytes = CHAT_ATTACHMENT_MAX_BYTES_FALLBACK) {
  if (!(count > 1)) return refuseAttachmentMessage(totalBytes, maxBytes);
  return translate("messages.attachment.totalTooLarge", { count, size: formatAttachmentSize(totalBytes), limit: formatAttachmentSize(maxBytes) });
}

// What the send path hands unifiedChatService for the pictures of a turn:
// an array when several were attached (primary first), the single base64
// string when one was, null when none.
export function attachedPictures(sendOptions) {
  const many = Array.isArray(sendOptions?.imagesBase64)
    ? sendOptions.imagesBase64.filter((b) => typeof b === "string" && b)
    : [];
  if (many.length > 1) return many;
  if (many.length === 1) return many[0];
  return sendOptions?.imageBase64 || null;
}

export function chatErrorMessage(payload) {
  if (payload == null) return translate("messages.attachment.chatError");
  if (typeof payload === "string" && payload.trim()) return payload;
  const nested = payload.error;
  if (typeof nested === "string" && nested.trim()) return nested;
  if (typeof nested?.message === "string" && nested.message.trim()) return nested.message;
  if (typeof payload.message === "string" && payload.message.trim()) return payload.message;
  return translate("messages.attachment.chatError");
}

function defaultCreateCanvas(width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function defaultLoadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(translate("messages.attachment.readFailed")));
    };
    img.src = url;
  });
}

function canvasHasTransparency(canvas) {
  const ctx = canvas.getContext("2d");
  if (!ctx || typeof ctx.getImageData !== "function") return false;
  const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] < 255) return true;
  }
  return false;
}

function canvasToBlob(canvas, mime, quality) {
  return new Promise((resolve, reject) => {
    if (typeof canvas.toBlob === "function") {
      canvas.toBlob(
        (blob) => {
          if (!blob) reject(new Error(translate("messages.attachment.encodeFailed")));
          else resolve(blob);
        },
        mime,
        quality,
      );
      return;
    }
    try {
      const dataUrl = canvas.toDataURL(mime, quality);
      resolve(dataUrlToBlob(dataUrl));
    } catch (err) {
      reject(err);
    }
  });
}

function dataUrlToBlob(dataUrl) {
  const [header, b64] = dataUrl.split(",");
  const mime = /data:([^;]+)/.exec(header)?.[1] || "application/octet-stream";
  const binary = atob(b64 || "");
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error(translate("messages.attachment.readResizedFailed")));
    reader.readAsDataURL(blob);
  });
}

function scaledSize(width, height, maxEdge) {
  const w = Math.max(1, Number(width) || 1);
  const h = Math.max(1, Number(height) || 1);
  const longest = Math.max(w, h);
  if (longest <= maxEdge) return { width: Math.round(w), height: Math.round(h) };
  const scale = maxEdge / longest;
  return {
    width: Math.max(1, Math.round(w * scale)),
    height: Math.max(1, Math.round(h * scale)),
  };
}

/**
 * @param {File|Blob} file
 * @param {object} [options]
 * @returns {Promise<{
 *   file: File,
 *   preview: string,
 *   byteLength: number,
 *   mimeType: string,
 *   width: number,
 *   height: number,
 * }>}
 */
export async function downscaleChatAttachment(file, options = {}) {
  if (!file) throw new Error(translate("messages.attachment.noImage"));
  const maxEdge = options.maxEdge ?? CHAT_ATTACHMENT_MAX_EDGE;
  const quality = options.quality ?? CHAT_ATTACHMENT_JPEG_QUALITY;
  const loadImage = options.loadImage ?? defaultLoadImage;
  const createCanvas = options.createCanvas ?? defaultCreateCanvas;

  const img = await loadImage(file);
  const { width, height } = scaledSize(img.width, img.height, maxEdge);
  const canvas = createCanvas(width, height);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error(translate("messages.attachment.drawFailed"));
  ctx.drawImage(img, 0, 0, width, height);

  const sourceType = (file.type || "").toLowerCase();
  const keepPng = sourceType === "image/png" && canvasHasTransparency(canvas);
  const mimeType = keepPng ? "image/png" : "image/jpeg";
  const blob = await canvasToBlob(canvas, mimeType, keepPng ? undefined : quality);
  const preview = await blobToDataUrl(blob);
  const ext = keepPng ? "png" : "jpg";
  const baseName = (file.name || "image").replace(/\.[^.]+$/, "");
  const outFile = new File([blob], `${baseName}.${ext}`, { type: mimeType });
  return {
    file: outFile,
    preview,
    byteLength: blob.size,
    mimeType,
    width,
    height,
  };
}

let cachedMaxBytes;
let inflightMaxBytes;

export function resetAttachmentMaxBytesCache() {
  cachedMaxBytes = undefined;
  inflightMaxBytes = undefined;
}

/**
 * Read CHAT_ATTACHMENT_MAX_BYTES from GET /api/chat/config.
 * Missing route, missing key, or a failed fetch all fall back to 8 MB.
 */
export async function fetchAttachmentMaxBytes() {
  if (cachedMaxBytes != null) return cachedMaxBytes;
  if (inflightMaxBytes) return inflightMaxBytes;
  inflightMaxBytes = (async () => {
    try {
      const res = await fetch(`${BASE_URL}/chat/config`);
      if (!res.ok) return CHAT_ATTACHMENT_MAX_BYTES_FALLBACK;
      const json = await res.json();
      return resolveAttachmentMaxBytes(json);
    } catch {
      return CHAT_ATTACHMENT_MAX_BYTES_FALLBACK;
    }
  })();
  try {
    cachedMaxBytes = await inflightMaxBytes;
    return cachedMaxBytes;
  } finally {
    inflightMaxBytes = undefined;
  }
}
