// The Agent Screen shows either in the page (the floating viewer) or in its
// own window (the /agent-screen page). Guaardvark windows on the same origin
// talk over one BroadcastChannel so only one of the two is up at a time.
//
// Messages, all { type }:
//   popout-open    the /agent-screen window is up (also the answer to ping)
//   popout-closed  the /agent-screen window is going away on its own
//   docked         the window's own button sent the screen back into the page
//   dock           a page asked for the screen back; the window closes
//   ping           a page that just loaded asks whether a window is up

export const AGENT_SCREEN_PATH = '/agent-screen';
export const AGENT_SCREEN_WINDOW_NAME = 'guaardvark_agent_screen';
const CHANNEL_NAME = 'guaardvark-agent-screen';

export function isAgentScreenWindow() {
  return window.location.pathname === AGENT_SCREEN_PATH;
}

// Listen for messages; returns a function that stops listening.
export function listenAgentScreen(onMessage) {
  if (typeof BroadcastChannel === 'undefined') return () => {};
  const channel = new BroadcastChannel(CHANNEL_NAME);
  channel.onmessage = (e) => onMessage(e.data || {});
  return () => channel.close();
}

// One long-lived sender per window, so a message posted just before the
// window closes is not lost with a channel closed straight after it.
let sender = null;

export function postAgentScreen(type) {
  if (typeof BroadcastChannel === 'undefined') return;
  if (!sender) sender = new BroadcastChannel(CHANNEL_NAME);
  sender.postMessage({ type });
}

export function openAgentScreenWindow() {
  const w = 1064;
  const h = 1100;
  const left = Math.max(0, (window.screen.width - w) / 2);
  const top = Math.max(0, (window.screen.height - h) / 2);
  return window.open(
    AGENT_SCREEN_PATH,
    AGENT_SCREEN_WINDOW_NAME,
    `popup=yes,width=${w},height=${h},left=${left},top=${top},resizable=yes`,
  );
}
