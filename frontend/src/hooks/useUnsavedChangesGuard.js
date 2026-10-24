// Asks before a page with unsaved edits is left.
//
// Covers closing or reloading the tab (the browser shows its own prompt) and
// in-app navigation through react-router: links, navigate(), navigate(-1).
// The app mounts <BrowserRouter>, where react-router's useBlocker is not
// available (it needs a data router), so while `when` is true the router's
// navigator is wrapped and a navigation to another path waits for a confirm.
// Staying on the same path (a search-param change) is never asked about.
//
// The browser's own Back and Forward buttons cannot be held this way and pass
// without a prompt; moving the app to a data router would cover them.
import { useContext, useEffect, useRef } from 'react';
import { UNSAFE_NavigationContext } from 'react-router-dom';
import { translate, useUiTranslation } from '../i18n';

// The English text; the guard asks in the interface's language.
export const DEFAULT_UNSAVED_MESSAGE = 'You have unsaved changes on this page. Leave and discard them?';

const trimSlash = (path) => (path.length > 1 ? path.replace(/\/+$/, '') : path);

/** Pathname a router navigation targets; `to` is a path string or a Path object. */
export const targetPathname = (to) => {
  if (to && typeof to === 'object') return to.pathname || null;
  if (typeof to === 'string') return to.split(/[?#]/)[0] || null;
  return null;
};

/** True when navigating to `to` would move off `currentPathname`. */
export const leavesPath = (to, currentPathname) => {
  const next = targetPathname(to);
  if (!next) return false;
  return trimSlash(next) !== trimSlash(currentPathname || '/');
};

// Several guards can be active at once (one per row of a list, say), and they
// mount and unmount in any order. The navigator is therefore wrapped once,
// while at least one guard holds it, and asks a single question for all.
const holds = new WeakMap(); // navigator -> { messages: Set<ref>, original }

const holdNavigator = (navigator, messageRef) => {
  let hold = holds.get(navigator);
  if (!hold) {
    const original = { push: navigator.push, replace: navigator.replace, go: navigator.go };
    const messages = new Set();
    const ask = () => window.confirm(messages.values().next().value.current);
    // The history's own location is in the same terms as the `to` it is
    // handed (basename included), unlike useLocation().
    const currentPathname = () => navigator.location?.pathname ?? window.location.pathname;
    const mayLeave = (to) => !leavesPath(to, currentPathname()) || ask();

    navigator.push = (to, ...rest) => {
      if (mayLeave(to)) original.push.call(navigator, to, ...rest);
    };
    navigator.replace = (to, ...rest) => {
      if (mayLeave(to)) original.replace.call(navigator, to, ...rest);
    };
    navigator.go = (delta) => {
      if (!delta || ask()) original.go.call(navigator, delta);
    };
    hold = { messages, original };
    holds.set(navigator, hold);
  }
  hold.messages.add(messageRef);
  return () => {
    hold.messages.delete(messageRef);
    if (hold.messages.size === 0) {
      Object.assign(navigator, hold.original);
      holds.delete(navigator);
    }
  };
};

const useUnsavedChangesGuard = (when, message = translate('messages.unsavedChanges.leaveConfirm')) => {
  // Re-render on a language change so the question below follows it.
  useUiTranslation();
  const { navigator } = useContext(UNSAFE_NavigationContext);
  const messageRef = useRef(message);
  messageRef.current = message;

  useEffect(() => {
    if (!when) return undefined;
    const onBeforeUnload = (event) => {
      event.preventDefault();
      // Some browsers still show the prompt only when returnValue is set.
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [when]);

  useEffect(() => {
    if (!when || !navigator) return undefined;
    return holdNavigator(navigator, messageRef);
  }, [when, navigator]);
};

export default useUnsavedChangesGuard;
