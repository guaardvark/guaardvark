// Input forwarding from a picture of the agent display to the display itself.
//
// Shared by the floating Agent Screen viewer and the /agent-screen window, so
// both send the same events through the same route. Everything lands on
// /api/agent-control/learn/input, which drives display :99 with xdotool and,
// when a demonstration is being recorded, records clicks, keys and scrolls.

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
// interleaved. Every input from this window waits for the one before it.
// The timeout keeps one stuck request from holding up everything after it;
// typing runs at about 12 characters a second, so 30 s covers any burst.
const INPUT_TIMEOUT_MS = 30000;
let inputQueue = Promise.resolve();

function enqueue(task) {
  const result = inputQueue.then(task, task);
  inputQueue = result.catch(() => {});
  return result;
}

export function sendScreenInput(payload) {
  return enqueue(() => axios.post(INPUT_URL, payload, { timeout: INPUT_TIMEOUT_MS }));
}

// Pointer moves are coalesced: at most one waits in the queue, and when its
// turn comes it sends wherever the pointer is by then. A fast mouse therefore
// never builds a backlog, and moves stay in order with clicks and keys.
let pendingMove = null;

function sendMove(point) {
  const queued = pendingMove !== null;
  pendingMove = point;
  if (queued) return;
  enqueue(() => {
    const p = pendingMove;
    pendingMove = null;
    return p ? axios.post(INPUT_URL, { action: 'move', ...p }, { timeout: INPUT_TIMEOUT_MS }) : null;
  });
}

// Map a pointer position over the <img> to display pixels. Frames arrive at
// the display's own resolution, so the image's natural size is the display
// size, whatever GUAARDVARK_AGENT_RESOLUTION is set to. Points outside the
// image are pulled to its edge.
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

const BUTTONS = ['left', 'middle', 'right'];

// Listen on `el` for pointer input over the image and send it to the display.
// getMode() decides, per event:
//   'off'    nothing is sent
//   'click'  clicks (left, right, middle) and the wheel, as whole clicks;
//            used while a demonstration is recorded, which records clicks
//   'full'   press and release separately, pointer moves (hover, drag),
//            and the wheel
// Only events on the image itself count, so controls laid over it stay
// local. Returns a function that removes the listeners.
export function attachMouseForwarding(el, getImg, { getMode, onSent, onError } = {}) {
  const win = el.ownerDocument.defaultView;
  let wheelAcc = 0;
  let wheelPoint = null;
  let wheelTimer = null;
  let held = null; // button name while pressed in 'full' mode

  const mode = () => getMode?.() || 'off';

  const send = (payload) => {
    sendScreenInput(payload)
      .then(() => onSent?.(payload))
      .catch((err) => onError?.(err));
  };

  const overImage = (e) => {
    const img = getImg();
    return img && e.target === img ? img : null;
  };

  const pointFor = (e, img = getImg()) => (img ? displayPoint(img, e.clientX, e.clientY) : null);

  // --- 'click' mode ---------------------------------------------------
  const onClick = (e) => {
    if (!overImage(e) || mode() === 'off') return;
    e.preventDefault();
    if (mode() !== 'click') return;
    const p = pointFor(e);
    if (p) send({ action: 'click', button: 'left', ...p });
  };

  const onContextMenu = (e) => {
    if (!overImage(e) || mode() === 'off') return;
    e.preventDefault();
    if (mode() !== 'click') return;
    const p = pointFor(e);
    if (p) send({ action: 'click', button: 'right', ...p });
  };

  const onAuxClick = (e) => {
    if (e.button !== 1 || !overImage(e) || mode() !== 'click') return;
    e.preventDefault();
    const p = pointFor(e);
    if (p) send({ action: 'click', button: 'middle', ...p });
  };

  // --- 'full' mode ----------------------------------------------------
  const release = (e) => {
    if (!held) return;
    const button = held;
    held = null;
    const p = e && 'clientX' in e ? pointFor(e) : null;
    send({ action: 'up', button, ...(p || {}) });
  };

  const onMouseDown = (e) => {
    if (!overImage(e) || mode() !== 'full') return;
    // Stops image drag, text selection and middle-button autoscroll here.
    e.preventDefault();
    const button = BUTTONS[e.button];
    if (!button) return;
    if (held) release(e);
    const p = pointFor(e);
    if (!p) return;
    held = button;
    send({ action: 'down', button, ...p });
  };

  const onMouseUp = (e) => {
    if (held && BUTTONS[e.button] === held) release(e);
  };

  const onMouseMove = (e) => {
    if (mode() !== 'full') return;
    // While a button is held the drag follows the pointer even off the image.
    if (!held && !overImage(e)) return;
    const p = pointFor(e);
    if (p) sendMove(p);
  };

  // Losing the window mid-drag must not leave the display's button down.
  const onBlur = () => release(null);

  // --- wheel, both modes ----------------------------------------------
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
    if (!overImage(e) || mode() === 'off') return;
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
  el.addEventListener('mousedown', onMouseDown);
  // Not passive, or preventDefault cannot stop the page behind from scrolling.
  el.addEventListener('wheel', onWheel, { passive: false });
  win.addEventListener('mousemove', onMouseMove);
  win.addEventListener('mouseup', onMouseUp);
  win.addEventListener('blur', onBlur);

  return () => {
    el.removeEventListener('click', onClick);
    el.removeEventListener('contextmenu', onContextMenu);
    el.removeEventListener('auxclick', onAuxClick);
    el.removeEventListener('mousedown', onMouseDown);
    el.removeEventListener('wheel', onWheel);
    win.removeEventListener('mousemove', onMouseMove);
    win.removeEventListener('mouseup', onMouseUp);
    win.removeEventListener('blur', onBlur);
    if (wheelTimer) clearTimeout(wheelTimer);
    release(null);
  };
}
