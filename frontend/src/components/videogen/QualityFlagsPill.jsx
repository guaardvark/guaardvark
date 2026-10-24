// frontend/src/components/videogen/QualityFlagsPill.jsx
// The post-render quality check on a Video Gen clip: what the frame checker
// (backend video_consistency_metrics.inspect_video_frames) found wrong with it.

import React from "react";
import { StatusPill } from "../settings/ui";
import { useUiTranslation, translate } from "../../i18n";

const shortLabels = () => ({
  unreadable: translate("video.qualityFlags.unreadable"),
  black_frames: translate("video.qualityFlags.blackFrames"),
  black_tiles: translate("video.qualityFlags.blackTiles"),
  clipped_highlights: translate("video.qualityFlags.clippedHighlights"),
  crushed_shadows: translate("video.qualityFlags.crushedShadows"),
  washed_out: translate("video.qualityFlags.washedOut"),
  desaturated: translate("video.qualityFlags.desaturated"),
  oversaturated: translate("video.qualityFlags.oversaturated"),
  frozen: translate("video.qualityFlags.frozen"),
  flashes: translate("video.qualityFlags.flashes"),
  wrong_size: translate("video.qualityFlags.wrongSize"),
  wrong_frame_count: translate("video.qualityFlags.wrongFrameCount"),
  low_colour_match: translate("video.qualityFlags.colourDrift"),
  low_keyframe_match: translate("video.qualityFlags.keyframeDrift"),
  // Older batches: the same histogram check under its former name.
  low_identity_score: translate("video.qualityFlags.colourDrift"),
  low_vlm_score: translate("video.qualityFlags.lowReviewScore"),
});

/**
 * @param {object} quality  a result's metadata.quality
 */
const QualityFlagsPill = ({ quality }) => {
  const { t } = useUiTranslation();
  const flags = (quality?.flags || []).filter((f) => f && f.code);
  if (flags.length === 0) {
    // Batches from before the frame checker carry only flag_reasons.
    const reasons = quality?.flagged ? quality.flag_reasons || [] : [];
    if (reasons.length === 0) return null;
    return <StatusPill tone="warn" label={t("video.qualityFlags.review")} tooltip={reasons.join(", ")} />;
  }
  const label = flags.length === 1
    ? shortLabels()[flags[0].code] || t("video.qualityFlags.review")
    : t("video.qualityFlags.count", { count: flags.length });
  return (
    <StatusPill
      tone={flags.some((f) => f.code === "unreadable" || f.code === "black_tiles" || f.code === "black_frames") ? "error" : "warn"}
      label={label}
      tooltip={flags.map((f) => f.message).join(" · ")}
    />
  );
};

export default QualityFlagsPill;
