// frontend/src/components/videogen/KeyframeMatchPill.jsx
// How much a cinematic clip's middle frame still shows what its keyframe showed
// (backend video_consistency_metrics.keyframe_match), by the image encoder
// installed in Settings > Generation > Cast identity check. Clips recorded
// without the encoder carry a colour match instead (ColourMatchPill).

import React from "react";
import { StatusPill } from "../settings/ui";
import { useUiTranslation } from "../../i18n";

/**
 * @param {object} quality  a result's metadata.quality
 */
const KeyframeMatchPill = ({ quality }) => {
  const { t } = useUiTranslation();
  const match = quality?.keyframe_match;
  if (typeof match?.score !== "number") return null;
  const where = typeof match.frame_index === "number"
    ? ` ${t("video.keyframeMatch.atFrame", { frame: match.frame_index, frames: match.frames })}`
    : "";
  const low = typeof match.threshold === "number" && match.score < match.threshold;
  return (
    <StatusPill
      tone={low ? "warn" : "neutral"}
      label={t("video.keyframeMatch.label", { percent: Math.round(match.score * 100) })}
      tooltip={t("video.keyframeMatch.tooltip", {
        where,
        hold: typeof match.threshold === "number"
          ? t("video.keyframeMatch.holdBelow", { percent: Math.round(match.threshold * 100) })
          : "",
      })}
    />
  );
};

export default KeyframeMatchPill;
