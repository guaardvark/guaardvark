// Input forwarding from a picture of the agent display to the display itself.
//
// Shared by the floating Agent Screen viewer and its popup window, so both
// send the same events through the same route. Everything lands on
// /api/agent-control/learn/input, which drives display :99 with xdotool and,
// when a demonstration is being recorded, records the event as well.

import axios from 'axios';

export const INPUT_URL = '/api/agent-control/learn/input';

// Wheel deltas are pixels in Chrome and Firefox (deltaMode 0); a notch is
// about 100. Line and page modes are converted so one notch is one step.
const WHEEL_PIXELS_PER_STEP = 100;
const WHEEL_LINE_PX = 33;
const WHEEL_PAGE_PX = 400;
const WHEEL_FLUSH_MS = 80;
const WHEEL_MAX_STEPS = 10;

// The backend answers each request once xdotool has finished, but it serves
// requests in parallel, so "hello", Enter, "world" sent back to back come out
// interleaved. Every input from this page waits for the one before it.
// The timeout keeps one stuck request from holding up everything after it;
// typing runs at about 12 characters a second, so 30 s covers any burst.
const INPUT_TIMEOUT_MS = 30000;
let inputQueue = Promise.resolve();

export function sendScreenInput(payload) {
  const run = () => axios.post(INPUT_URL, payload, { timeout: INPUT_TIMEOUT_MS });
  const result = inputQueue.then(run, run);
  inputQueue = result.catch(() => {});
  return result;
}

// Map a pointer position over the <img> to display pixels. Frames arrive at
// the display's own resolution, so the image's natural size is the display
// size, whatever GUAARDVARK_AGENT_RESOLUTION is set to.
export function displayPoint(img, clientX, clientY) {
  const rect = img.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const w = img.naturalWidth || 1000;
  const h = img.naturalHeight || 1000;
  const x = Math.round(((clientX - rect.left) * w) / rect.width);
  const y = Math.round(((clientY - rect.top) * h) / rect.height);
  return {
    x: Math.max(0, Math.min(w - 1, x)),
    y: Math.max(0, Math.min(h - 1, y)),
  };
}

// Listen on `el` for clicks, right and middle clicks and the wheel, and send
// each one to the display when isActive() says so. Only events on the image
// itself count, so the FPS slider and resize handle stay local. Returns a
// function that removes the listeners.
export function attachMouseForwarding(el, getImg, { isActive, onSent, onError } = {}) {
  let wheelAcc = 0;
  let wheelPoint = null;
  let wheelTimer = null;

  const send = (payload) => {
    sendScreenInput(payload)
      .then(() => onSent?.())
      .catch((err) => onError?.(err));
  };

  const pointFor = (e) => {
    const img = getImg();
    if (!img || e.target !== img || !isActive?.()) return null;
    return displayPoint(img, e.clientX, e.clientY);
  };

  const onClick = (e) => {
    const p = pointFor(e);
    if (!p) return;
    e.preventDefault();
    send({ action: 'click', button: 'left', ...p });
  };

  const onContextMenu = (e) => {
    const p = pointFor(e);
    if (!p) return;
    e.preventDefault();
    send({ action: 'click', button: 'right', ...p });
  };

  const onAuxClick = (e) => {
    if (e.button !== 1) return;
    const p = pointFor(e);
    if (!p) return;
    e.preventDefault();
    send({ action: 'click', button: 'middle', ...p });
  };

  const flushWheel = () => {
    wheelTimer = null;
    const acc = wheelAcc;
    wheelAcc = 0;
    if (!acc || !wheelPoint) return;
    let steps = Math.round(acc / WHEEL_PIXELS_PER_STEP) || Math.sign(acc);
    steps = Math.max(-WHEEL_MAX_STEPS, Math.min(WHEEL_MAX_STEPS, steps));
    // Positive deltaY scrolls down; the backend's negative amount does too.
    send({ action: 'scroll', ...wheelPoint, amount: -steps });
  };

  const onWheel = (e) => {
    const p = pointFor(e);
    if (!p) return;
    e.preventDefault();
    const scale = e.deltaMode === 1 ? WHEEL_LINE_PX : e.deltaMode === 2 ? WHEEL_PAGE_PX : 1;
    wheelAcc += e.deltaY * scale;
    wheelPoint = p;
    if (!wheelTimer) wheelTimer = setTimeout(flushWheel, WHEEL_FLUSH_MS);
  };

  el.addEventListener('click', onClick);
  el.addEventListener('contextmenu', onContextMenu);
  el.addEventListener('auxclick', onAuxClick);
  // Not passive, or preventDefault cannot stop the page behind from scrolling.
  el.addEventListener('wheel', onWheel, { passive: false });

  return () => {
    el.removeEventListener('click', onClick);
    el.removeEventListener('contextmenu', onContextMenu);
    el.removeEventListener('auxclick', onAuxClick);
    el.removeEventListener('wheel', onWheel);
    if (wheelTimer) clearTimeout(wheelTimer);
  };
}
