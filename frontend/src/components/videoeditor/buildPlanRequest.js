export const getPlanInputs = (timeline) => {
  const bin = Array.isArray(timeline?.bin) ? timeline.bin : [];
  const videoClips = bin.filter((clip) => clip.kind === "video");
  const masterSong = bin.find((clip) => clip.kind === "audio" && clip.isMasterSong) || null;

  return {
    videoClips,
    masterSong,
    videoCount: videoClips.length,
    hasMasterSong: Boolean(masterSong),
    canPlan: videoClips.length > 0 && Boolean(masterSong),
  };
};

export const buildPlanRequest = ({
  timeline,
  masterSong,
  scanMode,
  styleRecipeName,
  clipOverrides,
  seed = Math.floor(Math.random() * 1_000_000),
}) => ({
  bin_clips: (timeline?.bin || [])
    .filter((clip) => clip.kind === "video")
    .map((clip) => ({ clip_id: clip.clipId, document_id: clip.documentId })),
  song_document_id: masterSong.documentId,
  scan_mode: scanMode,
  style_recipe_name: styleRecipeName,
  seed,
  clip_overrides: clipOverrides,
});

export const getKeptRangeDecorations = (result) => {
  const kept = result?.kept_ranges_by_clip || {};
  return Object.fromEntries(
    Object.entries(kept).map(([clipId, ranges]) => [
      clipId,
      {
        keptRanges: ranges,
        durationSeconds: ranges?.length ? Math.max(...ranges.map((range) => range[1])) : null,
      },
    ]),
  );
};
