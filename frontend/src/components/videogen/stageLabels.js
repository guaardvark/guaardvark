import { translate } from "../../i18n";

/** Human-readable VideoGen batch stage labels, in the interface's language. */
export const videoGenStageLabels = () => ({
  queued: translate("video.stage.queued"),
  gpu_wait: translate("video.stage.gpuWait"),
  storyboard: translate("video.stage.storyboard"),
  director: translate("video.stage.director"),
  keyframe: translate("video.stage.keyframe"),
  generate: translate("video.stage.generate"),
  post: translate("video.stage.post"),
  register: translate("video.stage.register"),
  done: translate("video.stage.done"),
});

export function videoGenStageLabel(stage) {
  if (!stage) return "";
  return videoGenStageLabels()[stage] || stage;
}
