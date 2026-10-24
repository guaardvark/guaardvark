// frontend/src/components/videogen/ColourMatchPill.jsx
// How closely a cinematic clip's middle frame keeps its keyframe's colours
// (backend video_consistency_metrics.colour_match). A palette comparison, so
// it is labelled a colour match and never an identity score.

import React from "react";
import { StatusPill } from "../settings/ui";
import { useUiTranslation } from "../../i18n";

/** The colour match record of a clip, or null. Older batches stored the same
 *  histogram score under quality.identity with method "hist". */
const matchOf = (quality) => {
  if (quality?.colour_match) return quality.colour_match;
  return quality?.identity?.method === "hist" ? quality.identity : null;
};

/**
 * @param {object} quality  a result's metadata.quality
 */
const ColourMatchPill = ({ quality }) => {
  const { t } = useUiTranslation();
  const match = matchOf(quality);
  if (typeof match?.score !== "number") return null;
  const where = typeof match.frame_index === "number"
    ? ` ${t("video.keyframeMatch.atFrame", { frame: match.frame_index, frames: match.frames })}`
    : "";
  return (
    <StatusPill
      label={t("video.colourMatch.label", { percent: Math.round(match.score * 100) })}
      tooltip={t("video.colourMatch.tooltip", { where })}
    />
  );
};

export default ColourMatchPill;
