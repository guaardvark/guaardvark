// One Socket.IO connection for the whole tab.
//
// A browser allows six HTTP connections per host. Each io() call to the root
// namespace opens its own Engine.IO connection, and a connection that is on
// the polling transport keeps one request open the whole time, so a page with
// five of them leaves one connection for every fetch on the page. Everything
// that listens to the backend therefore shares the connection opened here.

import { io } from "socket.io-client";
import { SOCKET_URL } from "./apiClient";

let shared = null;

const sharedSocket = () => {
  if (!shared) {
    shared = io(SOCKET_URL, {
      path: "/socket.io",
      // Polling first, then the upgrade: a websocket that a proxy accepts and
      // then drops never reports an error, so starting on it can leave the
      // tab with no connection, while a failed upgrade just stays on polling.
      transports: ["polling", "websocket"],
      upgrade: true,
      rememberUpgrade: true,
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 10000,
      timeout: 10000,
    });
  }
  return shared;
};

/**
 * A handle on the tab's connection with the parts of the socket.io-client
 * Socket the app uses: on, off, emit, connect, disconnect, connected,
 * disconnected, id and io.
 *
 * Listeners belong to the handle. off(event) and disconnect() remove only the
 * handle's own, and disconnect() leaves the connection up for the others.
 * A "connect" listener added while the connection is already up runs once
 * straight away, since that is where callers send their subscribe events.
 * Rooms joined through one handle are joined for the tab.
 */
export function openSocket() {
  const socket = sharedSocket();
  let listeners = [];
  let open = true;

  const handle = {
    on(event, fn) {
      listeners.push([event, fn]);
      socket.on(event, fn);
      if (event === "connect" && socket.connected) {
        queueMicrotask(() => {
          if (open && socket.connected && listeners.some(([e, f]) => e === event && f === fn)) fn();
        });
      }
      return handle;
    },
    off(event, fn) {
      const gone = (e, f) => (event === undefined || e === event) && (fn === undefined || f === fn);
      listeners.filter(([e, f]) => gone(e, f)).forEach(([e, f]) => socket.off(e, f));
      listeners = listeners.filter(([e, f]) => !gone(e, f));
      return handle;
    },
    emit(...args) {
      socket.emit(...args);
      return handle;
    },
    connect() {
      socket.connect();
      return handle;
    },
    disconnect() {
      handle.off();
      open = false;
      return handle;
    },
    get connected() {
      return socket.connected;
    },
    get disconnected() {
      return !socket.connected;
    },
    get id() {
      return socket.id;
    },
    get io() {
      return socket.io;
    },
  };
  return handle;
}

export default openSocket;
