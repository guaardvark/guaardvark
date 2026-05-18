import { useEffect, useRef } from 'react';
import axios from 'axios';
import { useAppStore } from '../stores/useAppStore';

// Global keyboard forwarder → DISPLAY=:99 via /api/agent-control/learn/input.
// Mounted once by AppContainer; all activation goes through the Zustand flag
// `keyboardForwardingEnabled`. When ON, window-level keydown events are routed
// to the agent screen unless the user is typing in a Guaardvark input.
//
// Printable characters are batched into a debounced `type` call so "hello"
// becomes one HTTP request, not five. Special keys (Enter, Backspace, arrows,
// F-keys) and modifier combos (Ctrl+A, Alt+Tab) flush the buffer and fire
// immediately as `hotkey` calls, which map to xdotool key names.
//
// Browser-reserved shortcuts (Ctrl+T, Ctrl+W, F5, etc.) are intentionally not
// intercepted — browsers won't let JS block them outside fullscreen keyboard
// lock. We cover everything else cleanly.

const API_URL = '/api/agent-control/learn/input';
const TYPE_DEBOUNCE_MS = 150;

// e.key → xdotool key name. Only keys we want to forward; anything not in this
// map and not a single printable char is ignored.
const KEY_MAP = {
  Enter: 'Return',
  Backspace: 'BackSpace',
  Tab: 'Tab',
  Escape: 'Escape',
  Delete: 'Delete',
  Insert: 'Insert',
  Home: 'Home',
  End: 'End',
  PageUp: 'Prior',
  PageDown: 'Next',
  ArrowLeft: 'Left',
  ArrowRight: 'Right',
  ArrowUp: 'Up',
  ArrowDown: 'Down',
  ' ': 'space',
};

// Keys the browser owns — don't try to intercept or we just annoy the user.
const BROWSER_RESERVED = new Set([
  'F1', 'F3', 'F4', 'F5', 'F6', 'F7', 'F10', 'F11', 'F12',
]);

// F2/F8/F9 are usually fine; F-keys we WILL forward:
for (let i = 1; i <= 12; i++) {
  const name = `F${i}`;
  if (!BROWSER_RESERVED.has(name)) {
    KEY_MAP[name] = name;
  }
}

function isEditableTarget(target) {
  if (!target) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (target.isContentEditable) return true;
  return false;
}

export default function useKeyboardForwarding() {
  const enabled = useAppStore((s) => s.keyboardForwardingEnabled);
  const typeBufferRef = useRef('');
  const flushTimerRef = useRef(null);

  useEffect(() => {
    if (!enabled) return;

    const postAction = (payload) => {
      axios.post(API_URL, payload).catch((err) => {
        console.error('[kbd-forward] failed:', err?.message || err);
      });
    };

    const flushType = () => {
      if (flushTimerRef.current) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      const text = typeBufferRef.current;
      typeBufferRef.current = '';
      if (text) postAction({ action: 'type', text });
    };

    const scheduleFlush = () => {
      if (flushTimerRef.current) clearTimeout(flushTimerRef.current);
      flushTimerRef.current = setTimeout(flushType, TYPE_DEBOUNCE_MS);
    };

    const onKeyDown = (e) => {
      // Don't hijack keys inside Guaardvark's own inputs — chat box, text
      // fields, Monaco, etc. User still controls the local UI.
      if (isEditableTarget(e.target)) return;

      // Browser-reserved — let them through untouched.
      if (BROWSER_RESERVED.has(e.key)) return;

      const hasModifier = e.ctrlKey || e.altKey || e.metaKey;

      if (hasModifier) {
        // Modifier combo → hotkey. Map printable to lowercase; xdotool wants
        // ctrl+a not ctrl+A for the letter chord.
        let keyPart = null;
        if (e.key.length === 1) {
          keyPart = e.key.toLowerCase();
        } else if (KEY_MAP[e.key]) {
          keyPart = KEY_MAP[e.key];
        }
        if (!keyPart) return;

        const parts = [];
        if (e.ctrlKey) parts.push('ctrl');
        if (e.altKey) parts.push('alt');
        if (e.metaKey) parts.push('super');
        if (e.shiftKey && keyPart.length !== 1) parts.push('shift');
        parts.push(keyPart);

        flushType();
        postAction({ action: 'hotkey', keys: parts.join('+') });
        e.preventDefault();
        return;
      }

      if (e.key.length === 1) {
        // Printable, no modifier — batch into type buffer. Shift is already
        // reflected in e.key (uppercase letters, shifted symbols).
        typeBufferRef.current += e.key;
        scheduleFlush();
        e.preventDefault();
        return;
      }

      // Special key, no modifier.
      const mapped = KEY_MAP[e.key];
      if (!mapped) return;
      flushType();
      postAction({ action: 'hotkey', keys: mapped });
      e.preventDefault();
    };

    window.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.removeEventListener('keydown', onKeyDown, true);
      // Flush pending buffer on teardown so a disable doesn't eat characters.
      if (flushTimerRef.current) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      if (typeBufferRef.current) {
        const text = typeBufferRef.current;
        typeBufferRef.current = '';
        axios.post(API_URL, { action: 'type', text }).catch(() => {});
      }
    };
  }, [enabled]);
}
