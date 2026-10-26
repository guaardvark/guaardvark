// When to ask for a GitHub star, and what this browser has answered so far.
// The ask is shown by DidYouKnowTip.jsx, in the same card slot as a tip.
// Someone becomes eligible after their first finished render or on their third
// day of use; the ask comes at most twice, and never again once answered.
// Nothing here leaves the browser: the only network step is the person's own
// click, which opens the project page in a new tab.

export const STAR_ASK_KEY = "guaardvark:star-ask";
/** Fired on window when a job finishes that counts as a win. */
export const STAR_ASK_WIN_EVENT = "guaardvark:star-ask-win";

/** Job kinds whose successful finish is a good moment to ask. */
export const WIN_PROCESS_TYPES = new Set([
  "image_generation",
  "video_gen",
  "video_render",
  "production",
  "upscale",
  "lora_train",
]);

export const DAYS_BEFORE_FIRST_ASK = 3;
export const DAYS_BEFORE_SECOND_ASK = 5;
export const MAX_ASKS = 2;

const EMPTY = Object.freeze({ wins: 0, dayCount: 0, lastDay: "", asks: 0, dayCountAtLastAsk: 0, done: false });

function storage() {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

const count = (v) => (Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);

/** This browser's star-ask record, with defaults for anything missing. Never throws. */
export function readStarAsk() {
  try {
    const raw = storage()?.getItem(STAR_ASK_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return {
      wins: count(parsed.wins),
      dayCount: count(parsed.dayCount),
      lastDay: typeof parsed.lastDay === "string" ? parsed.lastDay : "",
      asks: count(parsed.asks),
      dayCountAtLastAsk: count(parsed.dayCountAtLastAsk),
      done: parsed.done === true,
    };
  } catch {
    return { ...EMPTY };
  }
}

function writeStarAsk(state) {
  try {
    storage()?.setItem(STAR_ASK_KEY, JSON.stringify(state));
  } catch {
    // Storage full or blocked: the ask simply waits.
  }
  return state;
}

/** Today in the browser's own time zone, as YYYY-MM-DD. */
export function localDay(date = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Count today as a day of use, once per calendar day. */
export function recordDayOpened(today = localDay()) {
  const state = readStarAsk();
  if (state.lastDay === today) return state;
  return writeStarAsk({ ...state, dayCount: state.dayCount + 1, lastDay: today });
}

/** Note a finished job; a win also tells an open card slot it may ask now. */
export function recordWin(processType) {
  if (!WIN_PROCESS_TYPES.has(processType)) return null;
  const state = readStarAsk();
  if (state.done) return state;
  const next = writeStarAsk({ ...state, wins: state.wins + 1 });
  try {
    window.dispatchEvent(new Event(STAR_ASK_WIN_EVENT));
  } catch {
    // no window (tests, workers): the next session's check still sees the win
  }
  return next;
}

/** Whether this browser should be asked now. */
export function starAskDue(state = readStarAsk()) {
  if (state.done || state.asks >= MAX_ASKS) return false;
  if (state.asks === 0) return state.wins > 0 || state.dayCount >= DAYS_BEFORE_FIRST_ASK;
  return state.dayCount - state.dayCountAtLastAsk >= DAYS_BEFORE_SECOND_ASK;
}

export function markStarAsked() {
  const state = readStarAsk();
  return writeStarAsk({ ...state, asks: state.asks + 1, dayCountAtLastAsk: state.dayCount });
}

/** Starred, or asked not to be asked again: either way, never ask again. */
export function markStarAskDone() {
  return writeStarAsk({ ...readStarAsk(), done: true });
}
