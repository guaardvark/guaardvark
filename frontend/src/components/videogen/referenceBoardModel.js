// frontend/src/components/videogen/referenceBoardModel.js
//
// The reference board's data: pictures, clips and audio a reference-to-video
// model (capabilities.modes includes "ref2v") is conditioned on. Pure helpers
// so the numbering, the limits and the request body are testable and match
// what the backend wires.
//
// Tag numbering follows the graph (MiniMaxH3ReferenceToVideo): <Picture N>
// and <Video N> count their own row; <Audio N> counts each clip's soundtrack
// first, in clip order, then the standalone tracks.

export const REF_KINDS = ["images", "videos", "audios"];

export const KIND_OF_ROW = { images: "image", videos: "video", audios: "audio" };

export const ROW_LABELS = { images: "Pictures", videos: "Clips", audios: "Audio" };

export const ROLE_OPTIONS = {
  images: [
    { value: "keep", label: "Keep exactly" },
    { value: "loose", label: "Keep the look loosely" },
    { value: "detail", label: "Borrow one detail" },
    { value: "hint", label: "Light hint" },
    { value: "first_frame", label: "Use as the first frame" },
  ],
  videos: [
    { value: "subject", label: "Keep who is in it" },
    { value: "motion", label: "Copy its motion and camera" },
    { value: "edit", label: "Edit this clip" },
    { value: "continue", label: "Continue this clip" },
  ],
  audios: [
    { value: "voice", label: "Voice for a character" },
    { value: "music", label: "Use as the music" },
    { value: "sound", label: "Match this sound" },
  ],
};

export const DEFAULT_ROLE = { images: "keep", videos: "subject", audios: "voice" };

/** A clip's sound when nobody picked one: an edit or a continuation keeps the
 * clip's soundtrack; a clip used for who is in it or for its motion goes in
 * silent, since its own dialogue is a second voice beside any voice reference.
 * Mirrors _default_clip_audio in backend/api/batch_video_generation_api.py. */
export const defaultClipAudio = (role) => (role === "edit" || role === "continue" ? "own" : "none");

export const emptyBoard = () => ({ images: [], videos: [], audios: [] });

let _seq = 0;
const nextKey = () => {
  _seq += 1;
  return `ref-${Date.now().toString(36)}-${_seq}`;
};

/** A board entry from a library Document (or an upload's returned Document). */
export function entryFromDocument(doc, row) {
  const id = doc?.id;
  const download = id != null ? `/api/files/document/${id}/download` : null;
  return {
    key: nextKey(),
    ref: id != null ? String(id) : String(doc?.path || ""),
    fileName: doc?.filename || doc?.name || `document ${id}`,
    previewUrl: download,
    thumbnailUrl: doc?.thumbnail_url || (row === "images" ? download : null),
    role: DEFAULT_ROLE[row],
    name: "",
    note: "",
    speaker: "",
    // Clips: "own" | "none" | {ref, fileName}; audioPicked once the person chose.
    audio: row === "videos" ? defaultClipAudio(DEFAULT_ROLE.videos) : undefined,
    audioPicked: false,
    durationS: null,
    hasAudio: null,
  };
}

/** True when the clip goes in with a soundtrack: a separate track, or its
 * own sound unless the probe found it has none. */
export const clipHasSoundtrack = (clip) =>
  typeof clip?.audio === "object" && clip.audio !== null
    ? true
    : clip?.audio !== "none" && clip?.hasAudio !== false;

/** Display tags for every entry, keyed by entry key. */
export function boardTags(board) {
  const tags = {};
  (board?.images || []).forEach((e, i) => { tags[e.key] = `<Picture ${i + 1}>`; });
  (board?.videos || []).forEach((e, i) => { tags[e.key] = `<Video ${i + 1}>`; });
  let audio = 0;
  (board?.videos || []).forEach((e) => {
    if (clipHasSoundtrack(e)) {
      audio += 1;
      tags[`${e.key}:audio`] = `<Audio ${audio}>`;
    }
  });
  (board?.audios || []).forEach((e) => {
    audio += 1;
    tags[e.key] = `<Audio ${audio}>`;
  });
  return tags;
}

/** Files the board sends: every entry plus each clip's separate soundtrack. */
export const boardFileCount = (board) =>
  (board?.images?.length || 0) + (board?.videos?.length || 0) + (board?.audios?.length || 0)
  + (board?.videos || []).filter((v) => typeof v.audio === "object" && v.audio !== null).length;

/** Room left in a row under the model's ref_limits and the shared file cap. */
export function roomInRow(board, row, limits) {
  const cap = limits?.[row];
  const used = board?.[row]?.length || 0;
  const rowRoom = cap == null ? Infinity : Math.max(0, cap - used);
  const fileRoom = limits?.files == null ? Infinity : Math.max(0, limits.files - boardFileCount(board));
  return Math.min(rowRoom, fileRoom);
}

