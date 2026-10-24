// Helpers for the image editor's operation list, which the backend applies
// in order (/api/files/image/edit).
import { translate } from "../i18n";

// The part of the original image the applied crops leave, in natural pixels,
// or null when nothing is cropped. Each crop op is relative to the image as
// the previous ops left it, so a second crop is offset by the first.
export function cropRectFromOperations(operations) {
  let rect = null;
  for (const op of operations || []) {
    if (op.type !== 'crop') continue;
    rect = {
      x: (rect ? rect.x : 0) + op.x,
      y: (rect ? rect.y : 0) + op.y,
      width: op.width,
      height: op.height,
    };
  }
  return rect;
}

// The backend applies operations in order, so a crop recorded after a rotate
// or flip is applied to the rotated or flipped image, while the editor draws
// the crop on the image as loaded. Until the preview follows that order, a
// crop is offered only while no rotate or flip has been applied; this says why.
export const cropAfterTransformReason = () => translate('messages.imageEdit.cropAfterTransform');

export function cropIsAvailable(operations) {
  return !(operations || []).some((op) => op.type === 'rotate' || op.type === 'flip');
}

// Display scale that fits a width x height region of natural pixels in the
// editor canvas without enlarging it: the same limits the uncropped preview
// gets from CSS (90vw wide, the viewport minus the 52px toolbar high).
export function fitScale(width, height, viewportWidth, viewportHeight) {
  if (!width || !height) return 1;
  return Math.min(1, (viewportWidth * 0.9) / width, (viewportHeight - 52) / height);
}
