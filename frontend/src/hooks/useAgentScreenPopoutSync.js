import { useEffect } from 'react';
import { useAppStore } from '../stores/useAppStore';
import { isAgentScreenWindow, listenAgentScreen, postAgentScreen } from '../utils/agentScreenChannel';

// Keeps this page's floating Agent Screen viewer in step with the
// /agent-screen window. Mounted once by AppContainer; does nothing inside
// the /agent-screen window itself.
//
// - The window opens (or answers this page's ping after a reload): the
//   viewer hides and the screen counts as open.
// - The window is closed by the person: the Agent Screen closes here too.
// - The window's own "back to the page" button: the viewer shows again.
// - A dock from the sidebar has already cleared agentScreenPopout, so the
//   window's closing message that follows is ignored.
export default function useAgentScreenPopoutSync() {
  useEffect(() => {
    if (isAgentScreenWindow()) return undefined;
    const stop = listenAgentScreen((msg) => {
      const store = useAppStore.getState();
      if (msg.type === 'popout-open') {
        store.setAgentScreenPopout(true);
        store.setAgentScreenOpen(true);
      } else if (msg.type === 'popout-closed' && store.agentScreenPopout) {
        store.setAgentScreenPopout(false);
        store.setAgentScreenOpen(false);
      } else if (msg.type === 'docked') {
        store.setAgentScreenPopout(false);
        store.setAgentScreenOpen(true);
      }
    });
    postAgentScreen('ping');
    return stop;
  }, []);
}
