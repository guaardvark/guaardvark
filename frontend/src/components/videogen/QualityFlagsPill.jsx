// frontend/src/components/videogen/QualityFlagsPill.jsx
// The post-render quality check on a Video Gen clip: what the frame checker
// (backend video_consistency_metrics.inspect_video_frames) found wrong with it.

import React from "react";
import { StatusPill } from "../settings/ui";

const SHORT = {
  unreadable: "Unreadable",
  black_frames: "Black frames",
  black_tiles: "Black tiles",
  clipped_highlights: "Blown out",
  crushed_shadows: "Crushed shadows",
  washed_out: "Washed out",
  desaturated: "No colour",
  oversaturated: "Oversaturated",
  frozen: "Frozen",
  wrong_size: "Wrong size",
  wrong_frame_count: "Wrong length",
  low_identity_score: "Identity drift",
  low_vlm_score: "Low review score",
};

/**
 * @param {object} quality  a result's metadata.quality
 */
const QualityFlagsPill = ({ quality }) => {
  const flags = (quality?.flags || []).filter((f) => f && f.code);
  if (flags.length === 0) {
    // Batches from before the frame checker carry only flag_reasons.
    const reasons = quality?.flagged ? quality.flag_reasons || [] : [];
    if (reasons.length === 0) return null;
    return <StatusPill tone="warn" label="Review" tooltip={reasons.join(", ")} />;
  }
  const label = flags.length === 1 ? SHORT[flags[0].code] || "Review" : `${flags.length} quality flags`;
  return (
    <StatusPill
      tone={flags.some((f) => f.code === "unreadable" || f.code === "black_tiles" || f.code === "black_frames") ? "error" : "warn"}
      label={label}
      tooltip={flags.map((f) => f.message).join(" · ")}
    />
  );
};

export default QualityFlagsPill;
