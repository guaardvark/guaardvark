import { useCallback, useEffect, useRef, useState } from "react";
import { useVoiceSession } from "../contexts/VoiceSessionContext";

// If a send never makes the chat busy (refused as a duplicate, failed before
// starting) the queue moves on after this long instead of waiting forever.
const SEND_START_TIMEOUT_MS = 3000;

/**
 * Make the calling chat where voice transcripts go while it is mounted.
 *
 * A turn that arrives while `busy` (a reply is streaming) waits and goes out,
 * in order, when the chat is free. `send(text, turn)` is called at that
 * moment, so it uses whatever session the chat is on then. When the reply to
 * a voice turn finishes, the voice session is told so it can listen again;
 * a `send` that resolves to `false` has no reply coming and frees it at once.
 *
 * @param {{id: string, priority?: number, busy: boolean,
 *          send: (text: string, turn: object) => unknown, enabled?: boolean}} options
 * @returns {{queued: number}} turns waiting for the chat to be free
 */
export default function useVoiceSink({ id, priority = 0, busy, send, enabled = true }) {
  const voice = useVoiceSession();
  const queueRef = useRef([]);
  const sendRef = useRef(send);
  const busyRef = useRef(busy);
  const prevBusyRef = useRef(busy);
  const inFlightRef = useRef(false);
  const sawBusyRef = useRef(false);
  const startTimerRef = useRef(null);
  const [queued, setQueued] = useState(0);

  useEffect(() => {
    sendRef.current = send;
  }, [send]);

  const finishInFlight = useCallback(() => {
    clearTimeout(startTimerRef.current);
    startTimerRef.current = null;
    inFlightRef.current = false;
    voice.replyFinished();
  }, [voice]);

  const flush = useCallback(() => {
    if (busyRef.current || inFlightRef.current) return;
    const turn = queueRef.current.shift();
    setQueued(queueRef.current.length);
    if (!turn) return;
    inFlightRef.current = true;
    sawBusyRef.current = false;
    startTimerRef.current = setTimeout(() => {
      startTimerRef.current = null;
      if (inFlightRef.current && !sawBusyRef.current) {
        finishInFlight();
        flush();
      }
    }, SEND_START_TIMEOUT_MS);
    Promise.resolve()
      .then(() => sendRef.current(turn.text, turn))
      .then((sent) => {
        // `false`: the turn was taken without a reply to wait for.
        if (sent === false && inFlightRef.current) {
          finishInFlight();
          flush();
        }
      })
      .catch(() => {
        finishInFlight();
        flush();
      });
  }, [finishInFlight]);

  useEffect(() => {
    busyRef.current = busy;
    const wasBusy = prevBusyRef.current;
    prevBusyRef.current = busy;
    if (busy) {
      sawBusyRef.current = true;
      return;
    }
    if (wasBusy && inFlightRef.current) finishInFlight();
    flush();
  }, [busy, flush, finishInFlight]);

  useEffect(() => {
    if (!enabled) return undefined;
    return voice.registerSink({
      id,
      priority,
      deliver: (turn) => {
        queueRef.current.push(turn);
        setQueued(queueRef.current.length);
        flush();
      },
    });
  }, [voice, id, priority, enabled, flush]);

  useEffect(() => () => clearTimeout(startTimerRef.current), []);

  return { queued };
}