/** Latent frames MiniMax H3 gives `frames` video frames: the count snaps up
 * to the 17k+5 grid and each 17 frames become 5 latent ones. Mirrors
 * _h3_latent_frames in backend/services/comfyui_video_generator.py. */
export function h3LatentFrames(frames) {
  let n = Math.max(5, Math.floor(frames || 0));
  n += (5 - (n % 17) + 17) % 17;
  return n <= 5 ? 2 : Math.floor((n - 5) / 17) * 5 + 2;
}

/** The longest 17k+5 frame count not over `frames`: what the node reads of
 * a clip that long (a 2 s clip is read as 39 frames). */
export const h3GridFloor = (frames) => {
  const n = Math.max(5, Math.floor(frames));
  return Math.floor((n - 5) / 17) * 17 + 5;
};

/** Frames of reference video each clip may add on a card whose board has a
 * measured token budget (tier_defaults.ref_token_budget), or null when none
 * applies. Mirrors reference_clip_frames in the backend: clips and pictures are
 * read at the render's size, so each latent frame costs (w/16)*(h/16) tokens. */
export function clipFramesAllowed(budget, renderFrames, width, height, pictures, clips) {
  if (!budget || !clips || !width || !height) return null;
  const patches = Math.max(1, Math.floor(width / 16) * Math.floor(height / 16));
  const room = Math.floor((Math.floor(budget / patches) - h3LatentFrames(renderFrames) - pictures) / clips);
  if (room < 2) return 0;
  return Math.floor((room - 2) / 5) * 17 + 5;
}

/** Sentences naming what stops this board from rendering; empty when it can.
 * `fit` is {budget, renderFrames, width, height, fps} for a card with a
 * measured budget. */
export function boardProblems(board, limits, fit = {}) {
  const problems = [];
  if (!board?.images?.length && !board?.videos?.length) {
    problems.push("Add at least one picture or clip; audio cannot be the only reference.");
  }
  REF_KINDS.forEach((row) => {
    const cap = limits?.[row];
    if (cap != null && (board?.[row]?.length || 0) > cap) {
      problems.push(`${ROW_LABELS[row]} take at most ${cap}.`);
    }
  });
  if (limits?.files != null && boardFileCount(board) > limits.files) {
    problems.push(`At most ${limits.files} files in all.`);
  }
  const shortest = limits?.video_seconds?.[0];
  (board?.videos || []).forEach((v, i) => {
    if (shortest && v.durationS != null && v.durationS < shortest) {
      problems.push(`Clip ${i + 1} is ${v.durationS.toFixed(1)} s; clips need at least ${shortest} s.`);
    }
  });
  // The budget was measured for boards with clips; pictures alone cost one
  // latent frame each, about what the base build costs at the same length.
  const { budget, renderFrames, width, height, fps = 24 } = fit;
  if (budget && renderFrames && width && height && board?.videos?.length) {
    const seconds = Math.round(renderFrames / fps);
    const pictures = board?.images?.length || 0;
    const clips = board?.videos?.length || 0;
    const patches = Math.max(1, Math.floor(width / 16) * Math.floor(height / 16));
    const room = Math.floor(budget / patches) - h3LatentFrames(renderFrames);
    if (pictures > room) {
      problems.push(`On this card a ${seconds} s clip at ${width}x${height} with a reference clip takes at most ${Math.max(0, room)} pictures.`);
    } else {
      const each = clipFramesAllowed(budget, renderFrames, width, height, pictures, clips);
      if (each != null && shortest && each < h3GridFloor(shortest * fps)) {
        problems.push(`On this card a ${seconds} s clip at ${width}x${height} leaves ${(each / fps).toFixed(1)} s of reference video per clip, under the ${shortest} s a clip needs. Choose a shorter duration or a smaller size, or use fewer clips or pictures.`);
      }
    }
  }
  return problems;
}

/** The request body's `references` (POST /batch-video/generate/references). */
export function serializeBoard(board) {
  return {
    images: (board?.images || []).map((e) => ({ ref: e.ref, role: e.role, name: e.name, note: e.note })),
    videos: (board?.videos || []).map((e) => ({
      ref: e.ref,
      role: e.role,
      name: e.name,
      audio: typeof e.audio === "object" && e.audio !== null ? { ref: e.audio.ref } : e.audio || defaultClipAudio(e.role),
    })),
    audios: (board?.audios || []).map((e) => ({ ref: e.ref, role: e.role, speaker: e.speaker })),
  };
}

/** The board for /batch-video/enhance-preview, which reads no files: each
 * clip says whether a soundtrack goes in with it. */
export function previewBoard(board) {
  const body = serializeBoard(board);
  body.videos = body.videos.map((v, i) => ({ ...v, soundtrack: clipHasSoundtrack(board.videos[i]) }));
  return body;
}

/** Names typed on pictures and clips, for the voice picker. */
export const boardNames = (board) =>
  [...new Set([...(board?.images || []), ...(board?.videos || [])]
    .map((e) => (e.name || "").trim())
    .filter(Boolean))];
